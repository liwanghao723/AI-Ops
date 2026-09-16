"""配置编辑对话框（对应架构 T16 / ui/config_dialog.py）。

可视化修改 config/app.yaml 并保存（支持热重载不重启即生效的部分：
LLM 厂商、刷新间隔、阈值）。保存后发出 saved(AppConfig) 信号。
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QPushButton, QTabWidget, QWidget, QVBoxLayout, QDialogButtonBox, QLabel,
)
from PyQt5.QtCore import pyqtSignal, Qt

from core.config import AppConfig
from core.logging_setup import get_logger


logger = get_logger(__name__)


class ConfigDialog(QDialog):
    saved = pyqtSignal(AppConfig)

    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置 — H3C 云桌面智能运维助手")
        self.resize(520, 480)
        self._build()

    def _build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._platform_tab(), "H3C 平台")
        tabs.addTab(self._llm_tab(), "大模型")
        tabs.addTab(self._rag_tab(), "知识库")
        tabs.addTab(self._health_tab(), "健康/告警")
        tabs.addTab(self._update_tab(), "更新")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

        box = QDialogButtonBox()
        self.btn_save = box.addButton("保存", QDialogButtonBox.AcceptRole)
        self.btn_test = box.addButton("测试平台连接", QDialogButtonBox.ActionRole)
        box.addButton("取消", QDialogButtonBox.RejectRole)
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        self.btn_test.clicked.connect(self._on_test)
        layout.addWidget(box)

    # ---- 各 Tab ----
    def _platform_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.le_base = QLineEdit(self.cfg.platform.base_url)
        self.le_user = QLineEdit(self.cfg.platform.user)
        self.le_pass = QLineEdit(self.cfg.platform.password)
        self.le_pass.setEchoMode(QLineEdit.Password)
        self.cb_ssl = QComboBox()
        self.cb_ssl.addItems(["false", "true"])
        self.cb_ssl.setCurrentText("true" if self.cfg.platform.verify_ssl else "false")
        # 平台前端会话凭据（告警管理页同款接口 /vdi/warnManage/realTimeAlarms）
        self.le_fuser = QLineEdit(
            str(getattr(self.cfg.platform, "frontend_user", "") or "admin"))
        self.le_fpass = QLineEdit(
            str(getattr(self.cfg.platform, "frontend_password", "") or ""))
        self.le_fpass.setEchoMode(QLineEdit.Password)
        self.le_fpass.setPlaceholderText("留空则告警回退旧接口（会漏告警）")
        f.addRow("地址:", self.le_base)
        f.addRow("账号:", self.le_user)
        f.addRow("密码:", self.le_pass)
        f.addRow("校验SSL:", self.cb_ssl)
        f.addRow("前端账号:", self.le_fuser)
        f.addRow("前端密码:", self.le_fpass)
        hint = QLabel(
            "「前端账号/密码」= 浏览器登录 H3C 平台网页用的账号密码"
            "（走 /uis/spring_check），与上面的接口密码通常不同。\n"
            "填写后告警走平台「告警管理页」同款接口，可拿到完整告警"
            "（含 License / 终端 / 虚拟应用等多子系统）；\n留空则回退旧接口，"
            "会因总量截断而漏掉一部分未确认告警。")
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return w

    def _llm_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.cb_provider = QComboBox()
        self.cb_provider.addItems(["deepseek", "glm", "qwen"])
        self.cb_provider.setCurrentText(self.cfg.llm.provider)
        self.le_llm_base = QLineEdit(self.cfg.llm.base_url)
        self.le_api_key = QLineEdit(self.cfg.llm.api_key)
        self.le_api_key.setEchoMode(QLineEdit.Password)
        self.le_model = QLineEdit(self.cfg.llm.model)
        self.spin_temp = QSpinBox()
        self.spin_temp.setRange(0, 100)
        self.spin_temp.setValue(int(self.cfg.llm.temperature * 100))
        f.addRow("厂商:", self.cb_provider)
        f.addRow("base_url:", self.le_llm_base)
        f.addRow("api_key:", self.le_api_key)
        f.addRow("模型:", self.le_model)
        f.addRow("温度(×100):", self.spin_temp)
        return w

    def _rag_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        f = QFormLayout()
        self.spin_topk = QSpinBox(); self.spin_topk.setRange(1, 20)
        self.spin_topk.setValue(self.cfg.rag.top_k)
        self.cb_vector = QComboBox(); self.cb_vector.addItems(["false", "true"])
        self.cb_vector.setCurrentText("true" if self.cfg.rag.use_vector else "false")
        self.cb_ima = QComboBox(); self.cb_ima.addItems(["true", "false"])
        self.cb_ima.setCurrentText("true" if self.cfg.rag.ima_enabled else "false")
        self.le_ima_ep = QLineEdit(self.cfg.rag.ima_endpoint)
        self.le_ima_id = QLineEdit(self.cfg.rag.ima_client_id)
        self.le_ima_id.setPlaceholderText("ima.qq.com/agent-interface 获取")
        self.le_ima_key = QLineEdit(self.cfg.rag.ima_api_key)
        self.le_ima_key.setEchoMode(QLineEdit.Password)
        self.le_ima_key.setPlaceholderText("IMA OpenAPI API Key")
        self.le_kb_dir = QLineEdit(self.cfg.knowledge_dir)
        f.addRow("top_k:", self.spin_topk)
        f.addRow("向量模式:", self.cb_vector)
        f.addRow("IMA 启用:", self.cb_ima)
        f.addRow("IMA 端点:", self.le_ima_ep)
        f.addRow("IMA Client ID:", self.le_ima_id)
        f.addRow("IMA API Key:", self.le_ima_key)
        f.addRow("本地知识库目录(相对ROOT):", self.le_kb_dir)
        outer.addLayout(f)

        # 「测试 IMA 连接」：用当前输入框内容真实枚举知识库并就地反馈结果
        self.btn_test_ima = QPushButton("测试 IMA 连接")
        self.btn_test_ima.setProperty("cssClass", "secondary")
        self.btn_test_ima.clicked.connect(self._on_test_ima)
        outer.addWidget(self.btn_test_ima, alignment=Qt.AlignLeft)
        self.lbl_ima_test = QLabel("")
        self.lbl_ima_test.setObjectName("ImaTestLabel")
        self.lbl_ima_test.setWordWrap(True)
        self.lbl_ima_test.setVisible(False)
        outer.addWidget(self.lbl_ima_test)
        outer.addStretch(1)
        return w

    def _set_ima_test_state(self, text: str, state: str) -> None:
        """更新「测试 IMA 连接」结果标签（state: ok / err / busy）。"""
        self.lbl_ima_test.setText(text)
        self.lbl_ima_test.setProperty("imaState", state)
        self.lbl_ima_test.setVisible(True)
        self.lbl_ima_test.style().unpolish(self.lbl_ima_test)
        self.lbl_ima_test.style().polish(self.lbl_ima_test)

    def _on_test_ima(self) -> None:
        """测试 IMA 连通性：用当前输入框的凭证真实枚举知识库。"""
        from ai.ima_retriever import ImaRetriever
        self._set_ima_test_state("⏳ 正在测试 IMA 连接…", "busy")
        retriever = ImaRetriever(
            endpoint=self.le_ima_ep.text().strip(),
            enabled=self.cb_ima.currentText() == "true",
            client_id=self.le_ima_id.text().strip(),
            api_key=self.le_ima_key.text().strip(),
        )
        try:
            ok, msg = retriever.check_connection()
        except Exception as exc:
            ok, msg = False, f"测试异常：{exc}"
        self._set_ima_test_state(("✅ " if ok else "⚠️ ") + msg,
                                 "ok" if ok else "err")
        logger.info("[ConfigDialog] IMA 连接测试: ok=%s msg=%s", ok, msg)

    def _health_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.spin_refresh = QSpinBox(); self.spin_refresh.setRange(10, 300)
        self.spin_refresh.setValue(self.cfg.health.alarm_refresh_sec)
        self.spin_cpu = QSpinBox(); self.spin_cpu.setRange(50, 100)
        self.spin_cpu.setValue(self.cfg.health.threshold_cpu)
        self.spin_mem = QSpinBox(); self.spin_mem.setRange(50, 100)
        self.spin_mem.setValue(self.cfg.health.threshold_mem)
        self.spin_conc = QSpinBox(); self.spin_conc.setRange(1, 20)
        self.spin_conc.setValue(self.cfg.health.max_concurrency)
        f.addRow("告警刷新间隔(s):", self.spin_refresh)
        f.addRow("CPU 阈值(%):", self.spin_cpu)
        f.addRow("内存 阈值(%):", self.spin_mem)
        f.addRow("并发数:", self.spin_conc)
        return w

    def _update_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.le_src = QLineEdit(self.cfg.update.source)
        self.le_remote = QLineEdit(self.cfg.update.remote_url)
        self.cb_chk = QComboBox(); self.cb_chk.addItems(["false", "true"])
        self.cb_chk.setCurrentText("true" if self.cfg.update.verify_checksum else "false")
        f.addRow("版本源:", self.le_src)
        f.addRow("远程URL:", self.le_remote)
        f.addRow("校验checksum:", self.cb_chk)
        return w

    # ---- 操作 ----
    def _collect(self) -> AppConfig:
        self.cfg.platform.base_url = self.le_base.text().strip()
        self.cfg.platform.user = self.le_user.text().strip()
        self.cfg.platform.password = self.le_pass.text()
        self.cfg.platform.verify_ssl = self.cb_ssl.currentText() == "true"
        self.cfg.platform.frontend_user = self.le_fuser.text().strip() or "admin"
        self.cfg.platform.frontend_password = self.le_fpass.text()
        self.cfg.llm.provider = self.cb_provider.currentText()
        self.cfg.llm.base_url = self.le_llm_base.text().strip()
        self.cfg.llm.api_key = self.le_api_key.text()
        self.cfg.llm.model = self.le_model.text().strip()
        self.cfg.llm.temperature = self.spin_temp.value() / 100.0
        self.cfg.rag.top_k = self.spin_topk.value()
        self.cfg.rag.use_vector = self.cb_vector.currentText() == "true"
        self.cfg.rag.ima_enabled = self.cb_ima.currentText() == "true"
        self.cfg.rag.ima_endpoint = self.le_ima_ep.text().strip()
        self.cfg.rag.ima_client_id = self.le_ima_id.text().strip()
        self.cfg.rag.ima_api_key = self.le_ima_key.text()
        self.cfg.knowledge_dir = self.le_kb_dir.text().strip() or "knowledge"
        self.cfg.health.alarm_refresh_sec = self.spin_refresh.value()
        self.cfg.health.threshold_cpu = self.spin_cpu.value()
        self.cfg.health.threshold_mem = self.spin_mem.value()
        self.cfg.health.max_concurrency = self.spin_conc.value()
        self.cfg.update.source = self.le_src.text().strip()
        self.cfg.update.remote_url = self.le_remote.text().strip()
        self.cfg.update.verify_checksum = self.cb_chk.currentText() == "true"
        return self.cfg

    def _on_save(self) -> None:
        cfg = self._collect()
        try:
            cfg.save()
            logger.info("[ConfigDialog] 配置已保存")
            self.saved.emit(cfg)
            self.accept()
        except Exception as exc:
            logger.error("[ConfigDialog] 保存失败: %s", exc)

    def _on_test(self) -> None:
        from core.workspace_client import H3CWorkspaceClient
        cfg = self._collect()
        client = None
        try:
            client = H3CWorkspaceClient.from_config(cfg)
            client.list_realtime_alarms(limit=1, offset=0)
            tip = "连接成功"
            # 前端会话（平台告警管理页同款接口）单独探测并回显角标，
            # 便于确认「前端密码」是否填对、告警能否取全。
            if getattr(client, "frontend", None) is not None:
                try:
                    counts = client.get_warn_count()
                    if counts:
                        tip = (f"连接成功；前端会话正常，当前未确认告警 "
                               f"紧急{counts.get('urgent', 0)}/"
                               f"重要{counts.get('important', 0)}/"
                               f"次要{counts.get('accessory', 0)}/"
                               f"提示{counts.get('warning', 0)}")
                    else:
                        tip = "连接成功；前端会话已登录，但告警角标为空"
                except Exception as exc:
                    tip = f"连接成功；前端会话不可用（告警回退旧接口）: {exc}"
            else:
                tip = "连接成功；未配置前端密码，告警走旧接口（会漏告警）"
            self.btn_test.setToolTip(tip)
        except Exception as exc:
            self.btn_test.setToolTip(f"连接失败: {exc}")
        finally:
            # 测试用的临时 client 用完即释放连接池，避免反复点击累积连接
            if client is not None:
                client.close()
