"""主窗口（对应架构 T16 / ui/main_window.py）。

- 左侧导航 + 右侧内容区（Tab 容器），顶部状态栏
- 状态栏：版本号 / H3C 平台连接状态 / 最后刷新时间
- 菜单：设置、自检、检查更新
- 启动各 Worker（告警轮询 30s、健康采集、版本自检）并对接信号
"""

from __future__ import annotations

import datetime
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QListWidget, QStackedWidget, QHBoxLayout,
    QVBoxLayout, QStatusBar, QLabel, QMenuBar, QMenu, QMessageBox, QListWidgetItem,
    QSplitter,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor

from core.config import AppConfig
from core.logging_setup import get_logger
from core.workspace_client import H3CWorkspaceClient
from core.models import VersionInfo
from ai.llm_client import LLMClient
from ai.knowledge_manager import KnowledgeManager
from ai.analyzer import Analyzer
from ai.web_search import WebSearch
from update.version_manager import APP_VERSION, VersionManager

from workers.alarm_worker import AlarmWorker
from workers.health_worker import HealthWorker
from workers.analyze_worker import AnalyzeWorker
from workers.version_worker import VersionWorker

from .styles import get_qss, APP_TITLE, BRAND_NAME
from .widgets import StatusDot, HeaderBar, NavDelegate, BreadcrumbBar
from .config_dialog import ConfigDialog
from .settings_footer import SettingsFooter
from .alarm_panel import AlarmPanel
from .health_panel import HealthPanel
from .knowledge_panel import KnowledgePanel
from .update_panel import UpdatePanel


logger = get_logger(__name__)

_NAV = ["智能告警中心", "桌面健康监控", "知识库与联网", "版本更新"]


