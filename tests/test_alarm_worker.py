"""workers.alarm_worker 分页与实时刷新测试。"""

from __future__ import annotations

import pytest

from core.models import RpcPagingResult, WarnInfoDTO
from workers.alarm_worker import AlarmWorker, MAX_ALARM_COUNT, _PAGE_SIZE


def _warn(i: int) -> WarnInfoDTO:
    return WarnInfoDTO(
        id=i,
        eventName=f"告警{i}",
        eventDesc="",
        eventLevel=1,
        eventTime=1000 * i,
        eventType=1,
        state=2,
        eventSrc="host01",
        eventCount=1,
    )


class _FrontendMixin:
    """标记该客户端「已启用平台前端会话」。

    AlarmWorker 会据此判断走哪条路径：
      * 有 frontend → 新接口 /vdi/warnManage/realTimeAlarms，offset 可翻页
      * 无 frontend → 旧接口，offset 无效，只能单次拉满
    """

    frontend = True
    alarm_paging_supported = True


class FakeClient(_FrontendMixin):
    """按页返回，用于验证 AlarmWorker 是否会翻页。"""

    def __init__(self, pages: list[list[WarnInfoDTO]]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int, sort_field: str, **filters) -> RpcPagingResult:
        self.calls.append({
            "limit": limit,
            "offset": offset,
            "sort_dir": sort_dir,
            "sort_field": sort_field,
            "filters": dict(filters),
        })
        idx = offset // limit if limit else 0
        total = sum(len(p) for p in self.pages)
        if idx < len(self.pages):
            return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                                   state=0, total=total, items=self.pages[idx])
        return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                               state=0, total=total, items=[])


class FakeServerClient(_FrontendMixin):
    """按 total + 当前 limit/offset 动态切片，模拟真实分页服务端。"""

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[dict] = []

    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int, sort_field: str, **filters) -> RpcPagingResult:
        self.calls.append({
            "limit": limit,
            "offset": offset,
            "sort_dir": sort_dir,
            "sort_field": sort_field,
            "filters": dict(filters),
        })
        end = min(offset + limit, self.total)
        page = [_warn(i) for i in range(offset, end)]
        return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                               state=0, total=self.total, items=page)


class ServerCapClient(_FrontendMixin):
    """服务端『单页返回上限』为 ``cap``，但会如实上报真实 ``total``。

    用于验证：当服务端单页上限 < 请求的 page_size 时，循环是否仍按 total 正确翻页
    （当前源码的 ``len(page_items) < page_size`` 终止条件会在此场景误判到底、漏拉数据）。
    """

    def __init__(self, total: int, cap: int) -> None:
        self.total = total
        self.cap = cap
        self.calls: list[dict] = []

    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int, sort_field: str, **filters) -> RpcPagingResult:
        self.calls.append({
            "limit": limit,
            "offset": offset,
            "sort_dir": sort_dir,
            "sort_field": sort_field,
            "filters": dict(filters),
        })
        n = min(self.cap, self.total - offset)
        if n <= 0:
            return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                                   state=0, total=self.total, items=[])
        page = [_warn(i) for i in range(offset, offset + n)]
        return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                               state=0, total=self.total, items=page)


class IgnoreLimitClient(_FrontendMixin):
    """服务端『无视 limit』，offset=0 直接吐出 ``total`` 条 —— 用于真实触发截断分支。"""

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[dict] = []

    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int, sort_field: str, **filters) -> RpcPagingResult:
        self.calls.append({
            "limit": limit,
            "offset": offset,
            "sort_dir": sort_dir,
            "sort_field": sort_field,
            "filters": dict(filters),
        })
        if offset != 0:
            return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                                   state=0, total=self.total, items=[])
        page = [_warn(i) for i in range(self.total)]
        return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                               state=0, total=self.total, items=page)


