"""H3C 云桌面智能运维助手 —— 主机/桌面定位演示脚本。

演示「定位桌面所在主机 + 列出该主机上所有虚拟机 + 获取桌面详情」的两步走流程：

  步骤一：GET /hosts/getHostList 获取所有主机列表，
          -> 对每个主机打印其原始字段 keys + 防御式取 id/name；
          -> 构建 host_map: {host_id: name}（供步骤二解析“所在主机名称”）；
          -> 交叉引用 GET /vms/queryVmList（VmBrief 含 hostId），
             对每个主机列出该主机上所有虚拟机的 id 与 name。

  步骤二：用步骤一得到的桌面 id，GET /virtual/domain/{id}/domainDetail 获取详情：
          -> 先打印原始响应全文（从真实返回中学习字段结构）；
          -> 再按真实 schema 防御式解析：
                · 所在主机名称：data.hostId -> host_map 解析；
                · IPv4：遍历 data.networkList 取首个非 null 的 ip/ipAddr/address，
                        回退 data.server.addresses；
                · 配置（分配值，非使用率，真实 schema 在 *顶层*，非 summary 标签）：
                    CPU  -> data.cpu.cpudetail.curValue（vCPU），附 socket/core；
                    内存 -> data.mem.detail.curValue + curMemoryUnit；
                    （保留对 data.summary 标签的兼容扫描作为兜底）；
                · 实时使用率：若 data.server 非 null，尝试从中取
                    cpuRate/memRate/usage 等并标注“实时使用率”；
                    否则说明“该桌面未运行/无实时使用率”。
          -> 除首台桌面外，额外挑一台运行中的桌面再查一次，
             观察运行中桌面的 data.server 是否带实时 CPU/内存使用率。

客户端构建方式严格照抄 src/main.py：
    from core.config import AppConfig
    cfg = AppConfig.load()
    client = H3CWorkspaceClient.from_config(cfg)

运行（项目根目录，src 同级）：
    cd <项目根目录>
    py -3.14 -m demo_host_detail
    # 或： py -3.14 demo_host_detail.py

真实环境说明（主理人已实跑确认两接口均可用）：
  getHostList ✅、domainDetail ✅（本次未出现 400）。本脚本两步仍做 try/except
  优雅处理，遇错只打印错误信息而不崩溃。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# ---- 路径引导：脚本位于项目根目录，而 core/ 在 src/ 下，需将 src 加入 sys.path，
#      使 `from core...` 可用（与 tests/conftest.py 行为一致，便于主理人直接运行）。
#      若已在 src 内运行或 PYTHONPATH 已含 src，则跳过。
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path and not os.environ.get("H3C_OPS_RUNNING_IN_SRC"):
    # 仅当 `core` 尚不可导入时才注入，避免重复污染
    try:
        import core  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(_SRC))


def _build_client():
    """照抄 src/main.py 的客户端构建方式。"""
    from core.config import AppConfig
    from core.workspace_client import H3CWorkspaceClient

    cfg = AppConfig.load()
    return H3CWorkspaceClient.from_config(cfg)


def _first_present(d: dict, *keys: str, default: Any = None) -> Any:
    """从 dict 中按优先级返回第一个存在的非 None 键的值。"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _fmt(value: Any) -> str:
    """把任意值格式化为可读字符串（None 显示为 '-'）。"""
    if value is None or value == "":
        return "-"
    return str(value)


# H3C 虚拟机状态码（以平台为准；query_vm_list 的 status 为数字字符串）。
#   1=运行(running) / 2=关机(shutOff) / 3=挂起或暂停(suspended) ...
# demo 脚本只关心“是否运行中”，其余状态仅用于日志说明。
_VM_STATUS_MAP: dict[str, str] = {
    "1": "运行(running)",
    "2": "关机(shutOff)",
    "3": "挂起/暂停(suspended)",
}


