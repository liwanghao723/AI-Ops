"""模块三 知识库与联网分析（对应架构 T17 / ui/knowledge_panel.py）。

- 智能问答入口：输入问题 → 关键词 → RAG → 模型 →（必要时联网）回答
- 展示回答与 RAG 来源/联网报告（失败时展示友好中文提示）
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTextEdit, QLabel, QGroupBox, QDialog, QScrollArea, QFrame,
)
from PyQt5.QtCore import pyqtSignal, Qt, QUrl
from PyQt5.QtGui import QDesktopServices

from core.models import Chunk
from .error_messages import classify_error, extract_raw_detail, is_error_text


class KnowledgePanel(QWidget):
    request_answer = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(10)

        box = QGroupBox("智能问答（IMA 知识库 → 本地兜底 → 联网兜底）")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 14, 12, 10)
        box_layout.setSpacing(10)

        q = QHBoxLayout()
        q.setContentsMargins(0, 0, 0, 0)
        q.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入运维问题，回车或点击发送…")
        self.input.returnPressed.connect(self._on_send)
        self.btn_send = QPushButton("发送")
        self.btn_send.setFixedWidth(96)
        self.btn_send.clicked.connect(self._on_send)
        q.addWidget(self.input, 1)
        q.addWidget(self.btn_send)
        box_layout.addLayout(q)

        # 检索来源状态条：展示本次回答基于哪个知识库（IMA / 本地 / 联网兜底）
        # 有命中时可点击展开「命中片段 / 原文链接」详情
        self.source_bar = QLabel("")
        self.source_bar.setObjectName("AnswerSourceBar")
        self.source_bar.setProperty("cssClass", "source-hint")
        self.source_bar.setWordWrap(True)
        self.source_bar.setVisible(False)
        self.source_bar.setCursor(Qt.PointingHandCursor)
        self.source_bar.mousePressEvent = self._on_source_bar_clicked
        self._hit_chunks: "list[Chunk]" = []
        box_layout.addWidget(self.source_bar)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        box_layout.addWidget(self.output, 1)

        # 失败时的原始错误详情：默认折叠，与告警/健康页的 AnalysisDialog 保持一致，
        # 不把英文堆栈直接怼到用户眼前
        self.btn_detail = QPushButton("查看原始错误详情")
        self.btn_detail.setProperty("cssClass", "secondary")
        self.btn_detail.setCheckable(True)
        self.btn_detail.setFixedWidth(160)
        self.btn_detail.setVisible(False)
        self.te_detail = QTextEdit()
        self.te_detail.setReadOnly(True)
        self.te_detail.setMaximumHeight(110)
        self.te_detail.setVisible(False)
        self.btn_detail.toggled.connect(self.te_detail.setVisible)
        self.btn_detail.toggled.connect(
            lambda on: self.btn_detail.setText(
                "收起原始错误详情" if on else "查看原始错误详情"))
        box_layout.addWidget(self.btn_detail)
        box_layout.addWidget(self.te_detail)

        layout.addWidget(box, 1)

    def _on_send(self) -> None:
        q = self.input.text().strip()
        if not q:
            return
        self._set_error_detail(None)
        self.output.setPlainText("正在分析…")
        self.show_source("⏳ 正在检索知识库…", transient=True)
        self.request_answer.emit(q)

    def _set_error_detail(self, detail: str | None) -> None:
        """显示/隐藏原始错误详情（折叠区，默认不展开）。"""
        if detail:
            self.te_detail.setPlainText(detail)
            self.btn_detail.setVisible(True)
            self.btn_detail.setChecked(False)
            self.te_detail.setVisible(False)
        else:
            self.btn_detail.setVisible(False)
            self.btn_detail.setChecked(False)
            self.te_detail.setVisible(False)
            self.te_detail.clear()

    def show_hits(self, chunks: "list[Chunk]") -> None:
        """保存本次问答命中的知识片段，并在来源条追加「点击查看 N 条命中」提示。

        若 chunks 为空则不追加提示（来源条仍按 banner 文案展示）。
        """
        self._hit_chunks = list(chunks or [])
        if not self._hit_chunks:
            return
        cur = self.source_bar.text()
        if "· 点击查看" in cur:
            return
        self.source_bar.setText(f"{cur}  · 点击查看 {len(self._hit_chunks)} 条命中 ▾")

    def _on_source_bar_clicked(self, event) -> None:
        """点击来源条：有命中则弹出命中详情对话框；否则忽略。"""
        if event.button() == Qt.LeftButton and self._hit_chunks:
            self._open_hit_dialog(self._hit_chunks)

    def _open_hit_dialog(self, chunks: "list[Chunk]") -> None:
        """弹出命中片段详情：来源 / 相关度 / 片段全文 / 原文链接。"""
        import html
        dlg = QDialog(self)
        dlg.setWindowTitle("知识库命中片段")
        dlg.setMinimumSize(580, 440)
        dlg.setWindowModality(Qt.ApplicationModal)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(10)

        for i, c in enumerate(chunks, 1):
            card = QFrame()
            card.setObjectName("HitCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(5)

            src = QLabel(f"<b>{i}. {html.escape(c.source)}</b>")
            src.setWordWrap(True)
            cl.addWidget(src)

            score_txt = f"{c.score:.2f}" if c.score else "—"
            # IMA 对本平台的文档只返回标题（拿不到正文），此处明确标注，
            # 避免用户把「标题」误当成「正文片段」
            score = QLabel(
                f"相关度：{score_txt}（标题命中）" if c.is_title_hit
                else f"相关度：{score_txt}")
            score.setObjectName("HitScore")
            cl.addWidget(score)

            txt = QTextEdit()
            txt.setReadOnly(True)
            txt.setPlainText(c.text)
            txt.setMaximumHeight(150)
            cl.addWidget(txt)

            if c.url:
                link = QLabel(f'<a href="{html.escape(c.url)}">打开原文 ↗</a>')
                link.setOpenExternalLinks(True)
                link.setWordWrap(True)
                cl.addWidget(link)
            elif c.is_title_hit:
                note = QLabel("（IMA 仅返回文档标题，正文请在 IMA 中查看）")
                note.setObjectName("HitNote")
                cl.addWidget(note)
            else:
                note = QLabel("（无原文链接，仅片段预览）")
                note.setObjectName("HitNote")
                cl.addWidget(note)

            il.addWidget(card)

        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        btn = QPushButton("关闭")
        btn.setFixedWidth(96)
        btn.clicked.connect(dlg.accept)
        v.addWidget(btn, alignment=Qt.AlignRight)
        dlg.exec_()

    def show_source(self, label: str, transient: bool = False) -> None:
        """显示检索来源状态条（命中哪个知识库 / 联网兜底 / 检索中）。

        Args:
            label: 来源提示文本（由 Analyzer 构造，含状态 emoji）。
            transient: True 表示「检索中」临时态，不渲染配色（仅灰色）。
        """
        self.source_bar.setText(label)
        self.source_bar.setVisible(True)
        if transient:
            self.source_bar.setProperty("cssClass", "source-hint")
        else:
            # 按来源类型着色（与 Analyzer banner 的 emoji 前缀对应）
            if label.startswith("✅"):
                self.source_bar.setProperty("cssClass", "source-ima")
            elif label.startswith("📁"):
                self.source_bar.setProperty("cssClass", "source-local")
            elif label.startswith("🌐"):
                self.source_bar.setProperty("cssClass", "source-web")
            elif label.startswith("⏳"):
                self.source_bar.setProperty("cssClass", "source-hint")
            elif label.startswith(("🔒", "⚠️")):
                # IMA 鉴权失败 / 连接异常：红色告警色，与「未命中」区分开
                self.source_bar.setProperty("cssClass", "source-error")
            else:  # ℹ️ 无命中 / ⚙️ 未配置 / 未启用
                self.source_bar.setProperty("cssClass", "source-none")
        # 触发样式重算
        self.source_bar.style().unpolish(self.source_bar)
        self.source_bar.style().polish(self.source_bar)

    def show_answer(self, text: str) -> None:
        if is_error_text(text):
            title, msg = classify_error(text)
            detail = extract_raw_detail(text)
            # 主区只给一句中文说明，原始堆栈折叠在详情区（与 AnalysisDialog 一致）
            self._set_error_detail(detail if detail and detail != text else None)
            self.output.setPlainText(f"⚠ {title}\n{msg}")
            # 错误时保留「来源」提示（若已设置）；无来源则隐藏状态条
            return
        self._set_error_detail(None)
        self.output.setPlainText(text)
