"""复用组件（对应架构 T16 / ui/widgets.py）。

- LevelBadge：告警等级紧凑徽标（小圆角色块，不铺满整格）
- ThresholdCell：健康阈值单元格（≥阈值红标）
- StatusDot：连接状态指示点
- HeaderBar：顶部品牌栏（左标题+版本，右「紫光汇智 / 智能运维平台」）
- NavDelegate：左侧导航委托（几何色块图标 + 选中左侧色条 + 背景加深）
- CardFrame：卡片分区容器（弹窗/面板结构化展示）
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QFrame, QSizePolicy,
    QStyledItemDelegate, QStyle,
)
from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QPainter, QFont, QPalette

from .styles import (
    THRESHOLD_BREACH_STYLE, THRESHOLD_NORMAL_STYLE,
    APP_TITLE, BRAND_NAME, BRAND_SUB,
    level_style_sheet, level_text, nav_icon_color,
)


class LevelBadge(QLabel):
    """告警等级彩色徽标（紧凑：小圆角 + 内边距 + 居中）。"""

    def __init__(self, level: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LevelBadge")
        self.setText(level_text(level))
        self.setStyleSheet(level_style_sheet(level))
        self.setAlignment(Qt.AlignCenter)
        self.setMargin(2)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


def level_badge_cell(level: int) -> QWidget:
    """把紧凑徽章包一层居中容器，作为表格单元格控件使用。"""
    wrapper = QWidget()
    lay = QHBoxLayout(wrapper)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setSpacing(0)
    lay.setAlignment(Qt.AlignCenter)
    lay.addWidget(LevelBadge(level))
    return wrapper


def status_badge_cell(label: str, fg: str, bg: str) -> QWidget:
    """状态彩色徽标（居中容器，作为表格单元格控件使用）。

    ``label/fg/bg`` 由调用方按业务状态映射后传入（见 health_panel._status_display），
    与本函数解耦，避免 widgets 反向依赖 health 模块。
    """
    wrapper = QWidget()
    lay = QHBoxLayout(wrapper)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setSpacing(0)
    lay.setAlignment(Qt.AlignCenter)
    badge = QLabel(label)
    badge.setObjectName("StatusBadge")
    badge.setAlignment(Qt.AlignCenter)
    badge.setStyleSheet(
        f"color:{fg};background-color:{bg};border-radius:9px;"
        f"padding:1px 10px;font-size:11px;font-weight:bold;")
    lay.addWidget(badge)
    return wrapper


class ThresholdCell(QLabel):
    """健康利用率单元格：≥阈值红色加粗，否则正常绿底。

    ``value`` 为 ``None`` 表示无数据（如桌面关机、2.27.31 未返回该桌面），
    显示 "-" 且**不着红**（不参与过载预警）。既有 ``float`` 调用向后兼容。
    """

    NO_DATA_TEXT = "-"

    def __init__(self, value: float | None = 0.0, threshold: int = 85,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_value(value, threshold)

    def set_value(self, value: float | None, threshold: int = 85) -> None:
        if value is None:
            self.setText(self.NO_DATA_TEXT)
            self.setStyleSheet(THRESHOLD_NORMAL_STYLE)
            return
        self.setText(f"{value:.0f}%")
        if value >= threshold:
            self.setStyleSheet(THRESHOLD_BREACH_STYLE)
        else:
            self.setStyleSheet(THRESHOLD_NORMAL_STYLE)


class StatusDot(QWidget):
    """连接状态圆点 + 文字。"""

    def __init__(self, connected: bool = False, text: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dot = QLabel("●")
        self.label = QLabel(text)
        self.label.setObjectName("HintText")
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.set_status(connected, text)

    def set_status(self, connected: bool, text: str = "") -> None:
        color = "#43a047" if connected else "#e53935"
        self.dot.setStyleSheet(f"color:{color};font-size:12px;")
        if text:
            self.label.setText(text)


class HeaderBar(QFrame):
    """顶部品牌栏：左侧应用标题 + 版本号，右侧「紫光汇智 / 智能运维平台」。"""

    def __init__(self, version: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self.setFixedHeight(58)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.lbl_title = QLabel(APP_TITLE)
        self.lbl_title.setObjectName("AppTitle")
        self.lbl_version = QLabel(f"v{version}" if version else "")
        self.lbl_version.setObjectName("AppVersion")
        left.addWidget(self.lbl_title)
        left.addWidget(self.lbl_version)
        layout.addLayout(left)
        layout.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(1)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_brand = QLabel(BRAND_NAME)
        self.lbl_brand.setObjectName("BrandName")
        self.lbl_brand.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_brand_sub = QLabel(BRAND_SUB)
        self.lbl_brand_sub.setObjectName("BrandSub")
        self.lbl_brand_sub.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.addWidget(self.lbl_brand)
        right.addWidget(self.lbl_brand_sub)
        layout.addLayout(right)

    def set_version(self, version: str) -> None:
        self.lbl_version.setText(f"v{version}")


class NavDelegate(QStyledItemDelegate):
    """左侧导航绘制：几何色块图标 + 选中态左侧色条 + 背景加深 + hover。"""

    ICON_SIZE = 16
    ACCENT_BAR = 3

    # 深/浅/企业级三套导航配色（dark/light 按调色板亮度自动选；admin 由主窗显式指定）
    _C = {
        "dark": ("#16324f", "#303030", "#252525", "#1e88e5", "#f0f0f0", "#dcdcdc"),
        "light": ("#e3f2fd", "#eceff1", "#fafafa", "#1e88e5", "#0d47a1", "#333333"),
        # admin：深海军蓝侧栏 #0f172a + 灰白字 + 选中亮蓝块 #2563eb
        "admin": ("#2563eb", "#1e293b", "#0f172a", "#3b82f6", "#ffffff", "#94a3b8"),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scheme: str | None = None

    def _colors(self, option):
        if self.scheme in self._C:
            return self._C[self.scheme]
        light = option.palette.color(QPalette.Text).lightness() < 128
        return self._C["light" if light else "dark"]

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), 46)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        c_sel, c_hover, c_bg, c_bar, c_text_sel, c_text = self._colors(option)

        # 背景
        if selected:
            painter.fillRect(rect, QColor(c_sel))
        elif hovered:
            painter.fillRect(rect, QColor(c_hover))
        else:
            painter.fillRect(rect, QColor(c_bg))

        # 选中态左侧色条
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top() + 6, self.ACCENT_BAR,
                                   rect.height() - 12), QColor(c_bar))

        name = index.data(Qt.DisplayRole) or ""

        # 几何色块图标（不依赖外部图片/字体图标）
        icon_y = rect.top() + (rect.height() - self.ICON_SIZE) // 2
        icon_rect = QRect(rect.left() + 14, icon_y, self.ICON_SIZE, self.ICON_SIZE)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(nav_icon_color(str(name))))
        painter.drawRoundedRect(icon_rect, 4, 4)

        # 文字
        text_rect = QRect(icon_rect.right() + 12, rect.top(),
                          rect.width() - icon_rect.right() - 12, rect.height())
        painter.setPen(QColor(c_text_sel if selected else c_text))
        font = QFont()
        font.setFamily("Microsoft YaHei")
        font.setPixelSize(13)
        font.setBold(selected)
        painter.setFont(font)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, str(name))
        painter.restore()


class CardFrame(QFrame):
    """卡片分区容器：标题 + 内容区（内容由调用方通过 body() 填充）。"""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        self.setFrameShape(QFrame.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        if title:
            self.lbl_title = QLabel(title)
            self.lbl_title.setObjectName("CardTitle")
            layout.addWidget(self.lbl_title)
        else:
            self.lbl_title = None

    def body(self) -> QVBoxLayout:
        return self.layout()  # type: ignore[return-value]

    def add_widget(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)  # type: ignore[union-attr]

    def add_layout(self, layout) -> None:
        self.layout().addLayout(layout)  # type: ignore[union-attr]


class BreadcrumbBar(QWidget):
    """顶部面包屑通栏（企业级后台风格）：白色底 + 底部细线，路径以 › 分隔。

    用法：``bar.set_path(["运维控制台", "智能告警中心"])``
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Breadcrumb")
        self.setFixedHeight(44)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(16, 0, 16, 0)
        self._lay.setSpacing(6)
        self._lay.setAlignment(Qt.AlignVCenter)

    def set_path(self, parts: list[str]) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for i, part in enumerate(parts):
            if i > 0:
                sep = QLabel("›")
                sep.setObjectName("BreadcrumbSep")
                self._lay.addWidget(sep)
            lbl = QLabel(part)
            lbl.setObjectName("BreadcrumbCurrent" if i == len(parts) - 1
                              else "BreadcrumbNode")
            self._lay.addWidget(lbl)
        self._lay.addStretch(1)
