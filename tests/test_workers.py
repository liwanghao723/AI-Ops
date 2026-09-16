"""workers 轻量单元测试（仅纯逻辑，不拉起 QApplication / 事件循环）。

覆盖 AlarmWorker 的：刷新间隔钳制(10-300s)、set_interval、set_filters 丢弃 None、
fetch_once 成功时通过 signals 发出数据（用替换 emit 的方式离线验证）。

覆盖 HealthWorker（桌面级口径 v1.0.0-health）的：清单/概要/批量性能合并、
关机桌面 vm_* 置 None、行数=清单数、IPv4 两级兜底、批量接口失败上报。

PyQt5 不可用时整体跳过。
"""

pytest = __import__("pytest")
pytest.importorskip("PyQt5")

from workers.alarm_worker import AlarmWorker  # noqa: E402
from workers.health_worker import HealthWorker, NO_DATA  # noqa: E402
from core.errors import WorkspaceAPIError  # noqa: E402
from core.models import (  # noqa: E402
    HealthRow, HostBrief, HostPerf, RpcPagingResult, RsDomainSummary,
    VmBrief, VmPerf, WarnInfoDTO,
)


def _fake_client(items):
    class FakeClient:
        def list_realtime_alarms(self, **kw):
            return RpcPagingResult(
                success=True, errorCode=0, failureMessage="", state=0,
                total=len(items), items=items,
            )
    return FakeClient()


class _Collector:
    """替代 pyqtBoundSignal 的离线收集器（emit 为只读，不能整体替换，故整对象替换）。"""

    def __init__(self):
        self.items = []

    def emit(self, value):
        self.items.append(value)


def test_alarm_worker_interval_clamp():
    w = AlarmWorker(object(), interval_sec=5)
    assert w.interval_sec == 10          # 下限
    w2 = AlarmWorker(object(), interval_sec=999)
    assert w2.interval_sec == 300        # 上限
    w3 = AlarmWorker(object(), interval_sec=30)
    assert w3.interval_sec == 30         # 合法值保持


def test_set_interval_clamp():
    w = AlarmWorker(object(), interval_sec=30)
    w.set_interval(2)
    assert w.interval_sec == 10
    w.set_interval(400)
    assert w.interval_sec == 300


def test_set_filters_drops_none():
    w = AlarmWorker(object(), interval_sec=30)
    w.set_filters(eventLevel=1, state=None, eventSrc="x")
    assert w._filters == {"eventLevel": 1, "eventSrc": "x"}


def test_fetch_once_emits_items():
    items = [WarnInfoDTO(id=1, eventName="n", eventLevel=1, eventTime=1,
                         eventType=1, state=2, eventSrc="s", eventCount=1, eventDesc="d")]
    w = AlarmWorker(_fake_client(items), interval_sec=30)
    w.alarms_ready = _Collector()   # 整对象替换绑定信号，离线收集
    w.fetch_once()
    assert len(w.alarms_ready.items) == 1
    assert w.alarms_ready.items[0][0].id == 1


def test_fetch_once_bridges_exception():
    class BoomClient:
        def list_realtime_alarms(self, **kw):
            raise RuntimeError("api down")

    w = AlarmWorker(BoomClient(), interval_sec=30)
    w.error = _Collector()
    w.fetch_once()
    assert len(w.error.items) == 1 and "api down" in w.error.items[0]


# ---------------------------------------------------------------------------
# HealthWorker（桌面级口径）：合并逻辑与采集链路
# ---------------------------------------------------------------------------
def _vm(vm_id: int, uuid: str, title: str = "", ip: str = "", status: str = "",
        hostId: int = 0):
    return VmBrief(id=vm_id, uuid=uuid, title=title or f"vm-{vm_id}",
                   ipAddr=ip, status=status, hostId=hostId)


def _summary(vm_id: int, ip: str = "", os_version: str = "Win10",
             status: str = "running", title: str = "", hostId: int = 1):
    return RsDomainSummary(title=title or f"vm-{vm_id}", osVersion=os_version,
                           status=status, hostId=hostId, ip=ip)


def _perf(uuid: str, cpu: float, mem: float, disk: float):
    return VmPerf(uuid=uuid, cpuRate=cpu, memRate=mem, diskMaxUsage=disk)