def _vm_status_is_running(status: Any) -> bool:
    """判定虚拟机是否运行中：兼容数字状态码与英文串。"""
    s = str(status).strip().lower()
    return s in ("1", "running", "up", "active")


def _vm_status_label(status: Any) -> str:
    """把状态（数字串/英文）映射为中文可读标签，未知则原样返回。"""
    s = str(status).strip()
    return _VM_STATUS_MAP.get(s, s)


# ---------------------------------------------------------------------------
# 步骤一辅助
# ---------------------------------------------------------------------------
def _print_hosts(hosts: list[dict]) -> None:
    """步骤一（上半）：打印主机数量、每主机的字段 keys 与防御式 id/name。"""
    print("=" * 70)
    print(f"[步骤一] 主机列表：共 {len(hosts)} 台主机")
    print("=" * 70)
    for idx, host in enumerate(hosts, start=1):
        hid = _first_present(host, "id", "hostId", "hostID", default="-")
        name = _first_present(host, "name", "hostName", "host_name",
                              "hostname", "displayName", default="-")
        keys = ", ".join(sorted(host.keys()))
        print(f"  主机#{idx}: id={_fmt(hid)}  name={_fmt(name)}")
        print(f"          原始字段 keys: [{keys}]")


def _build_host_map(hosts: list[dict]) -> dict[int, str]:
    """由主机列表构建 {host_id: name}，供步骤二把 data.hostId 解析成主机名。"""
    mapping: dict[int, str] = {}
    for host in hosts:
        hid = _first_present(host, "id", "hostId", "hostID", default=None)
        if hid is None:
            continue
        name = _first_present(host, "name", "hostName", "host_name",
                              "hostname", "displayName", default="") or ""
        mapping[int(hid)] = name
    return mapping


def _build_host_vm_map(vms: list) -> dict[int, list]:
    """交叉引用：构建 {host_id: [VmBrief, ...]}。"""
    mapping: dict[int, list] = {}
    for vm in vms:
        hid = getattr(vm, "hostId", 0) or 0
        mapping.setdefault(int(hid), []).append(vm)
    return mapping


def _print_vms_per_host(hosts: list[dict], host_vm_map: dict[int, list]) -> None:
    """步骤一（下半）：对每个主机，列出该主机上所有虚拟机的 id 与 name。

    主机 id 取防御式（兼容 name/hostName 等），并与 query_vm_list 的 hostId
    做弱匹配：若精确 hostId 无命中，退而用「所有主机 + 全部 VM」兜底展示，
    确保主理人至少能看到全量 VM 清单。
    """
    print()
    print("=" * 70)
    print("[步骤一] 各主机上的虚拟机清单（id / name）")
    print("=" * 70)

    all_vms = [vm for vms in host_vm_map.values() for vm in vms]

    for idx, host in enumerate(hosts, start=1):
        hid = _first_present(host, "id", "hostId", "hostID", default=None)
        name = _first_present(host, "name", "hostName", "host_name",
                              "hostname", "displayName", default="-")
        vms_here = host_vm_map.get(int(hid), []) if hid is not None else []
        print(f"  主机#{idx}: id={_fmt(hid)}  name={_fmt(name)}  ->  "
              f"{len(vms_here)} 台虚拟机")
        if vms_here:
            for vm in vms_here:
                print(f"      - vm_id={vm.id}  name={_fmt(vm.name or vm.title)}")

    # 兜底：若干主机无 hostId 匹配（schema 不一致），单独列出未归属的 VM
    accounted = set()
    for vms in host_vm_map.values():
        accounted.update(vm.id for vm in vms)
    orphan = [vm for vm in all_vms if vm.id not in accounted]
    if orphan:
        print()
        print("  注意：以下虚拟机未在任一主机 hostId 下匹配到（可检查主机/VM "
              "schema 是否一致）：")
        for vm in orphan:
            print(f"      - vm_id={vm.id}  hostId={vm.hostId}  "
                  f"name={_fmt(vm.name or vm.title)}")


