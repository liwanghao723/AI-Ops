"""ui.alarm_panel.AlarmPanel 增量测试（离屏 Qt）。

覆盖本轮两项 UI 增强：
- 拉取上限下拉框 current_cap() 的映射（"不限制"→None / "5000"→5000 ...）
- 面板实时「共 N 条」计数：set_alarms / apply_filters 后 lbl_count 文本正确
"""

from __future__ import annotations

import os

# 必须在创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

QtWidgets = pytest.importorskip("PyQt5.QtWidgets")

from core.models import WarnInfoDTO  # noqa: E402
from ui.alarm_panel import AlarmPanel, _fmt_time  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _warn(i: int, level: int = 1, state: int = 2) -> WarnInfoDTO:
    return WarnInfoDTO(
        id=i,
        eventName=f"告警{i}",
        eventDesc=f"描述{i}",
        eventLevel=level,
        eventTime=1000 * i,
        eventType=1,
        state=state,
        eventSrc="host01",
        eventCount=1,
    )


# ---------------------------------------------------------------------------
# 拉取上限下拉框 current_cap() 映射
# ---------------------------------------------------------------------------
def test_current_cap_maps_unlimited_to_none(qapp) -> None:
    panel = AlarmPanel()
    panel.cb_limit.setCurrentText("不限制")
    assert panel.current_cap() is None


@pytest.mark.parametrize("text,expected", [
    ("1000", 1000),
    ("5000", 5000),
    ("10000", 10000),
])
def test_current_cap_maps_number(qapp, text, expected) -> None:
    panel = AlarmPanel()
    panel.cb_limit.setCurrentText(text)
    assert panel.current_cap() == expected


def test_current_cap_default_is_unlimited(qapp) -> None:
    """面板默认上限为「不限制」。

    回归：旧版默认 10000，而平台真实告警总量可达 1.8w 条以上，
    固定上限会截断并漏掉最旧的未确认告警（平台 47 条 / 软件 10 条）。
    """
    panel = AlarmPanel()
    assert panel.cb_limit.currentText() == "不限制"
    assert panel.current_cap() is None


def test_set_warn_count_renders_badge(qapp) -> None:
    """平台未确认告警角标（与平台网页红点同口径）能正确渲染。"""
    panel = AlarmPanel()
    assert panel.lbl_badge.text() == ""
    panel.set_warn_count({"urgent": 4, "important": 16,
                          "accessory": 8, "warning": 1})
    text = panel.lbl_badge.text()
    assert "紧急4" in text and "重要16" in text
    assert "次要8" in text and "提示1" in text
    assert "合计29" in text
    panel.set_warn_count({})
    assert panel.lbl_badge.text() == ""


# ---------------------------------------------------------------------------
# 回归：新建面板时下拉框默认值
# （四级独立映射 + 默认只展示未确认告警）
# ---------------------------------------------------------------------------
def test_panel_default_combo_state_is_unconfirmed_level_is_all(qapp) -> None:
    """回归：新建 AlarmPanel 时状态下拉默认「未确认」，等级下拉默认「全部」。

    四级独立映射 + 默认只展示未确认告警：cb_state 应被显式设为「未确认」，
    而 cb_level 保持首个选项「全部」（源码不对其 setCurrentText）。
    """
    panel = AlarmPanel()
    assert panel.cb_state.currentText() == "未确认"
    assert panel.cb_level.currentText() == "全部"


def test_panel_default_filter_shows_only_unconfirmed(qapp) -> None:
    """回归：未手动改状态下拉时，面板默认只展示未确认告警。

    构造 已确认/未确认 交替的数据，默认「未确认」应过滤掉已确认项。
    """
    panel = AlarmPanel()
    alarms = [_warn(i, level=2, state=s) for i, s in enumerate([1, 2, 1, 2], start=1)]
    panel.set_alarms(alarms)
    # state=1（已确认）的 id 1、3 被默认「未确认」过滤掉，仅剩 id 2、4
    assert panel.table.rowCount() == 2
    assert all(
        panel.table.item(r, panel.STATE_COL).text() == "未确认"
        for r in range(panel.table.rowCount())
    )
    # 仅 eventLevel=2 且 state=2（未确认）的 id 2、4 留在可见列表（按 id 升序）
    assert [w.id for w in sorted(panel._visible, key=lambda w: w.id)] == [2, 4]


# ---------------------------------------------------------------------------
# 面板实时「共 N 条」计数
# ---------------------------------------------------------------------------
def test_lbl_count_initial_zero(qapp) -> None:
    panel = AlarmPanel()
    assert panel.lbl_count.objectName() == "HintText"
    assert panel.lbl_count.text() == "共 0 条"


