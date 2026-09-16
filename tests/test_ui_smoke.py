"""离屏 GUI 冒烟测试（QT_QPA_PLATFORM=offscreen，不依赖真实显示器）。

覆盖本轮 UI 增量改版：
- styles.py：主题 QSS 渲染完整（无未替换占位符）+ 品牌常量
- widgets.py：HeaderBar / NavDelegate / CardFrame / LevelBadge / level_badge_cell
- analysis_dialog.py：成功路径（正文 + 处理建议分区）与失败路径（中文标题 + 折叠原始堆栈）
- main_window.py：离屏构造主窗口不抛异常（stub 掉 Worker 线程以避免真实网络调用）
"""

from __future__ import annotations

import os

# 必须在创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
QtCore = pytest.importorskip("PyQt5.QtCore")

import ui.styles as styles  # noqa: E402
from ui.widgets import (  # noqa: E402
    CardFrame, HeaderBar, LevelBadge, NavDelegate, level_badge_cell,
)
from ui.analysis_dialog import AnalysisDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


# ---------------------------------------------------------------------------
# 样式与品牌常量
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_get_qss_rendered_without_placeholders(theme):
    qss = styles.get_qss(theme)
    assert qss
    assert "%" not in qss.replace("%%", ""), "QSS 中残留未替换的 %VAR% 占位符"
    for key in ("#HeaderBar", "#NavList", "#CardFrame", "#CardTitle",
                "#CardValue", "#ErrorTitle", "#BrandName", "#BrandSub",
                "#AppTitle", "#HintText"):
        assert key in qss, f"QSS 缺少本轮新增的选择器: {key}"


def test_brand_constants():
    assert styles.BRAND_NAME == "紫光汇智"
    assert styles.BRAND_SUB
    assert "H3C" in styles.APP_TITLE


def test_nav_icon_color_mapping():
    assert styles.nav_icon_color("智能告警中心").startswith("#")
    assert styles.nav_icon_color("不存在的页签") == styles.NAV_ICON_COLOR_FALLBACK


# ---------------------------------------------------------------------------
# widgets
# ---------------------------------------------------------------------------
def test_header_bar_shows_title_version_and_brand(qapp):
    bar = HeaderBar(version="1.2.3")
    assert bar.lbl_title.text() == styles.APP_TITLE
    assert bar.lbl_version.text() == "v1.2.3"
    assert bar.lbl_brand.text() == styles.BRAND_NAME
    assert bar.lbl_brand_sub.text() == styles.BRAND_SUB
    bar.set_version("9.9.9")
    assert bar.lbl_version.text() == "v9.9.9"


def test_header_bar_empty_version(qapp):
    bar = HeaderBar(version="")
    assert bar.lbl_version.text() == ""


def test_nav_delegate_paints_and_size_hint(qapp):
    lst = QtWidgets.QListWidget()
    lst.setObjectName("NavList")
    delegate = NavDelegate(lst)
    lst.setItemDelegate(delegate)
    for name in ["智能告警中心", "桌面健康监控", "知识库与联网", "版本更新", "未知页签"]:
        lst.addItem(QtWidgets.QListWidgetItem(name))
    lst.show()
    qapp.processEvents()

    hint = delegate.sizeHint(lst.viewOptions(), lst.model().index(0, 0))
    assert hint.width() >= 0 and hint.height() == 46
    assert NavDelegate.ICON_SIZE == 16 and NavDelegate.ACCENT_BAR == 3
    lst.hide()


def test_card_frame_title_and_body(qapp):
    card = CardFrame("概要")
    assert card.lbl_title.text() == "概要"
    assert card.body() is not None
    card.add_widget(QtWidgets.QLabel("x"))
    assert card.layout().count() == 2

    empty = CardFrame()
    assert empty.lbl_title is None


@pytest.mark.parametrize("level, expect", [(1, "紧急"), (2, "重要"),
                                           (3, "次要"), (4, "提示"),
                                           (99, "未知")])
def test_level_badge_text(qapp, level, expect):
    assert LevelBadge(level).text() == expect


def test_level_badge_cell_is_widget(qapp):
    cell = level_badge_cell(2)
    assert isinstance(cell, QtWidgets.QWidget)
    badges = cell.findChildren(LevelBadge)
    assert len(badges) == 1
    assert badges[0].text() == "重要"


