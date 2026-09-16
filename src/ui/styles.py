"""样式与等级→颜色映射（对应架构 §7.5 / ui/styles.py）。

事件等级颜色（表格/标签，平台口径对齐，四级独立映射）：
  1 紧急 红 #E53935（高亮底色）
  2 重要 橙 #FB8C00（高亮底色；API eventLevel=2）
  3 次要 黄 #FDD835（浅底；API eventLevel=3，原「次要」）
  4 提示 蓝灰 #90A4AE（普通；API eventLevel=4，原「警告」改名「提示」）

健康监控阈值：CPU/内存 ≥ threshold（默认 85）→ 单元格红色加粗。
全局 QSS 主题集中于此，便于统一调整。
整体风格：专业运维控制台（NOC）——深色底 + 顶部品牌栏 + 左侧导航 + 卡片分区。
"""

from __future__ import annotations

from PyQt5.QtGui import QColor

# 品牌信息（顶部品牌栏 / 状态栏小字）
BRAND_NAME = "紫光汇智"
BRAND_SUB = "智能运维平台"
APP_TITLE = "H3C 云桌面智能运维助手"

# 事件等级 → (前景色, 背景色, 文案)。平台口径四级独立映射：
# eventLevel 1=紧急 / 2=重要 / 3=次要 / 4=提示（原「警告」改名「提示」）。
LEVEL_COLORS: dict[int, tuple[str, str, str]] = {
    1: ("#FFFFFF", "#E53935", "紧急"),
    2: ("#FFFFFF", "#FB8C00", "重要"),
    3: ("#000000", "#FDD835", "次要"),
    4: ("#FFFFFF", "#90A4AE", "提示"),
}

# 导航项：文案 → (几何色块颜色, 说明)
NAV_ICON_COLORS: dict[str, str] = {
    "智能告警中心": "#EF5350",
    "桌面健康监控": "#26A69A",
    "知识库与联网": "#42A5F5",
    "版本更新": "#AB47BC",
}
NAV_ICON_COLOR_FALLBACK = "#78909C"


def level_color(level: int) -> QColor:
    """返回等级对应的背景 QColor。"""
    return QColor(LEVEL_COLORS.get(level, ("#000000", "#ECEFF1", "未知"))[1])


def level_fg_color(level: int) -> QColor:
    return QColor(LEVEL_COLORS.get(level, ("#000000", "#ECEFF1", "未知"))[0])


def level_text(level: int) -> str:
    return LEVEL_COLORS.get(level, ("#000000", "#ECEFF1", "未知"))[2]


def level_style_sheet(level: int) -> str:
    """生成紧凑徽章样式（小圆角 + 内边距，不铺满整格）。"""
    fg, bg, _ = LEVEL_COLORS.get(level, ("#000000", "#ECEFF1", "未知"))
    return (f"color:{fg};background-color:{bg};border-radius:9px;"
            f"padding:1px 8px;font-size:11px;font-weight:bold;")


def nav_icon_color(name: str) -> str:
    """导航项几何色块颜色。"""
    return NAV_ICON_COLORS.get(name, NAV_ICON_COLOR_FALLBACK)


# 健康阈值越界单元格样式（红色加粗）
THRESHOLD_BREACH_STYLE = "color:#FFFFFF;background-color:#E53935;font-weight:bold;"
THRESHOLD_NORMAL_STYLE = "color:#000000;background-color:#E8F5E9;"

