"""AI 分析结果结构化弹窗（ui 层）。

卡片式分区：
  1. 概要（名称 / 级别 / 状态 / 时间 …）
  2. AI 分析（正文，纯文本换行分段）
  3. 处理建议 / 参考（可按 AI 输出拆分时独立成区）
失败时：一句中文说明 + 可折叠的原始错误详情，绝不把英文堆栈作为主体。
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QTextEdit,
    QPushButton, QWidget,
)
from PyQt5.QtCore import Qt

from .widgets import CardFrame
from .error_messages import classify_error, extract_raw_detail, is_error_text, split_sections


class AnalysisDialog(QDialog):
    """AI 分析结果弹窗（告警详情 / 健康解读 / 问答结果通用）。"""

    def __init__(self, title: str = "AI 分析",
                 summary: list | None = None,
                 analysis_text: str = "",
                 body_title: str = "AI 分析",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        summary = summary or []
        if summary:
            root.addWidget(self._build_summary_card(summary))

        text = analysis_text or ""
        if is_error_text(text):
            root.addWidget(self._build_error_card(text), 1)
        else:
            main, suggest = split_sections(text)
            root.addWidget(self._build_text_card(body_title, main), 1)
            if suggest:
                root.addWidget(self._build_text_card("处理建议 / 参考", suggest), 1)

        # 底部操作
        ops = QHBoxLayout()
        ops.addStretch(1)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setProperty("cssClass", "secondary")
        self.btn_close.setFixedWidth(96)
        self.btn_close.clicked.connect(self.accept)
        ops.addWidget(self.btn_close)
        root.addLayout(ops)

    # ---- 卡片构建 ----
    def _build_summary_card(self, summary: list) -> QWidget:
        card = CardFrame("概要")
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for r, pair in enumerate(summary):
            label, value = pair
            lbl = QLabel(f"{label}")
            lbl.setObjectName("CardLabel")
            lbl.setFixedWidth(60)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            grid.addWidget(lbl, r, 0)
            if isinstance(value, QWidget):
                grid.addWidget(value, r, 1)
            else:
                val = QLabel(str(value))
                val.setObjectName("CardValue")
                val.setWordWrap(True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(val, r, 1)
        grid.setColumnStretch(1, 1)
        card.body().addLayout(grid)
        return card

    def _build_text_card(self, title: str, text: str) -> QWidget:
        card = CardFrame(title)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(text or "（无内容）")
        te.setMinimumHeight(90)
        card.body().addWidget(te)
        return card

    def _build_error_card(self, raw: str) -> QWidget:
        title, msg = classify_error(raw)
        card = CardFrame("AI 分析")
        lbl_title = QLabel(f"⚠ {title}")
        lbl_title.setObjectName("ErrorTitle")
        lbl_msg = QLabel(msg)
        lbl_msg.setObjectName("CardValue")
        lbl_msg.setWordWrap(True)
        card.body().addWidget(lbl_title)
        card.body().addWidget(lbl_msg)

        detail = extract_raw_detail(raw)
        if detail:
            self.btn_detail = QPushButton("查看原始错误详情")
            self.btn_detail.setProperty("cssClass", "secondary")
            self.btn_detail.setCheckable(True)
            self.btn_detail.setFixedWidth(160)
            self.te_detail = QTextEdit()
            self.te_detail.setReadOnly(True)
            self.te_detail.setPlainText(detail)
            self.te_detail.setMaximumHeight(120)
            self.te_detail.setVisible(False)
            self.btn_detail.toggled.connect(self.te_detail.setVisible)
            self.btn_detail.toggled.connect(
                lambda on: self.btn_detail.setText(
                    "收起原始错误详情" if on else "查看原始错误详情"))
            card.body().addWidget(self.btn_detail)
            card.body().addWidget(self.te_detail)
        return card


def show_analysis_dialog(parent: QWidget | None, title: str, summary: list,
                         text: str, body_title: str = "AI 分析") -> None:
    """便捷入口：构建并模态展示分析弹窗。"""
    dlg = AnalysisDialog(title=title, summary=summary, analysis_text=text,
                         body_title=body_title, parent=parent)
    dlg.exec_()