# ---------------------------------------------------------------------------
# AnalysisDialog：成功路径
# ---------------------------------------------------------------------------
def test_analysis_dialog_success_splits_suggestion(qapp):
    text = "主机 cvknode-01 CPU 持续偏高。\n\n处理建议：\n1. 排查进程\n2. 扩容"
    dlg = AnalysisDialog("AI 分析", [("主机", "cvk-01"), ("级别", "重要")],
                         text, "AI 分析")
    dlg.show()
    qapp.processEvents()

    assert not hasattr(dlg, "btn_detail"), "成功路径不应出现「原始错误详情」按钮"
    edits = [e.toPlainText() for e in dlg.findChildren(QtWidgets.QTextEdit)]
    assert any("cvknode-01" in e for e in edits)
    assert any("处理建议" in e for e in edits)
    dlg.hide()


def test_analysis_dialog_success_without_summary(qapp):
    dlg = AnalysisDialog(analysis_text="运行正常。")
    dlg.show()
    qapp.processEvents()
    assert dlg.windowTitle() == "AI 分析"
    dlg.hide()


def test_analysis_dialog_empty_text_goes_error_path(qapp):
    """空结果文本走失败卡片：中文兜底标题，且无原始堆栈可展开。"""
    dlg = AnalysisDialog(analysis_text="")
    dlg.show()
    qapp.processEvents()
    labels = [lbl.text() for lbl in dlg.findChildren(QtWidgets.QLabel)]
    assert any("分析未成功完成" in s for s in labels)
    assert not hasattr(dlg, "btn_detail"), "无原始堆栈时不应生成详情按钮"
    dlg.hide()


# ---------------------------------------------------------------------------
# AnalysisDialog：失败路径
# ---------------------------------------------------------------------------
def test_analysis_dialog_error_shows_chinese_and_folds_stack(qapp):
    raw = ("⚠ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required "
           "for url: https://api.deepseek.com/v1/chat/completions）。")
    dlg = AnalysisDialog("告警 AI 分析", [("告警", "CPU 过高")], raw, "AI 分析")
    dlg.show()
    qapp.processEvents()

    labels = [lbl.text() for lbl in dlg.findChildren(QtWidgets.QLabel)]
    assert any("余额不足" in s for s in labels), "失败卡片未显示中文「余额不足」标题"
    # 主体不得是英文堆栈
    bodies = [e.toPlainText() for e in dlg.findChildren(QtWidgets.QTextEdit)]
    visible_bodies = " ".join(e.toPlainText()
                              for e in dlg.findChildren(QtWidgets.QTextEdit)
                              if e.isVisible())
    assert "Payment Required" not in visible_bodies

    # 原始堆栈默认折叠，点击后展开
    assert dlg.btn_detail.text() == "查看原始错误详情"
    assert not dlg.te_detail.isVisible()
    dlg.btn_detail.setChecked(True)
    qapp.processEvents()
    assert dlg.te_detail.isVisible()
    assert dlg.btn_detail.text() == "收起原始错误详情"
    assert "402 Client Error" in dlg.te_detail.toPlainText()
    assert bodies and "402 Client Error" in bodies[-1]
    dlg.hide()


@pytest.mark.parametrize("raw, kw", [
    ("⚠ 健康解读失败（Read timed out.）。", "连接"),
    ("⚠ 健康解读失败（403 Forbidden）。", "权限"),
    ("⚠ 问答失败（404 Not Found: model missing）。", "模型"),
    ("⚠ 问答失败（500 Internal Server Error）。", "不可用"),
])
def test_analysis_dialog_error_classifications(qapp, raw, kw):
    dlg = AnalysisDialog("AI 分析", [], raw, "AI 分析")
    dlg.show()
    qapp.processEvents()
    labels = " ".join(lbl.text() for lbl in dlg.findChildren(QtWidgets.QLabel))
    assert kw in labels
    dlg.hide()


# ---------------------------------------------------------------------------
# 知识库面板：失败路径内联展示（该面板不弹窗）
# ---------------------------------------------------------------------------
def test_knowledge_panel_error_path_shows_chinese_title(qapp):
    from ui.knowledge_panel import KnowledgePanel

    panel = KnowledgePanel()
    panel.show_answer("⚠ 问答失败（LLM 请求失败: 402 Client Error: Payment Required）。")
    out = panel.output.toPlainText()
    assert out.splitlines()[0].startswith("⚠ 大模型账户余额不足")
    assert "余额不足，请充值" in out

    panel.show_answer("主机正常。\n处理建议：\n重启")
    assert panel.output.toPlainText() == "主机正常。\n处理建议：\n重启"