def test_health_merge_rows_fills_vm_metrics_by_uuid():
    vms = [_vm(1, "u1"), _vm(2, "u2")]
    summaries = {1: _summary(1, ip="10.0.0.1"), 2: _summary(2, ip="10.0.0.2")}
    monitors = [_perf("u1", 12.0, 34.0, 56.0), _perf("u2", 90.0, 91.0, 92.0)]
    rows = HealthWorker._merge_rows(vms, summaries, monitors)
    assert len(rows) == 2
    assert rows[0].vm_cpu == 12.0 and rows[0].vm_mem == 34.0 and rows[0].vm_disk == 56.0
    assert rows[1].vm_cpu == 90.0 and rows[1].vm_mem == 91.0 and rows[1].vm_disk == 92.0
    assert rows[0].ip == "10.0.0.1" and rows[0].os == "Win10"


def test_health_merge_rows_powered_off_vm_is_none_and_kept():
    """关机桌面（2.27.31 无数据）保留行、vm_* 置 None，行数 = 清单数。"""
    vms = [_vm(1, "u1"), _vm(2, "u2-poweroff", status="3")]
    summaries = {1: _summary(1, ip="10.0.0.1"), 2: _summary(2, status="3")}
    monitors = [_perf("u1", 12.0, 34.0, 56.0)]
    rows = HealthWorker._merge_rows(vms, summaries, monitors)
    assert len(rows) == 2, "关机桌面仍占一行"
    off = rows[1]
    assert off.vm_cpu is None and off.vm_mem is None and off.vm_disk is None


def test_health_merge_rows_ip_fallback_to_ipaddr_then_dash():
    """IPv4 两级兜底：2.9.11 addresses → 2.27.40 ipAddr → '-'。"""
    vms = [_vm(1, "u1"), _vm(2, "u2", ip="10.2.2.2"), _vm(3, "u3")]
    summaries = {1: _summary(1, ip="10.1.1.1"), 2: _summary(2), 3: _summary(3)}
    rows = HealthWorker._merge_rows(vms, summaries, [])
    assert rows[0].ip == "10.1.1.1", "一级：server.addresses"
    assert rows[1].ip == "10.2.2.2", "二级：query_vm_list.ipAddr"
    assert rows[2].ip == NO_DATA == "-", "两级皆无 → '-'"


def test_health_merge_rows_ip_prefers_27257_ip_by_uuid():
    """IPv4 主来源（2.27.57 networks[].ipAddr，经 ip_by_uuid 传入）优先于旧兜底。"""
    vms = [_vm(1, "u1", ip="10.2.2.2")]
    # 2.9.11 概要也有值，但 2.27.57 主来源必须胜出
    summaries = {1: _summary(1, ip="10.1.1.1")}
    ip_by_uuid = {"u1": "10.9.9.9"}
    rows = HealthWorker._merge_rows(vms, summaries, [], ip_by_uuid=ip_by_uuid)
    assert rows[0].ip == "10.9.9.9", "ip_by_uuid(2.27.57) 必须优先于 server.addresses/ipAddr"


def test_health_merge_rows_ip_falls_back_when_ip_by_uuid_missing():
    """ip_by_uuid 缺某桌面时，回退到 server.addresses / ipAddr / '-'。"""
    vms = [_vm(1, "u1"), _vm(2, "u2", ip="10.2.2.2"), _vm(3, "u3")]
    summaries = {1: _summary(1, ip="10.1.1.1"), 2: _summary(2), 3: _summary(3)}
    # 仅 u1 在 ip_by_uuid 中，其余回退旧链
    rows = HealthWorker._merge_rows(vms, summaries, [], ip_by_uuid={"u1": "10.9.9.9"})
    assert rows[0].ip == "10.9.9.9"
    assert rows[1].ip == "10.2.2.2", "回退到清单 ipAddr"
    assert rows[2].ip == NO_DATA == "-", "全无 → '-'"


def test_health_merge_rows_disk_prefers_disk_by_id():
    """disk_by_id（2.9.10 domainDetail）优先于 perf.diskMaxUsage 和 host_perf。"""
    vms = [_vm(1, "u1"), _vm(2, "u2")]
    summaries = {1: _summary(1), 2: _summary(2)}
    # monitor 自带 disk=77，但 disk_by_id 有更高优先级的值
    monitors = [_perf("u1", 12.0, 34.0, 77.0)]
    host_perf = {2: HostPerf(cpuRate=11.0, memRate=22.0, diskMaxUsage=88.0)}
    disk_by_id = {1: 56.74, 2: 2.89}  # 2.9.10 真实值
    rows = HealthWorker._merge_rows(vms, summaries, monitors,
                                    host_perf_by_vm=host_perf,
                                    disk_by_id=disk_by_id)
    assert rows[0].vm_disk == 56.74, "disk_by_id 优先于 monitor 的 diskMaxUsage=77"
    assert rows[1].vm_disk == 2.89, "disk_by_id 优先于 host_perf 的 diskMaxUsage=88"


