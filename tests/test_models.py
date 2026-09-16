"""core.models 单元测试：统一外层封装 raise_if_fail、各数据类默认值与解析。"""

import pytest

from core.errors import WorkspaceAPIError
from core.models import (
    Chunk,
    HealthRow,
    HostBrief,
    HostPerf,
    RpcPagingResult,
    RpcResult,
    RsDomainSummary,
    VmBrief,
    VmDetail,
    VmPerf,
    WarnInfoDTO,
)


def test_rpc_result_success_ok():
    RpcResult(success=True, errorCode=0, failureMessage="", state=0).raise_if_fail()


def test_rpc_result_raise_on_failure():
    with pytest.raises(WorkspaceAPIError):
        RpcResult(success=False, errorCode=0, failureMessage="boom", state=0).raise_if_fail()
    with pytest.raises(WorkspaceAPIError):
        RpcResult(success=True, errorCode=5001, failureMessage="err", state=0).raise_if_fail()


def test_rpc_paging_raise_on_failure():
    RpcPagingResult(success=True, errorCode=0, failureMessage="", state=0).raise_if_fail()
    with pytest.raises(WorkspaceAPIError):
        RpcPagingResult(success=False, errorCode=2, failureMessage="z", state=0).raise_if_fail()


def test_warninfo_defaults_and_construction():
    w = WarnInfoDTO(
        id=1, eventName="CPU高", eventDesc="desc", eventLevel=1,
        eventTime=123, eventType=2, state=1, eventSrc="h", eventCount=3,
    )
    assert w.id == 1 and w.eventLevel == 1 and w.eventDesc == "desc"
    w2 = WarnInfoDTO()
    assert w2.eventLevel == 0 and w2.eventName == "" and w2.eventCount == 0


def test_hostperf_from_raw_diskmax():
    disk = [{"usage": 10}, {"usage": 95}, {"usage": 40}]
    hp = HostPerf.from_raw(50.5, 60.2, disk)
    assert hp.cpuRate == 50.5
    assert hp.memRate == 60.2
    assert hp.diskMaxUsage == 95.0


def test_hostperf_from_raw_empty_and_missing():
    assert HostPerf.from_raw(1, 2, []).diskMaxUsage == 0.0
    assert HostPerf.from_raw(1, 2, [{"x": 1}]).diskMaxUsage == 0.0
    # 非数值 usage 容错
    assert HostPerf.from_raw(1, 2, [{"usage": "bad"}]).diskMaxUsage == 0.0


def test_other_dataclasses_defaults():
    assert VmBrief().id == 0
    assert RsDomainSummary().hostId == 0
    assert HealthRow().vm_id == 0
    assert Chunk().score == 0.0
    assert VmDetail().uuid == ""
    assert HostBrief().id == 0
    assert RpcPagingResult(success=True, errorCode=0, failureMessage="", state=0).total == 0


# ---------------------------------------------------------------------------
# 桌面级口径（v1.0.0-health）：VmPerf / HealthRow.vm_*
# ---------------------------------------------------------------------------
def test_vmperf_from_raw_basic():
    raw = {
        "uuid": "vm-uuid-1",
        "cpuRate": 73.4,
        "memRate": 51.0,
        "disk": [{"device": "C", "usage": 30}, {"device": "D", "usage": 88.5}],
    }
    p = VmPerf.from_raw(raw)
    assert p.uuid == "vm-uuid-1"
    assert p.cpuRate == 73.4
    assert p.memRate == 51.0
    assert p.diskMaxUsage == 88.5, "多盘取 max(usage)"


def test_vmperf_from_raw_defaults_and_bad_values():
    assert VmPerf.from_raw({}).uuid == ""
    assert VmPerf.from_raw({}).cpuRate == 0.0
    # 无任何磁盘/分区 usage → None（UI 显示 "-"，与「利用率 0%」区分）
    assert VmPerf.from_raw({}).diskMaxUsage is None
    assert VmPerf.from_raw({"disk": []}).diskMaxUsage is None
    assert VmPerf.from_raw({"disk": [{"device": "C"}]}).diskMaxUsage is None
    # usage 字段存在但非数值 → 回退 0.0（有字段即视为有数据，避免误判为无数据）
    assert VmPerf.from_raw({"disk": [{"usage": "bad"}]}).diskMaxUsage == 0.0
    assert VmPerf.from_raw({"cpuRate": None, "memRate": "x"}).cpuRate == 0.0
    assert VmPerf.from_raw({"cpuRate": None, "memRate": "x"}).memRate == 0.0


def test_vmperf_from_raw_partition_fallback():
    """disk[] 无可用 usage 时兜底 partition[].usage（现网机型差异防御）。"""
    # disk 为空数组 → 兜底分区
    raw = {"uuid": "u1", "cpuRate": 20.0, "memRate": 30.0,
           "disk": [], "partition": [{"device": "C", "usage": 40},
                                     {"device": "D", "usage": 77.5}]}
    assert VmPerf.from_raw(raw).diskMaxUsage == 77.5
    # 完全无 disk 字段
    assert VmPerf.from_raw({"partition": [{"usage": 55}]}).diskMaxUsage == 55.0
    # disk 条目无 usage 字段（只有 device）→ 同样兜底分区
    assert VmPerf.from_raw({"disk": [{"device": "C"}],
                            "partition": [{"usage": 61}]}).diskMaxUsage == 61.0
    # partition 也无 usage → None
    assert VmPerf.from_raw({"disk": [], "partition": [{"device": "C"}]
                            }).diskMaxUsage is None


def test_vmperf_disk_takes_precedence_over_partition():
    """disk[].usage 仍是主源：有值时不得被 partition 覆盖。"""
    raw = {"uuid": "u1", "disk": [{"usage": 20}, {"usage": 35}],
           "partition": [{"usage": 90}]}
    assert VmPerf.from_raw(raw).diskMaxUsage == 35.0


def test_vmperf_from_raw_none_input():
    assert VmPerf.from_raw(None) == VmPerf()


def test_healthrow_vm_fields_default_none():
    """桌面级性能缺省为 None（关机/未采集），宿主级字段保留默认 0.0。"""
    row = HealthRow()
    assert row.vm_cpu is None and row.vm_mem is None and row.vm_disk is None
    assert row.host_cpu == 0.0 and row.host_mem == 0.0 and row.host_disk == 0.0


def test_healthrow_powered_off_row():
    row = HealthRow(vm_id=7, title="关机桌面", ip="10.0.0.7", os="Win10",
                    status="关机", vm_cpu=None, vm_mem=None, vm_disk=None)
    assert row.vm_cpu is None, "关机桌面性能为 None → UI 显示 '-' 且不参与阈值判定"
    assert row.host_cpu == 0.0


def test_vmbrief_ipaddr_default():
    assert VmBrief().ipAddr == ""
    assert VmBrief(id=1, uuid="u", ipAddr="10.1.1.1").ipAddr == "10.1.1.1"
