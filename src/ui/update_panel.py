"""模块四 版本更新与软件库（对应架构 T17 / ui/update_panel.py）。

- 显示当前 / 最新版本
- 检测到新版本 → 下载并提示重启
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox, QGroupBox,
    QHBoxLayout,
)
from PyQt5.QtCore import pyqtSignal

from core.models import VersionInfo


class UpdatePanel(QWidget):
    request_download = pyqtSignal(VersionInfo)

    def __init__(self, current_version: str = "1.0.0", parent=None) -> None:
        super().__init__(parent)
        self.current_version = current_version
        self._info: VersionInfo | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(10)

        box = QGroupBox("版本信息")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 14, 12, 12)
        v.setSpacing(8)

        self.lbl_current = QLabel(f"当前版本：{self.current_version}")
        self.lbl_latest = QLabel("最新版本：检测中…")
        self.lbl_notes = QLabel("")
        self.lbl_notes.setObjectName("CardValue")
        self.lbl_notes.setWordWrap(True)
        v.addWidget(self.lbl_current)
        v.addWidget(self.lbl_latest)
        v.addWidget(self.lbl_notes)

        ops = QHBoxLayout()
        ops.setContentsMargins(0, 6, 0, 0)
        ops.setSpacing(8)
        self.btn_check = QPushButton("检查更新")
        self.btn_check.setFixedWidth(110)
        self.btn_download = QPushButton("立即下载并更新")
        self.btn_download.setFixedWidth(150)
        self.btn_download.setProperty("cssClass", "secondary")
        self.btn_download.setEnabled(False)
        ops.addWidget(self.btn_check)
        ops.addWidget(self.btn_download)
        ops.addStretch(1)
        v.addLayout(ops)

        layout.addWidget(box)
        layout.addStretch(1)

    def set_version_info(self, info: VersionInfo | None) -> None:
        self._info = info
        if info is None:
            self.lbl_latest.setText("最新版本：已是最新 / 无更新")
            self.btn_download.setEnabled(False)
            return
        self.lbl_latest.setText(f"最新版本：{info.latest_version}")
        self.lbl_notes.setText(f"更新内容：{info.notes}")
        self.btn_download.setEnabled(bool(info.download_url))

    def on_download_clicked(self, info: VersionInfo | None = None) -> None:
        info = info or self._info
        if info is None:
            return
        self.request_download.emit(info)
