"""告警过滤（需求 1）+ 表格复制/列宽（需求 2/3）测试。

- 纯函数层：ui.alarm_filters.filter_alarms 的等级 / 状态 / 关键字 / 日期组合过滤
- 离屏 GUI 层：AlarmPanel 组合过滤后行数与显示一致；复制取显示文本；列可拖拽
"""

from __future__ import annotations

import datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
QtCore = pytest.importorskip("PyQt5.QtCore")

from core.models import WarnInfoDTO  # noqa: E402
import ui.styles as styles  # noqa: E402
from ui.alarm_filters import (  # noqa: E402
    LEVEL_LABELS, STATE_LABELS, STATE_CONFIRMED, filter_alarms, is_confirmed,
    parse_level, parse_state, state_text,
)


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _ms(y: int, m: int, d: int, hour: int = 10) -> int:
    return int(datetime.datetime(y, m, d, hour).timestamp() * 1000)


def _warn(i: int, level: int, state: int, name: str = "", desc: str = "",
          day=(2026, 9, 1)) -> WarnInfoDTO:
    return WarnInfoDTO(
        id=i, eventName=name or f"告警{i}", eventDesc=desc or f"描述{i}",
        eventLevel=level, state=state, eventTime=_ms(*day), eventSrc=f"host{i}",
    )


# 覆盖 4 个等级 × 2 种确认状态，另加 2 条跨日期数据
SAMPLE = [
    _warn(1, 1, 1, "CPU 过高", "主机 cvk-01 CPU 持续偏高"),
    _warn(2, 1, 2, "CPU 过高", "主机 cvk-02 CPU 持续偏高"),
    _warn(3, 2, 1, "内存不足", "主机 cvk-03 内存吃紧"),
    _warn(4, 2, 2, "内存不足", "主机 cvk-04 内存吃紧"),
    _warn(5, 2, 2, "磁盘告警", "主机 cvk-05 磁盘将满"),
    _warn(6, 3, 2, "网络抖动", "交换机丢包"),
    _warn(7, 4, 1, "快照失败", "定时快照未生成"),
    _warn(8, 4, 2, "网络抖动", "上行链路闪断", day=(2026, 9, 5)),
]


# ---------------------------------------------------------------------------
# 映射一致性
# ---------------------------------------------------------------------------
def test_level_labels_match_styles_and_event_level():
    """下拉文案与平台口径一致：全部/紧急/重要/次要/提示，四级独立映射。"""
    assert LEVEL_LABELS == ["全部", "紧急", "重要", "次要", "提示"]
    # 平台口径：eventLevel 1=紧急 / 2=重要 / 3=次要 / 4=提示
    assert styles.level_text(1) == "紧急"
    assert styles.level_text(2) == "重要"
    assert styles.level_text(3) == "次要"
    assert styles.level_text(4) == "提示"
    assert styles.level_text(0) == "未知"
    # 下拉文案 → eventLevel 集合映射（四级独立，单值）
    assert parse_level("紧急") == {1}
    assert parse_level("重要") == {2}
    assert parse_level("次要") == {3}
    assert parse_level("提示") == {4}
    assert parse_level("全部") is None and parse_level(None) is None


def test_state_labels_and_state_field():
    """确认状态取 WarnInfoDTO.state：1=已确认，其余=未确认。"""
    assert STATE_LABELS == ["全部", "已确认", "未确认"]
    assert STATE_CONFIRMED == 1
    assert parse_state("已确认") is True
    assert parse_state("未确认") is False
    assert parse_state("全部") is None
    assert is_confirmed(_warn(9, 1, 1)) is True
    assert is_confirmed(_warn(9, 1, 2)) is False
    assert state_text(_warn(9, 1, 1)) == "已确认"
    assert state_text(_warn(9, 1, 2)) == "未确认"


def test_models_state_field_exists_in_dto():
    """确认状态字段 state 是 models.py 原生字段（非本轮新增）。"""
    fields = WarnInfoDTO.__dataclass_fields__
    assert "state" in fields and "eventLevel" in fields


# ---------------------------------------------------------------------------
# 纯函数过滤
# ---------------------------------------------------------------------------
def test_no_criteria_returns_all_and_keeps_order():
    assert filter_alarms(SAMPLE) == SAMPLE
    assert filter_alarms(SAMPLE, {}) == SAMPLE
    assert filter_alarms([]) == []