# ---------------------------------------------------------------------------
# MainWindow 离屏构造
# ---------------------------------------------------------------------------
def test_main_window_constructs_offscreen(qapp, monkeypatch):
    """构造 MainWindow 校验布局（本轮改动点），屏蔽 worker 线程以避免真实网络请求。"""
    pytest.importorskip("core.config")
    from core.config import AppConfig
    from ui.main_window import MainWindow

    def _no_workers(self):  # 不启动后台线程/网络
        self.alarm_worker = self.health_worker = None
        self.analyze_worker = self.version_worker = None

    monkeypatch.setattr(MainWindow, "_start_workers", _no_workers)

    win = MainWindow(AppConfig())
    win.show()
    qapp.processEvents()

    assert "H3C" in win.windowTitle()
    # 顶部品牌栏 + 左侧导航 + 右侧堆叠 + 状态栏品牌小字
    assert isinstance(win.header, HeaderBar)
    assert win.header.lbl_brand.text() == styles.BRAND_NAME
    assert [win.nav.item(i).text() for i in range(win.nav.count())] == [
        "智能告警中心", "桌面健康监控", "知识库与联网", "版本更新"]
    assert win.stack.count() == 4
    assert win.lbl_brand_status.text() == styles.BRAND_NAME
    assert win.header.lbl_brand_sub.text() == styles.BRAND_SUB
    # 左下角可视化设置模块
    assert hasattr(win, "settings_footer")
    assert win.settings_footer.objectName() == "SettingsFooter"
    assert win.settings_footer.btn.objectName() == "SettingsBtn"
    # 摘要默认反映 AppConfig 默认连接（deepseek / 平台 base_url）
    assert "H3C" in win.settings_footer.lbl_platform.text()
    assert "AI" in win.settings_footer.lbl_llm.text()
    win.close()


def test_settings_footer_opens_config(qapp, monkeypatch):
    """点击左下角「系统设置」按钮应打开配置对话框（不真正弹窗）。"""
    pytest.importorskip("core.config")
    from core.config import AppConfig
    from ui.main_window import MainWindow

    def _no_workers(self):
        self.alarm_worker = self.health_worker = None
        self.analyze_worker = self.version_worker = None

    monkeypatch.setattr(MainWindow, "_start_workers", _no_workers)

    calls = []
    monkeypatch.setattr(MainWindow, "_open_config",
                        lambda self: calls.append(1))

    win = MainWindow(AppConfig())
    win.show()
    qapp.processEvents()

    assert win.settings_footer.btn.receivers(win.settings_footer.btn.clicked) >= 1
    win.settings_footer.btn.click()
    assert calls == [1], "点击设置按钮应触发配置对话框打开"
    win.close()


# ---------------------------------------------------------------------------
# 告警面板：等级 + 状态 组合过滤（离屏）
# ---------------------------------------------------------------------------
def _warn(i, level, state, name, ts=0, desc=""):
    from core.models import WarnInfoDTO
    return WarnInfoDTO(id=i, eventName=name, eventDesc=desc or f"描述{i}",
                       eventLevel=level, eventTime=ts, state=state)


def _sample_alarms():
    base = 1780000000000
    return [
        _warn(1, 1, 2, "主机CPU过高", base),
        _warn(2, 2, 2, "虚拟机内存告警", base + 86400000),
        _warn(3, 2, 1, "存储容量不足", base + 2 * 86400000),
        _warn(4, 3, 1, "网络抖动", base + 3 * 86400000),
        _warn(5, 4, 2, "许可证即将到期", base + 4 * 86400000),
        _warn(6, 2, 2, "磁盘告警", base + 5 * 86400000),
    ]


