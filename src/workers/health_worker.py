"""健康 Worker（对应架构 T12 / workers/health_worker.py）。

**桌面级口径**（v1.0.0-health）：

- collect() 流程：
  1. ``query_vm_list``（2.27.33 /vms/page 分页）拿全量桌面清单（**含关机**，带 uuid/ipAddr；替代硬截断 60 条的 2.27.40）
  2. 线程池（max=5）并发 ``get_vm_summary``（2.9.11）取 IPv4 / OS / 状态
  3. 并发 ``get_domain_detail``（2.9.10）取**磁盘使用率%**（``diskList[].detail.usage``）
  4. 并发 ``get_vm_monitor``（2.27.30）**逐台桌面 UUID 查询**单台 CPU/内存实时率
     （按 uuid 关联，替代 2.27.31 批量与逐 host 轮询 2.4.12 的主用路径）
  5. 逐台合并：命中性能 → 填 ``vm_*``；关机桌面 cpuRate/memRate=null → ``vm_* = None``
- 关机桌面**保留行**（行数 = 清单数），性能列显示 "-"，不计入过载预警
- 仅当 2.27.30 **全部调用报错**时才防御性回退宿主级（2.4.12）；其余情况不回退

请求数由原来的 O(主机数) 降为 1 次清单 + N 次概要 + N 次单台监控（并发）。

磁盘使用率来源：2.9.10 ``domainDetail.diskList[].detail.usage``（单位 %，0~100；
关机桌面也有值）。注意 2.27.30 的 ``partition[].usage`` 是容量(GB)不是 %，
切勿混用。

阈值（CPU/内存/磁盘 ≥ 85%）标注在 UI 层完成（见 health_panel）。
"""

from __future__ import annotations

import threading
from PyQt5.QtCore import pyqtSignal, QRunnable, QThreadPool, Qt

from core.errors import WorkspaceAPIError
from core.models import HealthRow, HostPerf, RsDomainSummary, VmPerf
from core.workspace_client import H3CWorkspaceClient
from core.logging_setup import get_logger
from .base_worker import BaseWorker


logger = get_logger(__name__)

# IPv4 两级兜底都取不到时的占位符（与 UI "-" 显示口径一致）
NO_DATA = "-"


class _SummaryTask(QRunnable):
    def __init__(self, client: H3CWorkspaceClient, vm_id: int,
                 store: dict, lock: threading.Lock):
        super().__init__()
        self.client = client
        self.vm_id = vm_id
        self.store = store
        self.lock = lock

    def run(self) -> None:
        try:
            summary = self.client.get_vm_summary(self.vm_id)
            with self.lock:
                self.store[self.vm_id] = summary
        except Exception as exc:  # 单条失败不影响整体
            logger.warning("[HealthWorker] 桌面 %s 概要失败: %s", self.vm_id, exc)


class _MonitorTask(QRunnable):
    """逐台桌面性能采集任务（2.27.30 单台 UUID 查询）。

    与 ``_SummaryTask`` 形态一致：单台失败仅计入 ``err_count`` 不影响整体；
    成功后把 ``data`` 字典（含 cpuRate/memRate/status/partition/disk/net）存入
    ``store``（按 uuid 索引），便于 collect 后续解析。
    """

    def __init__(self, client: H3CWorkspaceClient, vm_uuid: str,
                 store: dict, lock: threading.Lock, err_count: list) -> None:
        super().__init__()
        self.client = client
        self.vm_uuid = vm_uuid
        self.store = store
        self.lock = lock
        self.err_count = err_count

    def run(self) -> None:
        try:
            raw = self.client.get_vm_monitor(self.vm_uuid)
            data = (raw or {}).get("data") or {}
            with self.lock:
                self.store[self.vm_uuid] = data
        except Exception as exc:  # 单台失败不影响整体
            logger.warning("[HealthWorker] 桌面 %s 监控失败: %s", self.vm_uuid, exc)
            with self.lock:
                self.err_count[0] += 1