def test_health_merge_rows_disk_falls_back_without_disk_by_id():
    """无 disk_by_id 时回退到 perf/host_perf 的 disk（行为不变）。"""
    vms = [_vm(1, "u1"), _vm(2, "u2")]
    summaries = {1: _summary(1), 2: _summary(2)}
    monitors = [_perf("u1", 12.0, 34.0, 56.0)]
    host_perf = {2: HostPerf(cpuRate=11.0, memRate=22.0, diskMaxUsage=88.0)}
    rows = HealthWorker._merge_rows(vms, summaries, monitors,
                                    host_perf_by_vm=host_perf)
    # 无 disk_by_id → 回退旧链：monitor.disk > host_perf.disk > None
    assert rows[0].vm_disk == 56.0, "回退到 monitor diskMaxUsage"
    assert rows[1].vm_disk == 88.0, "回退到 host_perf diskMaxUsage"


def test_health_merge_rows_disk_none_when_all_empty():
    """disk_by_id 空 + 无 monitor/host_perf → vm_disk 为 None。"""
    vms = [_vm(1, "u1")]
    rows = HealthWorker._merge_rows(vms, {}, [])
    assert rows[0].vm_disk is None


def test_health_merge_rows_summary_missing_still_keeps_row():
    """概要接口单条失败不丢行（标题回退清单 title）。"""
    vms = [_vm(9, "u9", title="清单标题")]
    rows = HealthWorker._merge_rows(vms, {}, [_perf("u9", 1.0, 2.0, 3.0)])
    assert len(rows) == 1
    assert rows[0].title == "清单标题"
    assert rows[0].vm_cpu == 1.0