def test_alarm_panel_level_and_state_combined_filter(qapp):
    from ui.alarm_panel import AlarmPanel

    panel = AlarmPanel()
    panel.show()
    qapp.processEvents()

    # 默认状态过滤为「未确认」：_sample_alarms 中未确认的有 id1/2/5/6 共 4 条
    panel.set_alarms(_sample_alarms())
    assert panel.table.rowCount() == 4, "默认状态=未确认，仅展示未确认告警"
    assert [panel.table.item(r, panel.STATE_COL).text() for r in range(panel.table.rowCount())] == [
        "未确认", "未确认", "未确认", "未确认"]

    # 等级=紧急（eventLevel 1）+ 默认未确认 → id 1
    panel.cb_level.setCurrentText("紧急")
    qapp.processEvents()
    assert [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())] == [
        "主机CPU过高"]

    # 等级=重要（eventLevel 2）+ 未确认 → id 2/6
    panel.cb_level.setCurrentText("重要")
    qapp.processEvents()
    assert [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())] == [
        "虚拟机内存告警", "磁盘告警"]
    assert [panel.table.item(r, panel.STATE_COL).text() for r in range(panel.table.rowCount())] == [
        "未确认", "未确认"], "状态列文案须与过滤条件一致"

    # 叠加关键字
    panel.le_keyword.setText("内存")
    panel.apply_filters()
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "虚拟机内存告警"

    # 切换状态为「已确认」 → 重要 且 已确认 = id 3
    panel.le_keyword.clear()
    panel.cb_state.setCurrentText("已确认")
    qapp.processEvents()
    assert [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())] == [
        "存储容量不足"]
    assert panel.table.item(0, panel.STATE_COL).text() == "已确认"

    # 等级=次要（eventLevel 3）+ 已确认 → id 4
    panel.cb_level.setCurrentText("次要")
    qapp.processEvents()
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "网络抖动"
    panel.table.setCurrentCell(0, 1)
    assert panel._selected_warn().id == 4

    # 等级=提示（eventLevel 4）+ 已确认 → 无命中（id5 为未确认）
    panel.cb_level.setCurrentText("提示")
    qapp.processEvents()
    assert panel.table.rowCount() == 0

    # 回到全部 + 全部 → 6 条
    panel.cb_level.setCurrentText("全部")
    panel.cb_state.setCurrentText("全部")
    qapp.processEvents()
    assert panel.table.rowCount() == 6
    panel.close()


def test_alarm_table_columns_resizable_and_copy(qapp):
    from ui.alarm_panel import AlarmPanel
    from ui.table_utils import copy_cell, copy_row, row_display_text

    panel = AlarmPanel()
    panel.resize(900, 600)
    panel.show()
    qapp.processEvents()
    panel.set_alarms(_sample_alarms())

    header = panel.table.horizontalHeader()
    modes = [header.sectionResizeMode(c) for c in range(panel.table.columnCount())]
    assert set(modes) == {QtWidgets.QHeaderView.Interactive}, "所有列必须可拖拽调宽"
    assert panel.table.columnWidth(2) >= 140, "描述列默认弹性填充剩余宽度"

    # 手动拖拽描述列后不再自动覆盖用户设定
    panel.table.setColumnWidth(2, 300)
    qapp.processEvents()
    assert panel._resizer.auto_stretch is False
    assert panel.table.columnWidth(2) == 300

    # 复制单元格（描述列完整文本）
    panel.table.setCurrentCell(1, 2)
    assert copy_cell(panel.table) is True
    assert QtWidgets.QApplication.clipboard().text() == "描述2"

    # 复制整行：含等级徽章文本，制表符分隔
    panel.table.setCurrentCell(1, 1)
    assert copy_row(panel.table) is True
    copied = QtWidgets.QApplication.clipboard().text()
    assert copied.split("\t")[0] == "重要", "复制的是显示文本（徽章文案）而非内部 id"
    assert copied.split("\t")[1] == "虚拟机内存告警"
    assert copied == row_display_text(panel.table, 1)
    panel.close()


def test_health_table_copy(qapp):
    from ui.health_panel import HealthPanel
    from core.models import HealthRow
    from ui.table_utils import copy_row

    panel = HealthPanel()
    panel.show()
    qapp.processEvents()
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", ip="10.0.0.1", os="Win10",
                              status="running", host_id=5, host_name="host5",
                              vm_cpu=90.0, vm_mem=40.0, vm_disk=70.0)])
    header = panel.table.horizontalHeader()
    assert set(header.sectionResizeMode(c) for c in range(8)) == {
        QtWidgets.QHeaderView.Interactive}

    panel.table.setCurrentCell(0, 0)
    assert copy_row(panel.table) is True
    copied = QtWidgets.QApplication.clipboard().text()
    # 状态列现为彩色徽标，复制文本为友好中文「运行中」（不再透传 API 原始串）
    assert copied.split("\t")[:6] == ["vm-01", "10.0.0.1", "Win10", "运行中",
                                      "host5", "90%"]
    panel.close()


def test_health_status_display_mapping():
    from ui.health_panel import _status_display, _status_priority

    # running → 运行中(绿)；shutOff/关机 → 已关机(灰)；未知值保留原文中性灰
    assert _status_display("running") == ("运行中", "#0f7a3d", "#e6f4ea")
    assert _status_display("shutOff") == ("已关机", "#5b6472", "#eaecef")
    assert _status_display("SHUTOFF") == ("已关机", "#5b6472", "#eaecef")
    assert _status_display("weird") == ("weird", "#5b6472", "#eaecef")
    # 排序优先级：running(0) < 关机(1) < 未知(3)
    assert _status_priority("running") < _status_priority("shutOff")
    assert _status_priority("shutOff") < _status_priority("weird")


