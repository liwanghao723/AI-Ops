"""QA 严过关 · 独立回归：桌面健康监控「桌面级 400 → 自动回退宿主级」Bug 修复验证。

本文件与实现方自测解耦，逐条覆盖 team-lead 验收清单 1–6，使用**真断言**
（不依赖实现方测试结论）证明：桌面级接口 400 时，collect() 仍能发射非空表格行，
且性能数据确实来自宿主级 2.4.12。

验证点：
  1. 回退真的填数据（端到端 collect，vm_* == host 级性能，data_source=="host"）
  2. 桌面级成功时不回退（data_source=="vm"，get_host_cpumemdisk 调用计数 == 0）
  3. URL 日志（REST 失败时日志含完整 URI + 状态码 400）
  4. 边界（hostId==0 / 概要失败 → vm_* 保持 None，不崩）
  5. 两侧都失败才报错（桌面级 400 + 宿主级失败 / 全部 hostId 无效 → bridge）
  6. 回归：全量 pytest 0 failed（见最终汇报，不在本文件内）

附带：真实 H3CWorkspaceClient 端到端集成回退用例（不走 Spy 替身方法）。
"""

from __future__ import annotations

import inspect
import logging

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.errors import WorkspaceAPIError  # noqa: E402
from core.models import (  # noqa: E402
    HealthRow, HostPerf, RsDomainSummary, VmBrief,
)
from core.workspace_client import H3CWorkspaceClient  # noqa: E402
from workers.health_worker import HealthWorker  # noqa: E402


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
class _Collector:
    """替代 pyqtBoundSignal 的离线信号收集器。"""

    def __init__(self):
        self.items = []

    def emit(self, value):
        self.items.append(value)


@pytest.fixture(scope="session")
def _qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# Spy 客户端：编排桌面级/宿主级行为，并统计宿主级调用次数
# ---------------------------------------------------------------------------
class SpyClient(H3CWorkspaceClient):
    """可编排的假客户端：让桌面级单台监控(2.27.30)抛错、统计宿主级调用次数。"""

    def __init__(self, *, vms, summaries, host_perf,
                 monitor_by_uuid=None, monitors_raise=None,
                 host_raise=None, summary_fail_ids=None):
        # 不调用 super().__init__（无需真实 http）
        self.vms = list(vms)
        self.summaries = summaries
        self.host_perf = host_perf
        # 2.27.30 逐台监控：{uuid: {cpuRate, memRate}}
        self.monitor_by_uuid = dict(monitor_by_uuid or {})
        self.monitors_raise = monitors_raise
        self.host_raise = host_raise
        self.summary_fail_ids = set(summary_fail_ids or [])
        self.host_calls = 0

    def query_vm_list(self, domain_name=None):
        return list(self.vms)

    def get_vm_summary(self, vm_id):
        if vm_id in self.summary_fail_ids:
            raise RuntimeError(f"summary failed for {vm_id}")
        return self.summaries[vm_id]

    def get_vm_monitor(self, domain_uuid):
        if self.monitors_raise is not None:
            raise self.monitors_raise
        return {"success": True, "errorCode": 0,
                "data": self.monitor_by_uuid.get(domain_uuid, {})}

    def get_host_cpumemdisk(self, host_id):
        self.host_calls += 1
        if self.host_raise is not None:
            raise self.host_raise
        return self.host_perf


def _collect(worker: HealthWorker):
    ready, err = _Collector(), _Collector()
    worker.health_ready, worker.error = ready, err
    worker.collect()
    return ready, err