class _IpFetchTask(QRunnable):
    """逐台桌面 IPv4 采集任务（2.27.57 单台详情，取 networks[].ipAddr）。

    与 ``_SummaryTask`` / ``_MonitorTask`` 形态一致：单台失败仅影响单行 IP
    （run 内部已吞异常），成功后把 IPv4 字符串存入 ``store``（按 uuid 索引）。
    """

    def __init__(self, client: H3CWorkspaceClient, vm_uuid: str,
                 store: dict, lock: threading.Lock) -> None:
        super().__init__()
        self.client = client
        self.vm_uuid = vm_uuid
        self.store = store
        self.lock = lock

    def run(self) -> None:
        try:
            ip = self.client.get_vm_ip_by_uuid(self.vm_uuid)
            with self.lock:
                self.store[self.vm_uuid] = ip
        except Exception as exc:  # 单台失败不影响整体
            logger.warning("[HealthWorker] 桌面 %s IPv4 获取失败: %s", self.vm_uuid, exc)


class _DiskTask(QRunnable):
    """逐台桌面磁盘使用率采集任务（2.9.10 domainDetail，取 diskList[].detail.usage）。"""

    def __init__(self, client: H3CWorkspaceClient, vm_id: int,
                 store: dict, lock: threading.Lock) -> None:
        super().__init__()
        self.client = client
        self.vm_id = vm_id
        self.store = store
        self.lock = lock

    def run(self) -> None:
        try:
            detail = self.client.get_domain_detail(self.vm_id)
            d = (detail or {}).get("data") or {}
            disk_list = d.get("diskList") or []
            max_usage: float | None = None
            for disk in disk_list:
                if not isinstance(disk, dict):
                    continue
                detail_obj = disk.get("detail") or {}
                usage = detail_obj.get("usage")
                if usage is not None:
                    try:
                        u = float(usage)
                        max_usage = u if max_usage is None else max(max_usage, u)
                    except (TypeError, ValueError):
                        pass
            with self.lock:
                self.store[self.vm_id] = max_usage
        except Exception as exc:
            logger.warning("[HealthWorker] 桌面 %s 磁盘使用率获取失败: %s",
                           self.vm_id, exc)


