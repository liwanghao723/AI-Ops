"""H3C Workspace REST 客户端（对应架构 T4）。

封装 5 个 REST 接口：
  2.12.1  实时告警列表(分页)   GET  /realtimeAlarms/list
  2.27.33 虚拟机分页查询(替代2.27.40) GET /vms/page  ← query_vm_list 现走此分页接口（2.27.40 服务端硬截断 60 条）
  2.27.40 虚拟机列表(不分页)   GET  /vms/queryVmList（已弃用：服务端硬截断 60 条）
  2.9.11  虚拟机概要           GET  /virtual/domain/{id}/summary
  2.27.31 虚拟机批量性能       GET  /vms/monitors/all（桌面级口径）
  2.4.12  主机性能             GET  /hosts/{id}/cpumemdiskrate（宿主级，备选/保留）
  2.27.39 批量UUID(可选)       POST /vms/queryUuidsByVmUuids
  2.4.7   主机列表(可选)       GET  /hosts
  2.26    虚拟机使用率查询(新增) GET  /vdi/rest/center/vmusages  ← 前缀为 center，非 workspace
  2.27.30 单台VM性能监控(新增)  GET  /vms/monitor/{domainUuid}（按 UUID，与 2.27.31 批量独立）
  2.27.57 根据id查虚拟机信息(新增) GET /vms/{domainUuid}（桌面 IPv4 主来源：data.networks[].ipAddr）
  2.4.5   主机性能监控(新增)    GET  /hosts/hostMonitor（含每台主机 top5 桌面率 vmTopCpuRate/vmTopMemRate）

每个方法把统一外层包成 RpcResult / RpcPagingResult，
raise_if_fail() 在 success=False 或 errorCode!=0 时抛 WorkspaceAPIError。

基础路径：http://<ip>:<port>/vdi/rest/workspace/{URI}
（2.26 虚拟机使用率接口前缀为 /vdi/rest/center，由 _CENTER_PREFIX 单独处理）
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .digest_auth import DigestAuthHTTP
from .errors import AuthError, WorkspaceAPIError
from .frontend_auth import FrontendSession
from .models import (
    HostBrief, HostPerf, RpcPagingResult, RpcResult,
    RsDomainSummary, VmBrief, VmDetail, VmPerf, WarnInfoDTO,
)
from .logging_setup import get_logger


logger = get_logger(__name__)

# H3C Workspace REST 固定前缀
_REST_PREFIX = "/vdi/rest/workspace"

# 2.26 虚拟机使用率查询前缀（与 workspace 前缀不同，需临时切换底层 base_url 根）
_CENTER_PREFIX = "/vdi/rest/center"

# 平台前端会话（Spring Cookie 域）告警接口前缀。
# 与 /vdi/rest/workspace/realtimeAlarms/list 的区别（真机实测 2026-09-14）：
#   * totalLength = 真实总数（旧接口的 totalLength 只是 limit 回显，无法判定末页）
#   * offset 可正常翻页（旧接口 offset 被套在 [:limit] 之上，offset>=limit 直接返 0 行）
#   * state / eventLevel 过滤真实生效（旧接口忽略所有过滤参数）
#   * category 覆盖多子系统：终端(123)/License(135)/虚拟应用(99)/ONEStor(20)/...
#     （旧接口只返回 VDI+ONEStor 子集，永远拿不到 License/终端/虚拟应用告警）
_WARN_PREFIX = "/vdi/warnManage"


def _snake_to_camel(name: str) -> str:
    """把 snake_case 转为 camelCase（如 ``cluster_id`` -> ``clusterId``）。

    用于把 Python 风格的关键字参数统一成 H3C 接口的 camelCase 查询参数名。
    camelCase 字符串（无下划线）原样返回，避免二次转换。
    """
    parts = name.split("_")
    if len(parts) == 1:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])

# 2.27.31 批量性能分页保护：单次 limit 与最大翻页次数（200 * 200 = 40000 台，
# 远超现网规模，仅用于防止服务端 total 异常时无限循环）
_MONITORS_PAGE_LIMIT = 200
_MONITORS_MAX_PAGES = 200

# 2.27.33 虚拟机分页查询（替代 2.27.40 不分页版，后者服务端硬截断 60 条）：
# 单次 limit 与最大翻页次数（200 * 50 = 10000 台，远超现网规模，仅防异常死循环）
_VM_PAGE_SIZE = 200
_VM_LIST_MAX_PAGES = 50


class H3CWorkspaceClient:
    """封装 H3C Workspace 平台 REST 接口的客户端。"""

    def __init__(self, http: DigestAuthHTTP,
                 frontend: "FrontendSession | None" = None) -> None:
        self.http = http
        # 平台前端会话（可选）。未配置 frontend_password 时为 None，
        # 告警采集自动回退到旧接口 /vdi/rest/workspace/realtimeAlarms/list。
        self.frontend = frontend
        # 前端告警接口健康标记：失败一次后置 False，避免每页都重试登录；
        # 重建 client（配置热重载）时自动恢复。
        self._frontend_alarm_ok = True

    @property
    def alarm_paging_supported(self) -> bool:
        """告警接口当前是否支持 ``offset`` 翻页。

        - True：走前端会话新接口，可按页累加取满真实 total；
        - False：走 Digest 旧接口，offset 被服务端套在 ``[:limit]`` 之上
          （offset>=limit 直接返 0 行），只能单次拉满。

        ``AlarmWorker`` 据此决定 page_size，避免「前端会话中途失败回退旧接口
        后仍按小分页翻页」导致只拿到第一页的隐蔽漏数据。
        """
        return self.frontend is not None and self._frontend_alarm_ok

    # ------------------------------------------------------------------
    # 工厂：从 AppConfig 构建带摘要认证的客户端
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: Any) -> "H3CWorkspaceClient":
        from .config import AppConfig  # 延迟导入避免循环
        if not isinstance(cfg, AppConfig):
            raise TypeError("from_config 需要 AppConfig 实例")
        base = cfg.platform.base_url.rstrip("/") + _REST_PREFIX
        # 连接池按采集并发数放大：池容量 ≥ 并发线程数，才能让每个线程都拿到
        # 一条被复用的 keep-alive 连接（池满时也不会阻塞，只会新建临时连接）。
        conc = max(1, int(getattr(getattr(cfg, "health", None),
                                  "max_concurrency", 5) or 5))
        http = DigestAuthHTTP(
            base_url=base,
            user=cfg.platform.user,
            password=cfg.platform.password,
            verify_ssl=cfg.platform.verify_ssl,
            timeout=10.0,
            pool_connections=max(10, min(conc, 64)),
            pool_maxsize=max(32, conc * 2),
        )

        frontend: FrontendSession | None = None
        fpwd = str(getattr(cfg.platform, "frontend_password", "") or "")
        if fpwd:
            try:
                frontend = FrontendSession(
                    base_url=cfg.platform.base_url.rstrip("/"),
                    user=str(getattr(cfg.platform, "frontend_user", "") or "admin"),
                    password=fpwd,
                    verify_ssl=bool(cfg.platform.verify_ssl),
                    timeout=15.0,
                    pool_connections=4,
                    pool_maxsize=8,
                )
                logger.info("[Client] 已启用平台前端会话（告警接口走 "
                            "/vdi/warnManage/realTimeAlarms）")
            except Exception as exc:      # 缺 pycryptodome 等：降级不致命
                logger.warning("[Client] 前端会话初始化失败，告警回退旧接口: %s", exc)
                frontend = None
        else:
            logger.info("[Client] 未配置 platform.frontend_password，"
                        "告警回退旧接口 /vdi/rest/workspace/realtimeAlarms/list")
        return cls(http, frontend)

    def close(self) -> None:
        """释放底层连接池（配置热重载 / 程序退出前调用，幂等）。"""
        closer = getattr(self.http, "close", None)
        if callable(closer):
            closer()
        if self.frontend is not None:
            try:
                self.frontend.close()
            except Exception:
                pass

    def __enter__(self) -> "H3CWorkspaceClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 统一请求封装（集中补全「方法 + 完整 URI + 状态码 + 失败信息」日志）
    # ------------------------------------------------------------------
    def _request(self, method: str, uri: str, *,
                 params: dict | None = None,
                 json: dict | None = None,
                 prefix: str | None = None) -> dict:
        """统一发起 REST 请求（HTTP 层），失败时补全可排查日志后上抛。

        Args:
            method: ``"GET"`` 或 ``"POST"``。
            uri: 相对路径（不含前缀，如 ``"/vms/monitors/all"``）。
            params: GET 查询参数。
            json: POST body。
            prefix: 自定义 REST 前缀（如 ``_CENTER_PREFIX``）。为 ``None`` 时沿用
                默认 workspace 前缀；非 ``None`` 时临时把底层 ``self.http.base_url``
                从 workspace 根切到对应前缀根再发起请求，请求结束后立即恢复。
                对测试替身 FakeHTTP（无 ``base_url`` 属性）直接按原样调用，保持兼容。

        Returns:
            dict：H3C 统一外层响应（尚未校验 success/errorCode）。

        Raises:
            WorkspaceAPIError: HTTP 层业务失败（如 4xx）时由 digest_auth 抛出，
                此处捕获并补全「方法 + 完整 URI + 状态码 + 失败信息」日志后上抛。
                其余异常（网络/认证）原样透传。
        """
        target_prefix = prefix or _REST_PREFIX
        # 前缀切换：仅当底层 http 暴露 base_url 属性时切换（真实 DigestAuthHTTP）；
        # 测试替身 FakeHTTP 没有 base_url，直接按原样调用，避免报错。
        http = self.http
        saved_base_url = None
        swapped = False
        if prefix and hasattr(http, "base_url"):
            saved_base_url = http.base_url
            root = saved_base_url.rstrip("/")
            if root.endswith(_REST_PREFIX):
                root = root[: -len(_REST_PREFIX)]
            http.base_url = root + prefix
            swapped = True
        try:
            if method == "POST":
                return http.post(uri, json=json)
            return http.get(uri, params=params)
        except WorkspaceAPIError as exc:
            logger.error(
                "[Client] REST 失败: %s %s -> %s: %s",
                method, self._format_uri(uri, params, prefix=target_prefix),
                exc.code, exc.message,
            )
            raise
        finally:
            if swapped and saved_base_url is not None:
                http.base_url = saved_base_url

    def _require_ok(self, raw: dict, method: str, uri: str, *,
                    params: dict | None = None, paging: bool = False,
                    prefix: str | None = None):
        """校验 H3C 统一外层的 success/errorCode，失败时补全 URI 日志后上抛。

        与 ``_request`` 配合覆盖两类失败：
          - HTTP 层 4xx（在 ``_request`` 内由 digest_auth 抛 ``WorkspaceAPIError``）；
          - 业务层 success=False / errorCode!=0（在此处 ``raise_if_fail`` 抛出）。

        二者均会被统一记录「请求方法 + 完整 URI + 状态码 + failureMessage」。

        Args:
            raw: ``_request`` 返回的统一外层 dict。
            method: 请求方法（用于日志）。
            uri: 相对路径（用于日志）。
            params: 请求查询参数（用于日志）。
            paging: True 时构造 ``RpcPagingResult``，否则 ``RpcResult``。
            prefix: 自定义 REST 前缀（用于日志路径拼接，默认 workspace 前缀）。

        Returns:
            RpcResult / RpcPagingResult 实例（已通过 ``raise_if_fail``）。

        Raises:
            WorkspaceAPIError: 业务失败时上抛（已补全 URI 日志）。
        """
        target_prefix = prefix or _REST_PREFIX
        if paging:
            # RpcPagingResult 不含 data 字段，仅取统一外层基础字段校验
            fields = self._rpc_fields(raw)
            base = {k: fields[k] for k in ("success", "errorCode",
                                          "failureMessage", "state")}
            result = RpcPagingResult(**base)
        else:
            result = RpcResult[list](**self._rpc_fields(raw))
        try:
            result.raise_if_fail()
        except WorkspaceAPIError as exc:
            logger.error(
                "[Client] REST 失败: %s %s -> %s: %s",
                method, self._format_uri(uri, params, prefix=target_prefix),
                exc.code, exc.message,
            )
            raise
        return result

    @staticmethod
    def _format_uri(uri: str, params: dict | None = None, *,
                   prefix: str = _REST_PREFIX) -> str:
        """拼出用于日志的完整请求 URI（前缀 + 路径 + 查询串）。

        Args:
            uri: 相对路径（不含前缀）。
            params: 查询参数（拼到 ``?`` 之后）。
            prefix: REST 前缀，默认 workspace 前缀；center 前缀接口传入
                ``_CENTER_PREFIX`` 以保证日志路径前缀正确。
        """
        path = f"{prefix}{uri}"
        if params:
            path = f"{path}?{urlencode(params)}"
        return path

    # ------------------------------------------------------------------
    # 2.12.1 实时告警列表（分页）
    #
    # 两条实现路径，按可用性自动选择：
    #   A. 前端会话新接口 GET /vdi/warnManage/realTimeAlarms（**首选**）
    #      —— 平台「告警管理 → 实时告警」页面同款接口。真实 totalLength、
    #         offset 可翻页、state/eventLevel 过滤生效、覆盖多子系统
    #         （终端/License/虚拟应用/ONEStor…）。需配置 frontend_password。
    #   B. Digest 旧接口 GET /vdi/rest/workspace/realtimeAlarms/list（**回退**）
    #      —— 无需前端密码，但服务端忽略所有过滤参数、offset 无法翻页、
    #         totalLength 仅为 limit 回显，只能单次拉满且会漏子系统告警。
    # ------------------------------------------------------------------
    def list_realtime_alarms(self, *, limit: int, offset: int,
                             sort_dir: int = 2, sort_field: str = "eventTime",
                             **filters) -> RpcPagingResult[WarnInfoDTO]:
        if self.frontend is not None and self._frontend_alarm_ok:
            try:
                return self._list_alarms_frontend(
                    limit=limit, offset=offset, sort_dir=sort_dir,
                    sort_field=sort_field, filters=filters)
            except Exception as exc:        # 登录失败/接口异常 → 本轮回退旧接口
                self._frontend_alarm_ok = False
                logger.warning(
                    "[Client] 前端告警接口不可用，本次回退旧接口 "
                    "/vdi/rest/workspace/realtimeAlarms/list: %s", exc)
        return self._list_alarms_rest(
            limit=limit, offset=offset, sort_dir=sort_dir,
            sort_field=sort_field, filters=filters)

    def _list_alarms_frontend(self, *, limit: int, offset: int, sort_dir: int,
                              sort_field: str,
                              filters: dict) -> RpcPagingResult[WarnInfoDTO]:
        """路径 A：平台前端会话告警接口（与告警管理页一致）。"""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sortDir": sort_dir,
            "sortField": sort_field,
        }
        # 新接口确认生效的过滤项（旧接口被忽略的那些，这里逐个验证过）
        for key in ("category", "eventLevel", "eventSrc", "state", "eventDesc"):
            val = filters.get(key)
            if val is not None:
                params[key] = val

        uri = f"{_WARN_PREFIX}/realTimeAlarms"
        resp = self.frontend.get(uri, params=params)
        try:
            raw = resp.json()
        except ValueError as exc:
            raise WorkspaceAPIError(
                resp.status_code,
                f"[前端告警] 响应非 JSON: {resp.text[:200]}") from exc
        if not isinstance(raw, dict):
            raise WorkspaceAPIError(resp.status_code,
                                    f"[前端告警] 响应结构异常: {type(raw)}")
        if not raw.get("success"):
            raise WorkspaceAPIError(
                int(raw.get("errorCode") or -1),
                f"[前端告警] {raw.get('failureMessage') or '接口返回失败'}")

        items = self._extract_list(raw.get("data"))
        # totalLength 位于**外层**（旧代码错在从 data 里找 total，永远为 fallback）
        total = self._extract_total_from_raw(raw, len(items))
        result = RpcPagingResult(
            success=True,
            errorCode=int(raw.get("errorCode") or 0),
            failureMessage=str(raw.get("failureMessage") or ""),
            state=int(raw.get("state") or 0),
            total=total,
            items=[WarnInfoDTO(**self._clean_warn(d)) for d in items],
        )
        logger.info("[Client] 前端告警接口 limit=%d offset=%d -> %d 条（total=%d）",
                    limit, offset, len(items), total)
        return result

    def get_warn_count(self) -> dict[str, int]:
        """平台顶部告警角标计数（GET /vdi/warnManage/warnCount）。

        Returns:
            dict：``{"urgent": 紧急, "important": 重要,
            "accessory": 次要, "warning": 提示}``，均为**未确认**数量。
            前端会话不可用时返回空 dict（调用方需自行容错）。
        """
        if self.frontend is None:
            return {}
        try:
            resp = self.frontend.get(f"{_WARN_PREFIX}/warnCount")
            raw = resp.json()
        except Exception as exc:
            logger.warning("[Client] 读取告警角标失败: %s", exc)
            return {}
        if not isinstance(raw, dict) or not raw.get("success"):
            return {}
        data = raw.get("data")
        if not isinstance(data, dict):
            return {}
        out: dict[str, int] = {}
        for key in ("urgent", "important", "accessory", "warning"):
            try:
                out[key] = int(data.get(key) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        logger.info("[Client] 告警角标: %s（合计 %d）", out, sum(out.values()))
        return out

    def _list_alarms_rest(self, *, limit: int, offset: int, sort_dir: int,
                          sort_field: str,
                          filters: dict) -> RpcPagingResult[WarnInfoDTO]:
        """路径 B：Digest 域旧接口（无前端密码时的回退）。"""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sortDir": sort_dir,
            "sortField": sort_field,
        }
        # 可选过滤项（见 PRD F1-06）
        opt = {
            "category": filters.get("category"),
            "eventDesc": filters.get("eventDesc"),
            "eventLevel": filters.get("eventLevel"),
            "eventSrc": filters.get("eventSrc"),
            "eventTime_from": filters.get("eventTime_from"),
            "eventTime_to": filters.get("eventTime_to"),
            "state": filters.get("state"),
        }
        for k, v in opt.items():
            if v is not None:
                params[k] = v

        raw = self._request("GET", "/realtimeAlarms/list", params=params)
        result = self._require_ok(raw, "GET", "/realtimeAlarms/list",
                                  params=params, paging=True)

        items = self._extract_list(raw.get("data"))
        result.total = self._extract_total(raw.get("data"), len(items))
        result.items = [WarnInfoDTO(**self._clean_warn(d)) for d in items]
        return result

    @staticmethod
    def _clean_warn(d: dict) -> dict:
        return {
            "id": int(d.get("id", 0) or 0),
            "eventName": d.get("eventName", "") or "",
            "eventDesc": d.get("eventDesc", "") or "",
            "eventLevel": int(d.get("eventLevel", 0) or 0),
            "eventTime": int(d.get("eventTime", 0) or 0),
            "firstEventTime": int(d.get("firstEventTime", 0) or 0),
            "eventType": int(d.get("eventType", 0) or 0),
            "state": int(d.get("state", 0) or 0),
            "eventSrc": d.get("eventSrc", "") or "",
            "eventCount": int(d.get("eventCount", 0) or 0),
            "category": int(d.get("category", 0) or 0),
        }

    # ------------------------------------------------------------------
    # 2.27.33 虚拟机分页查询（替代 2.27.40 不分页版，后者服务端硬截断 60 条）
    # ------------------------------------------------------------------
    def query_vm_list(self, domain_name: str | None = None) -> list[VmBrief]:
        """拉取**全部**桌面清单（分页翻页，修复 2.27.40 硬截断 60 条丢桌面）。

        背景：2.27.40 ``/vms/queryVmList`` 文档标注「不分页」，但服务端实际把
        返回**硬截断到 60 条**（无论传什么 limit/offset 都只回 60），导致桌面数
        超过 60 时软件拿不全（报障：软件 60 台 vs 平台 95 台）。

        改用 2.27.33 ``/vms/page``（真分页接口）：按 ``limit``/``offset`` 循环
        翻页，直到取满外层 ``totalLength``（缺失时以「空页」判定末页），并按
        ``id`` 去重（分页边界重复不产生重复行）。

        Args:
            domain_name: 可选，按虚拟机名称过滤（透传 ``domainName`` 参数）。

        Returns:
            list[VmBrief]：全部桌面（含关机），按 id 升序。
        """
        params_base: dict = {}
        if domain_name:
            params_base["domainName"] = domain_name

        offset = 0
        total = -1          # -1 表示外层未给出 total（回退为「空页即末页」）
        out: list[dict] = []
        for page in range(_VM_LIST_MAX_PAGES):
            params = dict(params_base)
            params["limit"] = _VM_PAGE_SIZE
            params["offset"] = offset
            raw = self._request("GET", "/vms/page", params=params)
            self._require_ok(raw, "GET", "/vms/page", params=params)
            items = self._extract_list(raw.get("data"))
            if total < 0:
                # 2.27.33 的 totalLength 在统一外层（与 data 同级）
                total = self._extract_total_from_raw(raw, -1)
            out.extend(items)
            logger.info("[Client] 2.27.33 第 %d 页返回 %d 条，累计 %d 条（total=%s）",
                        page + 1, len(items), len(out),
                        "未知" if total < 0 else total)
            if not items:
                break                                   # 空页 → 末页
            if total >= 0 and len(out) >= total:        # 已取满
                break
            offset += len(items)
        else:
            logger.warning("[Client] 2.27.33 翻页达上限 %d 页（累计 %d 条），提前结束",
                           _VM_LIST_MAX_PAGES, len(out))

        # 按 id 去重（分页边界重复不产生重复行）
        seen: set[int] = set()
        cleaned: list[VmBrief] = []
        for d in out:
            vid = int(d.get("id", 0) or 0)
            if vid in seen:
                continue
            seen.add(vid)
            cleaned.append(VmBrief(**self._clean_vm(d)))
        return cleaned

    # ------------------------------------------------------------------
    # 2.27.57 根据 id 查询虚拟机信息（桌面 IPv4 主来源）
    # ------------------------------------------------------------------
    def get_vm_ip_by_uuid(self, vm_uuid: str) -> str:
        """获取单台桌面 IPv4（GET /vms/{domainUuid}，2.27.57）。

        桌面 IPv4 的可靠来源：真实平台 2.9.11 概要的 ``data.server`` 在多数
        环境下恒为 null（``server.addresses`` 取不到 IP），而本接口 ``data.networks``
        始终返回网卡列表，其中 ``networks[].ipAddr`` 即为该桌面实际 IPv4
        （关机桌面也有，开机则为运行 IP）。字段名与清单 2.27.33 的 ``ipAddr`` 一致。

        Args:
            vm_uuid: 桌面 UUID（来源 ``query_vm_list`` 的 ``VmBrief.uuid``）。

        Returns:
            str：IPv4 地址；取不到（无网卡/无 IP）时返回 ""（上层回退到 "-"）。
        """
        raw = self._request("GET", f"/vms/{vm_uuid}")
        self._require_ok(raw, "GET", f"/vms/{vm_uuid}")
        d = raw.get("data") or {}
        nets = d.get("networks") or []
        if not isinstance(nets, list):
            return ""
        for n in nets:
            if isinstance(n, dict) and n.get("ipAddr"):
                return str(n["ipAddr"])
        return ""

    @staticmethod
    def _clean_vm(d: dict) -> dict:
        return {
            "id": int(d.get("id", 0) or 0),
            "hostId": int(d.get("hostId", 0) or 0),
            "title": d.get("title", "") or "",
            "name": d.get("name", "") or "",
            "status": str(d.get("status", "")) if d.get("status") is not None else "",
            "uuid": d.get("uuid", "") or "",
            "clusterId": int(d.get("clusterId", 0) or 0),
            "cpu": int(d.get("cpu", 0) or 0),
            "memory": int(d.get("memory", 0) or 0),
            # IPv4 兜底：2.9.11 server.addresses 解析为空时使用
            "ipAddr": str(d.get("ipAddr", "") or ""),
        }

    # ------------------------------------------------------------------
    # 2.27.31 批量虚拟机性能（桌面级口径，仅返回“开机”桌面）
    # ------------------------------------------------------------------
    def list_vm_monitors_all(self, *, page_limit: int = _MONITORS_PAGE_LIMIT
                             ) -> list[VmPerf]:
        """批量拉取全部开机虚拟机性能（GET /vms/monitors/all）。

        按 ``limit``/``offset`` 循环翻页直到取满 total（total 缺失时以“短页”
        判定末页），并按 uuid 去重（分页边界重复不产生重复行）。

        Args:
            page_limit: 单页条数，默认 200（现网约 60 台，单批即可取完；
                保留翻页逻辑以防规模扩容）。

        Returns:
            list[VmPerf]：每台开机桌面一条，含 ``uuid`` 供与清单关联。

        Raises:
            WorkspaceAPIError: 任一页 success=False 或 errorCode!=0。
        """
        limit = max(1, int(page_limit or _MONITORS_PAGE_LIMIT))
        offset = 0
        total: int | None = None
        out: list[VmPerf] = []
        seen: set[str] = set()

        for page in range(_MONITORS_MAX_PAGES):
            raw = self._request("GET", "/vms/monitors/all",
                                params={"limit": limit, "offset": offset})
            self._require_ok(raw, "GET", "/vms/monitors/all",
                            params={"limit": limit, "offset": offset})

            items = self._extract_list(raw.get("data"))
            if total is None:
                # -1 表示外层未给出 total（回退为“短页即末页”判定）
                page_total = self._extract_total(raw.get("data"), -1)
                if page_total >= 0:
                    total = page_total

            for d in items:
                perf = VmPerf.from_raw(d)
                if perf.uuid and perf.uuid in seen:
                    continue        # 分页边界重复，跳过
                if perf.uuid:
                    seen.add(perf.uuid)
                out.append(perf)

            logger.info("[Client] 2.27.31 第 %d 页返回 %d 条，累计 %d 条（total=%s）",
                        page + 1, len(items), len(out), "未知" if total is None else total)

            if not items:
                break                                   # 空页 → 末页
            offset += len(items)
            if total is not None and len(out) >= total:  # 已取满
                break
            if total is None and len(items) < limit:     # 无 total 且短页 → 末页
                break
        else:
            logger.warning("[Client] 2.27.31 翻页达到上限 %d 页（累计 %d 条），提前结束",
                           _MONITORS_MAX_PAGES, len(out))
        return out

    # ------------------------------------------------------------------
    # 2.9.11 虚拟机概要（默认 per-id 路径，用于健康监控 osVersion/IP）
    # ------------------------------------------------------------------
    def get_vm_summary(self, vm_id: int) -> RsDomainSummary:
        raw = self._request("GET", f"/virtual/domain/{vm_id}/summary")
        self._require_ok(raw, "GET", f"/virtual/domain/{vm_id}/summary")
        d = raw.get("data") or {}
        return RsDomainSummary(
            title=d.get("title", "") or "",
            osVersion=d.get("osVersion", "") or "",
            status=str(d.get("status", "")) if d.get("status") is not None else "",
            hostId=self._extract_host_id(d),
            ip=self._extract_ip(d),
        )

    @staticmethod
    def _extract_host_id(d: dict) -> int:
        server = d.get("server") or {}
        if isinstance(server, dict):
            try:
                return int(server.get("hostId", 0) or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    @staticmethod
    def _extract_ip(d: dict) -> str:
        """健壮解析 server.addresses：遍历取第一个非空 addr。"""
        server = d.get("server") or {}
        if not isinstance(server, dict):
            return ""
        addresses = server.get("addresses") or {}
        if isinstance(addresses, dict):
            for val in addresses.values():
                if isinstance(val, dict) and val.get("addr"):
                    return str(val.get("addr"))
                # 兼容 addresses 直接为列表的情况
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and item.get("addr"):
                            return str(item.get("addr"))
        if isinstance(addresses, list):
            for item in addresses:
                if isinstance(item, dict) and item.get("addr"):
                    return str(item.get("addr"))
        return ""

    # ------------------------------------------------------------------
    # 2.4.12 主机性能
    # ------------------------------------------------------------------
    def get_host_cpumemdisk(self, host_id: int) -> HostPerf:
        raw = self._request("GET", f"/hosts/{host_id}/cpumemdiskrate")
        self._require_ok(raw, "GET", f"/hosts/{host_id}/cpumemdiskrate")
        d = raw.get("data") or {}
        cpu = d.get("cpuRate")
        mem = d.get("memRate")
        disk = d.get("disk") or []
        return HostPerf.from_raw(cpu, mem, disk)

    # ------------------------------------------------------------------
    # 2.27.39 批量 UUID 查询（可选优化路径）
    # ------------------------------------------------------------------
    def query_vms_by_uuids(self, vm_uuids: list[str]) -> list[VmDetail]:
        raw = self._request("POST", "/vms/queryUuidsByVmUuids",
                            json={"vmUuids": vm_uuids})
        self._require_ok(raw, "POST", "/vms/queryUuidsByVmUuids")
        items = self._extract_list(raw.get("data"))
        return [VmDetail(
            ip=d.get("ip", "") or "",
            osVersion=d.get("osVersion", "") or "",
            title=d.get("title", "") or "",
            status=str(d.get("status", "")) if d.get("status") is not None else "",
            uuid=d.get("uuid", "") or "",
        ) for d in items]

    # ------------------------------------------------------------------
    # 2.4.7 主机列表（可选，宿主机视角）
    # ------------------------------------------------------------------
    def list_hosts(self) -> list[HostBrief]:
        raw = self._request("GET", "/hosts")
        self._require_ok(raw, "GET", "/hosts")
        items = self._extract_list(raw.get("data"))
        return [HostBrief(
            id=int(d.get("id", 0) or 0),
            name=d.get("name", "") or "",
            status=str(d.get("status", "")) if d.get("status") is not None else "",
        ) for d in items]

    # ------------------------------------------------------------------
    # 主机列表（getHostList 路径，返回原始 host dict 列表）
    # 用于「定位桌面所在主机 + 列出该主机上所有虚拟机」流程的第一步。
    # 注意：不同 H3C 版本/部署下该接口字段名不稳定（name/hostName/...），
    # 因此不强制映射到 HostBrief，直接返回原始 dict 供调用方防御式读取。
    # ------------------------------------------------------------------
    def get_host_list(self) -> list[dict]:
        """获取全部主机列表（GET /hosts/getHostList）。

        与 ``list_hosts``（GET /hosts）不同，本方法返回**原始 host dict 列表**，
        不做字段强校验，便于在真实平台未知 schema 时由调用方（演示脚本）防御式
        提取 ``id`` / ``name`` 等字段。

        Returns:
            list[dict]：``data`` 内的原始主机 dict 列表（可能为空列表）。

        Raises:
            WorkspaceAPIError: 任一 HTTP 层 4xx 或业务层 success=False /
                errorCode!=0 时上抛（与其它方法行为一致）。
        """
        raw = self._request("GET", "/hosts/getHostList")
        self._require_ok(raw, "GET", "/hosts/getHostList")
        return self._extract_list(raw.get("data"))

    # ------------------------------------------------------------------
    # 虚拟机（桌面）详情（domainDetail 路径，返回原始响应整包）
    # 用于「定位桌面所在主机 + 列出该主机上所有虚拟机」流程的第二步。
    # 真实平台对该类「桌面级/虚拟机详情」接口已知可能返回 HTTP 400，
    # 因此调用方须自行 try/except 捕获 WorkspaceAPIError。
    # ------------------------------------------------------------------
    def get_domain_detail(self, vm_id: int) -> dict:
        """获取单台虚拟机（桌面）详情（GET /virtual/domain/{vm_id}/domainDetail）。

        返回 H3C 统一外层**整包** dict（含 ``success`` / ``errorCode`` /
        ``failureMessage`` / ``state`` / ``data`` 等），不做字段强映射，便于在
        真实平台未知 schema 时由调用方（演示脚本）打印原始结构并防御式提取
        IPv4 / CPU 使用率 / 内存使用率 / 所在主机名称。

        Args:
            vm_id: 虚拟机（桌面）数字 id，来源如 ``query_vm_list`` 返回的
                ``VmBrief.id``。

        Returns:
            dict：H3C 统一外层完整响应（已通过 ``_require_ok`` 校验）。

        Raises:
            WorkspaceAPIError: 任一 HTTP 层 4xx（真实环境常返回 400）或业务层
                success=False / errorCode!=0 时上抛。
        """
        uri = f"/virtual/domain/{vm_id}/domainDetail"
        raw = self._request("GET", uri)
        self._require_ok(raw, "GET", uri)
        return raw

    # ------------------------------------------------------------------
    # 2.26 虚拟机使用率查询（center 前缀，与其它 workspace 接口前缀不同）
    # ------------------------------------------------------------------
    def get_vm_usages(self, query_type: int, *, host_id: int | None = None,
                      vm_id: int | None = None,
                      start_time: int | None = None,
                      end_time: int | None = None,
                      **extra) -> dict:
        """查询虚拟机使用率指标（GET /vdi/rest/center/vmusages，2.26）。

        该接口 REST 前缀为 ``/vdi/rest/center``，与本项目其余接口
        （``/vdi/rest/workspace``）不同，方法内部通过 ``prefix=_CENTER_PREFIX``
        临时把底层 ``self.http.base_url`` 切到对应前缀根再请求，请求结束后恢复。

        真实平台文档明确：**``queryType`` 为必填 integer 查询参数**（int32，
        枚举值文档未列出，需在真实环境遍历验证，如 1=CPU 使用率、2=内存使用率、
        3=在线时长 等）。缺少该参数会导致接口对所有参数组合返回 HTTP 400。

        因真实平台 schema 未知，本方法**返回 H3C 统一外层整包 dict**
        （含 ``success`` / ``errorCode`` / ``failureMessage`` / ``state`` /
        ``data``，``data`` 为数组，元素含 ``name`` / ``objectId`` /
        ``datapoints`` / ``plotDataDTOList``），不做字段强映射，交由调用方
        （演示脚本）防御式提取使用率字段。

        Args:
            query_type: 必填，指标类型枚举（int32）。调用方必须传入，缺失会触发
                平台 400。具体枚举值需在真实环境遍历验证。
            host_id: 主机 id 过滤（对应查询参数 ``hostId``）。
            vm_id: 虚拟机 id 过滤（对应查询参数 ``vmId``）。
            start_time: 起始时间（Unix 秒，对应查询参数 ``startTime``）。
            end_time: 结束时间（Unix 秒，对应查询参数 ``endTime``）。
            **extra: 文档中可能出现的其它查询参数，透传到底层请求（不写死）。

        Returns:
            dict：H3C 统一外层完整响应（已通过 ``_require_ok`` 校验）。

        Raises:
            WorkspaceAPIError: HTTP 层 4xx 或业务层 success=False /
                errorCode!=0 时上抛（与其它方法行为一致）。
        """
        params: dict[str, Any] = {"queryType": query_type}
        if host_id is not None:
            params["hostId"] = host_id
        if vm_id is not None:
            params["vmId"] = vm_id
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        # 其它文档可能出现的参数（如 clusterId / poolId 等）透传，不写死；
        # 统一把 Python 风格 snake_case 关键字转换为 H3C camelCase 查询参数名。
        for key, value in extra.items():
            params[_snake_to_camel(key)] = value

        raw = self._request("GET", "/vmusages",
                            params=params or None, prefix=_CENTER_PREFIX)
        self._require_ok(raw, "GET", "/vmusages",
                        params=params or None, prefix=_CENTER_PREFIX)
        return raw

    # ------------------------------------------------------------------
    # 2.27.30 单台虚拟机性能监控（按 UUID，与 2.27.31 批量接口相互独立）
    # ------------------------------------------------------------------
    def get_vm_monitor(self, domain_uuid: str) -> dict:
        """获取单台虚拟机的 CPU/内存性能监控数据（GET /vms/monitor/{domainUuid}，2.27.30）。

        与 2.27.31（批量所有开机虚拟机）是**彼此独立**的接口：2.27.31 在真实平台
        已确认返回 HTTP 400，但本接口按单台 UUID 查询，路径与参数均不同，**很可能
        可用**（最终以真实平台验证为准）。

        因真实平台 schema 未知，本方法**返回 H3C 统一外层整包 dict**
        （含 ``success`` / ``errorCode`` / ``failureMessage`` / ``state`` /
        ``data``），不做字段强映射，便于在真实平台未知 schema 时由调用方（演示脚本）
        打印原始结构并防御式提取 CPU / 内存使用率。

        Args:
            domain_uuid: 虚拟机（桌面）UUID，来源如 ``query_vm_list`` 返回的
                ``VmBrief.uuid``。

        Returns:
            dict：H3C 统一外层完整响应（已通过 ``_require_ok`` 校验）。

        Raises:
            WorkspaceAPIError: HTTP 层 4xx（真实环境若也返回 400）或业务层
                success=False / errorCode!=0 时上抛（与其它方法行为一致）。
        """
        uri = f"/vms/monitor/{domain_uuid}"
        raw = self._request("GET", uri)
        self._require_ok(raw, "GET", uri)
        return raw

    # ------------------------------------------------------------------
    # 2.4.5 主机性能监控（含每台主机 top5 虚拟机的 cpu/内存利用率）
    # ------------------------------------------------------------------
    def get_host_monitor(self) -> dict:
        """查询所有主机性能数据（GET /hosts/hostMonitor，2.4.5）。

        返回每台主机的 ``cpuRate`` / ``memRate`` / ``diskRate`` / ``occRate``，
        以及 **``vmTopCpuRate`` / ``vmTopMemRate``（每台主机 top5 虚拟机的
        cpu/内存利用率）**——可作为「按主机看 top5 桌面率」的替代来源。

        因真实平台 schema 未知，本方法**返回 H3C 统一外层整包 dict**
        （含 ``success`` / ``errorCode`` / ``failureMessage`` / ``state`` /
        ``data``），不做字段强映射，交由调用方（演示脚本）防御式提取各主机及其
        ``vmTopCpuRate`` / ``vmTopMemRate`` 内容。

        Returns:
            dict：H3C 统一外层完整响应（已通过 ``_require_ok`` 校验）。

        Raises:
            WorkspaceAPIError: HTTP 层 4xx 或业务层 success=False /
                errorCode!=0 时上抛（与其它方法行为一致）。
        """
        uri = "/hosts/hostMonitor"
        raw = self._request("GET", uri)
        self._require_ok(raw, "GET", uri)
        return raw

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _rpc_fields(raw: dict) -> dict:
        return {
            "success": raw.get("success", False),
            "errorCode": raw.get("errorCode", -1),
            "failureMessage": raw.get("failureMessage", ""),
            "state": raw.get("state", 0),
            "data": raw.get("data"),
        }

    @staticmethod
    def _extract_list(data: Any) -> list:
        """从 data 提取列表：兼容直接列表或 {items/list/rows:[...]} 包装。"""
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "list", "rows", "records"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    @staticmethod
    def _extract_total(data: Any, fallback: int) -> int:
        if isinstance(data, dict):
            for key in ("total", "totalCount", "count", "totalElements"):
                if isinstance(data.get(key), int):
                    return data[key]
        return fallback

    @staticmethod
    def _extract_total_from_raw(raw: dict, fallback: int) -> int:
        """从**外层**响应取总数。

        H3C 分页接口的 ``totalLength`` 位于统一外层（与 ``data`` 同级），
        而不是 ``data`` 内部。旧实现只从 ``raw["data"]`` 里找 total，
        对裸列表型 data 永远返回 fallback（=len(items)），导致分页终止条件
        「已取满」在第一页就为真，实际只拉了一页。

        Args:
            raw: 统一外层 dict。
            fallback: 取不到时的兜底值（通常传 ``len(items)``）。

        Returns:
            int：服务端给出的总数，取不到时 fallback。
        """
        if not isinstance(raw, dict):
            return fallback
        for key in ("totalLength", "total", "totalCount", "totalElements",
                    "totalNum", "totalSize"):
            val = raw.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                return val
            if isinstance(val, str) and val.isdigit():
                return int(val)
        # 少数接口把分页信息包在 data 里（如 {"total":n,"list":[...]}）
        return H3CWorkspaceClient._extract_total(raw.get("data"), fallback)
