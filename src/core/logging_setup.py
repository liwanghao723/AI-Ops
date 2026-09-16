"""日志初始化（对应架构 §7.3）。

- 输出：logs/app_YYYYMMDD.log + 控制台
- 处理器：RotatingFileHandler（10MB/个，保留 5 个）
- 格式：%(asctime)s [%(levelname)s] [%(module)s.%(funcName)s] %(message)s
- 级别：默认 INFO；HTTP 请求/重试记 DEBUG；UI 异常记 ERROR
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import Paths

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(module)s.%(funcName)s] %(message)s"
_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """配置全局 logging：文件 + 控制台双输出。幂等。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    paths = Paths.instance()
    log_file = paths.logs_dir / f"app_{_today()}.log"

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_LOG_FORMAT)

    # 文件处理器（滚动）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    _CONFIGURED = True
    logging.getLogger(__name__).info("日志系统已初始化，文件: %s", log_file)


def get_logger(name: str) -> logging.Logger:
    """获取模块 logger（确保已初始化）。"""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


def _today() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y%m%d")
