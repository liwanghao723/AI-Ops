"""模块二 桌面健康监控（对应架构 T17 / ui/health_panel.py）。

**桌面级口径**（v1.0.0-health）：

- 表格列：名称 / IP / 操作系统 / 状态 / 所在主机 / 桌面CPU% / 桌面内存% / 桌面磁盘%
- 性能取**单台虚拟机自身**指标（2.27.31 批量接口），按 uuid 关联
- 关机桌面（2.27.31 无数据）保留行，三列显示 "-"，不着红、不计入预警
- CPU/内存/磁盘 ≥ 阈值（默认 85%）单元格红色加粗
- 一键刷新 + 自动刷新（默认 **30s** 自动开启，可选 关闭 / 10 / 30 / 60 / 120s）+ 最后刷新时间
- 选中某行 → "AI 解读"
"""

from __future__ import annotations

import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QMessageBox, QGroupBox, QComboBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

from core.models import HealthRow
from .table_utils import ElasticColumnResizer, install_copy_support
from .widgets import ThresholdCell, status_badge_cell
from .analysis_dialog import show_analysis_dialog


# 自动刷新下拉：默认 30s 自动开启（可在下拉切换为 关闭 / 10 / 30 / 60 / 120s）
REFRESH_OPTIONS = ["关闭", "10s", "30s", "60s", "120s"]
REFRESH_OFF = "关闭"
DEFAULT_REFRESH_SEC = 30

# 采集超时兜底：防止接口异常无回包时「刷新中」状态卡死
_REFRESH_GUARD_MS = 120_000

TITLE_COL = 0
IP_COL = 1
OS_COL = 2
STATUS_COL = 3
HOST_COL = 4            # 所在主机（2.4.7 主机名，hostId 关联解析）
CPU_COL = 5
MEM_COL = 6
DISK_COL = 7

HEADERS = ["名称", "IP", "操作系统", "状态", "所在主机",
           "桌面CPU%", "桌面内存%", "桌面磁盘%"]

COLUMN_WIDTHS = [150, 120, 110, 90, 130, 96, 96, 80]

NO_DATA = "-"


def _fmt_pct(value: float | None) -> str:
    """百分比格式化：None（关机/无数据）显示 "-"。"""
    if value is None:
        return NO_DATA
    return f"{value:.0f}%"


def _status_display(status: str) -> tuple[str, str, str]:
    """把 API 原始状态串映射为（中文文案, 前景色, 背景色），供状态列彩色徽标使用。

    覆盖 H3C 实测取值：``running`` → 运行中（绿），``shutOff``/``shutoff``/数值关机等
    → 已关机（灰）。未知值保留原文、用中性灰，确保任何状态都不漏标、不出错。
    """
    s = (status or "").strip().lower()
    if s in ("running", "1"):
        return ("运行中", "#0f7a3d", "#e6f4ea")
    if s in ("shutoff", "shut", "shutdown", "stopped", "poweredoff",
             "poweroff", "2", "3", "0"):
        return ("已关机", "#5b6472", "#eaecef")
    if s in ("suspend", "suspending", "suspended"):
        return ("已挂起", "#9a6b00", "#fdf3e0")
    if s in ("starting", "booting"):
        return ("启动中", "#1d4ed8", "#e6effd")
    if s in ("rebooting", "restarting"):
        return ("重启中", "#9a6b00", "#fdf3e0")
    return (status or "未知", "#5b6472", "#eaecef")


def _status_priority(status: str) -> int:
    """状态排序优先级：running 最小（升序时置顶），未知值最大（排最后）。"""
    s = (status or "").strip().lower()
    if s in ("running", "1"):
        return 0
    if s in ("shutoff", "shut", "shutdown", "stopped", "poweredoff",
             "poweroff", "2", "3", "0", ""):
        return 1
    if s in ("suspend", "suspending", "suspended", "starting", "booting",
             "rebooting", "restarting"):
        return 2
    return 3


def _status_category(status: str) -> str:
    """把状态归类为 ``running`` / ``shutoff`` / ``other``，供顶部概览统计使用。

    与 ``_status_display`` 判定同源：running/1 → running；
    shutOff/关机/stopped/poweredoff/2/3/0 → shutoff；其余（挂起/启动中/未知）→ other。
    """
    s = (status or "").strip().lower()
    if s in ("running", "1"):
        return "running"
    if s in ("shutoff", "shut", "shutdown", "stopped", "poweredoff",
             "poweroff", "2", "3", "0"):
        return "shutoff"
    return "other"