# ---------------------------------------------------------------------------
# 全局 QSS 主题
# ---------------------------------------------------------------------------
# 通用控件（深浅主题共用框架，颜色变量在各自主题中定义）
_COMMON = """
/* 全局字体：微软雅黑（所有控件文字统一，覆盖深/浅/admin 三套主题） */
QWidget {
    font-family: 'Microsoft YaHei', 'Microsoft YaHei UI', 'PingFang SC', sans-serif;
}

QWidget#HeaderBar {
    background-color: %HEADER_BG%;
    border-bottom: 1px solid %HEADER_LINE%;
}
QLabel#AppTitle {
    font-size: 15px;
    font-weight: bold;
    color: %TEXT_STRONG%;
}
QLabel#AppVersion {
    font-size: 11px;
    color: %TEXT_WEAK%;
}
QLabel#BrandName {
    font-size: 15px;
    font-weight: bold;
    letter-spacing: 2px;
    color: %BRAND_FG%;
}
QLabel#BrandSub {
    font-size: 11px;
    color: %TEXT_WEAK%;
}

/* 左侧导航 */
QListWidget#NavList {
    background-color: %NAV_BG%;
    border: none;
    border-right: 1px solid %PANEL_LINE%;
    outline: 0;
}
QListWidget#NavList::item {
    height: 44px;
    padding-left: 6px;
    color: %TEXT%;
    border: none;
}
QListWidget#NavList::item:hover {
    background-color: %NAV_HOVER%;
}
QListWidget#NavList::item:selected {
    background-color: %NAV_SELECTED%;
    color: %TEXT_STRONG%;
}

/* 卡片分区（弹窗/面板） */
QFrame#CardFrame {
    background-color: %CARD_BG%;
    border: 1px solid %CARD_LINE%;
    border-radius: 6px;
}
QLabel#CardTitle {
    font-size: 12px;
    font-weight: bold;
    color: %TEXT_STRONG%;
    padding-bottom: 2px;
}
QLabel#CardLabel {
    color: %TEXT_WEAK%;
    font-size: 12px;
}
QLabel#CardValue {
    color: %TEXT%;
    font-size: 12px;
}
QLabel#ErrorTitle {
    font-size: 13px;
    font-weight: bold;
    color: %ACCENT%;
}
QLabel#HintText {
    color: %TEXT_WEAK%;
    font-size: 11px;
}

/* 分组框（各页签容器统一） */
QGroupBox {
    border: 1px solid %CARD_LINE%;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-size: 12px;
    color: %TEXT_STRONG%;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
}

QTabWidget::pane {
    border: 1px solid %PANEL_LINE%;
    border-radius: 4px;
}
QTabBar::tab {
    background: %TAB_BG%;
    color: %TEXT%;
    padding: 8px 16px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: %ACCENT%;
    color: #ffffff;
    font-weight: bold;
}

QPushButton {
    background-color: %ACCENT%;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    min-height: 26px;
}
QPushButton:hover { background-color: %ACCENT_HOVER%; }
QPushButton:pressed { background-color: %ACCENT_PRESSED%; }
QPushButton:disabled { background-color: %DISABLED_BG%; color: %TEXT_WEAK%; }
/* 胶囊按钮：secondary = 空心描边蓝（对应「审核」类）；detail = 实心橙（对应「详情」类） */
QPushButton[cssClass="secondary"], QPushButton[cssClass="audit"] {
    background-color: transparent;
    color: %ACCENT%;
    border: 1px solid %ACCENT%;
    border-radius: 14px;
}
QPushButton[cssClass="secondary"]:hover, QPushButton[cssClass="audit"]:hover {
    background-color: %ROW_HOVER%;
}
QPushButton[cssClass="detail"] {
    background-color: #ef6c3a;
    color: #ffffff;
    border: none;
    border-radius: 14px;
}
QPushButton[cssClass="detail"]:hover { background-color: #e25a2a; }

/* 智能问答「检索来源」状态条：圆角胶囊 + 按来源着色 */
QLabel#AnswerSourceBar {
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
}
QLabel#AnswerSourceBar[cssClass="source-ima"]   { background-color: #e6f4ea; color: #0f7a3d; }
QLabel#AnswerSourceBar[cssClass="source-local"] { background-color: #e7f0fb; color: #1f5fb0; }
QLabel#AnswerSourceBar[cssClass="source-web"]   { background-color: #fdf0e3; color: #b5640a; }
QLabel#AnswerSourceBar[cssClass="source-none"]  { background-color: #eef0f3; color: #5b6472; }
QLabel#AnswerSourceBar[cssClass="source-hint"]  { background-color: #eef0f3; color: #5b6472; }
QLabel#AnswerSourceBar[cssClass="source-error"] { background-color: #fdecec; color: #b0322c; }
QLabel#AnswerSourceBar:hover { background-color: #e3e8ef; }
QLabel#AnswerSourceBar { border: 1px solid transparent; }
QLabel#AnswerSourceBar:hover { border-color: #c7d0db; }

/* 配置对话框「测试 IMA 连接」结果提示 */
QLabel#ImaTestLabel { font-size: 12px; padding: 2px 0; }
QLabel#ImaTestLabel[imaState="ok"]   { color: #0f7a3d; }
QLabel#ImaTestLabel[imaState="err"]  { color: #b0322c; }
QLabel#ImaTestLabel[imaState="busy"] { color: #6b7686; }

/* 命中片段详情弹窗 */
QFrame#HitCard {
    background-color: #f7f9fc;
    border: 1px solid #e3e8ef;
    border-radius: 10px;
}
QLabel#HitScore { color: #6b7686; font-size: 12px; }
QLabel#HitNote { color: #9aa4b2; font-size: 12px; }
QDialog QTextEdit { background-color: #ffffff; border: 1px solid #e3e8ef; border-radius: 6px; }

QLineEdit, QComboBox, QTextEdit, QSpinBox {
    background-color: %INPUT_BG%;
    color: %TEXT%;
    border: 1px solid %INPUT_LINE%;
    border-radius: 4px;
    padding: 5px 6px;
    min-height: 24px;
    selection-background-color: %ACCENT%;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid %ACCENT%;
}
QComboBox::drop-down { border: none; width: 18px; }

/* 表格：斑马纹 + hover + 强选中 */
QTableWidget {
    gridline-color: %GRID%;
    background-color: %TABLE_BG%;
    alternate-background-color: %TABLE_ALT%;
    border: 1px solid %PANEL_LINE%;
    border-radius: 4px;
    selection-background-color: %ROW_SELECTED%;
    selection-color: #ffffff;
    outline: 0;
}
QTableWidget::item {
    padding: 4px 8px;
    border: none;
}
QTableWidget::item:hover { background-color: %ROW_HOVER%; }
QTableWidget::item:selected {
    background-color: %ROW_SELECTED%;
    color: #ffffff;
}
QHeaderView::section {
    background-color: %HEAD_BG%;
    color: %TEXT_STRONG%;
    font-weight: bold;
    padding: 7px 8px;
    border: none;
    border-right: 1px solid %GRID%;
    border-bottom: 1px solid %GRID%;
}
QScrollBar:vertical {
    background: %TABLE_BG%;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: %INPUT_LINE%;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

QStatusBar {
    background-color: %STATUS_BG%;
    color: %TEXT_WEAK%;
    border-top: 1px solid %PANEL_LINE%;
}
QStatusBar QLabel { color: %TEXT_WEAK%; font-size: 11px; }
QLabel#Title { font-size: 16px; font-weight: bold; color: %TEXT_STRONG%; }
QLabel#SectionTitle { font-size: 13px; font-weight: bold; color: %TEXT_STRONG%; }
"""