def test_health_status_header_click_sorts(qapp):
    from ui.health_panel import HealthPanel
    from core.models import HealthRow
    from PyQt5.QtCore import Qt

    panel = HealthPanel()
    panel.show()
    qapp.processEvents()
    panel.set_rows([
        HealthRow(vm_id=1, title="b", status="shutOff"),
        HealthRow(vm_id=2, title="a", status="running"),
        HealthRow(vm_id=3, title="c", status="shutOff"),
    ])
    # 初始表头无箭头
    assert panel.table.horizontalHeaderItem(3).text() == "状态"

    # 第一次点击：升序 → running 置顶
    panel._on_header_clicked(3)
    assert panel._status_sort_dir == Qt.AscendingOrder
    assert panel.table.horizontalHeaderItem(3).text() == "状态 ▲"
    visible = panel._visible_rows()
    assert visible[0].status == "running"
    assert [r.status for r in visible] == ["running", "shutOff", "shutOff"]
    # 选中行映射随排序同步（点击 running 行取到的仍是该 HealthRow）
    panel.table.setCurrentCell(0, 0)
    assert panel._selected_row().title == "a"

    # 第二次点击：降序 → 关机置顶，表头 ▼
    panel._on_header_clicked(3)
    assert panel._status_sort_dir == Qt.DescendingOrder
    assert panel.table.horizontalHeaderItem(3).text() == "状态 ▼"
    assert [r.status for r in panel._visible_rows()] == ["shutOff", "shutOff", "running"]

    # 其它列点击不触发排序
    panel._on_header_clicked(0)
    assert panel._status_sort_dir == Qt.DescendingOrder
    panel.close()


def test_health_status_badge_cell_widget(qapp):
    from ui.health_panel import HealthPanel
    from core.models import HealthRow

    panel = HealthPanel()
    panel.show()
    qapp.processEvents()
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", status="running")])
    # 状态列既有隐藏 item（供复制/选中）又有彩色徽标 cell widget
    assert panel.table.item(0, 3).text() == "运行中"
    widget = panel.table.cellWidget(0, 3)
    assert widget is not None
    badge = widget.findChild(QtWidgets.QLabel)
    assert badge is not None and "运行中" in badge.text()
    panel.close()


# ---------------------------------------------------------------------------
# 健康面板：桌面级口径（表头 / "-" 渲染 / 阈值着色 / 自动刷新）
# ---------------------------------------------------------------------------
def test_health_panel_headers_are_desktop_level(qapp):
    from ui.health_panel import HealthPanel

    panel = HealthPanel()
    headers = [panel.table.horizontalHeaderItem(c).text()
               for c in range(panel.table.columnCount())]
    assert headers == ["名称", "IP", "操作系统", "状态", "所在主机",
                       "桌面CPU%", "桌面内存%", "桌面磁盘%"]
    assert panel.table.columnCount() == 8
    panel.close()


def test_health_panel_powered_off_row_shows_dash(qapp):
    from ui.health_panel import HealthPanel
    from core.models import HealthRow
    from ui.table_utils import row_display_text

    panel = HealthPanel()
    panel.show()
    qapp.processEvents()
    panel.set_rows([HealthRow(vm_id=1, title="关机桌面", ip="-", os="Win10",
                              status="关机", vm_cpu=None, vm_mem=None, vm_disk=None)])
    text = row_display_text(panel.table, 0)
    assert text.split("\t")[5:] == ["-", "-", "-"], "关机桌面三列性能显示 '-'"
    for col in (5, 6, 7):
        widget = panel.table.cellWidget(0, col)
        assert widget.text() == "-"
        assert "#E53935" not in widget.styleSheet(), "关机不得标红"
    panel.close()


def test_health_panel_threshold_coloring(qapp):
    from ui.health_panel import HealthPanel
    from core.models import HealthRow
    from ui.styles import THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE

    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_cpu=86.0, vm_mem=85.0,
                              vm_disk=10.0)])
    cpu_cell = panel.table.cellWidget(0, 5)
    mem_cell = panel.table.cellWidget(0, 6)
    disk_cell = panel.table.cellWidget(0, 7)
    assert cpu_cell.styleSheet() == THRESHOLD_BREACH_STYLE, "86% ≥ 85% 标红"
    assert mem_cell.styleSheet() == THRESHOLD_BREACH_STYLE, "85% 等于阈值也标红"
    assert disk_cell.styleSheet() == THRESHOLD_NORMAL_STYLE
    panel.close()


