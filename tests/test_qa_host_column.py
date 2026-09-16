"""QA 独立回归：桌面健康监控「所在主机」列新增改动（严过关，不依赖实现方自测）。

独立覆盖验收清单 1–5，针对实现方测试留下的覆盖缺口补强：

  1. 新列有数据：mock list_hosts(2.4.7) -> {id:name}，构造带 hostId 的 VM，
     端到端 collect()（真实 H3CWorkspaceClient + 假 HTTP）断言
     row.host_id == 期望、row.host_name == 期望（真断言，非仅通过）。
  2. 主机名解析链路：host_name 来自 list_hosts(2.4.7) 的 {id:name}，
     VM 的 hostId 不在 host_map 中 -> host_name 为空（不凭空猜、不崩）。
  3. 主机列表失败容错：list_hosts 抛 WorkspaceAPIError，collect() 不崩、
     其余列 IP/CPU/mem/disk 仍正常、host_name 为空。
  4. 不破坏既有回退：2.27.31 抛 400 -> 仍回退宿主级（data_source=="host"），
     且「所在主机」列仍有值：host_id 取自 VmBrief.hostId（与概要无关）、
     host_name 取自 list_hosts 映射。实现方 test_collect_falls_back_to_host_level_*
     未断言 host_id/host_name，本文件补强。
  5. 表头/列数契约：health_panel 表头 8 列、含「所在主机」且列序正确；
     所在主机单元格渲染 host_name 优先、回退裸 host_id、再回退 "-"。

所有用例均为黑盒/契约级断言，刻意不与实现方测试共享构造器，避免「同义反复」。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.errors import WorkspaceAPIError  # noqa: E402
from core.models import (  # noqa: E402
    HealthRow, HostBrief, HostPerf, RsDomainSummary, VmBrief, VmPerf,
)
from core.workspace_client import H3CWorkspaceClient  # noqa: E402
from ui.health_panel import HealthPanel  # noqa: E402
from workers.health_worker import HealthWorker  # noqa: E402

OK = {"success": True, "errorCode": 0, "failureMessage": "", "state": 0}


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Collector:
    def __init__(self):
        self.items = []

    def emit(self, value):
        self.items.append(value)


# ---------------------------------------------------------------------------
# 1) + 2) 端到端（真实 Client + 假 HTTP）：list_hosts -> HostBrief -> host_map
#        -> row.host_id / row.host_name
# ---------------------------------------------------------------------------
class _HostHTTP:
    """按 URI 路由的假 HTTP：2.27.40 / 2.9.11 / 2.27.30（逐台UUID）/ 2.4.7 / 2.27.57 / 2.9.10 全走通。"""

    def __init__(self, vm_items, summaries, monitor_by_uuid, host_items, vm_details=None,
                 domain_details=None):
        self.vm_items = vm_items
        self.summaries = summaries
        self.monitor_by_uuid = monitor_by_uuid
        self.host_items = host_items
        self.vm_details = vm_details or {}
        self.domain_details = domain_details or {}

    def get(self, uri, params=None, json=None):
        if uri == "/vms/page":
            return {**OK, "data": {"items": self.vm_items, "total": len(self.vm_items)}}
        if uri.startswith("/virtual/domain/") and uri.endswith("/summary"):
            vm_id = int(uri.strip("/").split("/")[2])
            return {**OK, "data": self.summaries.get(vm_id, {})}
        if uri.startswith("/virtual/domain/") and uri.endswith("/domainDetail"):
            # 2.9.10 桌面配置详情：磁盘使用率 %（diskList[].detail.usage）
            vm_id = int(uri.strip("/").split("/")[2])
            return {**OK, "data": self.domain_details.get(vm_id, {})}
        if uri.startswith("/vms/monitor/"):
            # 2.27.30 单台 UUID 监控
            uuid = uri.rsplit("/", 1)[-1]
            return {**OK, "data": self.monitor_by_uuid.get(uuid, {})}
        if uri.startswith("/vms/") and not uri.startswith("/vms/monitor/") and uri != "/vms/page":
            # 2.27.57 单台详情：networks[].ipAddr 为 IPv4 来源
            uuid = uri.rsplit("/", 1)[-1]
            return {**OK, "data": self.vm_details.get(uuid, {})}
        if uri == "/hosts":
            # 真实 list_hosts 会把 dict 解析为 HostBrief(id, name)
            return {**OK, "data": self.host_items}
        raise AssertionError(f"未预期接口: {uri}")


def test_qa_host_column_filled_from_list_hosts_end_to_end(qapp):
    """真实 H3CWorkspaceClient：2.4.7 主机名经 {id:name} 解析到行。"""
    vm_items = [
        {"id": 1, "uuid": "u1", "title": "vm-1", "status": "running",
         "ipAddr": "10.0.0.1", "hostId": 7},
        {"id": 2, "uuid": "u2", "title": "vm-2", "status": "3",
         "ipAddr": "10.0.0.2", "hostId": 9},
    ]
    summaries = {
        1: {"title": "vm-1", "osVersion": "Win10", "status": "running",
            "server": {"hostId": 7, "addresses": {"eth0": {"addr": "10.0.0.1"}}}},
        2: {"title": "vm-2", "osVersion": "Win7", "status": "3", "server": {}},
    }
    # 2.27.30 逐台监控：{uuid: {cpuRate, memRate}}（disk 为单对象、无 usage）
    monitor_by_uuid = {"u1": {"cpuRate": 42.0, "memRate": 55.0}}
    host_items = [{"id": 7, "name": "host-SEVEN", "status": 1},
                  {"id": 9, "name": "host-NINE", "status": 1}]

    http = _HostHTTP(vm_items, summaries, monitor_by_uuid, host_items)
    w = HealthWorker(H3CWorkspaceClient(http), max_concurrency=2)
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    w.collect()

    assert err.items == [], f"collect 不应报错: {err.items}"
    assert len(ready.items) == 1
    rows = ready.items[0]
    # 真断言：host_id / host_name 来自真实 list_hosts 链路
    assert rows[0].host_id == 7 and rows[0].host_name == "host-SEVEN"
    assert rows[1].host_id == 9 and rows[1].host_name == "host-NINE"
    # 性能口径未受影响（桌面级 2.27.30）
    assert rows[0].vm_cpu == 42.0 and rows[0].data_source == "vm"
    # 关机桌面（2.27.30 cpuRate 为 null）同样按 hostId 解析，与开关机无关
    assert rows[1].vm_cpu is None


def test_qa_build_host_map_reads_id_and_name(qapp):
    """_build_host_map 直接从 client.list_hosts 的 HostBrief 取 id/name。"""

    class MapClient(H3CWorkspaceClient):
        def __init__(self):
            pass

        def list_hosts(self):
            return [HostBrief(id=7, name="host7", status="running"),
                    HostBrief(id=9, name="host9", status="running")]

    hm = HealthWorker(MapClient())._build_host_map()
    assert hm == {7: "host7", 9: "host9"}, "host_map 必须 = {id: name}"


def test_qa_merge_rows_fills_host_from_map_and_empty_when_absent(qapp):
    """纯函数 _merge_rows + host_map：命中填 name，未命中 host_name 为空。"""
    vms = [VmBrief(id=1, uuid="u1", hostId=7),
           VmBrief(id=2, uuid="u2", hostId=9),    # 9 不在 map
           VmBrief(id=3, uuid="u3", hostId=0)]    # hostId=0
    summaries = {1: RsDomainSummary(title="vm-1", ip="10.0.0.1"),
                 2: RsDomainSummary(title="vm-2", ip="10.0.0.2"),
                 3: RsDomainSummary(title="vm-3", ip="10.0.0.3")}
    monitors = [VmPerf(uuid="u1", cpuRate=1.0, memRate=2.0, diskMaxUsage=3.0),
                VmPerf(uuid="u2", cpuRate=4.0, memRate=5.0, diskMaxUsage=6.0),
                VmPerf(uuid="u3", cpuRate=7.0, memRate=8.0, diskMaxUsage=9.0)]
    rows = HealthWorker._merge_rows(vms, summaries, monitors,
                                    host_map={7: "host7"})
    # 命中
    assert rows[0].host_id == 7 and rows[0].host_name == "host7"
    # hostId 不在 host_map -> 空字符串（不崩、不猜）
    assert rows[1].host_id == 9 and rows[1].host_name == ""
    # hostId=0 -> 空字符串
    assert rows[2].host_id == 0 and rows[2].host_name == ""
    # 性能未被 host 列逻辑破坏
    assert rows[1].vm_cpu == 4.0 and rows[2].vm_cpu == 7.0


# ---------------------------------------------------------------------------
# 2) 主机名解析链路：hostId 部分缺失（host_map 只含部分主机）
# ---------------------------------------------------------------------------
def test_qa_host_name_empty_when_hostid_missing_from_list(qapp):
    """VM 的 hostId 不在 /hosts 返回中 -> host_name 空，host_id 仍正确。"""

    class PartialClient(H3CWorkspaceClient):
        def __init__(self):
            pass

        def query_vm_list(self, domain_name=None):
            return [VmBrief(id=1, uuid="u1", status="running", hostId=9),
                    VmBrief(id=2, uuid="u2", status="3", hostId=0)]

        def get_vm_summary(self, vm_id):
            return RsDomainSummary(title=f"vm-{vm_id}", osVersion="Win10",
                                   status="running" if vm_id == 1 else "3",
                                   ip=f"10.0.0.{vm_id}")

        def get_vm_monitor(self, domain_uuid):
            if domain_uuid == "u1":
                return {"success": True, "errorCode": 0,
                        "data": {"cpuRate": 10.0, "memRate": 20.0}}
            # 关机桌面（u2）：cpuRate/memRate 为 null（真实平台行为）
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": None, "memRate": None, "status": 3}}

        def list_hosts(self):
            # 仅返回 host 7，VM 的 hostId=9 / 0 均不在其中
            return [HostBrief(id=7, name="host7", status="running")]

    w = HealthWorker(PartialClient(), max_concurrency=2)
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    w.collect()

    assert err.items == []
    rows = ready.items[0]
    # hostId=9 不在 host_map -> host_name 空，host_id 仍 = 9（数据未丢）
    assert rows[0].host_id == 9 and rows[0].host_name == ""
    # hostId=0 -> host_name 空
    assert rows[1].host_id == 0 and rows[1].host_name == ""
    # 其余列不受影响
    assert rows[0].vm_cpu == 10.0 and rows[0].ip == "10.0.0.1"


# ---------------------------------------------------------------------------
# 3) 主机列表失败容错
# ---------------------------------------------------------------------------
def test_qa_host_list_failure_tolerated_collect_keeps_other_columns(qapp):
    """list_hosts 抛 WorkspaceAPIError：collect 不崩、其余列正常、host_name 空。"""

    class BoomHostClient(H3CWorkspaceClient):
        def __init__(self):
            pass

        def query_vm_list(self, domain_name=None):
            return [VmBrief(id=1, uuid="u1", status="running", hostId=7),
                    VmBrief(id=2, uuid="u2", status="3", hostId=0)]

        def get_vm_summary(self, vm_id):
            return RsDomainSummary(title=f"vm-{vm_id}", osVersion="Win10",
                                   status="running" if vm_id == 1 else "3",
                                   ip=f"10.0.0.{vm_id}")

        def get_vm_monitor(self, domain_uuid):
            if domain_uuid == "u1":
                return {"success": True, "errorCode": 0,
                        "data": {"cpuRate": 10.0, "memRate": 20.0}}
            # 关机桌面（u2）：cpuRate/memRate 为 null（真实平台行为）
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": None, "memRate": None, "status": 3}}

        def list_hosts(self):
            raise WorkspaceAPIError(code=500, message="主机列表接口挂了")

    w = HealthWorker(BoomHostClient(), max_concurrency=2)
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    # 关键：collect 整体不得抛异常（失败仅在 host 列表降级，不进 bridge）
    w.collect()

    assert err.items == [], "host 列表失败不应触发 bridge 异常"
    assert len(ready.items) == 1
    rows = ready.items[0]
    assert len(rows) == 2
    # 主机名解析失败 -> 空；IP/CPU/mem 不受影响
    # （本 mock 未实现 get_domain_detail，磁盘率按 None 处理，UI 显示 "-"）
    assert rows[0].host_name == "" and rows[1].host_name == ""
    assert rows[0].ip == "10.0.0.1"
    assert (rows[0].vm_cpu, rows[0].vm_mem) == (10.0, 20.0)
    assert rows[0].vm_disk is None, "mock 未提供 domainDetail → 磁盘率 None"
    assert rows[0].data_source == "vm"
    assert rows[1].vm_cpu is None  # 关机桌面仍 None


def test_qa_host_cell_renders_name_then_hostid_then_dash(qapp):
    """所在主机单元格渲染：host_name 优先 -> 裸 host_id -> '-'。"""
    panel = HealthPanel()
    panel.show()
    qapp.processEvents()
    panel.set_rows([
        HealthRow(vm_id=1, title="vm-1", host_id=7, host_name="host7",
                  vm_cpu=1.0),
        HealthRow(vm_id=2, title="vm-2", host_id=5, host_name="",
                  vm_cpu=2.0),
        HealthRow(vm_id=3, title="vm-3", host_id=0, host_name="",
                  vm_cpu=3.0),
    ])
    assert panel.table.item(0, 4).text() == "host7", "有主机名显示主机名"
    assert panel.table.item(1, 4).text() == "5", "无主机名回退裸 host_id"
    assert panel.table.item(2, 4).text() == "-", "host_id 为 0 显示 '-'"
    panel.close()


# ---------------------------------------------------------------------------
# 4) 既有的「桌面级优先 + 宿主级回退」不被破坏，且 host 列在回退后仍有值
#    （实现方回退测试未断言 host_id/host_name，本用例补强）
# ---------------------------------------------------------------------------
def test_qa_fallback_to_host_level_keeps_host_column(qapp):
    """2.27.30 全部报错 -> 回退宿主级；host 列仍有值且 host_id 取自 VmBrief。"""
    host_perf = HostPerf(cpuRate=33.0, memRate=44.0, diskMaxUsage=55.0)

    class FallbackClient(H3CWorkspaceClient):
        def __init__(self):
            pass

        def query_vm_list(self, domain_name=None):
            # 回退依赖清单(2.27.40)的 VmBrief.hostId
            return [VmBrief(id=1, uuid="u1", title="vm-1", status="running",
                            ipAddr="10.0.0.1", hostId=1),
                    VmBrief(id=2, uuid="u2", title="vm-2", status="3",
                            ipAddr="10.0.0.2", hostId=1)]

        def get_vm_summary(self, vm_id):
            # 概要给出「冲突」hostId=999，证明行的 host_id 取自 VmBrief 而非概要
            return RsDomainSummary(title=f"vm-{vm_id}", osVersion="Win10",
                                   status="running" if vm_id == 1 else "3",
                                   hostId=999 if vm_id == 1 else 0,
                                   ip=f"10.0.0.{vm_id}")

        def get_vm_monitor(self, domain_uuid):
            raise WorkspaceAPIError(code=400, message="请求被拒绝 (400): ")

        def get_host_cpumemdisk(self, host_id):
            assert host_id == 1, "回退只查清单里的 hostId=1"
            return host_perf

        def list_hosts(self):
            return [HostBrief(id=1, name="host-ALPHA", status="running")]

    w = HealthWorker(FallbackClient(), max_concurrency=2)
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    w.collect()

    assert err.items == [], f"回退不应报错: {err.items}"
    assert len(ready.items) == 1
    rows = ready.items[0]
    assert len(rows) == 2
    # 回退生效
    assert rows[0].data_source == "host"
    assert (rows[0].vm_cpu, rows[0].vm_mem, rows[0].vm_disk) == (33.0, 44.0, 55.0)
    assert rows[1].vm_cpu == 33.0 and rows[1].data_source == "host"
    # 所在主机列在回退后仍有值
    assert rows[0].host_id == 1, "host_id 取自 VmBrief，不受概要 999 影响"
    assert rows[0].host_name == "host-ALPHA", "host_name 来自 list_hosts 映射"
    assert rows[1].host_id == 1 and rows[1].host_name == "host-ALPHA"


def test_qa_desktop_priority_does_not_invoke_host_level_when_vm_monitors_ok(qapp):
    """桌面级 2.27.30 正常时：绝不调用 2.4.12 宿主级接口（主口径纯净）。"""
    calls = {"get_host_cpumemdisk": 0}

    class StrictClient(H3CWorkspaceClient):
        def __init__(self):
            pass

        def query_vm_list(self, domain_name=None):
            return [VmBrief(id=1, uuid="u1", status="running", hostId=1)]

        def get_vm_summary(self, vm_id):
            return RsDomainSummary(title="vm-1", osVersion="Win10",
                                   status="running", ip="10.0.0.1")

        def get_vm_monitor(self, domain_uuid):
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": 12.0, "memRate": 34.0}}

        def get_host_cpumemdisk(self, host_id):
            calls["get_host_cpumemdisk"] += 1
            raise AssertionError("桌面级正常时不应调用 2.4.12")

        def list_hosts(self):
            return [HostBrief(id=1, name="host1", status="running")]

    w = HealthWorker(StrictClient(), max_concurrency=2)
    ready, err = _Collector(), _Collector()
    w.health_ready, w.error = ready, err
    w.collect()

    assert calls["get_host_cpumemdisk"] == 0, "桌面级主路径调用了宿主级接口"
    assert err.items == []
    rows = ready.items[0]
    assert rows[0].data_source == "vm"
    assert rows[0].vm_cpu == 12.0
    assert rows[0].host_id == 1 and rows[0].host_name == "host1"


# ---------------------------------------------------------------------------
# 5) 表头/列数契约
# ---------------------------------------------------------------------------
def test_qa_health_panel_header_contract(qapp):
    panel = HealthPanel()
    headers = [panel.table.horizontalHeaderItem(c).text()
               for c in range(panel.table.columnCount())]
    expected = ["名称", "IP", "操作系统", "状态", "所在主机",
                "桌面CPU%", "桌面内存%", "桌面磁盘%"]
    assert panel.table.columnCount() == 8, "必须为 8 列"
    assert headers == expected, "表头顺序/文本须与契约一致"
    assert headers.index("所在主机") == 4, "所在主机在 状态(3) 之后、桌面CPU%(5) 之前"
    assert all("桌面" in h for h in headers[5:]), "性能列须标注「桌面」口径"
    assert not any("宿主" in h for h in headers), "不得展示宿主级性能列"
    panel.close()