_DARK_VARS = {
    "HEADER_BG": "#212121", "HEADER_LINE": "#3a3a3a",
    "TEXT_STRONG": "#f0f0f0", "TEXT": "#dcdcdc", "TEXT_WEAK": "#9e9e9e",
    "BRAND_FG": "#7ec0ff",
    "NAV_BG": "#252525", "NAV_HOVER": "#303030", "NAV_SELECTED": "#16324f",
    "PANEL_LINE": "#3a3a3a", "CARD_BG": "#2b2b2b", "CARD_LINE": "#3d3d3d",
    "TAB_BG": "#333333", "ACCENT": "#1e88e5", "ACCENT_HOVER": "#2196f3",
    "ACCENT_PRESSED": "#1565c0", "DISABLED_BG": "#3a3a3a",
    "BTN_SECOND_BG": "#333333",
    "INPUT_BG": "#1e1e1e", "INPUT_LINE": "#4a4a4a",
    "GRID": "#3a3a3a", "TABLE_BG": "#232323", "TABLE_ALT": "#282828",
    "ROW_HOVER": "#313131", "ROW_SELECTED": "#0d47a1", "HEAD_BG": "#333333",
    "STATUS_BG": "#1b1b1b",
}

_LIGHT_VARS = {
    "HEADER_BG": "#ffffff", "HEADER_LINE": "#dcdcdc",
    "TEXT_STRONG": "#1f1f1f", "TEXT": "#333333", "TEXT_WEAK": "#757575",
    "BRAND_FG": "#0d47a1",
    "NAV_BG": "#fafafa", "NAV_HOVER": "#eceff1", "NAV_SELECTED": "#e3f2fd",
    "PANEL_LINE": "#dcdcdc", "CARD_BG": "#ffffff", "CARD_LINE": "#dcdcdc",
    "TAB_BG": "#eceff1", "ACCENT": "#1e88e5", "ACCENT_HOVER": "#2196f3",
    "ACCENT_PRESSED": "#1565c0", "DISABLED_BG": "#bdbdbd",
    "BTN_SECOND_BG": "#eceff1",
    "INPUT_BG": "#ffffff", "INPUT_LINE": "#bdbdbd",
    "GRID": "#e0e0e0", "TABLE_BG": "#ffffff", "TABLE_ALT": "#f5f7fa",
    "ROW_HOVER": "#e8f1fb", "ROW_SELECTED": "#1565c0", "HEAD_BG": "#eceff1",
    "STATUS_BG": "#f5f5f5",
}