def test_health_panel_disk_column_threshold_coloring(qapp):
    """磁盘列（col 7）必须参与阈值标红——这是 2.9.10 磁盘% 修复后的关键核对点。

    旧逻辑 vm_disk 恒为 None，磁盘列永远不着红；修复后磁盘有真实值，
    必须随 threshold_cpu 标红，且不受内存阈值影响。
    """
    from ui.health_panel import HealthPanel
    from core.models import HealthRow
    from ui.styles import THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE

    # 1) 磁盘 90% ≥ 85% → 标红
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_disk=90.0)])
    disk_cell = panel.table.cellWidget(0, 7)
    assert disk_cell.styleSheet() == THRESHOLD_BREACH_STYLE, "磁盘 90% ≥ 85% 必须标红"
    assert disk_cell.text() == "90%"
    panel.close()

    # 2) 磁盘 50% < 85% → 正常样式（不着红）
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_disk=50.0)])
    disk_cell = panel.table.cellWidget(0, 7)
    assert disk_cell.styleSheet() == THRESHOLD_NORMAL_STYLE, "磁盘 50% < 85% 不应标红"
    panel.close()

    # 3) 磁盘 None（关机/无数据）→ "-" 且不着红
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_disk=None)])
    disk_cell = panel.table.cellWidget(0, 7)
    assert disk_cell.text() == "-", "无数据应显示 -"
    assert disk_cell.styleSheet() == THRESHOLD_NORMAL_STYLE, "无数据不着红"
    panel.close()

    # 4) 磁盘阈值跟随 threshold_cpu（而非内存阈值）：mem=99 不改、cpu=50 时磁盘 60 仍标红
    panel = HealthPanel(threshold_cpu=50, threshold_mem=99)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", vm_disk=60.0)])
    disk_cell = panel.table.cellWidget(0, 7)
    assert disk_cell.styleSheet() == THRESHOLD_BREACH_STYLE, \
        "磁盘阈值应跟随 threshold_cpu(50)，60% ≥ 50% 标红，不受 mem=99 影响"
    panel.close()


def test_health_panel_auto_refresh_default_30s(qapp):
    from ui.health_panel import HealthPanel

    panel = HealthPanel()
    assert panel.cb_refresh.currentText() == "30s"
    assert panel.refresh_interval() == 30
    assert panel._timer.isActive() is True, "默认 30s 自动刷新应开启"
    assert panel._timer.interval() == 30_000

    panel.cb_refresh.setCurrentText("关闭")
    assert panel._timer.isActive() is False

    panel.cb_refresh.setCurrentText("60s")
    assert panel.refresh_interval() == 60
    assert panel._timer.isActive() is True
    panel.close()


def test_health_panel_refresh_request_dedup_and_unlock(qapp):
    """采集中重复触发被忽略；set_rows 后解锁可再次触发。"""
    from ui.health_panel import HealthPanel

    panel = HealthPanel()
    fired = []
    panel.request_refresh.connect(lambda: fired.append(1))

    panel.trigger_refresh()
    assert fired == [1]
    assert panel.btn_refresh.isEnabled() is False

    panel.trigger_refresh()          # 采集中 → 忽略
    assert len(fired) == 1

    panel.set_rows([])               # 数据回来 → 解锁
    assert panel.btn_refresh.isEnabled() is True
    panel.trigger_refresh()
    assert len(fired) == 2

    panel.release_refresh_lock()     # 失败路径解锁（worker error）
    panel.trigger_refresh()
    assert len(fired) == 3
    panel.close()


def test_health_panel_show_explain_uses_vm_metrics(qapp, monkeypatch):
    """AI 解读弹窗概要须带桌面级指标（关机显示 '-'）。"""
    from ui import health_panel as hp_mod
    from ui.health_panel import HealthPanel
    from core.models import HealthRow

    panel = HealthPanel()
    captured = {}

    def _fake_dialog(parent, title, summary, text, body_title="AI 分析"):
        captured["summary"] = summary
        captured["title"] = title

    monkeypatch.setattr(hp_mod, "show_analysis_dialog", _fake_dialog)
    panel.set_rows([HealthRow(vm_id=1, title="vm-01", ip="10.0.0.1", os="Win10",
                              status="running", vm_cpu=91.0, vm_mem=None,
                              vm_disk=70.0)])
    panel.table.setCurrentCell(0, 0)
    panel.show_explain("AI 说：CPU 偏高")
    pairs = dict(captured["summary"])
    assert pairs["桌面CPU"] == "91%"
    assert pairs["桌面内存"] == "-"
    assert pairs["桌面磁盘"] == "70%"
    assert pairs["IP"] == "10.0.0.1"
    panel.close()


