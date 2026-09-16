"""QA 独立回归：桌面健康监控「桌面级口径」验证（不依赖实现方自测结论）。

与实现方测试的区别：本文件**只做黑盒/契约级断言**，逐条覆盖验收清单 1–7：
 1. 口径纯净性：collect() 链路只允许调用 2.27.33 / 2.9.11 / 2.27.30（逐台 UUID
    单台监控 /vms/monitor/{uuid}），以及为「所在主机」列解析主机名的 2.4.7
    一次性 /hosts 调用；严禁按需调用 2.4.12（/hosts/{id}/cpumemdiskrate）
    宿主级性能接口（路由白名单 + 源码禁词双保险）。
    注：原 2.27.31 批量接口已被 2.27.30 逐台查询取代（2.27.31 真实平台 400）；
    桌面清单现走 2.27.33 /vms/page（分页，替代硬截断 60 条的 2.27.40）。
 2. IPv4 两级兜底（addresses → ipAddr → "-"），且不得调用 2.27.22 取 vmIp。
 3. 关机桌面：保留行 / vm_* 为 None / 表格显示 "-" / 不标红不预警。
 4. 磁盘使用率：来自 2.9.10 domainDetail（diskList[].detail.usage，单位 %，关机桌面也有值）；
    2.27.30 的 disk 为单对象（无 usage）、partition 为容量(GB)非使用率%，故不得复用
    VmPerf.from_raw 的 disk/partition 字段（会把容量当利用率算错）。
 5. 阈值一致性：非对称阈值（cpu≠mem）下表头着色与 AI 判定必须同源。
 6. 向后兼容：explain_health(row) / AnalyzeWorker(analyzer) 无参仍按 85/85。
 7. UI 契约：8 列表头（含「所在主机」）含「桌面」、自动刷新默认 30s 开启、
    host_* 性能不展示（仅展示所在主机名）。
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai.analyzer import Analyzer  # noqa: E402
from core.models import HealthRow, RsDomainSummary, VmBrief, VmPerf  # noqa: E402
from core.workspace_client import H3CWorkspaceClient  # noqa: E402
from ui.health_panel import HealthPanel  # noqa: E402
from ui.styles import (  # noqa: E402
    THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE,
)
from workers.analyze_worker import AnalyzeWorker  # noqa: E402
from workers.health_worker import HealthWorker  # noqa: E402

OK = {"success": True, "errorCode": 0, "failureMessage": "", "state": 0}

# 桌面级口径允许的接口（2.27.33 分页清单 / 2.9.11 概要 / 2.27.30 逐台UUID监控
# /vms/monitor/{uuid} / 2.4.7 主机列表[仅解析「所在主机」列]）；
# 2.4.12 宿主级性能接口仍禁用。
_ALLOWED_URIS = {"/vms/page", "/vms/monitor/", "/hosts"}
_FORBIDDEN_URI_MARKERS = ("/hosts/", "cpumemdiskrate")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Collector:
    def __init__(self):
        self.items = []

    def emit(self, value):
        self.items.append(value)


class _WhitelistHTTP:
    """按 URI 路由的假 HTTP：任何白名单外的请求直接判定为口径污染。"""

    def __init__(self, vm_items, summaries, monitor_by_uuid, host_items=None,
                 vm_details=None, domain_details=None):
        self.vm_items = vm_items
        self.summaries = summaries
        # 2.27.30 逐台 UUID 监控：{uuid: {cpuRate, memRate, ...}}
        self.monitor_by_uuid = monitor_by_uuid
        self.host_items = host_items or []
        # 2.27.57 单台详情：{uuid: {networks: [{ipAddr, ...}]}}（IPv4 来源）
        self.vm_details = vm_details or {}
        # 2.9.10 domainDetail：{vm_id: {diskList: [{detail: {usage: ...}}]}}（磁盘来源）
        self.domain_details = domain_details or {}
        self.calls: list[tuple[str, dict | None]] = []
        self.violations: list[str] = []

    def get(self, uri, params=None, json=None):
        self.calls.append((uri, params))
        for marker in _FORBIDDEN_URI_MARKERS:
            if marker in uri:
                self.violations.append(uri)
                raise AssertionError(f"桌面级口径禁止调用宿主级接口: {uri}")
        if uri == "/vms/page":
            return {**OK, "data": {"items": self.vm_items, "total": len(self.vm_items)}}
        if uri.startswith("/virtual/domain/") and uri.endswith("/summary"):
            vm_id = int(uri.strip("/").split("/")[2])
            return {**OK, "data": self.summaries.get(vm_id, {})}
        if uri.startswith("/virtual/domain/") and uri.endswith("/domainDetail"):
            # 2.9.10 桌面配置详情（磁盘使用率 % 来源：diskList[].detail.usage）
            vm_id = int(uri.strip("/").split("/")[2])
            return {**OK, "data": self.domain_details.get(vm_id, {})}
        if uri.startswith("/vms/monitor/"):
            # 2.27.30 单台 UUID 监控：/vms/monitor/{uuid}
            uuid = uri.rsplit("/", 1)[-1]
            return {**OK, "data": self.monitor_by_uuid.get(uuid, {})}
        if uri.startswith("/vms/") and not uri.startswith("/vms/monitor/") and uri != "/vms/page":
            # 2.27.57 单台详情：networks[].ipAddr 为 IPv4 来源
            # （2.9.11 概要 server.addresses 在当前平台恒为 null，不可用）
            uuid = uri.rsplit("/", 1)[-1]
            return {**OK, "data": self.vm_details.get(uuid, {})}
        if uri == "/hosts":
            # 2.4.7 主机列表：仅解析「所在主机」列的主机名（一次请求），
            # 不属于宿主级性能口径（2.4.12 /hosts/{id}/cpumemdiskrate 仍被禁止）。
            return {**OK, "data": self.host_items}
        self.violations.append(uri)
        raise AssertionError(f"未预期的接口调用（非桌面级白名单）: {uri}")

    def post(self, uri, json=None, params=None):
        self.violations.append(uri)
        raise AssertionError(f"桌面级口径不应发起 POST: {uri}")


def _run_collect(vm_items, summaries, monitor_by_uuid, host_items=None,
                 vm_details=None, domain_details=None):
    """用白名单 HTTP 跑一次 collect，返回 (rows, http, error_collector)。"""
    http = _WhitelistHTTP(vm_items, summaries, monitor_by_uuid, host_items,
                          vm_details, domain_details)
    worker = HealthWorker(H3CWorkspaceClient(http), max_concurrency=3)
    ready, err = _Collector(), _Collector()
    worker.health_ready = ready
    worker.error = err
    worker.collect()
    return (ready.items[0] if ready.items else None), http, err


# ---------------------------------------------------------------------------
# 1) 口径纯净性：collect() 链路不得触碰 2.4.12
# ---------------------------------------------------------------------------
def test_collect_calls_only_desktop_level_endpoints():
    """端到端（真实 Client）：collect 命中 2.27.40 / 2.9.11 / 2.27.30（逐台UUID监控），
    以及为「所在主机」列解析主机名的 2.4.7 一次性 /hosts 调用；
    但仍严禁按需调用 2.4.12（/hosts/{id}/cpumemdiskrate）宿主级性能接口。

    注：原 2.27.31 批量接口已被 2.27.30 逐台 /vms/monitor/{uuid} 查询取代
    （2.27.31 在真实平台返回 400）。
    """
    vm_items = [
        {"id": 1, "uuid": "u1", "title": "vm-1", "status": "running",
         "ipAddr": "10.0.0.1", "hostId": 7},
        {"id": 2, "uuid": "u2", "title": "vm-2", "status": "3",
         "ipAddr": "10.0.0.2", "hostId": 0},
    ]
    summaries = {
        1: {"title": "vm-1", "osVersion": "Win10", "status": "running",
            "server": {"hostId": 7, "addresses": {"eth0": {"addr": "10.0.0.1"}}}},
        2: {"title": "vm-2", "osVersion": "Win7", "status": "3", "server": {}},
    }
    # 2.27.30 逐台监控：{uuid: {cpuRate, memRate, ...}}（disk 为单对象、无 usage）
    monitor_by_uuid = {"u1": {"cpuRate": 42.0, "memRate": 55.0}}
    host_items = [{"id": 7, "name": "host7", "status": 1}]
    # 2.9.10 domainDetail：磁盘使用率 %（diskList[].detail.usage）
    domain_details = {
        1: {"diskList": [{"detail": {"usage": 56.74}}]},
        2: {"diskList": [{"detail": {"usage": 2.89}}]},
    }

    rows, http, err = _run_collect(vm_items, summaries, monitor_by_uuid, host_items,
                                   domain_details=domain_details)

    assert err.items == [], f"collect 不应报错: {err.items}"
    assert http.violations == [], f"出现非桌面级接口调用: {http.violations}"

    uris = {uri for uri, _ in http.calls}
    assert "/vms/page" in uris, "必须走 2.27.33 分页接口拿全量清单（修复硬截断 60 条）"
    assert any(u.startswith("/vms/monitor/") for u in uris), "必须走 2.27.30 逐台UUID监控"
    assert any(u.startswith("/virtual/domain/") for u in uris), "必须走 2.9.11 概要"
    assert "/hosts" in uris, "必须调 2.4.7 解析主机名（所在主机列）"
    assert not any(m in u for u in uris for m in _FORBIDDEN_URI_MARKERS), \
        f"残留宿主级性能调用: {uris}"

    assert len(rows) == 2
    # 2.27.30 返回单台 cpu/mem；disk 本次不取（恒为 None → UI 显示 "-"）
    assert (rows[0].vm_cpu, rows[0].vm_mem) == (42.0, 55.0)
    # 磁盘使用率来自 2.9.10 domainDetail（diskList[].detail.usage），非 2.27.30
    assert rows[0].vm_disk == 56.74, "磁盘使用率应从 domainDetail 取真实 %"
    assert rows[1].vm_cpu is None, "关机桌面（2.27.30 cpuRate 为 null）→ None"
    assert rows[1].vm_disk == 2.89, "关机桌面仍可从 domainDetail 取磁盘使用率"
    # 所在主机列：hostId=7 经 2.4.7 解析为主机名 host7
    assert rows[0].host_id == 7 and rows[0].host_name == "host7"


def test_collect_has_guarded_host_level_fallback():
    """源码级：health_worker 必须内置「桌面级优先 + 宿主级回退」逻辑，
    且宿主级调用被严格限定为「桌面级接口异常」时的**回退**（非默认主路径）。

    设计变更说明：原纯桌面级口径曾禁止任何宿主级（2.4.12）调用；
    2026-09 修复「桌面健康页空白」时改为「桌面级优先，失败自动回退宿主级」
    （见 workspace_client 日志增强 + health_worker.collect 回退分支）。
    此处断言该回退是被显式守卫的、可观测的，而非无条件启用。
    """
    import workers.health_worker as hw

    src = inspect.getsource(hw)
    # 1) 回退逻辑确实存在（2.4.12 get_host_cpumemdisk 调用）
    assert "get_host_cpumemdisk" in src, "缺失宿主级回退（2.4.12）调用"
    # 2) 回退仅在「桌面级 2.27.30 全部调用报错」时触发：由 err_count 守卫
    #    （非默认主路径；主路径是逐台 get_vm_monitor 并发查询）
    assert "err_count" in src, "宿主级回退缺少调用计数守卫"
    assert "回退宿主级" in src, "缺少回退日志（便于以后排查）"
    # 3) 主路径仍是桌面级单台监控接口（回退为辅，不改变主口径）
    assert "get_vm_monitor" in src, "主路径必须保留桌面级单台监控接口"
    # 4) 数据来源字段已写入，便于 UI 后续提示「当前为宿主级数据」
    assert "data_source" in src, "缺少 data_source 可观测字段"


def test_host_level_client_method_never_invoked_by_collect():
    """运行时：即便 Client 存在 get_host_cpumemdisk，collect 也不得调用它。"""

    class BoomClient(H3CWorkspaceClient):
        def __init__(self):
            pass  # 不初始化 http

        def get_host_cpumemdisk(self, host_id):      # pragma: no cover - 不应被调用
            raise AssertionError("collect 不应调用宿主级 2.4.12")

        def query_vm_list(self, domain_name=None):
            assert domain_name is None, "清单不得按域过滤（须全量含关机）"
            return [VmBrief(id=1, uuid="u1", title="vm-1", status="running",
                            ipAddr="10.0.0.1")]

        def get_vm_summary(self, vm_id):
            return RsDomainSummary(title="vm-1", osVersion="Win10",
                                   status="running", hostId=7, ip="10.0.0.1")

        def get_vm_monitor(self, domain_uuid):
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": 12.0, "memRate": 34.0}}

    w = HealthWorker(BoomClient())
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    w.collect()
    assert err.items == [], f"触发了被禁用的宿主级调用: {err.items}"
    assert ready.items and ready.items[0][0].vm_cpu == 12.0


# ---------------------------------------------------------------------------
# 2) IPv4 两级兜底 + 未使用 2.27.22
# ---------------------------------------------------------------------------
def test_ipv4_two_level_fallback_without_27222():
    """addresses → ipAddr → '-'；且全仓源码不出现 2.27.22 / vmIp。"""
    vms = [
        VmBrief(id=1, uuid="u1", status="running"),
        VmBrief(id=2, uuid="u2", ipAddr="10.2.2.2", status="running"),
        VmBrief(id=3, uuid="u3", status="running"),
    ]
    summaries = {
        1: RsDomainSummary(ip="10.1.1.1"),   # 一级：server.addresses
        2: RsDomainSummary(ip=""),           # 二级：ipAddr
        3: RsDomainSummary(ip=""),           # 无 → "-"
    }
    rows = HealthWorker._merge_rows(vms, summaries, [])
    assert [r.ip for r in rows] == ["10.1.1.1", "10.2.2.2", "-"]

    # 源码级：不得调用 2.27.22 / queryVmIp / vmIp
    src_root = pathlib.Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "2.27.22" in text or "vmIp" in text:
            offenders.append(str(path.relative_to(src_root)))
    assert offenders == [], f"出现 2.27.22/vmIp 调用: {offenders}"


def test_ipv4_e2e_from_27257_networks(qapp):
    """端到端：桌面 IPv4 经 2.27.57 单台详情的 networks[].ipAddr 取得。

    模拟真实平台：2.9.11 概要的 server 恒为 null（server.addresses 取不到 IP），
    此时 collect 必须回退 2.27.57 单台详情的 networks[].ipAddr，而非显示 '-'。
    同时整个链路不得触碰宿主级 2.4.12、也不得调用被禁的 2.27.22 /vms/info。
    """
    vm_items = [
        {"id": 1, "uuid": "u1", "title": "vm-1", "status": "running",
         "ipAddr": "", "hostId": 7},
        {"id": 2, "uuid": "u2", "title": "vm-2", "status": "3",
         "ipAddr": "", "hostId": 0},
    ]
    # 真实平台行为：2.9.11 概要 server 为 null
    summaries = {
        1: {"title": "vm-1", "osVersion": "Win10", "status": "running", "server": None},
        2: {"title": "vm-2", "osVersion": "Win7", "status": "3", "server": None},
    }
    monitor_by_uuid = {"u1": {"cpuRate": 12.0, "memRate": 34.0}}
    # 2.27.57 单台详情：networks[].ipAddr 为 IPv4 来源
    vm_details = {
        "u1": {"networks": [{"ipAddr": "10.5.5.5", "mac": "aa:aa"}]},
        "u2": {"networks": [{"ipAddr": "10.5.5.6", "mac": "bb:bb"}]},
    }
    http = _WhitelistHTTP(vm_items, summaries, monitor_by_uuid, [], vm_details)
    worker = HealthWorker(H3CWorkspaceClient(http), max_concurrency=3)
    ready, err = _Collector(), _Collector()
    worker.health_ready, worker.error = ready, err
    worker.collect()
    assert err.items == [], f"collect 不应报错: {err.items}"
    rows = ready.items[0]
    assert [r.ip for r in rows] == ["10.5.5.5", "10.5.5.6"], \
        "IP 必须来自 2.27.57 networks[].ipAddr（server 为 null 时）"
    # 口径纯净：未调用任何非桌面级接口
    assert http.violations == [], f"口径污染: {http.violations}"


# ---------------------------------------------------------------------------
# 3) 关机桌面：保留行、不标红、不预警
# ---------------------------------------------------------------------------
def test_powered_off_row_kept_and_never_flagged(qapp):
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    rows = HealthWorker._merge_rows(
        [VmBrief(id=1, uuid="u1", status="running"),
         VmBrief(id=2, uuid="u2", status="3")],
        {1: RsDomainSummary(title="vm-1", status="running", ip="10.0.0.1"),
         2: RsDomainSummary(title="vm-2", status="关机")},
        [VmPerf(uuid="u1", cpuRate=99.0, memRate=99.0, diskMaxUsage=99.0)],
    )
    panel.set_rows(rows)
    assert panel.table.rowCount() == 2, "关机桌面必须保留行"
    off_title = panel.table.item(1, 0).text()
    assert off_title == "vm-2"
    for col in (5, 6, 7):
        cell = panel.table.cellWidget(1, col)
        assert cell.text() == "-"
        assert cell.styleSheet() == THRESHOLD_NORMAL_STYLE, "关机不得标红"
    # 开机高负载行仍应标红（对照组）
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE
    panel.close()


# ---------------------------------------------------------------------------
# 4) 磁盘聚合边界
# ---------------------------------------------------------------------------
def test_disk_prefers_disk_over_partition_and_takes_max():
    """disk 有值（35）不得被 partition（90）覆盖；多盘取 max 而非 min。"""
    perf = VmPerf.from_raw({"uuid": "u1", "cpuRate": 1.0, "memRate": 2.0,
                            "disk": [{"usage": 20.0}, {"usage": 35.0}],
                            "partition": [{"usage": 90.0}]})
    assert perf.diskMaxUsage == 35.0, "disk 有值时 partition 不得覆盖"

    only_partition = VmPerf.from_raw({"uuid": "u2", "disk": [],
                                      "partition": [{"usage": 70.0}, {"usage": 81.0}]})
    assert only_partition.diskMaxUsage == 81.0, "disk 为空 → 兜底 partition 取 max"

    neither = VmPerf.from_raw({"uuid": "u3", "cpuRate": 5.0, "memRate": 6.0})
    assert neither.diskMaxUsage is None, "两者皆无 → None（UI 显示 '-'）"

    zero_usage = VmPerf.from_raw({"uuid": "u4", "disk": [{"usage": 0.0}]})
    assert zero_usage.diskMaxUsage == 0.0, "0% 是有效数据，不得当作缺失"


# ---------------------------------------------------------------------------
# 5) 阈值一致性（非对称阈值下 UI 与 AI 同源）
# ---------------------------------------------------------------------------
class _FakeLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def chat(self, messages, temperature=0.3):
        self.prompts.append(messages[1]["content"])
        return "ok"


def test_asymmetric_threshold_table_and_ai_agree(qapp):
    """cpu=50 / mem=90 时：CPU 60% 双端判越界，内存 60% 双端判正常。"""
    row = HealthRow(vm_id=1, title="vm-01", ip="10.0.0.1", os="Win10",
                    status="running", vm_cpu=60.0, vm_mem=60.0, vm_disk=10.0)

    panel = HealthPanel(threshold_cpu=50, threshold_mem=90)
    panel.set_rows([row])
    cpu_cell = panel.table.cellWidget(0, 5)
    mem_cell = panel.table.cellWidget(0, 6)
    assert cpu_cell.styleSheet() == THRESHOLD_BREACH_STYLE, "60 ≥ 50 → 标红"
    assert mem_cell.styleSheet() == THRESHOLD_NORMAL_STYLE, "60 < 90 → 不标红"

    llm = _FakeLLM()
    Analyzer(llm=llm).explain_health(row, panel.threshold_cpu, panel.threshold_mem)
    prompt = llm.prompts[0]
    assert "超过阈值 50%" in prompt, "AI 须按 50% 判 CPU 越界"
    assert "内存利用率 60% 超过阈值 90%" not in prompt, "AI 不得误判内存越界"
    panel.close()


def test_threshold_hot_reload_repaints_table_immediately(qapp):
    """配置保存 → 表格着色应立即与新阈值一致（与 AI 判定同源）。

    Round 1 曾为 xfail（阈值只改属性不重绘，表格滞后于 AI 判定）；
    Engineer 改为 property + ``_render_rows()`` 后转正常回归用例。
    """
    row = HealthRow(vm_id=1, title="vm-01", vm_cpu=60.0, vm_mem=10.0, vm_disk=10.0)
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([row])
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_NORMAL_STYLE

    panel.threshold_cpu = 50
    panel.threshold_mem = 50
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE, \
        "阈值改为 50 后，未刷新数据时表格着色仍停留在 85 口径"

    llm = _FakeLLM()
    Analyzer(llm=llm).explain_health(row, 50, 50)
    assert "超过阈值 50%" in llm.prompts[0]
    panel.close()


def test_threshold_hot_reload_keeps_worker_and_panel_aligned(qapp):
    """配置热更新后：Worker 透传新阈值，结论与同阈值面板一致（50% 边界）。"""
    row = HealthRow(vm_id=1, title="vm-01", vm_cpu=50.0, vm_mem=10.0, vm_disk=10.0)

    llm = _FakeLLM()
    analyzer = Analyzer(llm=llm)
    worker = AnalyzeWorker(analyzer, threshold_cpu=85, threshold_mem=85)
    ready = _Collector()
    worker.health_analysis_ready = ready
    worker.set_health_thresholds(50, 50)          # 模拟配置保存
    worker.explain_health(row)
    worker._pump()
    assert ready.items == ["ok"], "热更新后仍能正常出结果"
    assert "超过阈值 50%" in llm.prompts[0], "50 等于阈值 → AI 判越界"

    panel = HealthPanel(threshold_cpu=50, threshold_mem=50)
    panel.set_rows([row])
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE
    panel.close()


# ---------------------------------------------------------------------------
# 5b) 阈值变更的副作用边界（不得污染采集状态）
# ---------------------------------------------------------------------------
def test_threshold_change_must_not_touch_refresh_state(qapp):
    """改阈值 ≠ 采集：不得解锁刷新锁、不得刷新「最后刷新」时间戳。"""
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_cpu=60.0,
                              vm_mem=60.0, vm_disk=10.0)])
    # 用哨兵值而非时间戳比对：时间戳精确到秒，同秒内比对会假阴性
    panel.lbl_last.setText("SENTINEL")

    panel.btn_refresh.setEnabled(False)
    panel._pending = True                     # 模拟一次采集中
    panel.set_thresholds(50, 50)

    assert panel._pending is True, "阈值变更不得解锁采集锁（否则连点保护失效）"
    assert panel.btn_refresh.isEnabled() is False
    assert panel.lbl_last.text() == "SENTINEL", "阈值变更不得刷新「最后刷新」时间"
    assert panel.table.rowCount() == 1, "行数与数据不得变化"
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE
    panel.close()


def test_threshold_matrix_table_and_ai_always_agree(qapp):
    """阈值 × 指标矩阵：UI 着色与 AI 判定在 4 种组合下必须完全一致。"""
    row = HealthRow(vm_id=1, title="vm-01", os="Win10", status="running",
                    vm_cpu=60.0, vm_mem=60.0, vm_disk=10.0)
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([row])

    for cpu_th in (50, 85):
        panel.set_thresholds(cpu_th, cpu_th)
        red = panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE
        llm = _FakeLLM()
        Analyzer(llm=llm).explain_health(row, cpu_th, cpu_th)
        ai_breach = f"超过阈值 {cpu_th}%" in llm.prompts[0]
        assert red == ai_breach, \
            f"阈值 {cpu_th}% 下不一致：表格标红={red}，AI 判越界={ai_breach}"
        assert red is (cpu_th == 50), f"60% vs 阈值 {cpu_th}% 判定错误"
    panel.close()


# ---------------------------------------------------------------------------
# 6) 向后兼容
# ---------------------------------------------------------------------------
def test_backward_compatible_defaults_85():
    llm = _FakeLLM()
    Analyzer(llm=llm).explain_health(
        HealthRow(vm_cpu=90.0, vm_mem=90.0, vm_disk=90.0))
    assert "超过阈值 85%" in llm.prompts[0], "不传阈值默认 85"

    llm2 = _FakeLLM()
    Analyzer(llm=llm2).explain_health(
        HealthRow(vm_cpu=60.0, vm_mem=60.0, vm_disk=60.0))
    assert "运行正常" in llm2.prompts[0]

    class _Rec:
        def __init__(self):
            self.args = ()

        def explain_health(self, row, threshold_cpu=85, threshold_mem=85):
            self.args = (threshold_cpu, threshold_mem)
            return ""

    rec = _Rec()
    worker = AnalyzeWorker(rec)          # 无参构造
    worker.health_analysis_ready = _Collector()
    worker.explain_health(HealthRow(vm_cpu=1.0))
    worker._pump()
    assert rec.args == (85, 85), "AnalyzeWorker(analyzer) 无参仍按 85/85"


# ---------------------------------------------------------------------------
# 7) UI 契约
# ---------------------------------------------------------------------------
def test_ui_contract_headers_and_defaults(qapp):
    panel = HealthPanel()
    assert panel.table.columnCount() == 8
    headers = [panel.table.horizontalHeaderItem(c).text()
               for c in range(panel.table.columnCount())]
    assert headers == ["名称", "IP", "操作系统", "状态", "所在主机",
                       "桌面CPU%", "桌面内存%", "桌面磁盘%"]
    assert all("桌面" in h for h in headers[5:]), "性能列须标注「桌面」口径"
    assert not any("宿主" in h for h in headers), "不得展示宿主级性能列"
    assert panel.cb_refresh.currentText() == "30s"
    assert panel.refresh_interval() == 30
    assert panel._timer.isActive() is True

    # host_* 字段保留在数据模型（向后兼容）但 UI 不消费
    row = HealthRow(vm_id=1, title="vm-1", vm_cpu=1.0, host_cpu=99.0,
                    host_mem=99.0, host_disk=99.0)
    assert (row.host_cpu, row.host_mem, row.host_disk) == (99.0, 99.0, 99.0)
    panel.set_rows([row])
    rendered = [panel.table.cellWidget(0, c).text() for c in (5, 6, 7)]
    assert rendered == ["1%", "-", "-"], "UI 只渲染 vm_*，host_* 不参与"
    panel.close()


# ---------------------------------------------------------------------------
# 补充：uuid 关联正确性（乱序 / 清单外 uuid / 无 uuid）
# ---------------------------------------------------------------------------
def test_uuid_association_ignores_unknown_and_reorders():
    vms = [VmBrief(id=1, uuid="u1"), VmBrief(id=2, uuid="u2"),
           VmBrief(id=3, uuid="u3")]
    monitors = [
        VmPerf(uuid="u3", cpuRate=30.0, memRate=31.0, diskMaxUsage=32.0),
        VmPerf(uuid="u-ghost", cpuRate=1.0, memRate=1.0, diskMaxUsage=1.0),
        VmPerf(uuid="u1", cpuRate=10.0, memRate=11.0, diskMaxUsage=12.0),
        VmPerf(uuid="", cpuRate=99.0, memRate=99.0, diskMaxUsage=99.0),
    ]
    rows = HealthWorker._merge_rows(vms, {}, monitors)
    assert len(rows) == 3, "清单外 uuid 不产生额外行"
    by_id = {r.vm_id: r for r in rows}
    assert by_id[1].vm_cpu == 10.0 and by_id[3].vm_cpu == 30.0, "按 uuid 关联（乱序）"
    assert by_id[2].vm_cpu is None, "未命中性能 → None"