def test_set_alarms_updates_count_to_total(qapp) -> None:
    """set_alarms 经 apply_filters 渲染后，计数显示全部已拉取条数。"""
    panel = AlarmPanel()
    alarms = [_warn(i) for i in range(12)]
    panel.set_alarms(alarms)

    assert len(panel._visible) == 12
    assert panel.lbl_count.text() == "共 12 条"


def test_apply_filters_updates_count_after_filter(qapp) -> None:
    """切换等级筛选后，计数应随可见条数实时变化（N = 过滤后可见条数）。

    平台四级独立映射：紧急=1 / 重要=2 / 次要=3 / 提示=4。
    """
    panel = AlarmPanel()
    # 10 条：偶数 id → eventLevel 1(紧急)，奇数 id → eventLevel 2(重要)
    alarms = [_warn(i, level=1 if i % 2 == 0 else 2) for i in range(10)]
    # 追加 1 条「次要」(eventLevel 3) 用于验证下拉切换计数变化
    alarms.append(_warn(99, level=3))
    panel.set_alarms(alarms)
    assert panel.lbl_count.text() == "共 11 条"

    # 仅看「紧急」（eventLevel 1）→ 偶数 id 共 5 条
    panel.cb_level.setCurrentText("紧急")
    visible = panel.apply_filters()
    assert len(visible) == 5
    assert panel.lbl_count.text() == "共 5 条"

    # 切换到「重要」（eventLevel 2）→ 奇数 id 共 5 条
    panel.cb_level.setCurrentText("重要")
    visible = panel.apply_filters()
    assert len(visible) == 5
    assert panel.lbl_count.text() == "共 5 条"

    # 切换到「次要」（eventLevel 3）→ 仅 1 条
    panel.cb_level.setCurrentText("次要")
    visible = panel.apply_filters()
    assert len(visible) == 1
    assert panel.lbl_count.text() == "共 1 条"

    # 恢复「全部」
    panel.cb_level.setCurrentText("全部")
    assert panel.lbl_count.text() == "共 11 条"


def test_apply_filters_count_zero_when_filtered_out(qapp) -> None:
    """无匹配时计数显示 共 0 条。"""
    panel = AlarmPanel()
    panel.set_alarms([_warn(i) for i in range(3)])
    assert panel.lbl_count.text() == "共 3 条"

    panel.le_keyword.setText("不存在的关键字")
    panel.apply_filters()
    assert panel.lbl_count.text() == "共 0 条"


def test_switching_limit_refreshes_count(qapp) -> None:
    """切换「上限」下拉框应触发 apply_filters（计数同步刷新，不崩溃）。"""
    panel = AlarmPanel()
    panel.set_alarms([_warn(i) for i in range(8)])
    assert panel.lbl_count.text() == "共 8 条"

    # 切换上限不应改变可见条数（上限只影响「下一次拉取」，不影响已拉取数据）
    panel.cb_limit.setCurrentText("不限制")
    assert panel.lbl_count.text() == "共 8 条"
    panel.cb_limit.setCurrentText("1000")
    assert panel.lbl_count.text() == "共 8 条"


# ---------------------------------------------------------------------------
# 增量三：告警表格新增「首次告警时间 / 最新告警时间 / 重复次数」三列
# ---------------------------------------------------------------------------
def test_table_has_seven_columns(qapp) -> None:
    """表格列数应为 7（级别/告警名称/描述/首次/最新/重复次数/状态）。"""
    panel = AlarmPanel()
    assert panel.table.columnCount() == 7


def test_table_headers_include_new_fields(qapp) -> None:
    """表头需包含「首次告警时间 / 最新告警时间 / 重复次数」。"""
    panel = AlarmPanel()
    headers = [panel.table.horizontalHeaderItem(c).text()
               for c in range(panel.table.columnCount())]
    assert headers == ["级别", "告警名称", "描述", "首次告警时间",
                       "最新告警时间", "重复次数", "状态"]
    assert "首次告警时间" in headers
    assert "最新告警时间" in headers
    assert "重复次数" in headers


def _warn_times(first: int, last: int, count: int, i: int = 1) -> WarnInfoDTO:
    """构造带明显区分的首次/最新时间与重复次数的告警，便于断言不串字段。"""
    return WarnInfoDTO(
        id=i, eventName=f"告警{i}", eventDesc=f"描述{i}", eventLevel=1,
        eventTime=last, firstEventTime=first, eventType=1, state=2,
        eventSrc="host01", eventCount=count,
    )