def test_health_worker_collect_emits_rows_with_vm_metrics():
    """端到端（离线）：collect 走 清单→概要→2.27.30逐台监控→关联，emit 桌面级指标。"""
    from core.workspace_client import H3CWorkspaceClient  # 仅用于类型占位

    class FakeClient(H3CWorkspaceClient):
        def __init__(self):
            pass  # 不需要真实 http

        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1"), _vm(2, "u2-off", status="3")]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip=f"10.0.0.{vm_id}",
                            status="running" if vm_id == 1 else "3")

        def get_vm_monitor(self, domain_uuid):
            if domain_uuid == "u1":
                return {"success": True, "errorCode": 0, "data": {"cpuRate": 88.0, "memRate": 40.0}}
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": None, "memRate": None, "status": 3}}

    w = HealthWorker(FakeClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2, "行数 = 清单数（含关机桌面）"
    assert isinstance(rows[0], HealthRow)
    assert rows[0].vm_cpu == 88.0 and rows[0].vm_mem == 40.0
    # FakeClient 无 get_domain_detail → _build_disk_map 全部失败 → disk_by_id 为空
    # 且 monitor 返回数据无 disk 字段 → vm_disk 恒为 None（UI 显示 "-"）
    assert rows[0].vm_disk is None
    assert rows[1].vm_cpu is None, "关机桌面 cpuRate/memRate 为 null → 显示 '-'"
    assert rows[0].host_cpu == 0.0, "宿主级字段桌面级口径下不填充"


def test_health_worker_collect_empty_list_emits_empty():
    class EmptyClient:
        def query_vm_list(self, domain_name=None):
            return []

    w = HealthWorker(EmptyClient())
    w.health_ready = _Collector()
    w.collect()
    assert w.health_ready.items == [[]]


def test_health_worker_collect_bridges_api_error():
    class BoomClient:
        def query_vm_list(self, domain_name=None):
            raise RuntimeError("2.27.40 down")

    w = HealthWorker(BoomClient())
    w.error = _Collector()
    w.collect()
    assert len(w.error.items) == 1 and "2.27.40 down" in w.error.items[0]


def test_health_worker_no_monitor_method_falls_back_to_error():
    """客户端不支持 2.27.31（AttributeError）时同样经 bridge 上报，不静默。"""
    class LegacyClient:
        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1")]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip="10.0.0.1")

    w = HealthWorker(LegacyClient())
    w.error = _Collector()
    w.collect()
    assert len(w.error.items) == 1


def test_health_worker_disk_none_when_no_disk_data():
    """2.27.31 既无 disk 也无 partition → vm_disk 为 None（UI 显示 '-'）。"""
    vms = [_vm(1, "u1")]
    rows = HealthWorker._merge_rows(
        vms, {1: _summary(1, ip="10.0.0.1")},
        [VmPerf(uuid="u1", cpuRate=5.0, memRate=6.0, diskMaxUsage=None)])
    assert rows[0].vm_cpu == 5.0
    assert rows[0].vm_disk is None


# ---------------------------------------------------------------------------
# 桌面级接口不可用 → 自动回退宿主级（2.4.12）
# ---------------------------------------------------------------------------
def test_collect_falls_back_to_host_level_when_all_vm_monitor_error():
    """桌面级 2.27.30 全部报错 → 自动回退宿主级，health_ready 仍发射非空行，
    且 vm_* 来自 host 级数据、data_source == "host"。"""
    host_perf = HostPerf(cpuRate=33.0, memRate=44.0, diskMaxUsage=55.0)

    class FallbackClient:
        def query_vm_list(self, domain_name=None):
            # 回退依赖清单(2.27.40)的 VmBrief.hostId（真实环境 2.9.11 概要会 400）
            return [_vm(1, "u1", status="running", hostId=1),
                    _vm(2, "u2-off", status="3", hostId=1)]

        def get_vm_summary(self, vm_id):
            # 概要即便 400/缺失也不影响回退（hostId 取自清单）
            return _summary(vm_id, ip=f"10.0.0.{vm_id}",
                            status="running" if vm_id == 1 else "3", hostId=1)

        def get_vm_monitor(self, domain_uuid):
            raise WorkspaceAPIError(code=400, message="请求被拒绝 (400): ")

        def get_host_cpumemdisk(self, host_id):
            assert host_id == 1
            return host_perf

    w = HealthWorker(FallbackClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2, "回退后仍保留全部行（含关机桌面）"
    # 桌面级不可用时回退宿主级：同 host 的开机/关机桌面均填 host 性能
    assert rows[0].vm_cpu == 33.0 and rows[0].vm_mem == 44.0 and rows[0].vm_disk == 55.0
    assert rows[0].data_source == "host"
    assert rows[1].vm_cpu == 33.0  # 同 host，关机桌面也填宿主级数据
    assert rows[1].data_source == "host"


def test_collect_uses_vm_monitor_rates():
    """2.27.30 逐台监控返回 cpuRate/memRate → 对应行填充桌面级指标，data_source=="vm"。"""
    class MonitorClient:
        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1", status="running", hostId=7),
                    _vm(2, "u2", status="running", hostId=9)]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip=f"10.0.0.{vm_id}", status="running")

        def get_vm_monitor(self, domain_uuid):
            data = {"u1": {"cpuRate": 3.88, "memRate": 40.59},
                    "u2": {"cpuRate": 12.34, "memRate": 56.78}}
            return {"success": True, "errorCode": 0, "data": data[domain_uuid]}

    w = HealthWorker(MonitorClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2
    # 桌面级（2.27.30）单台指标正确关联
    assert rows[0].vm_cpu == 3.88 and rows[0].vm_mem == 40.59
    assert rows[1].vm_cpu == 12.34 and rows[1].vm_mem == 56.78
    # 磁盘使用率本次不取（2.27.30 无可用字段）→ None
    assert rows[0].vm_disk is None and rows[1].vm_disk is None
    # 数据源为桌面级，未触发宿主级回退
    assert rows[0].data_source == "vm" and rows[1].data_source == "vm"


def test_collect_off_vm_shows_dash():
    """关机桌面 2.27.30 返回 cpuRate/memRate=null（非 400）→ 该行 vm_cpu 为 None，不回退宿主级。"""
    class OffClient:
        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1-off", status="3", hostId=7),
                    _vm(2, "u2", status="running", hostId=7)]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip=f"10.0.0.{vm_id}", status="3" if vm_id == 1 else "running")

        def get_vm_monitor(self, domain_uuid):
            if domain_uuid == "u1-off":
                # 真实平台：成功响应但 cpu/mem 为 null（关机）
                return {"success": True, "errorCode": 0,
                        "data": {"cpuRate": None, "memRate": None, "status": 2}}
            return {"success": True, "errorCode": 0, "data": {"cpuRate": 9.0, "memRate": 11.0}}

    w = HealthWorker(OffClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2
    # 关机桌面：cpuRate/memRate 为 null → 不加入 monitors → vm_cpu 为 None（显示 "-"）
    assert rows[0].vm_cpu is None and rows[0].vm_mem is None
    # 关键：关机桌面**不**回退宿主级（仍按桌面级口径，data_source 保持 "vm"）
    assert rows[0].data_source == "vm"
    # 同 host 的正常运行桌面仍正常填充桌面级指标
    assert rows[1].vm_cpu == 9.0 and rows[1].data_source == "vm"


def test_collect_emits_empty_on_both_vm_and_host_failure():
    """桌面级 2.27.30 全部报错 + 宿主级也失败 → 两者皆无性能数据，最终 bridge 收到异常。"""
    class BothFailClient:
        def query_vm_list(self, domain_name=None):
            # 清单给有效 hostId，使宿主级接口真的被调用（再失败）
            return [_vm(1, "u1", hostId=1)]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip="10.0.0.1", hostId=1)

        def get_vm_monitor(self, domain_uuid):
            raise WorkspaceAPIError(code=400, message="请求被拒绝 (400): ")

        def get_host_cpumemdisk(self, host_id):
            raise WorkspaceAPIError(code=500, message="宿主接口也挂了")

    w = HealthWorker(BothFailClient())
    w.health_ready = _Collector()
    w.error = _Collector()
    w.collect()

    # 没有任何性能数据（桌面级与宿主级皆失败）→ 上报异常，不发射空表
    assert len(w.error.items) == 1
    assert "均不可用" in w.error.items[0]
    assert w.health_ready.items == [], "两者皆失败不应发射空表"