# ---------------------------------------------------------------------------
# 企业级后台管理主题（B2B SaaS / 经典 Admin Dashboard）
# 深海军蓝侧边栏 + 纯白主内容区，扁平化、无衬线、8px 网格观感
# ---------------------------------------------------------------------------
_ADMIN_VARS = {
    # 顶部品牌栏（白底）
    "HEADER_BG": "#ffffff", "HEADER_LINE": "#e3e8ef",
    # 文字
    "TEXT_STRONG": "#1f2733", "TEXT": "#3a4453", "TEXT_WEAK": "#8a94a6",
    "BRAND_FG": "#1f2d3d",
    # 左侧导航（深海军蓝侧边栏）
    "NAV_BG": "#0f172a", "NAV_HOVER": "#1e2f48", "NAV_SELECTED": "#234e8c",
    # 面板 / 卡片
    "PANEL_LINE": "#e3e8ef", "CARD_BG": "#ffffff", "CARD_LINE": "#e3e8ef",
    # 标签页 / 主色
    "TAB_BG": "#eef1f5", "ACCENT": "#2563eb", "ACCENT_HOVER": "#1d4ed8",
    "ACCENT_PRESSED": "#1e40af", "DISABLED_BG": "#e3e8ef",
    "BTN_SECOND_BG": "#ffffff",
    # 输入
    "INPUT_BG": "#ffffff", "INPUT_LINE": "#cdd5e0",
    # 表格
    "GRID": "#eceef1", "TABLE_BG": "#ffffff", "TABLE_ALT": "#fafbfc",
    "ROW_HOVER": "#f2f6fc", "ROW_SELECTED": "#2563eb", "HEAD_BG": "#f1f3f5",
    # 状态栏
    "STATUS_BG": "#f5f6f8",
}

