"""告警过滤（纯函数，不依赖 Qt 控件，便于单测）。

字段来源说明（与实测 API 2.12.1 一致）：
- ``WarnInfoDTO.eventLevel``：四级独立映射
  （1 紧急 / 2 重要 / 3 次要 / 4 提示；API 文档 eventLevel=4 原编码为「警告」，此处改名为「提示」）
  （文案由 ``ui.styles.LEVEL_COLORS`` 提供，此处直接复用以保证下拉文案与列表显示一致）
- ``WarnInfoDTO.state``：1 = 已确认，其余（2 未确认 / 0 未知）= 未确认
  （解析见 ``core.workspace_client.H3CWorkspaceClient._clean_warn``）

过滤条件 dict（``criteria``）可选键：
    level     : int | None   事件等级（None / 0 / "" 表示全部）
    state     : str | int     "全部" / "已确认" / "未确认"，或原始 state 值（1/2）
    keyword   : str          关键字，匹配 告警名称 + 描述 + 来源（忽略大小写）
    date_from : str          起始日期 yyyy-mm-dd（含当天 00:00:00）
    date_to   : str          截止日期 yyyy-mm-dd（含当天 23:59:59）
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable, Mapping

from .styles import level_text

# ----------------------------------------------------------------------
# 常量与下拉选项
# ----------------------------------------------------------------------
#: state == 1 表示已确认（core.workspace_client._clean_warn 解析）
STATE_CONFIRMED = 1

#: 下拉选项（与平台 UI 口径一致）：全部 / 紧急(1) / 重要(2) / 次要(3) / 提示(4)
LEVEL_LABELS: list[str] = ["全部", "紧急", "重要", "次要", "提示"]
STATE_LABELS: list[str] = ["全部", "已确认", "未确认"]

#: 下拉文案 → 匹配的 eventLevel 集合（四级独立映射，单值）
_LEVEL_MATCH: dict[str, set[int]] = {
    "紧急": {1},
    "重要": {2},
    "次要": {3},
    "提示": {4},
}

_DATE_FMT = "%Y-%m-%d"


# ----------------------------------------------------------------------
# 单条判定
# ----------------------------------------------------------------------
def is_confirmed(warn: Any) -> bool:
    """告警是否已确认（state == 1）。"""
    return int(getattr(warn, "state", 0) or 0) == STATE_CONFIRMED


def state_text(warn: Any) -> str:
    """告警确认状态文案（与列表显示保持一致）。"""
    return "已确认" if is_confirmed(warn) else "未确认"


def level_of(warn: Any) -> int:
    return int(getattr(warn, "eventLevel", 0) or 0)


def level_label(warn: Any) -> str:
    return level_text(level_of(warn))


# ----------------------------------------------------------------------
# 条件解析
# ----------------------------------------------------------------------
def parse_level(value: Any) -> set[int] | None:
    """把下拉文案/数值解析为 eventLevel 集合；无法识别或「全部」返回 None。

    - 文案「紧急/重要/次要/提示」映射到一组 eventLevel（见 ``_LEVEL_MATCH``）
    - 直接传入整数（单值 eventLevel）则包装为单元素集合
    - 传入集合则原样返回（便于组合筛选复用）
    """
    if value is None:
        return None
    if isinstance(value, (set, frozenset)):
        return set(value) or None
    if isinstance(value, int):
        return {value} if value else None
    text = str(value).strip()
    if not text or text == "全部":
        return None
    if text in _LEVEL_MATCH:
        return set(_LEVEL_MATCH[text])
    try:
        v = int(text)
        return {v} if v else None
    except ValueError:
        return None


def parse_state(value: Any) -> bool | None:
    """把下拉文案/数值解析为「是否要求已确认」；「全部」/空返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == STATE_CONFIRMED
    text = str(value).strip()
    if not text or text in ("全部", "all"):
        return None
    if text in ("已确认", "确认", "confirmed"):
        return True
    if text in ("未确认", "未确认告警", "unconfirmed"):
        return False
    try:
        return int(text) == STATE_CONFIRMED
    except ValueError:
        return None


def _parse_date(text: Any) -> datetime.date | None:
    s = str(text or "").strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:10], _DATE_FMT).date()
    except ValueError:
        return None


def _date_start_ms(text: Any) -> int | None:
    d = _parse_date(text)
    if d is None:
        return None
    return int(datetime.datetime(d.year, d.month, d.day).timestamp() * 1000)


def _date_end_ms(text: Any) -> int | None:
    d = _parse_date(text)
    if d is None:
        return None
    end = datetime.datetime(d.year, d.month, d.day, 23, 59, 59)
    return int(end.timestamp() * 1000)


# ----------------------------------------------------------------------
# 主过滤函数（纯函数：输入告警列表 + 条件 dict，输出过滤后列表）
# ----------------------------------------------------------------------
def filter_alarms(alarms: Iterable[Any], criteria: Mapping[str, Any] | None = None) -> list:
    """按 等级 + 状态 + 关键字 + 起止日期 组合过滤告警。

    所有条件为「与」关系；未给出或为「全部」的条件不参与过滤。
    输入顺序保持不变，返回新列表（不修改入参）。
    """
    c: Mapping[str, Any] = criteria or {}

    level = parse_level(c.get("level"))
    want_confirmed = parse_state(c.get("state"))
    keyword = str(c.get("keyword") or "").strip().lower()
    ts_from = _date_start_ms(c.get("date_from"))
    ts_to = _date_end_ms(c.get("date_to"))
    date_active = ts_from is not None or ts_to is not None

    result: list = []
    for warn in alarms or []:
        if level is not None and level_of(warn) not in level:
            continue
        if want_confirmed is not None and is_confirmed(warn) != want_confirmed:
            continue
        if keyword:
            haystack = " ".join(
                str(getattr(warn, name, "") or "")
                for name in ("eventName", "eventDesc", "eventSrc")
            ).lower()
            if keyword not in haystack:
                continue
        ts = int(getattr(warn, "eventTime", 0) or 0)
        if date_active:
            if ts <= 0:                      # 无时间信息的时间过滤直接排除
                continue
            if ts_from is not None and ts < ts_from:
                continue
            if ts_to is not None and ts > ts_to:
                continue
        result.append(warn)
    return result
