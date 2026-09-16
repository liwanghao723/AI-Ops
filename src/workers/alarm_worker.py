"""告警 Worker（对应架构 T11 / workers/alarm_worker.py）。

- 拉取实时告警并 emit alarms_ready(list[WarnInfoDTO])
- 内置定时器，间隔可配（默认 30s，范围 10–300s）
- 首次立即拉取
- 全量拉取：分页累加直到 total 已取满 / 本页不足 page_size / 达到上限；
  超出上限则截断，避免爆量。

两条拉取路径（由 ``H3CWorkspaceClient.list_realtime_alarms`` 自动选择）：

- **前端会话新接口** ``/vdi/warnManage/realTimeAlarms``（首选）：
  服务端给出真实 ``totalLength``，``offset`` 可正常翻页，因此按
  ``_PAGE_SIZE``(2000) 逐页累加即可取满全部告警（实测 1.8w 条约 1s）。
- **Digest 旧接口** ``/vdi/rest/workspace/realtimeAlarms/list``（回退）：
  服务端**忽略** offset（offset 被套在 ``[:limit]`` 之上，offset>=limit
  直接返 0 行），且 ``totalLength`` 只是 limit 的回显，无法判定末页。
  因此旧接口只能「单次拉满」——此处把 page_size 抬到 ``MAX_ALARM_COUNT``。

历史 Bug（2026-09-14 修复）：旧版 ``MAX_ALARM_COUNT=10000`` 且旧接口无法翻页，
真实告警总量 18251 > 10000，导致最旧的 8251 条被永久截断，其中 11 条
**未确认**告警（维护模式/主机故障/主机不可达/iSCSI/节点管理网…）永不显示，
UI 只显示 10 条而平台显示 47 条。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal, Qt, QTimer

from core.models import WarnInfoDTO
from core.workspace_client import H3CWorkspaceClient
from core.logging_setup import get_logger
from .base_worker import BaseWorker


logger = get_logger(__name__)

_MIN_INTERVAL = 10
_MAX_INTERVAL = 300

# 告警拉取硬上限（内存保护兜底）：现网实测总量约 1.8w 条，留足 2.7 倍余量。
# 注意：此值**不是**「默认只拉这么多」——默认由 UI 的「上限」下拉框决定
# （默认「不限制」），这里只是防止服务端 total 异常时的爆量保护。
MAX_ALARM_COUNT = 50000

# 新接口（/vdi/warnManage/realTimeAlarms）单页条数。该接口 offset 可正常翻页，
# 故用适中页大小逐页累加，兼顾内存与请求数（1.8w 条约 10 页 / 1s）。
_PAGE_SIZE = 2000

# 旧接口无法翻页时的「单次拉满」条数（见模块 docstring）。
_LEGACY_SINGLE_SHOT = MAX_ALARM_COUNT


class AlarmWorker(BaseWorker):
    alarms_ready = pyqtSignal(list)  # list[WarnInfoDTO]
    # 平台顶部「未确认告警角标」（与平台网页红点同口径）；仅前端会话可用时发出
    warn_count_ready = pyqtSignal(dict)
    # 主线程点「查询」时触发，QueuedConnection 保证在 worker 线程执行 fetch_once
    request_fetch = pyqtSignal()

    def __init__(self, client: H3CWorkspaceClient, interval_sec: int = 30,
                 page_size: int = _PAGE_SIZE,
                 max_count: int | None = None) -> None:
        super().__init__()
        self.client = client
        self.interval_sec = max(_MIN_INTERVAL, min(interval_sec, _MAX_INTERVAL))
        self.page_size = page_size
        # 运行时可改的拉取上限：None 表示不限制（由 MAX_ALARM_COUNT 兜底保护）
        self._max_count: int | None = max_count
        self._filters: dict = {}
        self._timer: QTimer | None = None
        self.request_fetch.connect(self.fetch_once, Qt.QueuedConnection)

    def set_max_count(self, value: int | None) -> None:
        """设置拉取上限；None 表示不限制，下一次 fetch_once 生效。

        Args:
            value: 新的上限条数（int），或 None 表示不限制。
        """
        self._max_count = value

    def set_interval(self, sec: int) -> None:
        self.interval_sec = max(_MIN_INTERVAL, min(sec, _MAX_INTERVAL))
        if self._timer is not None:
            self._timer.setInterval(self.interval_sec * 1000)

    def set_filters(self, **filters) -> None:
        """设置可选过滤项（eventLevel/eventSrc/state/eventTime_from/to...）。"""
        self._filters = {k: v for k, v in filters.items() if v is not None}

    # ---- 线程事件循环 ----
    def run(self) -> None:
        self._timer = QTimer()
        self._timer.timeout.connect(self.fetch_once)
        # 首次立即拉取
        self.fetch_once()
        self._timer.start(self.interval_sec * 1000)
        self.exec_()  # 进入事件循环，持续轮询

    def fetch_once(self) -> None:
        """分页拉取实时告警，直到 total 已取满 / 本页不足 page_size / 达到上限。"""
        try:
            all_items: list[WarnInfoDTO] = []
            offset = 0
            total = None

            # 旧接口（含「前端会话中途失败已回退」）offset 无法翻页 → 一次拉满；
            # 新接口按页累加。用 alarm_paging_supported 而非直接看 frontend，
            # 以覆盖前端会话首屏失败后回退旧接口的场景。
            legacy = not getattr(self.client, "alarm_paging_supported", False)
            page_size = _LEGACY_SINGLE_SHOT if legacy else self.page_size
            # 上限兜底：「不限制」时仍用 MAX_ALARM_COUNT 防爆量。
            cap = (MAX_ALARM_COUNT if self._max_count is None
                   else max(0, min(int(self._max_count), MAX_ALARM_COUNT)))
            if legacy:
                logger.info("[AlarmWorker] 未启用前端会话，旧接口无法翻页，"
                            "改为单次拉满 %d 条", page_size)

            while True:
                logger.info(
                    "[AlarmWorker] 拉取告警页 limit=%d offset=%d (上限 %s) filters=%s",
                    page_size, offset, cap, self._filters,
                )
                result = self.client.list_realtime_alarms(
                    limit=page_size, offset=offset,
                    sort_dir=2, sort_field="eventTime", **self._filters,
                )
                total = result.total
                page_items = result.items
                # 单页返回量超过累计上限时，先截断本页，避免一次爆量
                if len(all_items) + len(page_items) > cap:
                    remaining = cap - len(all_items)
                    page_items = page_items[:max(0, remaining)]
                    logger.info(
                        "[AlarmWorker] 本页超过累计上限，截断至剩余 %d 条", remaining)
                logger.info(
                    "[AlarmWorker] 本页 %d 条，累计 %d/%s 条（上限 %s）",
                    len(page_items),
                    len(all_items) + len(page_items),
                    "?" if total is None else total, cap,
                )
                all_items.extend(page_items)
                # 终止条件（注意：total 已知时「信任 total」，短页不作为末页信号，
                # 否则服务端单页返回上限 < page_size 时会漏拉数据）：
                # 1) 已知 total 且已取满
                # 2) total 未知 且 本页数量不足 page_size（视为末页）
                # 3) 累计已达到上限
                # 4) 空页（防御：服务端 total 异常时避免 offset 不推进的死循环）
                reached_total = total is not None and len(all_items) >= total
                short_page_no_total = (total is None
                                       and len(page_items) < page_size)
                reached_cap = len(all_items) >= cap
                empty_page = not page_items
                if reached_total or short_page_no_total or reached_cap or empty_page:
                    break
                # 按服务端实际返回条数推进 offset，避免单页上限 < page_size 时跳页漏数据
                offset += len(page_items)
            # 安全兜底：极端情况下再多裁一刀，确保不超过上限
            if len(all_items) > cap:
                all_items = all_items[:cap]
            self.alarms_ready.emit(all_items)
            logger.info(
                "[AlarmWorker] 本次共拉取告警 %d 条（total=%s，上限 %s）",
                len(all_items), total, cap)

            # 平台未确认告警角标（GET /vdi/warnManage/warnCount）。
            # 与平台网页顶部红点完全同口径，便于核对「平台 N 条 vs 软件 N 条」。
            # 仅前端会话可用时才有值；失败不影响主流程。
            try:
                getter = getattr(self.client, "get_warn_count", None)
                counts = getter() if callable(getter) else {}
            except Exception as exc:
                logger.warning("[AlarmWorker] 读取告警角标失败: %s", exc)
                counts = {}
            if counts:
                self.warn_count_ready.emit(counts)
        except Exception as exc:  # 统一桥接
            self.bridge(exc)
