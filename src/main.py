"""程序入口（对应架构 T18 / main.py）。

启动流程：
  0. 创建 QApplication（必须早于任何 QWidget，否则 Qt qFatal abort）
  1. Paths 定位版本目录（H3C_OPS_ROOT 可覆盖）→ 创建子目录
  2. 初始化日志（§7.3）
  3. AppConfig.load（缺失则拷贝 example 并提示）
  4. 创建 MainWindow（内部启动各 Worker：告警轮询 30s、健康采集、版本自检）
  5. 进入 Qt 事件循环

运行：python main.py
打包：pyinstaller --onefile --windowed --name H3COpsAssistant main.py
"""

from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication, QMessageBox


def bootstrap() -> int:
    # 0) QApplication 必须在任何 QWidget 构造之前创建（MainWindow 内含 QWidget）
    app = QApplication(sys.argv)

    # 1) 路径与版本目录
    from core.paths import Paths
    paths = Paths.instance()  # 首次访问创建子目录

    # 2) 日志
    from core.logging_setup import setup_logging, get_logger
    setup_logging()
    logger = get_logger("main")

    # 3) 配置加载（缺失则拷贝 example）
    from core.config import AppConfig
    try:
        cfg = AppConfig.load()
    except Exception as exc:  # 配置致命错误：弹窗后退出
        logger.error("配置加载失败: %s", exc)
        QMessageBox.critical(None, "配置错误", f"加载配置失败：{exc}")
        return 1

    # 4) 主窗口
    from ui.main_window import MainWindow
    window = MainWindow(cfg)
    window.show()

    logger.info("H3C 云桌面智能运维助手启动完成，数据目录: %s", paths.ROOT)
    return app.exec_()


def main() -> None:
    sys.exit(bootstrap())


if __name__ == "__main__":
    main()
