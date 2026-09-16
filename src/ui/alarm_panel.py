"""模块一 智能告警中心（对应架构 T17 / ui/alarm_panel.py）。

- 筛选栏：等级 / 状态 / 关键字 / 时间起止 / 查询 / 自动刷新间隔
- 过滤基于已拉取的真实告警数据，等级 + 状态 + 关键字 + 时间可组合
  （纯函数见 ``ui.alarm_filters.filter_alarms``）
- 列表按 eventLevel 配色（§7.5）：紧凑徽章 + 斑马纹 + hover + 强选中
- 表格支持 Ctrl+C / 右键复制单元格与整行；列宽可拖拽
- 点击告警 → "AI 分析" 弹出「概要 + AI 分析 + 处理建议」卡片式弹窗
"""

from __future__ import annotations

import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QLineEdit, QPushButton, QLabel, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QEvent, QTimer

from core.models import WarnInfoDTO
from .styles import level_text
from .alarm_filters import LEVEL_LABELS, STATE_LABELS, filter_alarms, state_text
from .table_utils import ElasticColumnResizer, install_copy_support
from .widgets import LevelBadge, level_badge_cell
from .analysis_dialog import show_analysis_dialog


def _fmt_time(ms: int) -> str:
    if not ms:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def _fmt_full_time(ms: int) -> str:
    if not ms:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