# ===========================================================================
# 验收 1：回退真的填数据（端到端 collect）
# ===========================================================================
def test_qa_p1_fallback_fills_rows_with_host_data(_qapp):
    """桌面级 2.27.30 全部报错 → 自动回退宿主级，health_ready 发射非空行，
    且开机 VM 的 vm_* 来自 host 级 HostPerf，data_source=="host"。"""
    host = HostPerf(cpuRate=33.0, memRate=44.0, diskMaxUsage=55.0)
    vms = [
        VmBrief(id=1, uuid="u1", status="running", title="vm-1", hostId=1),
        VmBrief(id=2, uuid="u2-off", status="3", title="vm-2-off", hostId=1),
    ]
    summaries = {
        1: RsDomainSummary(title="vm-1", osVersion="Win10", status="running",
                           hostId=1, ip="10.0.0.1"),
        2: RsDomainSummary(title="vm-2-off", osVersion="Win7", status="3",
                           hostId=1, ip="10.0.0.2"),
    }
    client = SpyClient(
        vms=vms, summaries=summaries, host_perf=host,
        monitors_raise=WorkspaceAPIError(code=400, message="请求被拒绝 (400): "),
    )
    w = HealthWorker(client, max_concurrency=2)
    ready, err = _collect(w)

    assert err.items == [], f"回退成功不应报错: {err.items}"
    assert ready.items, "health_ready 未发射任何数据 —— 表格会空白！这是原 Bug 复现。"

    rows = ready.items[0]
    assert len(rows) == 2, "回退后仍保留全部行（含关机桌面）"

    on = next(r for r in rows if r.vm_id == 1)
    # 关键断言：数据必须真来自宿主级 HostPerf（而非凭空生成/桌面级残留）
    assert (on.vm_cpu, on.vm_mem, on.vm_disk) == (33.0, 44.0, 55.0), \
        "回退性能数据必须 == 宿主级 HostPerf"
    assert on.data_source == "host", "来源口径应为 host"
    assert client.host_calls >= 1, "必须真的调用了宿主级 2.4.12 接口"


# ===========================================================================
# 验收 2：桌面级成功时不回退（调用计数断言）
# ===========================================================================
def test_qa_p2_desktop_success_no_host_call(_qapp):
    """get_vm_monitor 正常返回 → VM 的 vm_* 来自桌面级数据、
    data_source=="vm"、**没有**调用 get_host_cpumemdisk（计数断言）。"""
    vms = [
        VmBrief(id=1, uuid="u1", status="running"),
        VmBrief(id=2, uuid="u2-off", status="3"),
    ]
    summaries = {
        1: RsDomainSummary(title="vm-1", status="running", hostId=1, ip="10.0.0.1"),
        2: RsDomainSummary(title="vm-2", status="3", hostId=1, ip="10.0.0.2"),
    }
    client = SpyClient(
        vms=vms, summaries=summaries, host_perf=HostPerf(),
        monitor_by_uuid={"u1": {"cpuRate": 88.0, "memRate": 40.0}},
    )
    w = HealthWorker(client)
    ready, err = _collect(w)

    assert err.items == []
    rows = ready.items[0]
    on = next(r for r in rows if r.vm_id == 1)
    assert (on.vm_cpu, on.vm_mem) == (88.0, 40.0), \
        "vm_* 应来自桌面级 2.27.30 数据"
    assert on.vm_disk is None, "2.27.30 不取磁盘使用率 → None"
    assert on.data_source == "vm", "成功路径来源口径应为 vm"
    # 核心：桌面级成功时绝不应触碰宿主级接口
    assert client.host_calls == 0, \
        f"桌面级成功时不应调用宿主级 2.4.12（实际调用 {client.host_calls} 次）"


# ===========================================================================
# 验收 3：URL 日志（完整 URI + 状态码）
# ===========================================================================
def test_qa_p3_logs_full_uri_and_status_on_error(caplog):
    """任一 REST 方法在 WorkspaceAPIError 时，日志必须含完整 URI 路径 + 状态码 400。"""
    class RaisingHTTP:
        def __init__(self, exc):
            self.exc = exc

        def get(self, uri, params=None, json=None):
            raise self.exc

        def post(self, uri, json=None, params=None):
            raise self.exc

    caplog.set_level(logging.DEBUG, logger="core.workspace_client")
    http = RaisingHTTP(WorkspaceAPIError(code=400, message="请求被拒绝 (400): 平台不支持"))
    c = H3CWorkspaceClient(http)

    with pytest.raises(WorkspaceAPIError):
        c.get_vm_monitor("u1")

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "[Client] REST 失败" in combined, "缺少 [Client] REST 失败 日志文本"
    # 完整 URI 路径（前缀 + 单台监控路径 + uuid）
    assert "/vdi/rest/workspace/vms/monitor/u1" in combined, \
        "日志必须包含完整请求 URI（含 /vdi/rest/workspace 前缀与 uuid）"
    assert "400" in combined, "日志必须包含 HTTP 状态码 400"


