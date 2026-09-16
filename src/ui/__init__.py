"""ui 包：界面层（依赖 PyQt5）。"""

from .main_window import MainWindow
from .styles import LEVEL_COLORS, get_qss, level_color, level_text, BRAND_NAME, BRAND_SUB
from .widgets import (LevelBadge, StatusDot, ThresholdCell, HeaderBar,
                      NavDelegate, CardFrame)
from .alarm_panel import AlarmPanel
from .health_panel import HealthPanel
from .knowledge_panel import KnowledgePanel
from .update_panel import UpdatePanel
from .config_dialog import ConfigDialog
from .analysis_dialog import AnalysisDialog, show_analysis_dialog

__all__ = [
    "MainWindow", "AlarmPanel", "HealthPanel", "KnowledgePanel", "UpdatePanel",
    "ConfigDialog", "LevelBadge", "StatusDot", "ThresholdCell",
    "HeaderBar", "NavDelegate", "CardFrame",
    "AnalysisDialog", "show_analysis_dialog",
    "LEVEL_COLORS", "get_qss", "level_color", "level_text",
    "BRAND_NAME", "BRAND_SUB",
]