class HealthPanel(QWidget):
    request_explain = pyqtSignal(HealthRow)
    # 请求刷新（手动点击与自动刷新统一出口），由 MainWindow 接到 HealthWorker
    request_refresh = pyqtSignal()

    def __init__(self, threshold_cpu: int = 85, threshold_mem: int = 85, parent=None):
        super().__init__(parent)
        self._threshold_cpu = int(threshold_cpu)
        self._threshold_mem = int(threshold_mem)
        self._rows: list[HealthRow] = []
        self._status_sort_dir = None     # 状态列排序方向：None/升序/降序
        self._pending = False            # 采集进行中（防重复触发）
        self._guard_timer = QTimer(self)  # 采集超时兜底解锁
        self._guard_timer.setSingleShot(True)
        self._timer = QTimer(self)        # 自动刷新定时器
        self._timer.setSingleShot(False)
        self._build()

    # ------------------------------------------------------------------
    # 阈值（property：任何改动都立即重绘，避免与 AI 解读判定脱节）
    # ------------------------------------------------------------------
    @property
    def threshold_cpu(self) -> int:
        """CPU / 磁盘阈值（%）。"""
        return self._threshold_cpu

    @threshold_cpu.setter
    def threshold_cpu(self, value: int) -> None:
        self._threshold_cpu = int(value)
        self._render_rows()

    @property
    def threshold_mem(self) -> int:
        """内存阈值（%）。"""
        return self._threshold_mem

    @threshold_mem.setter
    def threshold_mem(self, value: int) -> None:
        self._threshold_mem = int(value)
        self._render_rows()

    def set_thresholds(self, threshold_cpu: int, threshold_mem: int) -> None:
        """批量更新阈值并**只重绘一次**（配置热更新的推荐入口）。

        逐个赋值会触发两次重绘，配置保存场景请用本方法。
        """
        self._threshold_cpu = int(threshold_cpu)
        self._threshold_mem = int(threshold_mem)
        self._render_rows()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(10)

        box = QGroupBox("桌面资源概览（桌面级指标）")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 14, 12, 10)
        box_layout.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.btn_refresh = QPushButton("一键刷新")
        self.btn_refresh.setFixedWidth(110)
        self.lbl_last = QLabel("最后刷新：-")
        self.lbl_last.setObjectName("HintText")
        top.addWidget(self.btn_refresh)
        top.addWidget(self.lbl_last)
        # 顶部概览统计：桌面总数 / 运行中 / 已关机（不影响表格与其它功能）
        self.lbl_stats = QLabel("桌面总数：0 ｜ 运行中：0 ｜ 已关机：0")
        self.lbl_stats.setObjectName("HintText")
        top.addWidget(self.lbl_stats)
        top.addStretch(1)
        box_layout.addLayout(top)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(30)
        self.table.horizontalHeader().setFixedHeight(32)
        for col, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(col, width)
        # 所有列可拖拽；末列（磁盘%）默认弹性，手动拖拽后尊重用户设定
        self._resizer = ElasticColumnResizer(self.table, DISK_COL, min_width=90)
        install_copy_support(self.table)
        # 状态列（第 3 列）支持点击切换升/降序
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._init_status_header()
        box_layout.addWidget(self.table, 1)

        op = QHBoxLayout()
        op.setContentsMargins(0, 0, 0, 0)
        op.setSpacing(8)
        self.btn_ai = QPushButton("AI 解读")
        self.btn_ai.setFixedWidth(110)
        self.btn_ai.clicked.connect(self._on_ai)
        op.addWidget(self.btn_ai)
        op.addStretch(1)
        # 自动刷新（默认关闭）与最后刷新时间
        self.cb_refresh = QComboBox()
        self.cb_refresh.addItems(REFRESH_OPTIONS)
        self.cb_refresh.setCurrentText(f"{DEFAULT_REFRESH_SEC}s")
        self.cb_refresh.setFixedWidth(88)
        op.addWidget(QLabel("自动刷新"))
        op.addWidget(self.cb_refresh)
        box_layout.addLayout(op)

        layout.addWidget(box, 1)

        self.btn_refresh.clicked.connect(self.trigger_refresh)
        self.cb_refresh.currentTextChanged.connect(self._on_refresh_option_changed)
        self._timer.timeout.connect(self.trigger_refresh)
        self._guard_timer.timeout.connect(self.release_refresh_lock)
        # 默认开启 30s 自动刷新（用户可在下拉切换为关闭 / 其它间隔）
        self._apply_refresh_interval(DEFAULT_REFRESH_SEC)

    # ------------------------------------------------------------------
    # 数据填充（桌面级口径：vm_cpu / vm_mem / vm_disk）
    # ------------------------------------------------------------------
    def set_rows(self, rows: list[HealthRow]) -> None:
        """接收 Worker 采集结果并渲染（唯一的「数据更新」入口）。"""
        self._rows = list(rows or [])
        self.release_refresh_lock()
        self._render_rows()
        self._update_stats()
        self.lbl_last.setText(f"最后刷新：{datetime.datetime.now():%H:%M:%S}")

    def _update_stats(self) -> None:
        """刷新顶部「桌面总数 / 运行中 / 已关机」概览（不影响表格与其它功能）。"""
        total = len(self._rows)
        running = shutoff = 0
        for row in self._rows:
            cat = _status_category(row.status)
            if cat == "running":
                running += 1
            elif cat == "shutoff":
                shutoff += 1
        self.lbl_stats.setText(
            f"桌面总数：{total} ｜ 运行中：{running} ｜ 已关机：{shutoff}")

    def _visible_rows(self) -> list[HealthRow]:
        """当前展示顺序：未排序 → 原始采集顺序；已排序 → 按状态优先级排。

        采用「先按标题稳定排序、再按状态优先级排」的两段式，保证同状态组内
        标题仍保持 A-Z，排序结果与 ``self._rows`` 下标解耦（选中/AI 解读据此取行）。
        """
        if self._status_sort_dir is None:
            return self._rows
        rows = sorted(self._rows, key=lambda r: (r.title or ""))
        rows.sort(key=lambda r: _status_priority(r.status),
                  reverse=(self._status_sort_dir == Qt.DescendingOrder))
        return rows

    def _render_rows(self) -> None:
        """按当前阈值重绘表格（数据为最近一次采集的 ``self._rows``）。

        阈值变更（property setter / ``set_thresholds``）与数据变更（``set_rows``）
        共用本方法，保证表格着色始终与 ``Analyzer`` 的判定同源。
        """
        table = getattr(self, "table", None)
        if table is None:      # 构造期（_build 之前）调用：忽略
            return
        rows = self._visible_rows()
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            table.setItem(r, TITLE_COL, QTableWidgetItem(row.title))
            table.setItem(r, IP_COL, QTableWidgetItem(row.ip))
            table.setItem(r, OS_COL, QTableWidgetItem(row.os))
            # 状态列：彩色徽标（running 绿 / 已关机 灰），保留隐藏 item 文本供复制/选中
            label, fg, bg = _status_display(row.status)
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(r, STATUS_COL, item)
            table.setCellWidget(r, STATUS_COL, status_badge_cell(label, fg, bg))
            # 所在主机：优先主机名，无则回退裸 hostId，再无则 "-"
            table.setItem(
                r, HOST_COL,
                QTableWidgetItem(row.host_name
                                 or (str(row.host_id) if row.host_id else NO_DATA)))
            # None（关机 / 无数据）→ "-" 且不着红
            table.setCellWidget(
                r, CPU_COL, ThresholdCell(row.vm_cpu, self.threshold_cpu))
            table.setCellWidget(
                r, MEM_COL, ThresholdCell(row.vm_mem, self.threshold_mem))
            table.setCellWidget(
                r, DISK_COL, ThresholdCell(row.vm_disk, self.threshold_cpu))

    # ------------------------------------------------------------------
    # 刷新（手动 / 自动）
    # ------------------------------------------------------------------
    def trigger_refresh(self) -> None:
        """发起一次采集请求（手动点击 / 自动刷新共用出口）。

        上一次采集未返回时忽略重复触发（去重），并禁用按钮避免连点；
        数据回来（``set_rows``）、采集失败（``release_refresh_lock``）或
        超时（``_REFRESH_GUARD_MS``）后自动解锁。
        """
        if self._pending:
            return                      # 上一次采集尚未返回，忽略重复触发
        self._pending = True
        self.btn_refresh.setEnabled(False)
        self.lbl_last.setText("最后刷新：采集中…")
        self._guard_timer.start(_REFRESH_GUARD_MS)
        self.request_refresh.emit()

    def refresh_interval(self) -> int | None:
        """当前自动刷新间隔（秒）；None 表示关闭。"""
        return _parse_refresh_text(self.cb_refresh.currentText())

    def set_refresh_interval(self, sec: int | None) -> None:
        """按给定间隔同步下拉框并启/停定时器（None / 0 → 关闭）。"""
        text = f"{sec}s" if sec in (10, 30, 60, 120) else REFRESH_OFF
        idx = self.cb_refresh.findText(text)
        if idx >= 0:
            self.cb_refresh.setCurrentIndex(idx)
        else:
            self._apply_refresh_interval(None if sec is None else int(sec))

    def _on_refresh_option_changed(self, text: str) -> None:
        self._apply_refresh_interval(_parse_refresh_text(text))

    def _apply_refresh_interval(self, sec: int | None) -> None:
        if sec is None:
            self._timer.stop()
            return
        self._timer.setInterval(sec * 1000)
        self._timer.start()

    def release_refresh_lock(self) -> None:
        """采集完成 / 失败 / 超时后解锁刷新按钮（供外部与内部调用）。"""
        self._guard_timer.stop()
        self._pending = False
        self.btn_refresh.setEnabled(True)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _selected_row(self) -> HealthRow | None:
        r = self.table.currentRow()
        rows = self._visible_rows()
        if r < 0 or r >= len(rows):
            return None
        return rows[r]

    # ------------------------------------------------------------------
    # 状态列排序（点击表头在升序 / 降序间切换，表头显示 ▲/▼ 指示）
    # ------------------------------------------------------------------
    def _init_status_header(self) -> None:
        item = self.table.horizontalHeaderItem(STATUS_COL)
        if item is None:
            return
        item.setText("状态")
        item.setToolTip("点击切换升序 / 降序")

    def _update_status_header(self) -> None:
        item = self.table.horizontalHeaderItem(STATUS_COL)
        if item is None:
            return
        arrow = ""
        if self._status_sort_dir == Qt.AscendingOrder:
            arrow = " ▲"
        elif self._status_sort_dir == Qt.DescendingOrder:
            arrow = " ▼"
        item.setText(f"状态{arrow}")
        item.setToolTip("点击切换升序 / 降序")

    def _on_header_clicked(self, logical_index: int) -> None:
        if logical_index != STATUS_COL:
            return
        if self._status_sort_dir is None:
            self._status_sort_dir = Qt.AscendingOrder
        elif self._status_sort_dir == Qt.AscendingOrder:
            self._status_sort_dir = Qt.DescendingOrder
        else:
            self._status_sort_dir = Qt.AscendingOrder
        self._render_rows()
        self._update_status_header()

    def _on_ai(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "提示", "请先选择一行")
            return
        self.request_explain.emit(row)

    def show_explain(self, text: str) -> None:
        """健康解读结果同样用结构化弹窗展示（与告警页风格一致）。"""
        row = self._selected_row()
        summary = []
        if row is not None:
            summary = [
                ("云桌面", row.title),
                ("IP", row.ip),
                ("操作系统", row.os),
                ("状态", _status_display(row.status)[0]),
                ("所在主机", row.host_name or (str(row.host_id) if row.host_id else "-")),
                ("桌面CPU", _fmt_pct(row.vm_cpu)),
                ("桌面内存", _fmt_pct(row.vm_mem)),
                ("桌面磁盘", _fmt_pct(row.vm_disk)),
            ]
        show_analysis_dialog(self, "AI 健康解读", summary, text,
                             body_title="AI 解读")


def _parse_refresh_text(text: str) -> int | None:
    """把下拉文案（"关闭"/"10s"/"30s"...）解析为秒；"关闭" → None。"""
    text = (text or "").strip()
    if text == REFRESH_OFF or not text.endswith("s"):
        return None
    try:
        sec = int(text[:-1])
    except ValueError:
        return None
    return sec if sec > 0 else None