# ===========================================================================
# 验收 4：边界（hostId==0 / 概要失败 → vm_* 保持 None，不崩）
# ===========================================================================
def test_qa_p4_boundary_zero_hostid_and_summary_failure(_qapp):
    """边界：

    - vm1：清单 VmBrief.hostId 合法 → 正常回退宿主级（vm_* 来自 host）。
    - vm2：清单 VmBrief.hostId==0 → 回退不到宿主级，vm_* 保持 None（表格显示 -）。
    - vm3：**概要(2.9.11)失败**（模拟真实环境 400），但清单 VmBrief.hostId 合法
      → 回退**仍能**填出宿主级数据（证明修复点：回退不再依赖 2.9.11 概要的 hostId）。

    整体不崩、不报错。
    """
    host = HostPerf(cpuRate=11.0, memRate=22.0, diskMaxUsage=33.0)
    vms = [
        VmBrief(id=1, uuid="u1", status="running", hostId=1),
        VmBrief(id=2, uuid="u2", status="running", hostId=0),  # hostId==0 → 无法回退
        VmBrief(id=3, uuid="u3", status="running", hostId=1),  # 概要失败但清单 hostId 有效
    ]
    summaries = {
        1: RsDomainSummary(title="vm-1", hostId=1, ip="10.0.0.1"),
        2: RsDomainSummary(title="vm-2", hostId=0, ip="10.0.0.2"),
        # vm3 概要失败（不在 dict 中，由 summary_fail_ids 模拟）
    }
    client = SpyClient(
        vms=vms, summaries=summaries, host_perf=host,
        monitors_raise=WorkspaceAPIError(code=400, message="请求被拒绝 (400): "),
        summary_fail_ids=[3],
    )
    w = HealthWorker(client)
    ready, err = _collect(w)

    assert err.items == [], "边界 VM 不应导致整体报错/崩溃"
    rows = ready.items[0]
    assert len(rows) == 3, "所有 VM 仍保留行"
    by_id = {r.vm_id: r for r in rows}

    # vm1：合法 hostId → 正常回退
    assert by_id[1].vm_cpu == 11.0 and by_id[1].data_source == "host"
    # vm2：hostId==0 → 回退不到宿主级
    assert by_id[2].vm_cpu is None and by_id[2].vm_mem is None and by_id[2].vm_disk is None
    # vm3：概要失败，但清单 VmBrief.hostId 有效 → 修复后仍能回退（这正是本次 Bug 修复点）
    assert by_id[3].vm_cpu == 11.0 and by_id[3].data_source == "host", \
        "概要(2.9.11)失败不应再阻断宿主级回退"


# ===========================================================================
# 验收 5：两侧都失败才报错
# ===========================================================================
def test_qa_p5_both_sides_fail_bridges(_qapp):
    """桌面级 400 + 宿主级也失败 → 两者皆无性能数据，最终 bridge 收到异常、
    health_ready 不发射空表。"""
    vms = [VmBrief(id=1, uuid="u1", status="running", hostId=1)]
    summaries = {1: RsDomainSummary(title="vm-1", hostId=1, ip="10.0.0.1")}
    client = SpyClient(
        vms=vms, summaries=summaries, host_perf=HostPerf(),
        monitors_raise=WorkspaceAPIError(code=400, message="请求被拒绝 (400): "),
        host_raise=WorkspaceAPIError(code=500, message="宿主接口也挂了"),
    )
    w = HealthWorker(client)
    ready, err = _collect(w)

    assert len(err.items) == 1, "两侧都失败应经 bridge 上报异常"
    assert "均不可用" in err.items[0], "bridge 消息应说明桌面级与宿主级皆失败"
    assert ready.items == [], "两侧皆失败不应发射空表（否则表格静默空白，掩盖故障）"


