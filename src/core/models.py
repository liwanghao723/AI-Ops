"""数据结构 / 数据类（对应架构 §3.3）。

包含 H3C 统一外层封装（RpcResult / RpcPagingResult）与业务数据类。
所有字段命名与实测 API 文档一一对应。

统一外层（H3C 返回）：
    {"success":bool, "errorCode":int, "failureMessage":str, "state":int, "data":...}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .errors import WorkspaceAPIError


# ----------------------------------------------------------------------
# 统一外层封装
# ----------------------------------------------------------------------
@dataclass
class RpcResult(Generic[TypeVar("T")]):
    """H3C 统一外层结果封装（非分页）。"""
    success: bool
    errorCode: int
    failureMessage: str
    state: int
    data: Any = None

    def raise_if_fail(self) -> "RpcResult":
        """success=False 或 errorCode!=0 时抛 WorkspaceAPIError。"""
        if not self.success or self.errorCode != 0:
            raise WorkspaceAPIError(self.errorCode, self.failureMessage or "接口返回失败")
        return self


@dataclass
class RpcPagingResult(Generic[TypeVar("T")]):
    """H3C 统一外层结果封装（分页列表，如实时告警）。"""
    success: bool
    errorCode: int
    failureMessage: str
    state: int
    total: int = 0
    items: list = field(default_factory=list)

    def raise_if_fail(self) -> "RpcPagingResult":
        if not self.success or self.errorCode != 0:
            raise WorkspaceAPIError(self.errorCode, self.failureMessage or "接口返回失败")
        return self


# ----------------------------------------------------------------------
# 业务数据类（字段与实测 API 文档一致）
# ----------------------------------------------------------------------
@dataclass
class WarnInfoDTO:           # 2.12.1 实时告警
    id: int = 0
    eventName: str = ""
    eventDesc: str = ""
    eventLevel: int = 0      # 1紧急/2重要/3次要/4提示
    eventTime: int = 0       # 最新告警时间 ms
    firstEventTime: int = 0  # 首次告警时间 ms
    eventType: int = 0       # 1主机/2虚拟机/3集群/4CPU/5内存/6虚拟化软件
    state: int = 0           # 1确认/2未确认
    eventSrc: str = ""
    eventCount: int = 0
    category: int = 0        # 告警子系统：1主机/2虚拟机/3集群/4故障/6异常/
                             # 20 ONEStor/99虚拟应用/101桌面/123终端/
                             # 124 VIP/125网关/135 License
                             # （仅新接口 /vdi/warnManage/realTimeAlarms 返回）


@dataclass
class VmBrief:              # 2.27.40 虚拟机列表(不分页)
    id: int = 0
    hostId: int = 0
    title: str = ""
    name: str = ""
    status: str = ""        # 可 str 可 int，做容错
    uuid: str = ""
    clusterId: int = 0
    cpu: int = 0
    memory: int = 0
    ipAddr: str = ""        # 2.27.40 自带 IPv4（2.9.11 addresses 为空时的兜底）


@dataclass
class RsDomainSummary:      # 2.9.11 虚拟机概要
    title: str = ""
    osVersion: str = ""
    status: str = ""
    hostId: int = 0
    ip: str = ""


@dataclass
class HostPerf:            # 2.4.12 主机性能
    cpuRate: float = 0.0
    memRate: float = 0.0
    diskMaxUsage: float = 0.0   # = max(disk[].usage)，空则 0

    @staticmethod
    def from_raw(cpu_rate: float, mem_rate: float, disk: list | None) -> "HostPerf":
        disk_max = 0.0
        for d in (disk or []):
            try:
                usage = float(d.get("usage", 0) or 0)
            except (TypeError, ValueError):
                usage = 0.0
            disk_max = max(disk_max, usage)
        return HostPerf(cpuRate=float(cpu_rate or 0), memRate=float(mem_rate or 0),
                        diskMaxUsage=disk_max)


@dataclass
class VmPerf:              # 2.27.31 虚拟机性能（批量，仅开机桌面）
    """单台虚拟机性能 DTO（2.27.30 / 2.27.31 同一结构）。

    字段与 ``data[]`` 元素一一对应；``uuid`` 为与清单（2.27.40）关联的外键。
    ``diskMaxUsage`` = ``max(disk[].usage)``（多盘聚合，单位 %）；
    当 ``disk[]`` 无可用 ``usage`` 时兜底取 ``max(partition[].usage)``；
    两者都无可用值 → ``None``（UI 显示 "-"，不参与阈值判定）。
    """
    uuid: str = ""
    cpuRate: float = 0.0
    memRate: float = 0.0
    diskMaxUsage: float | None = None   # = max(disk[].usage)，无数据则 None

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """把任意 API 返回值安全转为 float（非数值/None 回退 default）。"""
        try:
            return float(value or default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _max_usage(cls, items: Any) -> float | None:
        """从 ``disk[]`` / ``partition[]`` 数组取 max(usage)。

        仅统计**携带 usage 字段**的条目；数组为空或全部无 usage 时返回 None
        （交由调用方继续兜底），与「无数据」语义区分于「利用率为 0」。
        """
        max_value: float | None = None
        for item in (items or []):
            if not isinstance(item, dict) or "usage" not in item:
                continue
            value = cls._to_float(item.get("usage"))
            max_value = value if max_value is None else max(max_value, value)
        return max_value

    @classmethod
    def from_raw(cls, d: dict) -> "VmPerf":
        """从 2.27.31 响应 ``data[]`` 的单个元素构造 VmPerf（容错解析）。

        磁盘利用率口径：主源 ``disk[].usage`` → 缺数据时兜底 ``partition[].usage``
        → 仍无则 ``None``（现网机型差异下的纯防御性兜底）。
        """
        d = d or {}
        disk_max = cls._max_usage(d.get("disk"))
        if disk_max is None:                # disk 为空/无 usage → 分区级兜底
            disk_max = cls._max_usage(d.get("partition"))
        return cls(
            uuid=str(d.get("uuid", "") or ""),
            cpuRate=cls._to_float(d.get("cpuRate")),
            memRate=cls._to_float(d.get("memRate")),
            diskMaxUsage=disk_max,
        )


@dataclass
class HealthRow:           # 模块二表格行聚合结果
    """健康监控表格行。

    性能字段分两套口径：
      - ``vm_*``    ：**桌面级**（单台虚拟机自身）利用率，来源 2.27.31
                      ``GET /vms/monitors/all``；单位 %（已乘 100）。
                      ``None`` 表示该桌面无性能数据（关机 / 未采集），UI 显示 "-"。
      - ``host_*``  ：宿主级（所在物理主机）利用率，来源 2.4.12；
                      桌面级口径下**不填充**（保留字段仅作向后兼容）。

    ``host_id`` / ``host_name``：所在物理主机标识（2.27.40 的 ``hostId``
    关联 2.4.7 主机名解析），用于「所在主机」列展示主机名；解析失败/未知时
    ``host_id=0``、``host_name=""``（UI 回退显示裸 ``host_id`` 或 ``"-"``）。

    ``data_source`` 记录本行性能数据的来源口径，便于 UI 后续提示：
      - ``"vm"``   ：桌面级（2.27.31）成功采集；
      - ``"host"`` ：桌面级接口不可用，已自动回退到宿主级（2.4.12）。
    """
    vm_id: int = 0
    title: str = ""
    ip: str = ""
    os: str = ""
    status: str = ""
    host_id: int = 0                 # 2.27.40 VmBrief.hostId（所在物理主机 id）
    host_name: str = ""              # 2.4.7 主机名（hostId 经 list_hosts 解析）
    vm_cpu: float | None = None     # 2.27.31 cpuRate
    vm_mem: float | None = None     # 2.27.31 memRate
    vm_disk: float | None = None    # 2.27.31 max(disk[].usage)
    host_cpu: float = 0.0           # 保留（宿主级对照，桌面级口径不填充）
    host_mem: float = 0.0
    host_disk: float = 0.0
    data_source: str = "vm"         # 性能数据来源: "vm"(桌面级) / "host"(宿主级回退)


@dataclass
class Chunk:               # 知识检索结果
    source: str = ""
    text: str = ""
    score: float = 0.0
    url: str = ""          # 原文链接：IMA 为 web/文档链接；本地为 file:// 绝对路径；空=无
    doc_id: str = ""       # 来源文档 ID（IMA media_id）；用于跨库去重与原文链接解析
    is_title_hit: bool = False  # True=仅命中标题（IMA 未返回正文），text 为《文档标题》


@dataclass
class SearchResult:       # 知识检索结果 + 来源标注（供 UI 显示「命中哪个知识库」）
    chunks: "list[Chunk]" = field(default_factory=list)
    origin: str = "none"          # "ima" / "local" / "none" / "unknown"
    kb_names: "list[str]" = field(default_factory=list)  # 命中的 IMA 知识库名（origin=ima 时非空）
    ima_status: str = ""          # IMA 步骤结果：ok/empty/auth_failed/no_cred/disabled/error


@dataclass
class VmDetail:            # 2.27.39 批量 UUID 查询返回
    ip: str = ""
    osVersion: str = ""
    title: str = ""
    status: str = ""
    uuid: str = ""


@dataclass
class HostBrief:           # 2.4.7 主机列表
    id: int = 0
    name: str = ""
    status: str = ""


# ----------------------------------------------------------------------
# 版本信息（update 模块共享）
# ----------------------------------------------------------------------
@dataclass
class VersionInfo:
    current_version: str = ""
    latest_version: str = ""
    download_url: str = ""
    notes: str = ""
    is_newer: bool = False
