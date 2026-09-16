"""表格通用能力（复制 + 可拖拽列宽），告警页与健康页共用。

- ``install_copy_support``：Ctrl+C 复制当前单元格；右键菜单「复制单元格 / 复制整行」
- ``ElasticColumnResizer``：所有列 Interactive（可拖拽），弹性列默认吃掉剩余宽度，
  用户一旦手动拖拽该列则尊重用户设定，不再自动调整
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QHeaderView, QLabel, QMenu, QTableWidget, QAction,
)


# ----------------------------------------------------------------------
# 复制
# ----------------------------------------------------------------------
def cell_display_text(table: QTableWidget, row: int, col: int) -> str:
    """取单元格「显示文本」：优先 item；无 item 时取单元格控件中的 QLabel 文本。

    （等级徽章 / 阈值单元格是 QWidget 而非 QTableWidgetItem，需要兼容。）
    """
    item = table.item(row, col)
    if item is not None:
        return item.text()
    widget = table.cellWidget(row, col)
    if widget is None:
        return ""
    if isinstance(widget, QLabel):
        return widget.text()
    return " ".join(lbl.text() for lbl in widget.findChildren(QLabel) if lbl.text())


def row_display_text(table: QTableWidget, row: int, sep: str = "\t") -> str:
    """整行文本（各列显示文本按 sep 连接，可直接粘贴到 Excel）。"""
    return sep.join(
        cell_display_text(table, row, col) for col in range(table.columnCount()))


def copy_cell(table: QTableWidget) -> bool:
    """复制当前单元格；行/列无效时返回 False。"""
    row, col = table.currentRow(), table.currentColumn()
    if row < 0:
        return False
    text = cell_display_text(table, row, col) if col >= 0 else row_display_text(table, row)
    if not text:
        return False
    QApplication.clipboard().setText(text)
    return True


def copy_row(table: QTableWidget) -> bool:
    """复制当前整行。"""
    row = table.currentRow()
    if row < 0:
        return False
    text = row_display_text(table, row)
    if not text:
        return False
    QApplication.clipboard().setText(text)
    return True


def install_copy_support(table: QTableWidget) -> None:
    """为表格安装复制能力（Ctrl+C + 右键菜单）。"""
    act_cell = QAction("复制单元格", table)
    act_cell.setShortcut(QKeySequence.Copy)
    act_cell.triggered.connect(lambda: copy_cell(table))
    act_row = QAction("复制整行", table)
    act_row.setShortcut(QKeySequence("Ctrl+Shift+C"))
    act_row.triggered.connect(lambda: copy_row(table))
    table.addActions([act_cell, act_row])

    table.setContextMenuPolicy(Qt.CustomContextMenu)
    table.customContextMenuRequested.connect(lambda pos: _popup_menu(table, pos))


def _popup_menu(table: QTableWidget, pos) -> None:
    index = table.indexAt(pos)
    if index.isValid():
        table.setCurrentIndex(index)
    menu = QMenu(table)
    menu.addAction("复制单元格", lambda: copy_cell(table), QKeySequence.Copy)
    menu.addAction("复制整行", lambda: copy_row(table))
    menu.exec_(table.viewport().mapToGlobal(pos))


# ----------------------------------------------------------------------
# 列宽：全部可拖拽 + 弹性列
# ----------------------------------------------------------------------
class ElasticColumnResizer(QObject):
    """所有列设为 Interactive（可拖拽调宽），弹性列自动填充剩余宽度。

    用户手动拖拽弹性列后 ``auto_stretch`` 置 False，此后完全尊重用户设定。
    拖拽其它列时弹性列仍会自动吸收/让出宽度。
    """

    def __init__(self, table: QTableWidget, elastic_col: int,
                 min_width: int = 120, parent: QObject | None = None) -> None:
        super().__init__(parent or table)
        self._table = table
        self._col = elastic_col
        self._min = min_width
        self._auto = True
        self._programmatic = False

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.sectionResized.connect(self._on_section_resized)
        table.installEventFilter(self)
        self.apply()
        # 延迟到事件循环处理完首帧布局后再计算一次，规避构造时视口宽度尚未就绪
        QTimer.singleShot(0, self.apply)

    @property
    def auto_stretch(self) -> bool:
        return self._auto

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._table and event.type() in (QEvent.Resize, QEvent.Show):
            # 延迟到当前布局/几何计算完成后再 apply，确保拿到的是最终视口宽度，
            # 避免「窗口刚显示/刚放大」时视口宽度还是旧值导致的右侧留白
            QTimer.singleShot(0, self.apply)
        return False

    def _on_section_resized(self, logical: int, _old: int, _new: int) -> None:
        if self._programmatic:
            return                      # apply() 自身触发的调整，不参与用户意图判定
        if logical == self._col:
            self._auto = False          # 用户直接拖拽弹性列 → 交出控制权
        else:
            # 拖拽其它列时，弹性列重新吸收/让出宽度，避免总宽溢出或留白
            self.apply()

    def apply(self) -> None:
        """把弹性列拉伸到填满视口剩余宽度（仅在其未被用户手动拖拽时）。"""
        if not self._auto:
            return
        table = self._table
        count = table.columnCount()
        if count == 0 or self._col >= count:
            return
        # 视口尚未就绪（尚未显示 / 布局未完成）时宽度可能为 0，
        # 直接返回，等下一次 Show/Resize 事件驱动重新计算，避免写入错误宽度
        vw = table.viewport().width()
        if vw <= 0:
            return
        others = sum(table.columnWidth(c) for c in range(count) if c != self._col)
        width = max(self._min, vw - others)
        if width == table.columnWidth(self._col):
            return
        self._programmatic = True
        try:
            table.setColumnWidth(self._col, width)
        finally:
            self._programmatic = False