# ---------------------------------------------------------------------------
# 步骤二辅助：按真实 schema 解析
# ---------------------------------------------------------------------------
def _extract_ipv4(data: dict) -> Any:
    """从 data.networkList 取首个非 null 的 ip/ipAddr/address；
    回退到 data.server.addresses；都取不到返回 None。"""
    network_list = data.get("networkList") or []
    if isinstance(network_list, list):
        for item in network_list:
            if not isinstance(item, dict):
                continue
            ip = _first_present(item, "ip", "ipAddr", "address", default=None)
            if ip not in (None, ""):
                return ip

    # 回退：data.server.addresses（兼容 2.9.11 风格）
    server = data.get("server")
    if isinstance(server, dict):
        addresses = server.get("addresses") or {}
        candidates = addresses.values() if isinstance(addresses, dict) else (
            addresses if isinstance(addresses, list) else [])
        for val in candidates:
            if isinstance(val, dict) and val.get("addr"):
                return val.get("addr")
            if isinstance(val, list):
                for it in val:
                    if isinstance(it, dict) and it.get("addr"):
                        return it.get("addr")
    return None


def _iter_summary_tabs(data: dict) -> list[dict]:
    """把 data.summary 规整为 tab 列表（兼容 list 或 dict 两种结构）。"""
    summary = data.get("summary")
    if isinstance(summary, list):
        return [t for t in summary if isinstance(t, dict)]
    if isinstance(summary, dict):
        return [v for v in summary.values() if isinstance(v, dict)]
    return []


def _extract_alloc(data: dict) -> dict:
    """解析“配置（分配值，非使用率）”：CPU vCPU 与 内存大小。

    真实 schema 为 **顶层** 字段（优先）：
      - CPU  -> data.cpu.cpudetail.curValue（vCPU），附 cpuSocket / cpuCore；
      - 内存 -> data.mem.detail.curValue + curMemoryUnit。
    兜底：若顶层未取到，再扫描 data.summary 各 tab（部分版本结构不同）。
    """
    alloc = {"cpu_vcpu": None, "cpu_socket": None, "cpu_core": None,
             "mem_value": None, "mem_unit": None}

    # 1) 顶层优先（真实 H3C schema）
    cpu = data.get("cpu") or {}
    if isinstance(cpu, dict):
        cpudetail = cpu.get("cpudetail") or {}
        if isinstance(cpudetail, dict):
            alloc["cpu_vcpu"] = cpudetail.get("curValue")
            alloc["cpu_socket"] = cpudetail.get("cpuSocket")
            alloc["cpu_core"] = cpudetail.get("cpuCore")
    mem = data.get("mem") or {}
    if isinstance(mem, dict):
        detail = mem.get("detail") or {}
        if isinstance(detail, dict):
            alloc["mem_value"] = detail.get("curValue")
            alloc["mem_unit"] = detail.get("curMemoryUnit") or detail.get("memoryUnit")

    # 2) 兜底：扫描 data.summary 标签（兼容其它版本结构）
    if alloc["cpu_vcpu"] is None or alloc["mem_value"] is None:
        for tab in _iter_summary_tabs(data):
            disp = str(tab.get("dispName") or tab.get("name") or "")
            detail = tab.get("detail") or {}
            if alloc["cpu_vcpu"] is None and "CPU" in disp.upper():
                cpudetail = detail.get("cpudetail") or {}
                if isinstance(cpudetail, dict):
                    alloc["cpu_vcpu"] = cpudetail.get("curValue")
                    alloc["cpu_socket"] = cpudetail.get("cpuSocket")
                    alloc["cpu_core"] = cpudetail.get("cpuCore")
                elif "curValue" in detail:
                    alloc["cpu_vcpu"] = detail.get("curValue")
            if alloc["mem_value"] is None and ("内存" in disp or "MEM" in disp.upper()):
                alloc["mem_value"] = detail.get("curValue")
                alloc["mem_unit"] = detail.get("curMemoryUnit") or detail.get("memoryUnit")
    return alloc