def test_collect_host_fallback_skips_zero_hostid():
    """边界：2.27.30 全部报错后回退，且 VmBrief.hostId 为 0 → 该 VM 回退不到宿主级，vm_* 保持 None。"""
    host_perf = HostPerf(cpuRate=11.0, memRate=22.0, diskMaxUsage=33.0)

    class ZeroHostClient:
        def query_vm_list(self, domain_name=None):
            # 回退以清单 VmBrief.hostId 为准：vm1 有合法 hostId，vm2 hostId=0
            return [_vm(1, "u1", hostId=1), _vm(2, "u2", hostId=0)]

        def get_vm_summary(self, vm_id):
            host = 1 if vm_id == 1 else 0
            return _summary(vm_id, ip=f"10.0.0.{vm_id}", hostId=host)

        def get_vm_monitor(self, domain_uuid):
            raise WorkspaceAPIError(code=400, message="请求被拒绝 (400): ")

        def get_host_cpumemdisk(self, host_id):
            assert host_id == 1
            return host_perf

    w = HealthWorker(ZeroHostClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    rows = w.health_ready.items[0]
    assert len(rows) == 2
    # vm1：宿主级成功填充
    assert rows[0].vm_cpu == 11.0 and rows[0].data_source == "host"
    # vm2：hostId=0，无法回退，vm_* 为 None（合理退化）
    assert rows[1].vm_cpu is None and rows[1].vm_mem is None and rows[1].vm_disk is None
    assert rows[1].data_source == "host"


# ---------------------------------------------------------------------------
# 所在主机列：2.4.7 主机名映射
# ---------------------------------------------------------------------------
def test_collect_fills_host_name():
    """collect 调一次 list_hosts(2.4.7) 构建 hostId->name，行填 host_id/host_name。"""
    hosts = [HostBrief(id=7, name="host7", status="running"),
             HostBrief(id=9, name="host9", status="running")]

    class HostClient:
        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1", status="running", hostId=7),
                    _vm(2, "u2-off", status="3", hostId=9)]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip=f"10.0.0.{vm_id}",
                            status="running" if vm_id == 1 else "3")

        def get_vm_monitor(self, domain_uuid):
            if domain_uuid == "u1":
                return {"success": True, "errorCode": 0, "data": {"cpuRate": 10.0, "memRate": 20.0}}
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": None, "memRate": None, "status": 3}}

        def list_hosts(self):
            return hosts

    w = HealthWorker(HostClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2
    # 开机 VM：host_id / host_name 正确解析
    assert rows[0].host_id == 7 and rows[0].host_name == "host7"
    # 关机 VM：同样按 hostId 解析（与开关机无关）
    assert rows[1].host_id == 9 and rows[1].host_name == "host9"
    # 性能口径未受影响（仍为桌面级，2.27.30）
    assert rows[0].vm_cpu == 10.0 and rows[0].data_source == "vm"


def test_collect_host_list_failure_shows_dash():
    """list_hosts(2.4.7) 抛错时主机列降级（host_name 空），不崩、其余列仍正常。"""

    class BoomHostClient:
        def query_vm_list(self, domain_name=None):
            return [_vm(1, "u1", status="running", hostId=7),
                    _vm(2, "u2-off", status="3", hostId=0)]

        def get_vm_summary(self, vm_id):
            return _summary(vm_id, ip=f"10.0.0.{vm_id}",
                            status="running" if vm_id == 1 else "3")

        def get_vm_monitor(self, domain_uuid):
            return {"success": True, "errorCode": 0, "data": {"cpuRate": 10.0, "memRate": 20.0}}

        def list_hosts(self):
            raise WorkspaceAPIError(code=500, message="主机列表接口挂了")

    w = HealthWorker(BoomHostClient(), max_concurrency=2)
    w.health_ready = _Collector()
    w.collect()

    assert len(w.health_ready.items) == 1
    rows = w.health_ready.items[0]
    assert len(rows) == 2
    # 主机名解析失败 → host_name 为空、其余列（IP/性能）不受影响
    assert rows[0].host_name == ""
    assert rows[0].ip == "10.0.0.1" and rows[0].vm_cpu == 10.0
    # hostId=0 的 VM 同样为空（回退裸 hostId 逻辑由 UI 处理）
    assert rows[1].host_name == "" and rows[1].host_id == 0
    # 整体采集流程未崩，正常发射
    assert rows[0].data_source == "vm"


# ---------------------------------------------------------------------------
# AnalyzeWorker：健康解读阈值透传（与 HealthPanel 着色同源）
# ---------------------------------------------------------------------------
class _FakeAnalyzer:
    """记录 explain_health 收到的阈值，并返回可断言的解读文本。"""

    def __init__(self):
        self.calls = []

    def explain_health(self, row, threshold_cpu=85, threshold_mem=85):
        self.calls.append((row, threshold_cpu, threshold_mem))
        return (f"cpu阈值={threshold_cpu} mem阈值={threshold_mem} "
                f"越界={row.vm_cpu is not None and row.vm_cpu >= threshold_cpu}")


def test_analyze_worker_passes_health_thresholds():
    from workers.analyze_worker import AnalyzeWorker

    analyzer = _FakeAnalyzer()
    w = AnalyzeWorker(analyzer, threshold_cpu=50, threshold_mem=60)
    w.health_analysis_ready = _Collector()
    w.explain_health(HealthRow(vm_id=1, vm_cpu=70.0))
    w._pump()
    assert analyzer.calls[0][1:] == (50, 60)
    assert w.health_analysis_ready.items[0] == "cpu阈值=50 mem阈值=60 越界=True"


def test_analyze_worker_default_thresholds_backward_compatible():
    """不传阈值时仍为 85/85（既有调用方与 Analyzer 默认行为不变）。"""
    from workers.analyze_worker import AnalyzeWorker

    analyzer = _FakeAnalyzer()
    w = AnalyzeWorker(analyzer)
    w.health_analysis_ready = _Collector()
    w.explain_health(HealthRow(vm_id=2, vm_cpu=70.0))
    w._pump()
    assert analyzer.calls[0][1:] == (85, 85)
    assert w.health_analysis_ready.items[0] == "cpu阈值=85 mem阈值=85 越界=False"


def test_analyze_worker_set_health_thresholds_hot_reload():
    """配置保存后热更新阈值，后续解读立即生效。"""
    from workers.analyze_worker import AnalyzeWorker

    analyzer = _FakeAnalyzer()
    w = AnalyzeWorker(analyzer, threshold_cpu=85, threshold_mem=85)
    w.health_analysis_ready = _Collector()
    w.set_health_thresholds(40, 45)
    w.explain_health(HealthRow(vm_id=3, vm_cpu=41.0))
    w._pump()
    assert analyzer.calls[0][1:] == (40, 45)
    assert w.health_analysis_ready.items[0] == "cpu阈值=40 mem阈值=45 越界=True"