def test_filter_by_level():
    # 整数 eventLevel 单值过滤（{2}）：仅 eventLevel==2
    assert [w.id for w in filter_alarms(SAMPLE, {"level": 2})] == [3, 4, 5]
    # 平台口径：「紧急」= eventLevel 1（独立档），故仅含 level1 告警
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "紧急"})] == [1, 2]
    # 「提示」= eventLevel 4（原「警告」）
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "提示"})] == [7, 8]


def test_filter_urgent_is_eventlevel_1_only():
    """平台口径：「紧急」仅匹配 eventLevel=1；2/3/4 被排除。"""
    urgent = filter_alarms(SAMPLE, {"level": "紧急"})
    # SAMPLE 中 eventLevel==1 的为 id 1,2（级别顺序 1,1）
    assert sorted(w.eventLevel for w in urgent) == [1, 1]
    assert all(w.eventLevel == 1 for w in urgent)
    # eventLevel=2/3/4 不在「紧急」集合内
    assert all(w.eventLevel not in (2, 3, 4) for w in urgent)


def test_filter_by_label_maps_each_level():
    """四级独立映射：紧急=1, 重要=2, 次要=3, 提示=4。"""
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "紧急"})] == [1, 2]
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "重要"})] == [3, 4, 5]
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "次要"})] == [6]
    assert [w.id for w in filter_alarms(SAMPLE, {"level": "提示"})] == [7, 8]


def test_filter_by_state():
    assert [w.id for w in filter_alarms(SAMPLE, {"state": "已确认"})] == [1, 3, 7]
    assert [w.id for w in filter_alarms(SAMPLE, {"state": "未确认"})] == [2, 4, 5, 6, 8]


def test_combined_level_and_state():
    """重点用例：等级=重要(2) 且 状态=未确认（平台口径「重要」=eventLevel 2）。"""
    got = filter_alarms(SAMPLE, {"level": "重要", "state": "未确认"})
    assert [w.id for w in got] == [4, 5]
    assert all(w.eventLevel == 2 and w.state != STATE_CONFIRMED for w in got)

    got2 = filter_alarms(SAMPLE, {"level": 1, "state": "已确认"})
    assert [w.id for w in got2] == [1]


def test_combined_level_state_keyword():
    got = filter_alarms(SAMPLE, {"level": 2, "state": "未确认", "keyword": "内存"})
    assert [w.id for w in got] == [4]
    # 关键字同时匹配名称与描述，忽略大小写
    assert [w.id for w in filter_alarms(SAMPLE, {"keyword": "cpu"})] == [1, 2]


def test_keyword_no_match_returns_empty():
    assert filter_alarms(SAMPLE, {"keyword": "不存在的字样"}) == []


def test_keyword_matches_name_desc_and_src():
    """关键字需同时覆盖 名称 / 描述 / 来源 三个字段，且忽略大小写。"""
    assert [w.id for w in filter_alarms(SAMPLE, {"keyword": "host6"})] == [6]   # 仅来源命中
    assert [w.id for w in filter_alarms(SAMPLE, {"keyword": "交换机"})] == [6]   # 仅描述命中
    assert [w.id for w in filter_alarms(SAMPLE, {"keyword": "cpu"})] == [1, 2]  # 名称+描述，小写
    assert [w.id for w in filter_alarms(SAMPLE, {"keyword": "  CPU  "})] == [1, 2]  # 去空白


def test_state_unknown_value_treated_as_unconfirmed():
    """state 为 0 / 其它未知值时按「未确认」处理。"""
    unknown = WarnInfoDTO(id=50, eventLevel=2, state=0, eventTime=_ms(2026, 9, 1))
    assert [w.id for w in filter_alarms([unknown], {"state": "未确认"})] == [50]
    assert filter_alarms([unknown], {"state": "已确认"}) == []
    assert state_text(unknown) == "未确认"