def _extract_realtime(server: Any) -> tuple[dict, bool]:
    """从 data.server 解析实时使用率（CPU/内存）。

    返回 (rt_dict, has_server)：has_server=False 表示 server 为 null
    （桌面未运行，无实时使用率）。
    """
    rt: dict[str, Any] = {"cpu": None, "mem": None}
    if not isinstance(server, dict):
        return rt, False
    for k in ("cpuRate", "cpuUsage", "cpuUsed", "cpuUtilization", "cpu"):
        if k in server and server[k] is not None:
            rt["cpu"] = server[k]
            break
    for k in ("memRate", "memUsage", "memUsed", "memUtilization", "mem"):
        if k in server and server[k] is not None:
            rt["mem"] = server[k]
            break
    return rt, True


def _try_extract_detail(detail: dict, host_map: dict[int, str]) -> None:
    """按真实 schema 防御式解析 domainDetail 的关键字段，并明确区分
    「配置（分配值）」与「实时使用率」。"""
    data = detail.get("data") or {}

    # 所在主机名称：data.hostId -> host_map 解析
    host_id = data.get("hostId")
    host_name = "-"
    if host_id is not None:
        host_name = host_map.get(int(host_id), "-") or "-"

    ip = _extract_ipv4(data)
    alloc = _extract_alloc(data)
    server = data.get("server")
    rt, has_server = _extract_realtime(server)

    print()
    print("  --- 所在主机 ---")
    print(f"  hostId        : {_fmt(host_id)}")
    print(f"  所在主机名称  : {host_name}")

    print("  --- IPv4 地址 ---")
    print(f"  IPv4          : {_fmt(ip)}")

    print("  --- 配置（分配值，非使用率）---")
    cpu_str = _fmt(alloc["cpu_vcpu"])
    if alloc["cpu_socket"] is not None or alloc["cpu_core"] is not None:
        cpu_str += (f"  (socket={_fmt(alloc['cpu_socket'])}, "
                    f"core={_fmt(alloc['cpu_core'])})")
    print(f"  分配 vCPU     : {cpu_str}")
    if alloc["mem_value"] is not None:
        mem_str = f"{_fmt(alloc['mem_value'])} {_fmt(alloc['mem_unit'])}".strip()
    else:
        mem_str = "-"
    print(f"  分配内存      : {mem_str}")

    print("  --- 实时使用率 ---")
    if not has_server:
        print("  该桌面未运行 / data.server 为 null，无实时使用率")
    else:
        print(f"  CPU 实时使用率  : {_fmt(rt['cpu'])}")
        print(f"  内存实时使用率  : {_fmt(rt['mem'])}")
        if rt["cpu"] is None and rt["mem"] is None:
            print("  （data.server 存在但未携带 cpuRate/memRate 等实时字段）")


def _probe_domain_detail(client, vm_id: int, host_map: dict[int, str],
                         label: str) -> None:
    """对单个桌面执行 domainDetail 调用：try/except 包裹 + 原始全文 dump + 解析。"""
    print()
    print("=" * 70)
    print(f"  >>> {label}：vm_id={vm_id}")
    print("=" * 70)
    try:
        detail = client.get_domain_detail(vm_id)
    except Exception as exc:
        # 真实平台个别桌面级接口可能返回 400（summary/monitors/all 同类），
        # 优雅捕获并提示，不崩溃。
        print(f"  [步骤二] 调用 /virtual/domain/{vm_id}/domainDetail 失败：")
        print(f"    {type(exc).__name__}: {exc}")
        return

    # 先打印原始响应全文（从真实返回中学习字段结构）
    print("  --- 原始响应全文 (raw) ---")
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))

    # 再按真实 schema 解析关键指标
    _try_extract_detail(detail, host_map)