def test_qa_p5b_all_hostids_invalid_bridges(_qapp):
    """补充：桌面级 400 且所有 VM 的 hostId 均无效（0） → 宿主级回退为空 → bridge。"""
    vms = [
        VmBrief(id=1, uuid="u1", status="running", hostId=0),
        VmBrief(id=2, uuid="u2", status="running", hostId=0),
    ]
    summaries = {
        1: RsDomainSummary(title="vm-1", hostId=0, ip="10.0.0.1"),
        2: RsDomainSummary(title="vm-2", hostId=0, ip="10.0.0.2"),
    }
    client = SpyClient(
        vms=vms, summaries=summaries, host_perf=HostPerf(),
        monitors_raise=WorkspaceAPIError(code=400, message="请求被拒绝 (400): "),
    )
    w = HealthWorker(client)
    ready, err = _collect(w)

    assert len(err.items) == 1, "全部 hostId 无效 → 宿主级回退为空 → 应 bridge"
    assert ready.items == [], "不应发射空表"


# ===========================================================================
# 补充：真实 H3CWorkspaceClient 端到端集成回退（不走 Spy 替身方法）
# ===========================================================================
def test_qa_integration_real_client_fallback(_qapp):
    """最强证据：用真实 H3CWorkspaceClient（仅替换底层 http），当 /vms/monitor/{uuid}
    真实抛 400 时，collect() 经真实 _request → 真实 _fallback_host_perf → 真实
    get_host_cpumemdisk 取数，最终表格发射非空行且 vm_* 来自宿主级。"""
    OK = {"success": True, "errorCode": 0, "failureMessage": "", "state": 0}

    class ScenarioHTTP:
        def __init__(self, responses, raise_for, raise_exc):
            self.responses = responses
            self.raise_for = raise_for
            self.raise_exc = raise_exc
            self.calls = []

        def get(self, uri, params=None, json=None):
            self.calls.append(("GET", uri, params))
            for marker in self.raise_for:
                if marker in uri:
                    raise self.raise_exc
            return self.responses[uri]

    responses = {
        "/vms/page": {**OK, "data": {
            "items": [{"id": 1, "uuid": "u1", "title": "vm-1", "status": "running", "hostId": 9},
                      {"id": 2, "uuid": "u2", "title": "vm-2", "status": "3", "hostId": 9}],
            "total": 2}},
        "/virtual/domain/1/summary": {**OK, "data": {
            "title": "vm-1", "osVersion": "Win10", "status": "running",
            "server": {"hostId": 9, "addresses": {"eth0": {"addr": "10.0.0.1"}}}}},
        "/virtual/domain/2/summary": {**OK, "data": {
            "title": "vm-2", "osVersion": "Win7", "status": "3", "server": {}}},
        "/hosts/9/cpumemdiskrate": {**OK, "data": {
            "cpuRate": 60.0, "memRate": 70.0, "disk": [{"usage": 80.0}]}},
    }
    http = ScenarioHTTP(responses, raise_for={"/vms/monitor/"},
                        raise_exc=WorkspaceAPIError(code=400, message="请求被拒绝 (400): "))
    client = H3CWorkspaceClient(http)
    w = HealthWorker(client)
    ready, err = _collect(w)

    assert err.items == [], f"真实客户端回退不应报错: {err.items}"
    rows = ready.items[0]
    by_id = {r.vm_id: r for r in rows}
    # vm1（开机，hostId=9）经真实宿主级取数
    assert (by_id[1].vm_cpu, by_id[1].vm_mem, by_id[1].vm_disk) == (60.0, 70.0, 80.0), \
        "真实客户端下回退数据应来自宿主级 HostPerf"
    assert by_id[1].data_source == "host"
    # 证明确实走了宿主级接口（而非桌面级残留）
    assert any("/hosts/9/cpumemdiskrate" in u for _, u, _ in http.calls), \
        "必须真实调用 /hosts/{id}/cpumemdiskrate"
    assert any("/vms/monitor/" in u for _, u, _ in http.calls), \
        "必须真实尝试过桌面级 /vms/monitor/{uuid}"