def test_combine_all_conditions():
    """等级 + 状态 + 关键字 + 起止日期 五条件同时生效（AND）。"""
    got = filter_alarms(SAMPLE, {
        "level": "提示", "state": "未确认", "keyword": "网络",
        "date_from": "2026-09-05", "date_to": "2026-09-05",
    })
    assert [w.id for w in got] == [8]
    # 同样条件但日期不匹配 → 空
    assert filter_alarms(SAMPLE, {
        "level": "提示", "state": "未确认", "keyword": "网络",
        "date_from": "2026-09-01", "date_to": "2026-09-02",
    }) == []


def test_empty_input_and_bad_criteria_are_safe():
    """空列表 / 非法日期 / 非法等级 都不应抛异常或误清空。"""
    assert filter_alarms([], {"level": "紧急", "state": "未确认"}) == []
    assert filter_alarms(None, {"keyword": "x"}) == []
    assert len(filter_alarms(SAMPLE, {"date_from": "2026-13-45"})) == len(SAMPLE)
    assert len(filter_alarms(SAMPLE, {"date_to": "abc"})) == len(SAMPLE)
    assert len(filter_alarms(SAMPLE, {"level": "", "state": "全部"})) == len(SAMPLE)


def test_date_range_is_inclusive():
    got = filter_alarms(SAMPLE, {"date_from": "2026-09-05", "date_to": "2026-09-05"})
    assert [w.id for w in got] == [8]
    assert len(filter_alarms(SAMPLE, {"date_from": "2026-09-01"})) == len(SAMPLE)
    assert filter_alarms(SAMPLE, {"date_to": "2026-08-31"}) == []


def test_date_no_timestamp_excluded_when_range_active():
    unknown = WarnInfoDTO(id=99, eventLevel=1, state=2, eventTime=0)
    assert filter_alarms([unknown], {"date_from": "2026-09-01"}) == []
    assert filter_alarms([unknown]) == [unknown]     # 不限日期时保留


def test_invalid_criteria_ignored_not_cleared():
    """无法识别的条件视为不过滤，避免误清空列表。"""
    assert len(filter_alarms(SAMPLE, {"level": "瞎写的"})) == len(SAMPLE)
    assert len(filter_alarms(SAMPLE, {"date_from": "不是日期"})) == len(SAMPLE)


def test_does_not_mutate_input():
    before = list(SAMPLE)
    filter_alarms(SAMPLE, {"level": 2, "state": "未确认"})
    assert SAMPLE == before


# ---------------------------------------------------------------------------
# 离屏 GUI：面板组合过滤 / 复制 / 列宽
# ---------------------------------------------------------------------------
def _panel(qapp):
    from ui.alarm_panel import AlarmPanel
    panel = AlarmPanel()
    panel.resize(900, 600)
    panel.show()
    qapp.processEvents()
    return panel


def test_panel_combined_filter_rows_and_display(qapp):
    """等级=紧急(1) & 状态=未确认 → 表格 1 行，徽章显示「紧急」（四级独立映射）。"""
    panel = _panel(qapp)
    panel.cb_state.setCurrentText("全部")  # 关闭默认未确认过滤以观察全量
    panel.set_alarms(list(SAMPLE))
    assert panel.table.rowCount() == len(SAMPLE)

    panel.cb_level.setCurrentText("紧急")
    panel.cb_state.setCurrentText("未确认")
    qapp.processEvents()

    # 匹配：id2(l1,s2) —— 紧急仅 eventLevel=1，id1 为已确认被排除
    assert panel.table.rowCount() == 1
    assert [panel.table.item(r, panel.NAME_COL).text() for r in range(1)] == [
        "CPU 过高"]
    # 级别列是徽章控件，取内部 QLabel 文本
    labels = panel.table.cellWidget(0, panel.LEVEL_COL).findChildren(QtWidgets.QLabel)
    assert labels[0].text() == "紧急"
    assert panel.table.item(0, panel.STATE_COL).text() == "未确认"

    # 选中行映射回正确的告警对象（过滤后索引不串位）
    panel.table.selectRow(0)
    assert panel._selected_warn().id == 2

    # 切换到「重要」(eventLevel=2) & 未确认 → id4、id5
    panel.cb_level.setCurrentText("重要")
    qapp.processEvents()
    assert panel.table.rowCount() == 2
    assert [panel.table.item(r, panel.NAME_COL).text() for r in range(2)] == [
        "内存不足", "磁盘告警"]
    labels0 = panel.table.cellWidget(0, panel.LEVEL_COL).findChildren(QtWidgets.QLabel)
    assert labels0[0].text() == "重要"