class HealthWorker(BaseWorker):
    health_ready = pyqtSignal(list)  # list[HealthRow]
    request_collect = pyqtSignal()   # 主线程请求采集（排队到 worker 线程执行）

    def __init__(self, client: H3CWorkspaceClient, max_concurrency: int = 5) -> None:
        super().__init__()
        self.client = client
        self.max_concurrency = max(1, max_concurrency)
        # 将采集请求跨线程排队到本 worker 线程执行，避免阻塞 UI
        self.request_collect.connect(self.collect, Qt.QueuedConnection)

    def run(self) -> None:
        self.collect()

    # ------------------------------------------------------------------
    # 采集：清单 → 概要 → 逐台监控(2.27.30)（桌面级优先，全部失败回退宿主级）→ 合并
    # ------------------------------------------------------------------
    def collect(self) -> None:
        try:
            vms = self.client.query_vm_list()
            if not vms:
                self.health_ready.emit([])
                return

            # 0) 主机名映射（2.4.7 一次请求），失败降级空 dict（不影响桌面行）
            host_map = self._build_host_map()
            logger.info("[HealthWorker] 主机名映射已构建（2.4.7）: 共 %d 台，示例=%s",
                        len(host_map), list(host_map.items())[:3])

            summary_store: dict[int, RsDomainSummary] = {}
            lock = threading.Lock()
            pool = QThreadPool.globalInstance()
            pool.setMaxThreadCount(self.max_concurrency)

            # 1) 并发拉取桌面概要（IPv4 / OS / 状态 / hostId）
            #    单条概要失败仅影响单行（_SummaryTask 内部已吞异常）。
            for vm in vms:
                pool.start(_SummaryTask(self.client, vm.id, summary_store, lock))
            pool.waitForDone()

            # 1.5) 构建桌面 IPv4 映射（2.27.57 单台详情，networks[].ipAddr 为主来源；
            #      summary 已解析到 ip 的环境优先复用，缺失的并发补齐）。
            ip_by_uuid = self._build_ip_map(vms, summary_store)

            # 1.7) 构建桌面磁盘使用率映射（2.9.10 domainDetail，diskList[].detail.usage）。
            #      关机桌面也有值（0~9%），全部并发拉取。
            disk_by_id = self._build_disk_map(vms)

            # 2) 逐台桌面性能（桌面级口径，2.27.30 单台 UUID 查询，并发）。
            #    运行中的桌面返回 cpuRate/memRate；关机桌面返回 success=true 但
            #    cpuRate/memRate 为 null → 不加入 monitors（UI 显示 "-"）。
            #    仅当 2.27.30 全部调用报错时才防御性回退宿主级（2.4.12），避免空白。
            monitor_store: dict[str, dict] = {}
            mlock = threading.Lock()
            err_count = [0]
            for vm in vms:
                if vm.uuid:
                    pool.start(_MonitorTask(self.client, vm.uuid,
                                            monitor_store, mlock, err_count))
            pool.waitForDone()

            monitors: list[VmPerf] = []
            for uuid, data in monitor_store.items():
                cr = data.get("cpuRate")
                mr = data.get("memRate")
                if cr is None and mr is None:
                    # 关机 / 无数据 → 不加入，UI 显示 "-"；不回退宿主级
                    continue
                monitors.append(VmPerf(
                    uuid=str(uuid),
                    cpuRate=VmPerf._to_float(cr),
                    memRate=VmPerf._to_float(mr),
                    # 2.27.30 不提供磁盘使用率%；真实值由 _merge_rows 从
                    # disk_by_id（2.9.10 domainDetail）取
                ))
            host_perf_by_vm: dict[int, HostPerf] = {}
            data_source = "vm"
            if err_count[0] >= len([v for v in vms if v.uuid]):
                # 2.27.30 完全不可用（全部调用报错）→ 防御性回退宿主级
                logger.warning("[HealthWorker] 单台桌面监控(2.27.30)全部失败，回退宿主级")
                host_perf_by_vm = self._fallback_host_perf(vms)
                if not host_perf_by_vm:
                    # 桌面级与宿主级都拿不到性能数据 → 上报异常
                    logger.error(
                        "[HealthWorker] 桌面级与宿主级性能接口均不可用，无法采集性能数据")
                    self.bridge(WorkspaceAPIError(
                        -1, "桌面级(2.27.30)与宿主级(2.4.12)性能接口均不可用"))
                    return
                data_source = "host"

            # 3) 逐台合并为 HealthRow（关机 / 未命中 → vm_* = None → UI 显示 "-"）
            rows = self._merge_rows(vms, summary_store, monitors,
                                    host_perf_by_vm, data_source, host_map,
                                    ip_by_uuid, disk_by_id)
            self.health_ready.emit(rows)
            logger.info("[HealthWorker] 采集健康数据 %d 行（数据源=%s）",
                        len(rows), data_source)
        except Exception as exc:
            self.bridge(exc)

    # ------------------------------------------------------------------
    # 宿主级回退：逐 host 拉取 2.4.12，按 VM 的 hostId 回填
    # ------------------------------------------------------------------
    def _fallback_host_perf(self, vms: list) -> dict[int, HostPerf]:
        """桌面级接口失败时的宿主级（2.4.12）回退。

        hostId 来源 = ``query_vm_list``(2.27.33 /vms/page) 的 ``VmBrief.hostId``，**而非**
        2.9.11 概要：真实 H3C 平台上 2.9.11 概要接口同样可能返回 400，导致
        ``summary_store`` 为空；若依赖概要的 hostId，则回退彻底失效、表格仍空白。
        清单接口(2.27.33)在真实环境稳定可用，故回退统一以清单的 hostId 为准
        （旧版宿主级实现正是从清单取 hostId 成功采集桌面行）。

        仅当 VM 的 ``hostId`` 非 0 时才尝试回退；``hostId`` 为 0 的 VM 无法回退，
        ``vm_*`` 保持 None。单台宿主级请求失败仅影响该 host 上的 VM，不影响其它 host。

        Args:
            vms: ``list[VmBrief]`` 全量桌面清单（hostId 来源）。

        Returns:
            ``{vm_id: HostPerf}``：能拿到宿主级性能的 VM。若全部失败或没有任何
            VM 可用于回退，返回空 dict（由调用方决定是否上报异常）。
        """
        # 1) 收集去重后的合法 hostId（来自清单 2.27.40 的 VmBrief.hostId）
        host_ids = {vm.hostId for vm in vms if vm.hostId}

        # 2) 逐 host 拉取性能（单 host 失败不影响其它 host）
        host_perf: dict[int, HostPerf] = {}
        for host_id in host_ids:
            try:
                host_perf[host_id] = self.client.get_host_cpumemdisk(host_id)
            except WorkspaceAPIError as exc:
                logger.warning("[HealthWorker] 宿主 %s 性能接口失败: %s", host_id, exc)

        # 3) 按 VM 的 hostId 回填到对应 VM
        by_vm: dict[int, HostPerf] = {}
        for vm in vms:
            if vm.hostId and vm.hostId in host_perf:
                by_vm[vm.id] = host_perf[vm.hostId]
        return by_vm

    # ------------------------------------------------------------------
    # 主机名映射（2.4.7 一次请求）：hostId -> hostName，供「所在主机」列使用
    # ------------------------------------------------------------------
    def _build_host_map(self) -> dict[int, str]:
        """构建 ``{host_id: host_name}`` 映射（2.4.7 主机列表，一次请求）。

        用于健康表格「所在主机」列展示**主机名**（而非裸 ``hostId``）。
        列表类请求失败（含 400 / 任意异常）时降级为空 dict：对应主机列
        渲染为 ``"-"`` 或裸 ``hostId``，**不影响其它列**与整体采集流程。

        Returns:
            ``{host_id: host_name}``：能解析到主机名的主机；全部失败返回 ``{}``。
        """
        try:
            hosts = self.client.list_hosts()
        except Exception as exc:    # 单 host 列表失败不影响其它桌面行
            logger.warning("[HealthWorker] 主机列表(2.4.7)获取失败，主机列降级: %s", exc)
            return {}
        host_map: dict[int, str] = {}
        for h in (hosts or []):
            if h.id:
                host_map[h.id] = h.name or ""
        return host_map

    # ------------------------------------------------------------------
    # 桌面 IPv4 映射（2.27.57 单台详情，networks[].ipAddr 为主来源）
    # ------------------------------------------------------------------
    def _build_ip_map(self, vms: list, summary_store: dict) -> dict[str, str]:
        """构建 ``{vm_uuid: ipv4}`` 映射（桌面 IPv4 主来源）。

        优先级：2.9.11 概要已解析的 ip（其他环境 server.addresses 可用）→
        2.27.57 单台详情的 networks[].ipAddr（当前平台可靠来源）。对 summary
        未提供 ip 的桌面并发调用 2.27.57 补齐；单条失败仅该行 IP 为空（"-"）。

        Args:
            vms: 全量桌面清单（含 uuid）。
            summary_store: ``{vm_id: RsDomainSummary}`` 概要结果（可能含 ip）。

        Returns:
            ``{vm_uuid: ipv4}``：能解析到 IP 的桌面；全部失败返回 ``{}``。
        """
        ip_by_uuid: dict[str, str] = {}
        need_fill: list[str] = []
        for vm in vms:
            s = summary_store.get(vm.id) or RsDomainSummary()
            if s.ip:                       # 其他环境 2.9.11 server.addresses 可用
                ip_by_uuid[vm.uuid] = s.ip
            elif vm.uuid:
                need_fill.append(vm.uuid)
        if need_fill:
            store: dict[str, str] = {}
            lock = threading.Lock()
            pool = QThreadPool.globalInstance()
            pool.setMaxThreadCount(self.max_concurrency)
            for u in need_fill:
                pool.start(_IpFetchTask(self.client, u, store, lock))
            pool.waitForDone()
            for u, ip in store.items():
                if ip:
                    ip_by_uuid[u] = ip
        logger.info("[HealthWorker] IPv4 映射：summary 命中 %d，2.27.57 补齐 %d 台",
                    len([v for v in ip_by_uuid.values() if v]),
                    len([u for u in need_fill if store.get(u)]))
        return ip_by_uuid

    # ------------------------------------------------------------------
    # 桌面磁盘使用率映射（2.9.10 domainDetail，diskList[].detail.usage）
    # ------------------------------------------------------------------
    def _build_disk_map(self, vms: list) -> dict[int, float]:
        """构建 ``{vm_id: disk_usage%}`` 映射（桌面磁盘使用率主来源）。

        并发调用 ``get_domain_detail``（2.9.10）提取每台桌面的
        ``diskList[].detail.usage`` 取 max（多盘聚合）。关机桌面也有值。
        单条失败仅该行磁盘率为 None（UI 显示 "-"）。

        Args:
            vms: 全量桌面清单（含 id）。

        Returns:
            ``{vm_id: max_usage%}``：能解析到磁盘使用率的桌面；
            全部失败返回 ``{}``。
        """
        store: dict[int, float | None] = {}
        lock = threading.Lock()
        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(self.max_concurrency)
        for vm in vms:
            if vm.id:
                pool.start(_DiskTask(self.client, vm.id, store, lock))
        pool.waitForDone()

        disk_by_id: dict[int, float] = {}
        for vm_id, usage in store.items():
            if usage is not None:
                disk_by_id[vm_id] = usage
        logger.info("[HealthWorker] 磁盘映射：成功 %d / %d 台",
                    len(disk_by_id), len([v for v in vms if v.id]))
        return disk_by_id

    # ------------------------------------------------------------------
    # 纯函数合并（便于单测，不涉及线程/信号）
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_rows(vms, summary_store: dict, monitors,
                    host_perf_by_vm: dict[int, HostPerf] | None = None,
                    data_source: str = "vm",
                    host_map: dict[int, str] | None = None,
                    ip_by_uuid: dict[str, str] | None = None,
                    disk_by_id: dict[int, float] | None = None) -> list[HealthRow]:
        """把「清单 + 概要 + 批量性能（+ 宿主级回退 + 磁盘映射）」合并为 HealthRow 列表。

        Args:
            vms: ``list[VmBrief]`` 全量桌面清单（含关机）。
            summary_store: ``{vm_id: RsDomainSummary}`` 概要结果。
            monitors: ``list[VmPerf]`` 批量性能（仅开机桌面，桌面级口径）。
            host_perf_by_vm: ``{vm_id: HostPerf}`` 宿主级回退性能；默认 None。
            data_source: 性能数据来源口径 ``"vm"`` / ``"host"``，写入 HealthRow。
            host_map: ``{host_id: host_name}``（2.4.7 解析），填充每行 host_id/host_name；
                默认 None → 主机列留空（UI 回退 "-"）。
            ip_by_uuid: ``{vm_uuid: ipv4}``（2.27.57 解析）；默认 None → IP 列 "-"。
            disk_by_id: ``{vm_id: usage%}``（2.9.10 domainDetail 解析）；
                默认 None → 磁盘列 "-"。

        Returns:
            list[HealthRow]：行数 = 清单数；优先级为「桌面级命中 → 宿主级命中
            → 无数据(None)」。未命中性能的桌面 ``vm_*`` 为 None，UI 显示 "-"。
        """
        host_perf_by_vm = host_perf_by_vm or {}
        perf_by_uuid: dict[str, VmPerf] = {m.uuid: m for m in (monitors or []) if m.uuid}
        rows: list[HealthRow] = []
        host_map = host_map or {}
        ip_by_uuid = ip_by_uuid or {}
        disk_by_id = disk_by_id or {}
        for vm in vms:
            s = summary_store.get(vm.id) or RsDomainSummary()
            perf = perf_by_uuid.get(vm.uuid)
            if perf is not None:
                # 桌面级命中：使用单台虚拟机自身指标
                cpu, mem = perf.cpuRate, perf.memRate
                # 磁盘优先用 2.9.10 domainDetail（disk_by_id），其次 monitor 自带
                disk = disk_by_id.get(vm.id, perf.diskMaxUsage)
            elif vm.id in host_perf_by_vm:
                # 宿主级回退命中：使用所在物理主机的指标近似
                hp = host_perf_by_vm[vm.id]
                cpu, mem = hp.cpuRate, hp.memRate
                disk = disk_by_id.get(vm.id, hp.diskMaxUsage)
            else:
                cpu = mem = None
                disk = disk_by_id.get(vm.id)  # 即使无 CPU/内存，磁盘也可能有值
            # IPv4 优先级：2.27.57 networks[].ipAddr → 2.9.11 server.addresses
            #             → 清单 2.27.33 ipAddr → "-"
            vm_ip = ip_by_uuid.get(vm.uuid) or s.ip or vm.ipAddr or NO_DATA
            rows.append(HealthRow(
                vm_id=vm.id,
                title=s.title or vm.title,
                ip=vm_ip,
                os=s.osVersion,
                status=s.status or vm.status,
                host_id=vm.hostId,
                host_name=host_map.get(vm.hostId, ""),
                vm_cpu=cpu,
                vm_mem=mem,
                vm_disk=disk,
                data_source=data_source,
            ))
        return rows