def _pick_targets(vms: list, hosts: list[dict],
                  host_vm_map: dict[int, list]) -> tuple[int | None, int | None]:
    """选取步骤二要查询的桌面 id。

    返回 (首台桌面 id, 运行中桌面 id)。运行中桌面优先不同于首台；若无运行中
    桌面，则第二个返回 None（维持原逻辑，不再额外查询）。
    """
    first_vm_id: int | None = None
    if vms:
        first_vm_id = vms[0].id
        first_host = hosts[0] if hosts else None
        if first_host is not None:
            hid = _first_present(first_host, "id", "hostId", "hostID", default=None)
            if hid is not None:
                vms_on_first = host_vm_map.get(int(hid), [])
                if vms_on_first:
                    first_vm_id = vms_on_first[0].id

    # 运行中桌面：query_vm_list 的 status 为数字状态码（1=运行），
    # 此处兼容数字与英文（running/up/active）。
    running_vm_id: int | None = None
    for vm in vms:
        if _vm_status_is_running(getattr(vm, "status", "")) and vm.id != first_vm_id:
            running_vm_id = vm.id
            break

    return first_vm_id, running_vm_id


def main() -> int:
    print("#" * 70)
    print("# H3C 云桌面智能运维助手 —— 主机/桌面定位演示")
    print("#" * 70)

    # ---- 客户端构建（照抄 main.py）----
    try:
        client = _build_client()
    except Exception as exc:  # 配置缺失 / 校验失败等
        print(f"[致命] 客户端构建失败：{exc}")
        return 1

    # ===================== 步骤一 =====================
    # 1a. 获取主机列表（getHostList）
    try:
        hosts = client.get_host_list()
    except Exception as exc:
        print()
        print("[步骤一-主机列表] 调用 /hosts/getHostList 失败：")
        print(f"    {type(exc).__name__}: {exc}")
        print("    （真实平台该接口通常可用，失败请检查网络/认证）")
        hosts = []

    _print_hosts(hosts)
    host_map = _build_host_map(hosts)

    # 1b. 获取虚拟机列表（已有 query_vm_list）做交叉引用
    try:
        vms = client.query_vm_list()
    except Exception as exc:
        print()
        print("[步骤一-虚拟机列表] 调用 /vms/queryVmList 失败：")
        print(f"    {type(exc).__name__}: {exc}")
        vms = []

    host_vm_map = _build_host_vm_map(vms)
    _print_vms_per_host(hosts, host_vm_map)

    # ===================== 步骤二 =====================
    print()
    print("=" * 70)
    print("[步骤二] 虚拟机（桌面）详情：GET /virtual/domain/{id}/domainDetail")
    print("=" * 70)

    first_vm_id, running_vm_id = _pick_targets(vms, hosts, host_vm_map)
    if first_vm_id is None:
        print("  [跳过] 未从步骤一获得任何桌面 id，无法进入步骤二。")
        return 0

    # 首台桌面
    _probe_domain_detail(client, first_vm_id, host_map,
                         "首台桌面（步骤一代表性桌面）")

    # 额外：运行中的桌面（观察 data.server 是否带实时 CPU/内存使用率）
    if running_vm_id is not None:
        _probe_domain_detail(client, running_vm_id, host_map,
                             "运行中的桌面（观察 server 实时使用率）")
    else:
        # 统计各状态分布，便于说明“为何无运行中桌面”（关机/挂起等识别出来）
        dist: dict[str, int] = {}
        for vm in vms:
            label = _vm_status_label(getattr(vm, "status", ""))
            dist[label] = dist.get(label, 0) + 1
        dist_str = ", ".join(f"{k}:{v}" for k, v in dist.items()) or "无"
        print()
        print(f"  [提示] query_vm_list 中未发现运行中的桌面"
              f"（status 分布：{dist_str}），未额外查询第二个 domainDetail。")

    print()
    print("#" * 70)
    print("# 演示结束。请主理人根据上述真实返回核对字段 schema。")
    print("#" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
