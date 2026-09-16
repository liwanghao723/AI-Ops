"""2.27.30 单台虚拟机监控 + 2.4.5 主机监控（按主机 top5 桌面率）演示脚本。

用途：在真实 H3C 平台核对这两个新挖掘接口的可用性与真实返回结构：
  - 2.27.30  GET /vdi/rest/workspace/vms/monitor/{domainUuid}
             按单台虚拟机 UUID 查询 CPU/内存性能（与 2.27.31 批量接口相互独立）。
  - 2.4.5    GET /vdi/rest/workspace/hosts/hostMonitor
             返回每台主机的 cpuRate/memRate/diskRate/occRate，以及
             vmTopCpuRate / vmTopMemRate（每台主机 top5 虚拟机的 cpu/内存利用率）。

构建客户端方式照抄 src/main.py 的 AppConfig.load() + H3CWorkspaceClient.from_config(cfg)，
并内置把 src/ 加入 sys.path 的引导，使脚本可从任意目录运行：
    cd /c/Users/liwanghao/Desktop/AI工具文件/v1.0.0
    py -3.14 demo_vm_monitor.py

每次调用均 try/except WorkspaceAPIError，失败仅打印 code/message、脚本不崩溃，
便于在真实环境逐步核对真实 schema（重点看 2.27.30 是否也像 2.27.31 那样 400）。

注意：本脚本会连接真实平台（需 src/config/app.yaml 中已正确配置 base_url/user/password）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 将 src 加入模块搜索路径，保证无论从哪个工作目录运行都能 import core.*
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.config import AppConfig
from core.errors import WorkspaceAPIError
from core.models import VmBrief
from core.workspace_client import H3CWorkspaceClient


# 常见 CPU / 内存使用率字段名（真实 schema 未知，防御式遍历）
_CPU_KEYS = ("cpuRate", "cpuUsage", "cpuUsageRate", "cpu", "cpuUtilization", "cpuUseRate")
_MEM_KEYS = ("memRate", "memUsage", "mem", "memory", "memoryUsage", "memUtilization", "memUseRate")

# 单台 VM 监控最多探测几台（建议覆盖不同主机）
_MAX_VM_SAMPLES = 3


def _print_section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _print_response(raw: dict) -> None:
    print("原始响应 JSON:")
    print(json.dumps(raw, ensure_ascii=False, indent=2, default=str))


def _pick_representative_vms(vms: list[VmBrief], max_count: int = _MAX_VM_SAMPLES
                             ) -> list[VmBrief]:
    """从 VmBrief 列表中挑出 uuid 非空的代表性桌面（优先覆盖不同主机）。

    Args:
        vms: ``query_vm_list`` 返回的桌面列表。
        max_count: 最多挑选数量，默认 3。

    Returns:
        list[VmBrief]：uuid 非空、且尽量覆盖不同主机的代表性桌面。
    """
    picked: list[VmBrief] = []
    seen_hosts: set[int] = set()
    for vm in vms:
        uuid = getattr(vm, "uuid", "") or ""
        if not uuid:
            continue
        host_id = getattr(vm, "hostId", 0) or 0
        if host_id not in seen_hosts:
            picked.append(vm)
            seen_hosts.add(host_id)
            if len(picked) >= max_count:
                break

    # 若代表性不足 max_count（主机种类少），放宽到任意 uuid 非空的桌面补足
    if len(picked) < max_count:
        for vm in vms:
            uuid = getattr(vm, "uuid", "") or ""
            if not uuid:
                continue
            if vm not in picked:
                picked.append(vm)
                if len(picked) >= max_count:
                    break
    return picked[:max_count]


def _collect_usage(data: object) -> tuple[list[tuple[str, object]], list[tuple[str, object]]]:
    """递归扫描 data 中的 CPU/内存使用率字段（key 命中即收集路径+值）。

    Args:
        data: H3C 统一外层响应中的 ``data`` 节点（结构未知）。

    Returns:
        (cpu_hits, mem_hits)：各自为 (路径, 值) 列表；未命中为空列表。
    """
    found_cpu: list[tuple[str, object]] = []
    found_mem: list[tuple[str, object]] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    full = f"{path}{key}"
                    if key in _CPU_KEYS:
                        found_cpu.append((full, value))
                    elif key in _MEM_KEYS:
                        found_mem.append((full, value))
                if isinstance(value, (dict, list)):
                    walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}]")

    walk(data, "")
    return found_cpu, found_mem


def _extract_vm_monitor_usage(raw: dict) -> None:
    """尝试从 2.27.30 响应中提取 CPU/内存使用率；找不到则打印 data 顶层字段。"""
    data = raw.get("data")
    cpu_hits, mem_hits = _collect_usage(data)
    if cpu_hits or mem_hits:
        print("尝试提取的 CPU/内存使用率字段:")
        for path, value in cpu_hits:
            print(f"  [CPU] {path} = {value}")
        for path, value in mem_hits:
            print(f"  [MEM] {path} = {value}")
    else:
        print("未命中常见 CPU/内存使用率字段，打印 data 顶层字段供核对:")
        if isinstance(data, dict):
            print(f"  data 顶层字段: {list(data.keys())}")
        elif isinstance(data, list):
            print(f"  data 为列表，长度={len(data)}")
            if data and isinstance(data[0], dict):
                print(f"  data[0] 字段: {list(data[0].keys())}")
        else:
            print(f"  data 类型: {type(data).__name__}")


def _sample(items: object, n: int = 2) -> object:
    """取列表前 n 个作为样例（非列表原样返回）。"""
    if not isinstance(items, list):
        return items
    return items[:n]


def _print_host_monitor_summary(raw: dict) -> None:
    """打印 2.4.5 主机监控响应结构摘要：主机数、每台主机 cpu/mem 率、top5 样例。

    这是「按主机看 top5 桌面率」的来源（vmTopCpuRate / vmTopMemRate）。
    """
    data = raw.get("data")
    hosts = H3CWorkspaceClient._extract_list(data)
    print(f"[摘要] 主机数量 = {len(hosts)}")
    for idx, host in enumerate(hosts):
        if not isinstance(host, dict):
            print(f"  [{idx}] 非 dict 元素: {type(host).__name__}")
            continue
        host_id = host.get("id", "?")
        host_name = host.get("name") or host.get("hostName") or "?"
        cpu = host.get("cpuRate")
        mem = host.get("memRate")
        disk = host.get("diskRate")
        occ = host.get("occRate")
        print(f"  [{idx}] id={host_id} name={host_name!r} "
              f"cpuRate={cpu} memRate={mem} diskRate={disk} occRate={occ}")

        top_cpu = host.get("vmTopCpuRate") or []
        top_mem = host.get("vmTopMemRate") or []
        cpu_len = len(top_cpu) if isinstance(top_cpu, list) else "N/A"
        mem_len = len(top_mem) if isinstance(top_mem, list) else "N/A"
        print(f"        vmTopCpuRate 条数={cpu_len} 样例={_sample(top_cpu)}")
        print(f"        vmTopMemRate 条数={mem_len} 样例={_sample(top_mem)}")


def _step_single_vm_monitor(client: H3CWorkspaceClient) -> None:
    """步骤一：2.27.30 单台 VM 监控（按 UUID，独立于 2.27.31 批量）。"""
    _print_section("步骤一：2.27.30 单台虚拟机性能监控（按 UUID）")

    # 先拿桌面清单，挑 uuid 非空的代表性桌面
    try:
        vms = client.query_vm_list()
    except WorkspaceAPIError as exc:
        print(f"[ERROR] query_vm_list 失败（WorkspaceAPIError）: code={exc.code} message={exc.message}")
        return
    except Exception as exc:  # noqa: BLE001 - 演示脚本需对任意异常容错
        print(f"[ERROR] query_vm_list 异常: {type(exc).__name__}: {exc}")
        return

    print(f"query_vm_list 返回 {len(vms)} 台桌面，挑选 UUID 非空的代表性桌面探测。")
    samples = _pick_representative_vms(vms, max_count=_MAX_VM_SAMPLES)
    print(f"本次探测 {len(samples)} 台："
          + ", ".join(f"#{v.id}({v.name}, uuid={v.uuid}, host={v.hostId})" for v in samples)
          + "\n")

    for vm in samples:
        _print_section(f"2.27.30 单台监控 -> 桌面 #{vm.id} {vm.name}")
        print(f"请求参数: get_vm_monitor(domain_uuid={vm.uuid!r})")
        print(f"桌面: id={vm.id} name={vm.name!r} uuid={vm.uuid!r} hostId={vm.hostId}")
        try:
            raw = client.get_vm_monitor(vm.uuid)
        except WorkspaceAPIError as exc:
            print(f"[ERROR] 调用失败（WorkspaceAPIError）: code={exc.code} message={exc.message}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] 调用异常: {type(exc).__name__}: {exc}")
            continue
        _print_response(raw)
        _extract_vm_monitor_usage(raw)


def _step_host_monitor(client: H3CWorkspaceClient) -> None:
    """步骤二：2.4.5 主机性能监控（含每台主机 top5 桌面率）。"""
    _print_section("步骤二：2.4.5 主机性能监控（含每台主机 top5 桌面率）")
    print("请求参数: get_host_monitor()")
    try:
        raw = client.get_host_monitor()
    except WorkspaceAPIError as exc:
        print(f"[ERROR] 调用失败（WorkspaceAPIError）: code={exc.code} message={exc.message}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 调用异常: {type(exc).__name__}: {exc}")
        return
    _print_response(raw)
    _print_host_monitor_summary(raw)


def main() -> None:
    print("=" * 72)
    print("H3C 2.27.30 单台VM监控 + 2.4.5 主机监控（top5 桌面率）演示")
    print("=" * 72)

    # 照抄 src/main.py：AppConfig.load + H3CWorkspaceClient.from_config
    try:
        cfg = AppConfig.load()
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] 配置加载失败: {exc}")
        return
    client = H3CWorkspaceClient.from_config(cfg)

    _step_single_vm_monitor(client)
    _step_host_monitor(client)

    print("\n" + "=" * 72)
    print("演示结束。请核对：")
    print("  - 步骤一：2.27.30 是否可用（对比 2.27.31 的 400）；成功则可取单台 CPU/内存率")
    print("  - 步骤二：2.4.5 的 vmTopCpuRate/vmTopMemRate 即为「按主机看 top5 桌面率」来源")
    print("=" * 72)


if __name__ == "__main__":
    main()