def test_health_panel_set_rows_count_matches_list(qapp):
    """行数 = 清单数（含关机桌面）。"""
    from ui.health_panel import HealthPanel
    from core.models import HealthRow

    rows = [HealthRow(vm_id=i, title=f"vm-{i}",
                      vm_cpu=1.0 if i % 2 else None)
            for i in range(60)]
    panel = HealthPanel()
    panel.set_rows(rows)
    assert panel.table.rowCount() == 60
    panel.close()


def test_health_panel_stats_summary(qapp):
    """顶部概览：桌面总数 / 运行中 / 已关机（其它状态计入总数但不单列）。"""
    from ui.health_panel import HealthPanel
    from core.models import HealthRow

    panel = HealthPanel()
    panel.set_rows([
        HealthRow(vm_id=1, title="a", status="running"),
        HealthRow(vm_id=2, title="b", status="shutOff"),
        HealthRow(vm_id=3, title="c", status="shutOff"),
        HealthRow(vm_id=4, title="d", status="suspend"),   # 其它状态：只算进总数
        HealthRow(vm_id=5, title="e", status="weird"),      # 未知值：只算进总数
    ])
    assert panel.lbl_stats.text() == "桌面总数：5 ｜ 运行中：1 ｜ 已关机：2"
    # 空数据归零
    panel.set_rows([])
    assert panel.lbl_stats.text() == "桌面总数：0 ｜ 运行中：0 ｜ 已关机：0"
    panel.close()


def test_health_custom_threshold_consistent_between_table_and_ai(qapp):
    """自定义阈值（CPU 50%）时：表格着色与 AI 解读判定必须一致。

    回归背景：AI 解读曾硬编码 85%，与配置阈值脱节，会出现
    「表格已标红、AI 却说运行正常」的自相矛盾。
    """
    from ui.health_panel import HealthPanel
    from ui.styles import THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE
    from ai.analyzer import Analyzer
    from core.models import HealthRow

    row = HealthRow(vm_id=1, title="vm-01", os="Win10", status="running",
                    vm_cpu=60.0, vm_mem=10.0, vm_disk=10.0)

    panel = HealthPanel(threshold_cpu=50, threshold_mem=50)
    panel.set_rows([row])
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat(self, messages, temperature=0.3):
            self.calls.append(messages)
            return "ok"

    llm = FakeLLM()
    Analyzer(llm=llm).explain_health(row, panel.threshold_cpu, panel.threshold_mem)
    user = llm.calls[0][1]["content"]
    assert "超过阈值 50%" in user, "AI 须按面板阈值 50% 判定越界"

    # 同一行在默认阈值 85% 下：表格不标红、AI 判定正常（向后兼容）
    default_panel = HealthPanel()
    default_panel.set_rows([row])
    assert default_panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_NORMAL_STYLE
    llm2 = FakeLLM()
    Analyzer(llm=llm2).explain_health(row)
    assert "运行正常" in llm2.calls[0][1]["content"]
    panel.close()
    default_panel.close()


def test_health_panel_set_thresholds_repaints_immediately(qapp):
    """阈值热更新（set_thresholds）立即重绘，且不篡改「最后刷新」时间。

    回归背景：配置保存后只改属性不重绘，表格着色会一直停留在旧阈值口径，
    与 AnalyzeWorker 已按新阈值给出的 AI 判定自相矛盾（自动刷新默认关闭，
    该不一致可持续到用户手动刷新为止）。
    """
    from ui.health_panel import HealthPanel
    from ui.styles import THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE
    from core.models import HealthRow

    row = HealthRow(vm_id=1, title="vm-01", vm_cpu=60.0, vm_mem=60.0, vm_disk=10.0)
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([row])
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_NORMAL_STYLE
    last_text = panel.lbl_last.text()
    assert last_text.startswith("最后刷新：")

    panel.set_thresholds(50, 50)          # 配置保存 → 批量更新，只重绘一次
    assert panel.threshold_cpu == 50 and panel.threshold_mem == 50
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE
    assert panel.table.cellWidget(0, 6).styleSheet() == THRESHOLD_BREACH_STYLE
    assert panel.table.cellWidget(0, 7).styleSheet() == THRESHOLD_NORMAL_STYLE, "磁盘 10% 未越界"
    assert panel.table.rowCount() == 1, "重绘不得丢行/增行"
    assert panel.lbl_last.text() == last_text, "阈值变更不是一次采集，不得刷新时间戳"
    panel.close()