def test_render_first_last_and_count_columns(qapp) -> None:
    """_render 后首/最新时间列与重复次数列文本正确，且首次≠最新。"""
    panel = AlarmPanel()
    # 时间刻意拉开到不同分钟，避免 _fmt_time 的 "%m-%d %H:%M" 吞掉秒差而相等
    first, last, count = 1_000_000, 3_600_000, 7
    panel.set_alarms([_warn_times(first, last, count, i=1)])

    fc = panel.table.item(0, panel.FIRST_COL)
    lc = panel.table.item(0, panel.LAST_COL)
    cc = panel.table.item(0, panel.COUNT_COL)
    assert fc is not None and lc is not None and cc is not None

    # 首次告警时间 / 最新告警时间 文本分别与 _fmt_time 对应值一致
    assert fc.text() == _fmt_time(first)
    assert lc.text() == _fmt_time(last)
    # 首次 ≠ 最新，证明两时间列取的是不同字段
    assert fc.text() != lc.text()
    # 重复次数显示为整数文本
    assert cc.text() == str(count)


def test_render_first_last_distinct_values_set_alarms(qapp) -> None:
    """经 set_alarms 渲染后，不同告警的首/最新时间各自正确。"""
    panel = AlarmPanel()
    alarms = [
        _warn_times(first=1_000_000, last=2_000_000, count=3, i=1),
        _warn_times(first=5_000_000, last=8_000_000, count=9, i=2),
    ]
    panel.set_alarms(alarms)
    assert panel.table.rowCount() == 2

    assert panel.table.item(0, panel.FIRST_COL).text() == _fmt_time(1_000_000)
    assert panel.table.item(0, panel.LAST_COL).text() == _fmt_time(2_000_000)
    assert panel.table.item(0, panel.COUNT_COL).text() == "3"
    assert panel.table.item(1, panel.FIRST_COL).text() == _fmt_time(5_000_000)
    assert panel.table.item(1, panel.LAST_COL).text() == _fmt_time(8_000_000)
    assert panel.table.item(1, panel.COUNT_COL).text() == "9"


# ---------------------------------------------------------------------------
# 增量三（QA 补充）：薄弱点专项
# ---------------------------------------------------------------------------
def test_render_count_zero_shows_zero(qapp) -> None:
    """重复次数=0 时单元格文本显示 '0'（不被 _fmt_time 等误处理）。"""
    panel = AlarmPanel()
    panel.set_alarms([_warn_times(first=1_000_000, last=2_000_000, count=0, i=1)])
    cc = panel.table.item(0, panel.COUNT_COL)
    assert cc is not None
    assert cc.text() == "0"


def test_render_missing_first_event_time_shows_dash(qapp) -> None:
    """firstEventTime=0（缺失）经 _fmt_time 渲染为 '-'，不抛异常。"""
    panel = AlarmPanel()
    w = _warn_times(first=0, last=2_000_000, count=4, i=1)
    assert w.firstEventTime == 0
    panel.set_alarms([w])
    fc = panel.table.item(0, panel.FIRST_COL)
    assert fc is not None
    assert fc.text() == "-"
    # 最新时间列仍正常显示
    assert panel.table.item(0, panel.LAST_COL).text() == _fmt_time(2_000_000)


def test_render_first_equals_last_no_crash(qapp) -> None:
    """首末时间相等（同一时刻既是首次也是最新）渲染不崩，两列文本一致。"""
    panel = AlarmPanel()
    panel.set_alarms([_warn_times(first=1_500_000, last=1_500_000, count=2, i=1)])
    fc = panel.table.item(0, panel.FIRST_COL)
    lc = panel.table.item(0, panel.LAST_COL)
    assert fc is not None and lc is not None
    assert fc.text() == lc.text() == _fmt_time(1_500_000)


def test_render_first_last_cross_check_via_set_alarms(qapp) -> None:
    """独立复核（QA 严过关）：set_alarms 渲染后首/末列文本分别等于
    _fmt_time(首)/_fmt_time(末) 且不同。
    注意：_fmt_time 按分钟截断，故两值须相差≥1分钟方可区分显示
    （1000ms 与 5000ms 会显示成相同 "01-01 08:00"，故此处用 1h 跨度）。
    """
    panel = AlarmPanel()
    first, last = 1_000_000, 3_600_000  # 相差 1 小时，确保分钟级显示不同
    panel.set_alarms([_warn_times(first=first, last=last, count=5, i=1)])
    fc = panel.table.item(0, panel.FIRST_COL)
    lc = panel.table.item(0, panel.LAST_COL)
    assert fc.text() == _fmt_time(first)
    assert lc.text() == _fmt_time(last)
    assert fc.text() != lc.text()