class AlarmPanel(QWidget):
    # 请求对单条告警做 AI 分析
    request_analyze = pyqtSignal(WarnInfoDTO)

    LEVEL_COL = 0
    NAME_COL = 1
    DESC_COL = 2
    FIRST_COL = 3      # 首次告警时间
    LAST_COL = 4       # 最新告警时间（原 TIME_COL）
    COUNT_COL = 5      # 重复次数
    STATE_COL = 6
    TIME_COL = LAST_COL  # 向后兼容别名：历史测试（test_alarm_filters）仍引用 AlarmPanel.TIME_COL

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._alarms: list[WarnInfoDTO] = []    # 拉取到的全量告警
        self._visible: list[WarnInfoDTO] = []   # 当前过滤后、与表格行一一对应
        self._analysis_text: str | None = None
        self._build()

    def showEvent(self, event: QEvent) -> None:
        """面板显示（含首次显示与 Tab 切换到本页）后，延迟重算弹性列宽度。

        规避「构造 / Tab 切换瞬间视口宽度尚未就绪」导致的表格右侧留白。
        """
        super().showEvent(event)
        if getattr(self, "_resizer", None) is not None:
            QTimer.singleShot(0, self._resizer.apply)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(10)

        # 筛选栏（控件高度 / 间距统一，查询按钮主色）
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.cb_level = QComboBox()
        self.cb_level.addItems(LEVEL_LABELS)
        self.cb_level.setFixedWidth(96)
        self.cb_state = QComboBox()
        self.cb_state.addItems(STATE_LABELS)
        self.cb_state.setCurrentText("未确认")  # 默认只展示未确认告警
        self.cb_state.setFixedWidth(96)
        self.le_keyword = QLineEdit(); self.le_keyword.setPlaceholderText("关键字")
        self.le_keyword.setFixedWidth(170)
        self.le_from = QLineEdit(); self.le_from.setPlaceholderText("起(yyyy-mm-dd)")
        self.le_from.setFixedWidth(126)
        self.le_to = QLineEdit(); self.le_to.setPlaceholderText("止(yyyy-mm-dd)")
        self.le_to.setFixedWidth(126)
        self.btn_query = QPushButton("查询")
        self.btn_query.setFixedWidth(80)
        self.cb_limit = QComboBox()
        self.cb_limit.addItems(["不限制", "1000", "5000", "10000", "20000", "50000"])
        # 默认「不限制」：平台真实告警总量可能上万，任何固定上限都会截断
        # 并漏掉最旧的未确认告警（历史 Bug：上限 10000 只显示 10 条 / 平台 47 条）
        self.cb_limit.setCurrentText("不限制")
        self.cb_limit.setFixedWidth(96)
        self.lbl_count = QLabel("共 0 条")
        self.lbl_count.setObjectName("HintText")
        # 平台顶部「未确认告警角标」（与平台网页红点同口径，用于核对总数）
        self.lbl_badge = QLabel("")
        self.lbl_badge.setObjectName("HintText")
        self.cb_refresh = QComboBox()
        self.cb_refresh.addItems(["关闭", "10s", "30s", "60s", "120s"])
        self.cb_refresh.setCurrentText("30s")
        self.cb_refresh.setFixedWidth(88)
        self.lbl_last = QLabel("最后刷新：-")
        self.lbl_last.setObjectName("HintText")
        bar.addWidget(QLabel("等级")); bar.addWidget(self.cb_level)
        bar.addWidget(QLabel("状态")); bar.addWidget(self.cb_state)
        bar.addWidget(self.le_keyword); bar.addWidget(self.le_from)
        bar.addWidget(self.le_to); bar.addWidget(self.btn_query)
        bar.addWidget(QLabel("上限")); bar.addWidget(self.cb_limit)
        bar.addWidget(self.lbl_count)
        bar.addWidget(self.lbl_badge)
        bar.addStretch(1)
        layout.addLayout(bar)

        # 表格（NOC 风格：斑马纹 / hover / 强选中 / 描述列弹性伸展）
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["级别", "告警名称", "描述", "首次告警时间", "最新告警时间", "重复次数", "状态"])
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setFixedHeight(32)
        self.table.setColumnWidth(self.LEVEL_COL, 72)
        self.table.setColumnWidth(self.NAME_COL, 210)
        self.table.setColumnWidth(self.FIRST_COL, 112)
        self.table.setColumnWidth(self.LAST_COL, 112)
        self.table.setColumnWidth(self.COUNT_COL, 64)
        self.table.setColumnWidth(self.STATE_COL, 84)
        # 所有列可拖拽；描述列（2）默认弹性，手动拖拽后尊重用户设定
        self._resizer = ElasticColumnResizer(self.table, self.DESC_COL, min_width=140)
        install_copy_support(self.table)
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        # 操作按钮
        op = QHBoxLayout()
        op.setContentsMargins(0, 0, 0, 0)
        op.setSpacing(8)
        self.btn_ai = QPushButton("AI 分析")
        self.btn_ai.setFixedWidth(110)
        self.btn_ai.clicked.connect(self._on_ai)
        op.addWidget(self.btn_ai)
        op.addStretch(1)
        # 自动刷新与最后刷新移到操作行（筛选栏新增「状态」下行宽不足，避免控件互相挤压）
        op.addWidget(QLabel("自动刷新")); op.addWidget(self.cb_refresh)
        op.addWidget(self.lbl_last)
        layout.addLayout(op)

        self.cb_level.currentTextChanged.connect(self.apply_filters)
        self.cb_state.currentTextChanged.connect(self.apply_filters)
        self.le_keyword.returnPressed.connect(self.apply_filters)
        self.le_from.returnPressed.connect(self.apply_filters)
        self.le_to.returnPressed.connect(self.apply_filters)
        self.btn_query.clicked.connect(self.apply_filters)
        # 切换「上限」后顺带按新上限刷新（更直观：所见即所得）
        self.cb_limit.currentTextChanged.connect(self.apply_filters)

    # ---- 数据填充 ----
    def set_alarms(self, alarms: list[WarnInfoDTO]) -> None:
        """接收 Worker 拉取到的真实告警，按当前筛选条件渲染。"""
        self._alarms = list(alarms or [])
        self.lbl_last.setText(f"最后刷新：{datetime.datetime.now():%H:%M:%S}")
        self.apply_filters()

    def current_filters(self) -> dict:
        """从筛选栏读取当前过滤条件（供 filter_alarms 使用）。"""
        return {
            "level": self.cb_level.currentText(),
            "state": self.cb_state.currentText(),
            "keyword": self.le_keyword.text(),
            "date_from": self.le_from.text(),
            "date_to": self.le_to.text(),
        }

    def set_warn_count(self, counts: dict) -> None:
        """显示平台顶部「未确认告警角标」（与平台网页红点同口径）。

        用于核对「平台显示 N 条」与「软件拉到 N 条」是否一致；
        仅在启用前端会话（配置了 frontend_password）时才有值。

        Args:
            counts: ``{"urgent", "important", "accessory", "warning"}``。
        """
        if not counts:
            self.lbl_badge.setText("")
            return
        total = sum(int(v or 0) for v in counts.values())
        self.lbl_badge.setText(
            f"｜平台未确认 紧急{counts.get('urgent', 0)} "
            f"重要{counts.get('important', 0)} "
            f"次要{counts.get('accessory', 0)} "
            f"提示{counts.get('warning', 0)} 合计{total}")

    def current_cap(self) -> int | None:
        """读取当前「上限」下拉框对应的拉取上限。

        Returns:
            None 表示不限制；否则为对应整数（1000/5000/10000）。
        """
        text = self.cb_limit.currentText()
        if text == "不限制":
            return None
        return int(text)

    def apply_filters(self) -> list[WarnInfoDTO]:
        """按当前条件过滤已拉取的告警并刷新表格，返回可见告警列表。"""
        self._visible = filter_alarms(self._alarms, self.current_filters())
        self._render(self._visible)
        self.lbl_count.setText(f"共 {len(self._visible)} 条")
        return self._visible

    def _render(self, alarms: list[WarnInfoDTO]) -> None:
        self.table.setRowCount(len(alarms))
        for r, w in enumerate(alarms):
            self.table.setCellWidget(r, self.LEVEL_COL, level_badge_cell(w.eventLevel))
            self.table.setItem(r, self.NAME_COL, QTableWidgetItem(w.eventName))
            self.table.setItem(r, self.DESC_COL, QTableWidgetItem(w.eventDesc))
            first = QTableWidgetItem(_fmt_time(w.firstEventTime))
            first.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, self.FIRST_COL, first)
            last = QTableWidgetItem(_fmt_time(w.eventTime))
            last.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, self.LAST_COL, last)
            count = QTableWidgetItem(str(w.eventCount))
            count.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, self.COUNT_COL, count)
            s = QTableWidgetItem(state_text(w))
            s.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, self.STATE_COL, s)

    def set_refresh_interval(self, sec: int) -> None:
        """根据 Worker 当前间隔同步下拉框（避免与 Worker 不一致）。"""
        txt = f"{sec}s" if sec in (10, 30, 60, 120) else "关闭"
        idx = self.cb_refresh.findText(txt)
        if idx >= 0:
            self.cb_refresh.setCurrentIndex(idx)

    # ---- 交互 ----
    def _selected_warn(self) -> WarnInfoDTO | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._visible):
            return None
        return self._visible[row]

    def _on_double_click(self) -> None:
        self._on_ai()

    def _on_ai(self) -> None:
        warn = self._selected_warn()
        if warn is None:
            QMessageBox.information(self, "提示", "请先选择一条告警")
            return
        self.request_analyze.emit(warn)

    def show_analysis(self, text: str) -> None:
        """展示 AI 分析结果弹窗（概要 + AI 分析 + 处理建议）。"""
        self._analysis_text = text
        warn = self._selected_warn()
        summary: list = []
        if warn:
            summary = [
                ("名称", warn.eventName),
                ("级别", LevelBadge(warn.eventLevel)),
                ("状态", state_text(warn)),
                ("时间", _fmt_full_time(warn.eventTime)),
                ("描述", warn.eventDesc),
            ]
        else:
            summary = [("级别", level_text(0))]
        show_analysis_dialog(self, "告警详情 + AI 建议", summary, text)