class LegacyClient:
    """模拟「未启用前端会话」的旧接口客户端（无 ``frontend`` 属性）。

    旧接口 ``/vdi/rest/workspace/realtimeAlarms/list`` 的 offset 被服务端
    套在 ``[:limit]`` 之上（offset>=limit 直接返 0 行），因此必须单次拉满。
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[dict] = []

    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int, sort_field: str, **filters) -> RpcPagingResult:
        self.calls.append({"limit": limit, "offset": offset})
        # 旧接口：offset>=limit 时返回 0 行（真机实测行为）
        if offset >= limit:
            return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                                   state=0, total=0, items=[])
        page = [_warn(i) for i in range(offset, min(offset + limit, self.total))]
        return RpcPagingResult(success=True, errorCode=0, failureMessage="",
                               state=0, total=len(page), items=page)


def _collect(worker: AlarmWorker) -> list[WarnInfoDTO]:
    emitted: list[list[WarnInfoDTO]] = []
    worker.alarms_ready.connect(emitted.append)
    worker.fetch_once()
    assert len(emitted) == 1
    return emitted[0]


def test_fetch_once_paginates_to_completion() -> None:
    pages = [
        [_warn(0), _warn(1), _warn(2)],
        [_warn(3), _warn(4), _warn(5)],
        [_warn(6)],
    ]
    client = FakeClient(pages)
    worker = AlarmWorker(client, interval_sec=30, page_size=3)

    result = _collect(worker)

    assert len(result) == 7
    assert [w.id for w in result] == list(range(7))
    assert len(client.calls) == 3
    assert client.calls[0]["offset"] == 0
    assert client.calls[1]["offset"] == 3
    assert client.calls[2]["offset"] == 6


def test_fetch_once_single_page_no_extra_call() -> None:
    pages = [[_warn(0), _warn(1)]]
    client = FakeClient(pages)
    worker = AlarmWorker(client, interval_sec=30, page_size=50)

    result = _collect(worker)

    assert len(result) == 2
    assert len(client.calls) == 1
    assert client.calls[0]["offset"] == 0


def test_fetch_once_empty() -> None:
    client = FakeClient([])
    worker = AlarmWorker(client, interval_sec=30, page_size=50)

    result = _collect(worker)

    assert result == []
    assert len(client.calls) == 1


def test_fetch_once_respects_max_count() -> None:
    """total=12000 且显式 max_count=10000 时，拉到 10000 即停止，不再多拉。"""
    client = FakeServerClient(12000)
    worker = AlarmWorker(client, interval_sec=30, max_count=10000)

    result = _collect(worker)

    assert len(result) == 10000
    # page_size=2000 → 5 页（0/2000/4000/6000/8000），第 5 页填满上限即终止
    assert len(client.calls) == 5
    assert client.calls[0]["limit"] == _PAGE_SIZE
    assert client.calls[0]["offset"] == 0


def test_fetch_once_default_is_unlimited_up_to_hard_cap() -> None:
    """默认（max_count=None）时不受 1w 限制，能拉到真实 total。

    回归：旧版默认上限 10000，平台真实总量 18251 → 截断 8251 条，
    其中 11 条未确认告警永久丢失（平台 47 条 / 软件 10 条）。
    """
    client = FakeServerClient(12000)
    worker = AlarmWorker(client, interval_sec=30)

    assert worker._max_count is None

    result = _collect(worker)

    assert len(result) == 12000
    assert [w.id for w in result] == list(range(12000))


def test_fetch_once_small_total_single_page() -> None:
    """total=612、page_size=10000 时，一次拉完全部 612 条。"""
    client = FakeServerClient(612)
    worker = AlarmWorker(client, interval_sec=30)

    result = _collect(worker)

    assert len(result) == 612
    assert [w.id for w in result] == list(range(612))
    assert len(client.calls) == 1
    assert client.calls[0]["offset"] == 0


def test_fetch_once_original_pagination_still_works() -> None:
    """原有分页循环（page_size=50）在 total=612 时仍正确翻页至完成。"""
    client = FakeServerClient(612)
    worker = AlarmWorker(client, interval_sec=30, page_size=50)

    result = _collect(worker)

    assert len(result) == 612
    # 612 / 50 = 13 页（最后一页 12 条）
    assert len(client.calls) == 13
    assert client.calls[0]["offset"] == 0
    assert client.calls[-1]["offset"] == 600


def test_fetch_once_server_returns_over_limit_truncated() -> None:
    """服务端单次返回超过上限（无视 limit）时，截断到 MAX_ALARM_COUNT 条。

    注意：必须用『无视 limit』的客户端才能真正触发
    ``page_items = page_items[:remaining]`` 截断分支；若服务端遵守 limit，
    则单页最多返回 limit 条，截断分支永远不会被执行。
    """
    # 模拟服务端忽略 limit，offset=0 直接吐出 15000 条；上限设 10000
    client = IgnoreLimitClient(15000)
    worker = AlarmWorker(client, interval_sec=30, max_count=10000)

    result = _collect(worker)

    assert len(result) == 10000
    assert len(client.calls) == 1


def test_fetch_once_known_total_paginates_past_short_page() -> None:
    """回归测试：服务端单页上限 < page_size 但如实上报 total 时，必须按 total 翻页到底。

    场景：服务端每页最多返回 1000 条（cap=1000），但 total=5000；
    page_size=10000（本次改动后的默认值）。当前源码的
    ``len(page_items) < self.page_size`` 终止条件会把『1000 < 10000』误判为『已到末页』，
    从而提前 break，丢掉 total 明确告知还存在的 4000 条告警。

    正确行为：total 已知时，应信任 total 继续翻页，直到 len(all_items) >= total。
    """
    client = ServerCapClient(total=5000, cap=1000)
    worker = AlarmWorker(client, interval_sec=30, page_size=MAX_ALARM_COUNT)

    result = _collect(worker)

    assert len(result) == 5000
    assert [w.id for w in result] == list(range(5000))
    # 每页 1000 条 → 5 次请求（offset 0/1000/2000/3000/4000）
    assert len(client.calls) == 5
    assert [c["offset"] for c in client.calls] == [0, 1000, 2000, 3000, 4000]


def test_fetch_once_max_count_none_pulls_all() -> None:
    """max_count=None（不限制）时，即便 total 超过默认上限也全部拉取。

    场景：total=12000、page_size=_PAGE_SIZE(2000)，max_count=None。
    按 2000/页翻 6 次取满 12000 条，循环因 reached_total 终止，
    而非被硬上限截断（旧版 10000 上限会在此丢掉 2000 条）。
    """
    client = FakeServerClient(12000)
    worker = AlarmWorker(client, interval_sec=30, max_count=None)

    assert worker._max_count is None

    result = _collect(worker)

    assert len(result) == 12000
    assert [w.id for w in result] == list(range(12000))
    # 六页：offset 0/2000/4000/6000/8000/10000
    assert len(client.calls) == 6
    assert [c["offset"] for c in client.calls] == [0, 2000, 4000, 6000, 8000, 10000]


def test_set_max_count_affects_next_fetch() -> None:
    """运行时改上限，下一次 fetch_once 即生效。

    用 FakeServerClient 控制 total=12000：
    - 第一次（默认不限制）：按 2000/页翻 6 次取满 12000 条
    - set_max_count(5000) 后：翻 3 页（2000/2000/1000）截断到 5000 条
    """
    client = FakeServerClient(12000)
    worker = AlarmWorker(client, interval_sec=30)

    first = _collect(worker)
    assert len(first) == 12000

    # 运行时改上限，下一次 fetch_once 即用新上限
    worker.set_max_count(5000)
    assert worker._max_count == 5000

    second = _collect(worker)
    assert len(second) == 5000
    # 第一次 6 页 + 第二次 3 页 = 9 次请求
    assert len(client.calls) == 9
    assert client.calls[6]["offset"] == 0
    assert client.calls[7]["offset"] == 2000
    assert client.calls[8]["offset"] == 4000


def test_legacy_client_pulls_single_shot() -> None:
    """未启用前端会话（旧接口）时，必须单次拉满而非按 page_size 翻页。

    旧接口 offset 被服务端套在 [:limit] 之上（offset>=limit 直接返 0 行），
    按 page_size 翻页会漏数据 —— 此处验证 worker 会把 limit 抬到
    MAX_ALARM_COUNT 一次取完，且只发一次请求。
    """
    client = LegacyClient(18251)
    worker = AlarmWorker(client, interval_sec=30, page_size=_PAGE_SIZE)

    result = _collect(worker)

    assert len(result) == 18251
    assert len(client.calls) == 1
    assert client.calls[0]["limit"] == MAX_ALARM_COUNT
    assert client.calls[0]["offset"] == 0

