"""update 包：版本更新模块。"""

from .version_manager import APP_VERSION, VersionManager, compare_version, parse_version

__all__ = ["APP_VERSION", "VersionManager", "compare_version", "parse_version"]
