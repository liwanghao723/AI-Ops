"""版本 Worker（对应架构 T14 / workers/version_worker.py）。

启动时跑自检（配置存在性、目录可写、平台连通性探活可选）+ check_update，
发出 selfcheck_done(dict) / update_available(VersionInfo)。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal, Qt

from core.logging_setup import get_logger
from core.models import VersionInfo
from core.paths import Paths
from .base_worker import BaseWorker


logger = get_logger(__name__)


class VersionWorker(BaseWorker):
    selfcheck_done = pyqtSignal(dict)     # 自检结果
    update_available = pyqtSignal(VersionInfo)  # 有新版本
    request_selfcheck = pyqtSignal()      # 主线程请求自检（排队到 worker 线程）
    request_update = pyqtSignal()         # 主线程请求版本检查

    def __init__(self, version_manager, client=None) -> None:
        super().__init__()
        self.vm = version_manager
        self.client = client  # H3CWorkspaceClient | None（用于连通性探活）
        # 跨线程排队，避免阻塞 UI
        self.request_selfcheck.connect(self.do_selfcheck, Qt.QueuedConnection)
        self.request_update.connect(self.do_update_check, Qt.QueuedConnection)

    def run(self) -> None:
        self.do_selfcheck()
        info = self.do_update_check()
        if info is not None:
            self.update_available.emit(info)

    def do_selfcheck(self) -> dict:
        paths = Paths.instance()
        checks = {}
        # 1) 配置文件存在
        checks["config_exists"] = paths.config_file.exists()
        # 2) 各目录可写
        for name, d in (("config", paths.config_dir),
                        ("knowledge", paths.knowledge_dir),
                        ("logs", paths.logs_dir),
                        ("updates", paths.updates_dir)):
            try:
                probe = d / ".write_test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                checks[f"{name}_writable"] = True
            except Exception:
                checks[f"{name}_writable"] = False
        # 3) 平台连通性探活（可选，失败不致命）
        if self.client is not None:
            try:
                self.client.list_realtime_alarms(limit=1, offset=0)
                checks["platform_reachable"] = True
            except Exception as exc:
                checks["platform_reachable"] = False
                checks["platform_error"] = str(exc)
        else:
            checks["platform_reachable"] = None
        logger.info("[VersionWorker] 自检: %s", checks)
        self.selfcheck_done.emit(checks)
        return checks

    def do_update_check(self) -> "VersionInfo | None":
        try:
            return self.vm.check_update()
        except Exception as exc:
            logger.warning("[VersionWorker] 版本检查失败: %s", exc)
            self.bridge(exc)
            return None
