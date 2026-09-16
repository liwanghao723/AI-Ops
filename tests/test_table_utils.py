"""ui.table_utils.ElasticColumnResizer 列宽填充测试（离屏 Qt）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt5.QtWidgets import QApplication, QTableWidget

from ui.table_utils import ElasticColumnResizer


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_elastic_resizer_fills_remaining_width() -> None:
    """描述列（弹性列）应吃掉视口剩余宽度，使表格填满、无右侧留白。"""
    _app()
    table = QTableWidget(0, 3)
    table.setColumnWidth(0, 100)
    table.setColumnWidth(1, 100)
    resizer = ElasticColumnResizer(table, 2, min_width=50)
    table.show()
    table.resize(600, 400)
    QApplication.processEvents()
    resizer.apply()

    vw = table.viewport().width()
    others = table.columnWidth(0) + table.columnWidth(1)
    expected = max(50, vw - others)
    assert table.columnWidth(2) == expected
    assert table.columnWidth(2) >= 50


def test_elastic_resizer_refills_after_resize() -> None:
    """窗口放大后，弹性列应自动重新吸收剩余宽度。"""
    _app()
    table = QTableWidget(0, 3)
    table.setColumnWidth(0, 100)
    table.setColumnWidth(1, 100)
    resizer = ElasticColumnResizer(table, 2, min_width=50)
    table.show()
    table.resize(400, 300)
    QApplication.processEvents()
    resizer.apply()
    before = table.columnWidth(2)

    table.resize(900, 300)
    QApplication.processEvents()
    resizer.apply()

    vw = table.viewport().width()
    assert table.columnWidth(2) == max(50, vw - 200)
    assert table.columnWidth(2) > before  # 放大后弹性列变宽


def test_elastic_resizer_user_drag_disables_auto() -> None:
    """用户手动拖拽弹性列后，auto_stretch 置 False，不再自动调整。"""
    _app()
    table = QTableWidget(0, 3)
    table.setColumnWidth(0, 100)
    table.setColumnWidth(1, 100)
    resizer = ElasticColumnResizer(table, 2, min_width=50)
    table.show()
    table.resize(600, 400)
    QApplication.processEvents()

    # 模拟用户拖拽弹性列（真实拖拽走 setColumnWidth，会触发 sectionResized）
    table.setColumnWidth(2, 300)
    assert resizer.auto_stretch is False

    # 即使视口变化，apply 也不再改写弹性列宽度
    table.resize(900, 400)
    QApplication.processEvents()
    resizer.apply()
    assert table.columnWidth(2) == 300


def test_elastic_resizer_skips_when_viewport_not_ready() -> None:
    """视口尚未就绪（viewport 宽度 <= 0）时 apply 应提前返回，不写任何宽度。"""
    _app()
    table = QTableWidget(0, 3)
    table.setColumnWidth(0, 100)
    table.setColumnWidth(1, 100)
    table.setColumnWidth(2, 200)  # 预置初值，用于检测是否被误写
    resizer = ElasticColumnResizer(table, 2, min_width=50)
    resizer.apply()  # 先拿到一个基线宽度
    before = table.columnWidth(2)

    # 强制视口宽度为 0（未布局 / 未显示场景），验证守卫分支
    with patch.object(table.viewport(), "width", return_value=0):
        resizer.apply()

    assert table.columnWidth(2) == before  # 守卫令 apply 直接返回，未写 0/负数


def test_elastic_resizer_never_writes_zero_or_negative() -> None:
    """窗口极窄（其它列之和已超过视口）时，弹性列应取 min_width，绝不写 0/负数。"""
    _app()
    table = QTableWidget(0, 3)
    table.setColumnWidth(0, 300)
    table.setColumnWidth(1, 300)
    resizer = ElasticColumnResizer(table, 2, min_width=140)
    table.show()
    table.resize(50, 50)  # 视口极窄
    QApplication.processEvents()
    resizer.apply()

    width = table.columnWidth(2)
    assert width > 0
    assert width >= 140  # 被 min_width 兜住，而非写成 0/负数