# admin 专属规则（不依赖占位符，直接用字面色）
_ADMIN_EXTRA = """
QWidget#Breadcrumb {
    background-color: #ffffff;
    border-bottom: 1px solid #e3e8ef;
}
QLabel#BreadcrumbNode { color: #8a94a6; font-size: 13px; }
QLabel#BreadcrumbCurrent { color: #1f2733; font-size: 13px; font-weight: bold; }
QLabel#BreadcrumbSep { color: #c2cad6; font-size: 13px; }
QMenuBar {
    background-color: #ffffff;
    color: #1f2733;
    border-bottom: 1px solid #e3e8ef;
}
QMenuBar::item:selected { background-color: #eef1f5; }
QMenu {
    background-color: #ffffff;
    color: #1f2733;
    border: 1px solid #e3e8ef;
}
QMenu::item:selected { background-color: #eef1f5; }
/* 侧栏容器：与导航列表/设置区通体深海军蓝，杜绝白底露缝 */
QWidget#Sidebar {
    background-color: #0f172a;
    border: none;
}
/* 侧栏内所有子控件背景透明，防止 Fusion 风格默认白底溢出 */
QWidget#Sidebar QLabel,
QWidget#Sidebar QPushButton,
QWidget#Sidebar QWidget {
    background-color: transparent;
}
QListWidget#NavList {
    background-color: #0f172a;
    border: none;
    border-right: 1px solid #1e293b;
    outline: 0;
}
/* 左下角可视化设置模块：与侧栏通体深海军蓝融为一体 */
QWidget#SettingsFooter {
    background-color: #0f172a;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
}
/* 齿轮图标前缀（低透明度） */
QLabel#SettingsIcon {
    color: rgba(148, 163, 184, 0.5);
    font-size: 12px;
    padding: 2px 4px 2px 14px;
    background: transparent;
}
QLabel#SettingsTitle {
    color: #94a3b8;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 2px 12px 2px 2px;
    background: transparent;
}
QLabel#SettingsInfo {
    color: #cbd5e1;
    font-size: 12px;
    font-family: 'Microsoft YaHei', 'Consolas', 'JetBrains Mono', monospace;
    padding: 1px 12px 8px 14px;
    background: transparent;
}
/* 系统设置按钮：导航栏中的一行，hover/pressed 与导航项同步 */
QPushButton#SettingsBtn {
    background-color: transparent;
    color: #ffffff;
    border: none;
    border-radius: 0;
    padding: 11px 10px 11px 42px;
    font-size: 13px;
    text-align: left;
    margin-top: 6px;
}
QPushButton#SettingsBtn:hover { background-color: #1e293b; }
QPushButton#SettingsBtn:pressed { background-color: #2563eb; }
/* 侧栏与内容区分隔条：可拖拽拉伸，深色细线 */
QSplitter {
    background-color: #0f172a;
}
QSplitter::handle {
    background-color: #1e293b;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:hover {
    background-color: #334155;
}
/* 表格无边框，消除底部 #e3e8ef 浅灰残留线 */
QTableWidget {
    border: none;
}
/* 状态栏：与深海军蓝侧边栏统一，避免底部出现白色条框 */
QStatusBar {
    background-color: #0f172a;
    color: #cbd5e1;
    border: none;
}
QStatusBar::item {
    border: none;
    background: transparent;
}
QStatusBar QWidget {
    background-color: transparent;
}
QStatusBar QLabel {
    color: #cbd5e1;
    font-size: 11px;
    background-color: transparent;
}
QStatusBar QLabel#HintText {
    color: #cbd5e1;
}
"""

# 窗口/容器底色（深浅主题差异较大，单独声明）
QSS_DARK_BASE = "QMainWindow, QWidget { background-color: #2b2b2b; color: #dcdcdc; }"
QSS_LIGHT_BASE = "QMainWindow, QWidget { background-color: #f5f5f5; color: #222222; }"
QSS_ADMIN_BASE = (
    "QMainWindow, QWidget { background-color: #ffffff; color: #1f2733; "
    "font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; font-size: 13px; }"
)


def _render(vars_: dict[str, str]) -> str:
    qss = _COMMON
    for key, value in vars_.items():
        qss = qss.replace(f"%{key}%", value)
    return qss


QSS_DARK = QSS_DARK_BASE + _render(_DARK_VARS)
QSS_LIGHT = QSS_LIGHT_BASE + _render(_LIGHT_VARS)
QSS_ADMIN = QSS_ADMIN_BASE + _render(_ADMIN_VARS) + _ADMIN_EXTRA


def get_qss(theme: str = "dark") -> str:
    if theme == "admin":
        return QSS_ADMIN
    return QSS_DARK if theme == "dark" else QSS_LIGHT