def test_panel_filter_all_back_restores_rows(qapp):
    panel = _panel(qapp)
    panel.cb_state.setCurrentText("全部")  # 关闭默认未确认过滤以观察全量
    panel.set_alarms(list(SAMPLE))
    panel.cb_level.setCurrentText("提示")          # eventLevel=4 → 2 条
    assert panel.table.rowCount() == 2
    panel.cb_level.setCurrentText("全部")
    panel.cb_state.setCurrentText("全部")
    assert panel.table.rowCount() == len(SAMPLE)


def test_panel_query_button_applies_filter(qapp):
    panel = _panel(qapp)
    panel.cb_state.setCurrentText("全部")  # 关闭默认未确认过滤以观察全量
    panel.set_alarms(list(SAMPLE))
    panel.le_keyword.setText("快照")
    panel.btn_query.click()
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, panel.NAME_COL).text() == "快照失败"


def test_copy_uses_display_text_not_internal_id(qapp):
    """复制得到的是显示文本（等级徽章文案、描述原文），不是内部 id。"""
    from ui.table_utils import cell_display_text, row_display_text
    panel = _panel(qapp)
    panel.cb_state.setCurrentText("全部")  # 关闭默认未确认过滤以观察全量
    panel.set_alarms([SAMPLE[0]])       # id=1 / 紧急 / 已确认 / 描述"主机 cvk-01 ..."
    row = row_display_text(panel.table, 0)
    assert "主机 cvk-01 CPU 持续偏高" in row
    assert cell_display_text(panel.table, 0, panel.LEVEL_COL) == "紧急"
    assert cell_display_text(panel.table, 0, panel.STATE_COL) == "已确认"
    assert str(SAMPLE[0].id) not in row.split("\t")[1]     # 名称列不是 id


def test_columns_are_interactive_and_resizable(qapp):
    """所有列可拖拽（Interactive），且拖拽生效后宽度改变。"""
    from PyQt5.QtWidgets import QHeaderView
    panel = _panel(qapp)
    header = panel.table.horizontalHeader()
    for c in range(panel.table.columnCount()):
        assert header.sectionResizeMode(c) == QHeaderView.Interactive
    before = panel.table.columnWidth(panel.TIME_COL)
    panel.table.setColumnWidth(panel.TIME_COL, before + 60)
    assert panel.table.columnWidth(panel.TIME_COL) == before + 60


def _total_width(table):
    return sum(table.columnWidth(c) for c in range(table.columnCount()))


def test_elastic_column_absorbs_when_other_column_resized(qapp):
    """回归 DEF-QA-01：拖非弹性列时弹性列重新吸附，总宽不溢出视口。"""
    panel = _panel(qapp)
    table, header = panel.table, panel.table.horizontalHeader()
    viewport = table.viewport().width()
    assert _total_width(table) <= viewport

    header.resizeSection(panel.NAME_COL, 400)      # 拖宽非弹性列
    qapp.processEvents()
    assert _total_width(table) <= viewport, "弹性列未收缩，总宽溢出视口"
    assert panel._resizer.auto_stretch is True     # 用户未直接拖弹性列，仍自动

    header.resizeSection(panel.NAME_COL, 120)      # 拖窄，弹性列回补
    qapp.processEvents()
    assert _total_width(table) <= viewport
    assert table.columnWidth(panel.DESC_COL) > 400


def test_elastic_column_respects_user_after_own_drag(qapp):
    """拖过弹性列自身后：交出控制权，后续窗口缩放与其它列拖拽都不再改动它。"""
    panel = _panel(qapp)
    table, header = panel.table, panel.table.horizontalHeader()
    header.resizeSection(panel.DESC_COL, 300)
    qapp.processEvents()
    assert panel._resizer.auto_stretch is False

    header.resizeSection(panel.NAME_COL, 350)      # 拖其它列不应影响弹性列
    qapp.processEvents()
    assert table.columnWidth(panel.DESC_COL) == 300

    panel.resize(1150, 600)                        # 缩放窗口也不应被重新拉伸
    qapp.processEvents()
    assert table.columnWidth(panel.DESC_COL) == 300