class FlatStatusBar(QStatusBar):
    """无边框扁平状态栏：禁用原生顶部/左侧凹槽线，统一深蓝底色。"""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0f172a"))


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"H3C 云桌面智能运维助手  v{APP_VERSION}")
        self.resize(1100, 720)              # 默认尺寸不变
        # 可自由缩放（拖边缘 / 最大化），下限防止筛选栏等固定宽控件被挤爆
        self.setMinimumSize(1040, 620)

        self._build_core_objects()
        self._build_ui()
        self._create_workers()   # 先实例化 worker（_wire_panels 需要引用）
        self._wire_panels()
        self._start_workers()

    # ------------------------------------------------------------------
    # 核心对象构建（client / llm / km / analyzer / version_manager）
    # ------------------------------------------------------------------
    def _build_core_objects(self) -> None:
        self.client = H3CWorkspaceClient.from_config(self.cfg)
        self.llm = LLMClient(
            base_url=self.cfg.llm.base_url,
            api_key=self.cfg.llm.api_key,
            model=self.cfg.llm.model,
            timeout=self.cfg.llm.timeout,
        )
        self.llm.set_provider(self.cfg.llm.provider, self.cfg.llm)
        self.km = KnowledgeManager.from_config(self.cfg)
        self.web = WebSearch()
        self.analyzer = Analyzer(self.llm, self.km, self.web)
        self.vm = VersionManager(
            source=self.cfg.update.source,
            current=APP_VERSION,
            remote_url=self.cfg.update.remote_url,
        )

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部品牌栏（左：标题+版本，右：紫光汇智 / 智能运维平台）
        self.header = HeaderBar(version=APP_VERSION)
        root.addWidget(self.header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        # 左侧导航（深海军蓝侧边栏：几何色块图标 + 选中态亮蓝块 + hover）
        # 侧栏容器：上=导航列表，下=可视化设置模块（左下角）
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(160)
        sidebar.setMaximumWidth(400)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        for name in _NAV:
            self.nav.addItem(QListWidgetItem(name))
        self._nav_delegate = NavDelegate(self.nav)
        self._nav_delegate.scheme = "admin"      # 企业级深蓝侧栏配色
        self.nav.setItemDelegate(self._nav_delegate)
        self.nav.currentRowChanged.connect(self._on_nav)
        sidebar_layout.addWidget(self.nav, 1)

        # 左下角：可视化设置模块（显示连接摘要 + 一键打开设置）
        self.settings_footer = SettingsFooter(self.cfg)
        self.settings_footer.btn.clicked.connect(self._open_config)
        sidebar_layout.addWidget(self.settings_footer)

        # 右侧内容区：面包屑通栏 + 堆叠面板（纯白主操作区）
        content = QWidget()
        content.setObjectName("MainContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.breadcrumb = BreadcrumbBar()
        content_layout.addWidget(self.breadcrumb)

        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.alarm_panel = AlarmPanel()
        self.health_panel = HealthPanel(
            threshold_cpu=self.cfg.health.threshold_cpu,
            threshold_mem=self.cfg.health.threshold_mem,
        )
        self.knowledge_panel = KnowledgePanel()
        self.update_panel = UpdatePanel(current_version=APP_VERSION)
        self.stack.addWidget(self.alarm_panel)
        self.stack.addWidget(self.health_panel)
        self.stack.addWidget(self.knowledge_panel)
        self.stack.addWidget(self.update_panel)
        content_layout.addWidget(self.stack, 1)

        # 可拖拽拉伸分隔：侧栏 ↔ 主内容区
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(sidebar)
        splitter.addWidget(content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 1000])
        body.addWidget(splitter, 1)

        # 状态栏（连接状态 + 版本 + 品牌小字）
        self.status_bar = FlatStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        self.status_dot = StatusDot(connected=False, text="连接状态：检测中")
        self.status_bar.addPermanentWidget(self.status_dot)
        self.lbl_version = QLabel(f"v{APP_VERSION}")
        self.status_bar.addPermanentWidget(self.lbl_version)
        self.lbl_brand_status = QLabel(BRAND_NAME)
        self.lbl_brand_status.setObjectName("HintText")
        self.status_bar.addPermanentWidget(self.lbl_brand_status)
        self.status_bar.showMessage("就绪")

        # 菜单
        self._build_menu()

        # 应用主题（企业级后台管理风格）
        self.setStyleSheet(get_qss("admin"))
        self.nav.setCurrentRow(0)

    def _build_menu(self) -> None:
        menubar = QMenuBar()
        self.setMenuBar(menubar)
        file_menu = QMenu("菜单", self)
        act_setting = file_menu.addAction("设置")
        act_selfcheck = file_menu.addAction("立即自检")
        act_update = file_menu.addAction("检查更新")
        menubar.addMenu(file_menu)

        act_setting.triggered.connect(self._open_config)
        act_selfcheck.triggered.connect(self._run_selfcheck)
        act_update.triggered.connect(self._check_update)

    def _on_nav(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        # 面包屑随导航更新（运维控制台 / 当前模块）
        if 0 <= idx < len(_NAV):
            self.breadcrumb.set_path(["运维控制台", _NAV[idx]])
        # 进入健康页时若首次则触发采集（排队到 worker 线程）
        if idx == 1 and self.health_panel.table.rowCount() == 0:
            self.health_worker.request_collect.emit()

    # ------------------------------------------------------------------
    # 面板信号对接
    # ------------------------------------------------------------------
    def _wire_panels(self) -> None:
        self.alarm_panel.request_analyze.connect(self.analyze_worker.analyze_alarm)
        self.health_panel.request_explain.connect(self.analyze_worker.explain_health)
        self.knowledge_panel.request_answer.connect(self.analyze_worker.answer)

        self.alarm_panel.btn_query.clicked.connect(self._on_alarm_query)
        # 手动点击与自动刷新统一走 HealthPanel.request_refresh（内部去重）
        self.health_panel.request_refresh.connect(self.health_worker.request_collect.emit)
        self.update_panel.btn_check.clicked.connect(self._check_update)
        self.update_panel.btn_download.clicked.connect(
            lambda: self.update_panel.on_download_clicked())
        self.update_panel.request_download.connect(self._do_download)

    # ------------------------------------------------------------------
    # Worker 实例化（先于信号对接）
    # ------------------------------------------------------------------
    def _create_workers(self) -> None:
        self.alarm_worker = AlarmWorker(
            self.client, interval_sec=self.cfg.health.alarm_refresh_sec)
        self.health_worker = HealthWorker(
            self.client, max_concurrency=self.cfg.health.max_concurrency)
        self.analyze_worker = AnalyzeWorker(
            self.analyzer,
            threshold_cpu=self.cfg.health.threshold_cpu,
            threshold_mem=self.cfg.health.threshold_mem,
        )
        self.version_worker = VersionWorker(self.vm, self.client)

    # ------------------------------------------------------------------
    # Worker 启动与信号
    # ------------------------------------------------------------------
    def _start_workers(self) -> None:
        # 告警轮询
        self.alarm_worker.alarms_ready.connect(self.alarm_panel.set_alarms)
        self.alarm_worker.warn_count_ready.connect(self.alarm_panel.set_warn_count)
        self.alarm_worker.error.connect(self._on_worker_error)
        self.alarm_worker.start()

        # 健康采集
        self.health_worker.health_ready.connect(self.health_panel.set_rows)
        self.health_worker.error.connect(self._on_worker_error)
        self.health_worker.start()

        # 分析
        self.analyze_worker.analysis_ready.connect(self.alarm_panel.show_analysis)
        self.analyze_worker.health_analysis_ready.connect(self.health_panel.show_explain)
        self.analyze_worker.answer_ready.connect(self.knowledge_panel.show_answer)
        self.analyze_worker.answer_source.connect(self.knowledge_panel.show_source)
        self.analyze_worker.answer_chunks.connect(self.knowledge_panel.show_hits)
        self.analyze_worker.error.connect(self._on_worker_error)
        self.analyze_worker.start()

        # 版本自检
        self.version_worker.selfcheck_done.connect(self._on_selfcheck)
        self.version_worker.update_available.connect(self._on_update_available)
        self.version_worker.error.connect(self._on_worker_error)
        self.version_worker.start()

    # ------------------------------------------------------------------
    # 信号回调
    # ------------------------------------------------------------------
    def _on_worker_error(self, msg: str) -> None:
        self.status_bar.showMessage(f"错误：{msg}", 5000)
        logger.warning("[MainWindow] Worker 错误: %s", msg)
        # 采集失败时解锁健康页刷新按钮（成功路径由 set_rows 解锁）
        if getattr(self, "health_panel", None) is not None:
            self.health_panel.release_refresh_lock()

    def _apply_alarm_filters(self) -> None:
        """按 等级/状态/关键字/时间 过滤已拉取的真实告警（客户端组合过滤）。"""
        shown = self.alarm_panel.apply_filters()
        self.status_bar.showMessage(f"筛选出 {len(shown)} 条告警", 3000)

    def _on_alarm_query(self) -> None:
        """点击查询时从平台实时拉取告警，拿到结果后再由面板本地过滤。

        先读取面板当前「上限」选择并同步给 Worker，再触发拉取，
        确保本次查询即使用新的拉取上限（运行时不写死）。
        """
        self.status_bar.showMessage("正在从 H3C 平台刷新告警...", 3000)
        cap = self.alarm_panel.current_cap()
        self.alarm_worker.set_max_count(cap)
        self.alarm_worker.request_fetch.emit()

    def _on_selfcheck(self, checks: dict) -> None:
        reachable = checks.get("platform_reachable")
        if reachable is True:
            self.status_dot.set_status(True, "连接状态：●已连接")
        elif reachable is False:
            self.status_dot.set_status(False, "连接状态：●未连接")
        else:
            self.status_dot.set_status(False, "连接状态：未检测")

    def _on_update_available(self, info: VersionInfo) -> None:
        self.update_panel.set_version_info(info)
        QMessageBox.information(
            self, "发现新版本",
            f"当前 v{info.current_version}，最新 v{info.latest_version}\n"
            f"更新内容：{info.notes}")

    def _run_selfcheck(self) -> None:
        self.version_worker.request_selfcheck.emit()

    def _check_update(self) -> None:
        self.version_worker.request_update.emit()

    def _do_download(self, info: "VersionInfo") -> None:
        try:
            target = self.vm.download(info)
            QMessageBox.information(
                self, "下载完成",
                f"已下载到：{target}\n请重启应用以完成更新（将自动加载 v{info.latest_version}）。")
        except Exception as exc:  # 下载失败不崩溃
            self.status_bar.showMessage(f"下载失败：{exc}", 5000)

    # ------------------------------------------------------------------
    # 配置对话框（保存后热重载）
    # ------------------------------------------------------------------
    def _open_config(self) -> None:
        dlg = ConfigDialog(self.cfg, self)
        dlg.saved.connect(self._on_config_saved)
        dlg.exec_()

    def _on_config_saved(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        # 热重载可即时生效部分：LLM 厂商、刷新间隔、阈值
        self.llm.set_provider(cfg.llm.provider, cfg.llm)
        self.llm.api_key = cfg.llm.api_key
        self.llm.base_url = cfg.llm.base_url.rstrip("/")
        self.llm.model = cfg.llm.model
        self.alarm_worker.set_interval(cfg.health.alarm_refresh_sec)
        self.alarm_panel.set_refresh_interval(cfg.health.alarm_refresh_sec)
        # 阈值热更新（内部即时重绘，避免表格着色滞后于 AI 判定）
        self.health_panel.set_thresholds(
            cfg.health.threshold_cpu, cfg.health.threshold_mem)
        # AI 解读阈值同步，避免「表格标红但 AI 判正常」
        self.analyze_worker.set_health_thresholds(
            cfg.health.threshold_cpu, cfg.health.threshold_mem)
        # 重建 client / km（下次采集生效）；先建新的再释放旧连接池，避免中途失败
        # 导致无 client 可用，同时避免旧连接池泄漏。
        old_client = getattr(self, "client", None)
        self.client = H3CWorkspaceClient.from_config(cfg)
        if old_client is not None and old_client is not self.client:
            old_client.close()
        self.health_worker.client = self.client
        self.alarm_worker.client = self.client
        self.km = KnowledgeManager.from_config(cfg)
        self.analyzer = Analyzer(self.llm, self.km, self.web)
        self.analyze_worker.analyzer = self.analyzer
        # 左下角设置模块摘要实时刷新
        self.settings_footer.update_config(cfg)
        self.setStyleSheet(get_qss("admin"))
        self.status_bar.showMessage("配置已保存并热重载", 3000)