def test_health_panel_threshold_property_setter_repaints(qapp):
    """单独赋值 threshold_cpu / threshold_mem 也立即生效（property setter）。"""
    from ui.health_panel import HealthPanel
    from ui.styles import THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE
    from core.models import HealthRow

    row = HealthRow(vm_id=1, title="vm-01", vm_cpu=60.0, vm_mem=10.0, vm_disk=10.0)
    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows([row])
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_NORMAL_STYLE

    panel.threshold_cpu = 50              # 直接赋值（外部代码既有写法）
    assert panel.threshold_cpu == 50
    assert panel.table.cellWidget(0, 5).styleSheet() == THRESHOLD_BREACH_STYLE

    panel.threshold_mem = 5
    assert panel.table.cellWidget(0, 6).styleSheet() == THRESHOLD_BREACH_STYLE
    panel.threshold_mem = 85
    assert panel.table.cellWidget(0, 6).styleSheet() == THRESHOLD_NORMAL_STYLE
    panel.close()


def test_health_end_to_end_60_desktops(qapp):
    """集成冒烟：清单60台（45开机/15关机）→ 采集 → 渲染。

    口径：桌面级 2.27.30 逐台 /vms/monitor/{uuid}（按 uuid 关联），
    关机行 cpuRate/memRate 为 null → 显示 "-" 且不标红。
    """
    from ui.health_panel import HealthPanel
    from ui.table_utils import row_display_text
    from ui.styles import THRESHOLD_BREACH_STYLE
    from workers.health_worker import HealthWorker
    from core.models import RsDomainSummary, VmBrief

    TOTAL, POWERED_ON = 60, 45

    class FakeClient:
        def query_vm_list(self, domain_name=None):
            return [VmBrief(id=i, uuid=f"uuid-{i}", title=f"vm-{i:02d}",
                            ipAddr=f"10.0.0.{i}",
                            status="running" if i < POWERED_ON else "3")
                    for i in range(TOTAL)]

        def get_vm_summary(self, vm_id):
            return RsDomainSummary(
                title=f"vm-{vm_id:02d}", osVersion="Windows 10",
                status="running" if vm_id < POWERED_ON else "3", hostId=1,
                ip=f"10.0.0.{vm_id}" if vm_id < POWERED_ON else "")

        def get_vm_monitor(self, domain_uuid):
            # 开机桌面：cpu = (i*2)%100 → i=43/44 分别 86/88 超阈值
            i = int(domain_uuid.split("-")[-1])
            if i >= POWERED_ON:
                # 关机桌面：cpuRate/memRate 为 null（真实平台行为）
                return {"success": True, "errorCode": 0,
                        "data": {"cpuRate": None, "memRate": None, "status": 3}}
            return {"success": True, "errorCode": 0,
                    "data": {"cpuRate": float((i * 2) % 100), "memRate": 40.0}}

    worker = HealthWorker(FakeClient(), max_concurrency=5)
    collected: list = []
    worker.health_ready.connect(collected.append)
    worker.collect()
    qapp.processEvents()

    assert len(collected) == 1
    rows = collected[0]
    assert len(rows) == TOTAL, "行数 = 清单数（含 15 台关机）"
    assert sum(1 for r in rows if r.vm_cpu is not None) == POWERED_ON
    assert sum(1 for r in rows if r.vm_cpu is None) == TOTAL - POWERED_ON

    panel = HealthPanel(threshold_cpu=85, threshold_mem=85)
    panel.set_rows(rows)
    assert panel.table.rowCount() == TOTAL

    dash_rows = [r for r in range(TOTAL)
                 if row_display_text(panel.table, r).split("\t")[5] == "-"]
    assert len(dash_rows) == TOTAL - POWERED_ON, "关机桌面三列显示 '-'"
    for r in dash_rows:
        for col in (5, 6, 7):
            assert "#E53935" not in panel.table.cellWidget(r, col).styleSheet()

    breach = [r for r in range(TOTAL)
              if panel.table.cellWidget(r, 5).styleSheet() == THRESHOLD_BREACH_STYLE]
    assert breach == [43, 44], "(i*2)%100 ≥85 的桌面（86%/88%）标红"
    for r in breach:
        assert rows[r].vm_cpu >= 85
    panel.close()
