"""2.26 虚拟机使用率查询（/vdi/rest/center/vmusages）演示脚本。

用途：在真实 H3C 平台核对 2.26 接口的实际返回结构，验证参数与字段映射。

背景：2.26 接口有一个**必填** query 参数 ``queryType``（integer, int32）。
缺失该参数会导致平台对所有参数组合返回 HTTP 400（此前全 400 正是这个原因）。
文档未列出 ``queryType`` 的具体枚举值，本脚本遍历若干候选值（1~8），在真实
平台直接观察哪些可用、返回了什么指标（name/objectId/datapoints/plotDataDTOList）。

构建客户端方式照抄 src/main.py 的 AppConfig.load() + H3CWorkspaceClient.from_config(cfg)。
每次调用 get_vm_usages(query_type, ...) 均 try/except WorkspaceAPIError，失败仅打印
错误、脚本不崩溃，便于在真实环境逐步核对。

运行（项目根目录，Python 3.14）：
    cd /c/Users/liwanghao/Desktop/AI工具文件/v1.0.0
    py -3.14 demo_vm_usages.py

注意：本脚本会连接真实平台（需 src/config/app.yaml 中已配置正确的 base_url/user/password）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 将 src 加入模块搜索路径，保证无论从哪个工作目录运行都能 import core.*
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.config import AppConfig
from core.errors import WorkspaceAPIError
from core.workspace_client import H3CWorkspaceClient


# 候选 queryType 枚举值（文档未列出，需在真实平台遍历验证）。
# 覆盖 1~8：1=CPU 使用率 / 2=内存使用率 / 3=在线时长 等，具体语义由真实返回推断。
_QUERY_TYPES: list[int] = [1, 2, 3, 4, 5, 6, 7, 8]

# 常见 CPU / 内存使用率字段名（真实 schema 未知，防御式遍历）
_CPU_KEYS = ("cpuRate", "cpuUsage", "cpuUtilization", "cpu", "cpuUsageRate", "cpuUseRate")
_MEM_KEYS = ("memRate", "memUsage", "memoryUsage", "mem", "memory", "memoryRate", "memUseRate")
_RATE_KEYS = ("usage", "rate", "utilization")


def _scan_usage(obj: object, max_depth: int = 8) -> dict[str, list[tuple[str, object]]]:
    """递归扫描响应中的 CPU/内存/通用使用率字段，返回 {分类: [(key, value), ...]}。"""
    found: dict[str, list[tuple[str, object]]] = {"cpu": [], "mem": [], "other": []}

    def walk(node: object, level: int) -> None:
        if level > max_depth:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    k = str(key)
                    if any(k == name for name in _CPU_KEYS):
                        found["cpu"].append((k, value))
                    elif any(k == name for name in _MEM_KEYS):
                        found["mem"].append((k, value))
                    elif k in _RATE_KEYS:
                        found["other"].append((k, value))
                if isinstance(value, (dict, list)):
                    walk(value, level + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, level + 1)

    walk(obj, 0)
    return found


def _print_section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _print_request(args_repr: str) -> None:
    print(f"请求参数: {args_repr}")


def _print_response(raw: dict) -> None:
    print("原始响应 JSON:")
    print(json.dumps(raw, ensure_ascii=False, indent=2, default=str))


def _print_usage_extract(raw: dict) -> None:
    """尝试提取 CPU/内存使用率字段；找不到则打印可用字段列表。"""
    found = _scan_usage(raw)
    if any(found.values()):
        print("尝试提取的 CPU/内存使用率字段:")
        for category, items in found.items():
            label = {"cpu": "CPU", "mem": "内存", "other": "其它使用率"}[category]
            if items:
                for key, value in items:
                    print(f"  [{label}] {key} = {value}")
    else:
        print("未命中常见 CPU/内存使用率字段，打印可用字段供核对:")
        data = raw.get("data")
        top_keys = list(raw.keys())
        data_keys = list(data.keys()) if isinstance(data, dict) else None
        print(f"  顶层字段: {top_keys}")
        if data_keys is not None:
            print(f"  data 字段: {data_keys}")
        elif isinstance(data, list):
            print(f"  data 为列表，长度={len(data)}")


def _print_data_summary(raw: dict) -> None:
    """打印 data 数组的摘要：每条的 name/objectId/datapoints 长度/plotDataDTOList 长度。

    帮助主理人在真实平台直接看出某 queryType 返回的是哪些指标、数据点多不多。
    """
    data = raw.get("data")
    if not isinstance(data, list):
        print(f"[摘要] data 非数组（类型={type(data).__name__}），跳过逐条摘要。")
        return
    print(f"[摘要] data 数组长度 = {len(data)}")
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            print(f"  [{idx}] 非 dict 元素: {type(item).__name__}")
            continue
        name = item.get("name", "")
        object_id = item.get("objectId", "")
        datapoints = item.get("datapoints") or []
        plot = item.get("plotDataDTOList") or []
        dp_len = len(datapoints) if isinstance(datapoints, list) else "N/A"
        plot_len = len(plot) if isinstance(plot, list) else "N/A"
        print(f"  [{idx}] name={name!r} objectId={object_id!r} "
              f"datapoints={dp_len} plotDataDTOList={plot_len}")


def _safe_call(label: str, args_repr: str, fn) -> dict | None:
    """统一封装一次调用：打印请求参数与响应，异常时打印错误且不崩溃。"""
    _print_section(label)
    _print_request(args_repr)
    try:
        raw = fn()
    except WorkspaceAPIError as exc:
        print(f"[ERROR] 调用失败（WorkspaceAPIError）: code={exc.code} message={exc.message}")
        return None
    except Exception as exc:  # noqa: BLE001 - 演示脚本需对任意异常容错
        print(f"[ERROR] 调用异常: {type(exc).__name__}: {exc}")
        return None
    _print_response(raw)
    _print_usage_extract(raw)
    _print_data_summary(raw)
    return raw


def _resolve_host_and_vm(client: H3CWorkspaceClient) -> tuple[int | None, int | None]:
    """从 query_vm_list 取一个真实 host_id / vm_id 用于带过滤的探测调用。"""
    host_id: int | None = None
    vm_id: int | None = None
    try:
        vms = client.query_vm_list()
    except WorkspaceAPIError as exc:
        print(f"[WARN] query_vm_list 失败: code={exc.code} {exc.message}")
        return None, None
    for vm in vms:
        if getattr(vm, "hostId", 0):
            host_id = vm.hostId
            vm_id = vm.id
            break
    return host_id, vm_id


def main() -> None:
    print("=" * 72)
    print("H3C 2.26 虚拟机使用率查询演示 (GET /vdi/rest/center/vmusages)")
    print("=" * 72)

    # 照抄 src/main.py：AppConfig.load + H3CWorkspaceClient.from_config
    try:
        cfg = AppConfig.load()
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] 配置加载失败: {exc}")
        return
    client = H3CWorkspaceClient.from_config(cfg)

    now = int(time.time())
    one_hour_ago = now - 3600

    # 先取真实 host_id / vm_id 用于带过滤的探测
    host_id, vm_id = _resolve_host_and_vm(client)
    if host_id is None or vm_id is None:
        print("\n[INFO] 未取到可用 host_id/vm_id，将只执行「无过滤 + queryType + 时间窗」探测。")
    else:
        print(f"\n[INFO] 取得探测样本 host_id={host_id} vm_id={vm_id}")

    # ---- 阶段 1：遍历所有候选 queryType（带 host/vm 过滤 + 时间窗） ----
    for qt in _QUERY_TYPES:
        label = f"阶段1-queryType={qt}（带 host/vm 过滤）"
        args_repr = (f"get_vm_usages(query_type={qt}, host_id={host_id}, "
                     f"vm_id={vm_id}, start_time={one_hour_ago}, end_time={now})")
        if host_id is None or vm_id is None:
            # 没有真实 host/vm 时退化为仅带 queryType + 时间窗
            args_repr = (f"get_vm_usages(query_type={qt}, "
                         f"start_time={one_hour_ago}, end_time={now})")
            _safe_call(label, args_repr, lambda q=qt: client.get_vm_usages(
                q, start_time=one_hour_ago, end_time=now))
        else:
            _safe_call(label, args_repr, lambda q=qt: client.get_vm_usages(
                q, host_id=host_id, vm_id=vm_id,
                start_time=one_hour_ago, end_time=now))

    # ---- 阶段 2：无 host/vm 过滤，仅 queryType + 时间窗（看是否支持全量） ----
    print("\n" + "=" * 72)
    print("阶段 2：无 host/vm 过滤，仅 queryType + 时间窗（验证是否支持全量查询）")
    print("=" * 72)
    for qt in _QUERY_TYPES:
        label = f"阶段2-queryType={qt}（无过滤）"
        args_repr = (f"get_vm_usages(query_type={qt}, "
                     f"start_time={one_hour_ago}, end_time={now})")
        _safe_call(label, args_repr, lambda q=qt: client.get_vm_usages(
            q, start_time=one_hour_ago, end_time=now))

    print("\n" + "=" * 72)
    print("演示结束。请核对上方各 queryType 的返回：成功的即可用枚举，")
    print("其 name/objectId/datapoints/plotDataDTOList 即对应指标语义。")
    print("=" * 72)


if __name__ == "__main__":
    main()
