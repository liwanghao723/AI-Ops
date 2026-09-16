"""左侧侧栏底部「可视化设置模块」（对应需求：左下角设置入口）。

- 常驻显示当前 config/app.yaml 的关键连接摘要（H3C 平台地址、AI 模型），
  让配置「可视化」而非只能进弹窗才看得到。
- 提供「系统设置」按钮，点击打开既有的 ConfigDialog（完整编辑并写回 app.yaml）。
- 配置保存热重载后调用 update_config() 实时刷新摘要。

该模块挂在深海军蓝侧栏底部，不改变导航 4 项 / 堆叠 4 页结构。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from core.config import AppConfig


class SettingsFooter(QWidget):
    def __init__(self, cfg: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsFooter")
        self._build(cfg)

    def _build(self, cfg: AppConfig) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(4)

        # 模块标题（齿轮图标 + 文字）
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(0)
        self.lbl_icon = QLabel("⚙")
        self.lbl_icon.setObjectName("SettingsIcon")
        self.lbl_title = QLabel("连接配置")
        self.lbl_title.setObjectName("SettingsTitle")
        title_row.addWidget(self.lbl_icon)
        title_row.addWidget(self.lbl_title)
        title_row.addStretch()
        lay.addLayout(title_row)

        # 当前配置摘要（来自 app.yaml）
        self.lbl_platform = QLabel()
        self.lbl_platform.setObjectName("SettingsInfo")
        self.lbl_llm = QLabel()
        self.lbl_llm.setObjectName("SettingsInfo")
        self._refresh(cfg)
        lay.addWidget(self.lbl_platform)
        lay.addWidget(self.lbl_llm)

        # 打开完整设置对话框的按钮
        self.btn = QPushButton("系统设置")
        self.btn.setObjectName("SettingsBtn")
        self.btn.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.btn)

    def _refresh(self, cfg: AppConfig) -> None:
        base = (cfg.platform.base_url or "").strip()
        self.lbl_platform.setText(f"H3C：{base or '未配置'}")
        self.lbl_llm.setText(
            f"AI：{cfg.llm.provider or '—'} / {cfg.llm.model or '—'}")

    def update_config(self, cfg: AppConfig) -> None:
        """配置保存后由主窗口调用，刷新摘要文字。"""
        self._refresh(cfg)
