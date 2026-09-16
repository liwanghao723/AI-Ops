"""Worker 基类（对应架构 T10 / workers/base_worker.py）。

统一信号：
  - error(str)：工作异常桥接（UI 以状态栏/弹窗提示，不崩溃）
  - finished()：来自 QThread

提供 _bridge_exc 工具：捕获异常并转 error 信号，避免子线程异常导致程序崩溃。
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from core.errors import H3COpsError
from core.logging_setup import get_logger


logger = get_logger(__name__)


class BaseWorker(QThread):
    """后台任务基类：异常桥接到 error 信号。"""

    error = pyqtSignal(str)

    def bridge(self, exc: BaseException) -> None:
        """捕获异常后记录日志并发 error 信号。"""
        msg = str(exc)
        if isinstance(exc, H3COpsError):
            logger.error("[Worker] 业务异常: %s", msg)
        else:
            logger.exception("[Worker] 未预期异常")
        self.error.emit(msg)
