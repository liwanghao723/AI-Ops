"""配置加载 / 校验 / 热重载（对应架构 §7.2 schema）。

配置主文件：<ROOT>/config/app.yaml
模板文件：<ROOT>/config/app.yaml.example（首次缺失时拷贝生成）

AppConfig 启动时校验必填项（platform.base_url/user/password、
llm.base_url/api_key/model）。缺失则记录错误日志并抛 ConfigError。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import Paths
from .logging_setup import get_logger


logger = get_logger(__name__)


@dataclass
class PlatformCfg:
    base_url: str = "http://10.1.1.201:8083"
    user: str = ""
    password: str = ""
    verify_ssl: bool = False
    # 平台「前端会话」登录凭据（Spring Cookie 域 /uis/spring_check）。
    # 说明：H3C Workspace 有两套认证域——
    #   * Digest 域 (/vdi/rest/workspace/...)：用上面的 user/password；
    #   * Spring Cookie 域 (/vdi/warnManage/...)：用下面的 frontend_*。
    # 两者的密码在多数现场 **不相同**（平台前端对密码做了 DES 加密提交）。
    # 只有配置了 frontend_password，软件才能走平台告警管理页同款接口
    #   GET /vdi/warnManage/realTimeAlarms （真实总数、offset 可翻页、
    #   state 过滤生效、覆盖 License/终端/虚拟应用等多子系统告警）；
    # 留空则回退旧接口 /vdi/rest/workspace/realtimeAlarms/list
    #   （总量被硬截断，且拿不到 License/终端/虚拟应用类告警）。
    frontend_user: str = "admin"
    frontend_password: str = ""


@dataclass
class LLMCfg:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    timeout: float = 60.0


@dataclass
class RAGCfg:
    top_k: int = 3
    use_vector: bool = False
    similarity_threshold: float = 0.3
    ima_enabled: bool = True
    ima_endpoint: str = ""          # IMA OpenAPI 基址，留空则用默认 https://ima.qq.com
    ima_client_id: str = ""         # IMA OpenAPI Client ID（ima.qq.com/agent-interface）
    ima_api_key: str = ""           # IMA OpenAPI API Key


@dataclass
class HealthCfg:
    alarm_refresh_sec: int = 30
    threshold_cpu: int = 85
    threshold_mem: int = 85
    max_concurrency: int = 5


@dataclass
class UpdateCfg:
    source: str = "updates/version.json"
    remote_url: str = ""
    verify_checksum: bool = False


@dataclass
class UICfg:
    theme: str = "dark"


@dataclass
class AppConfig:
    """聚合全部配置分组的顶层配置对象。"""

    platform: PlatformCfg = field(default_factory=PlatformCfg)
    llm: LLMCfg = field(default_factory=LLMCfg)
    rag: RAGCfg = field(default_factory=RAGCfg)
    health: HealthCfg = field(default_factory=HealthCfg)
    update: UpdateCfg = field(default_factory=UpdateCfg)
    ui: UICfg = field(default_factory=UICfg)
    # 本地知识库相对 ROOT 的目录名（§7.2 knowledge_dir 字段）
    knowledge_dir: str = "knowledge"

    _raw: dict = field(default_factory=dict, repr=False)

    # -------- 加载 --------
    @classmethod
    def load(cls, path: "str | Path | None" = None) -> "AppConfig":
        """加载配置。path 缺省时按 Paths 单例定位 app.yaml。

        若 app.yaml 缺失但有 example，则拷贝 example 并提示用户填写后
        继续以 example 的内容加载（保证程序可启动进入配置界面）。
        """
        paths = Paths.instance()
        cfg_path = Path(path) if path else paths.config_file

        if not cfg_path.exists():
            example = paths.config_example_file
            if example.exists():
                logger.warning(
                    "配置文件缺失，已从模板拷贝生成: %s -> %s", example, cfg_path
                )
                cfg_path.write_text(
                    example.read_text(encoding="utf-8"), encoding="utf-8"
                )
            else:
                # 连模板都没有：用内置默认 schema 写一个最小 example
                logger.warning("配置模板也不存在，写入内置默认模板: %s", cfg_path)
                cfg_path.write_text(_DEFAULT_YAML, encoding="utf-8")

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg = cls.from_dict(raw)
        cfg._raw = raw
        cfg._validate()
        logger.info("配置加载完成: %s", cfg_path)
        return cfg

    @classmethod
    def from_dict(cls, raw: dict) -> "AppConfig":
        def pick(section: str, model: type):
            return model(**{k: v for k, v in (raw.get(section) or {}).items()
                            if k in model.__dataclass_fields__})

        return cls(
            platform=pick("platform", PlatformCfg),
            llm=pick("llm", LLMCfg),
            rag=pick("rag", RAGCfg),
            health=pick("health", HealthCfg),
            update=pick("update", UpdateCfg),
            ui=pick("ui", UICfg),
            knowledge_dir=raw.get("knowledge_dir", "knowledge"),
        )

    # -------- 校验 --------
    def _validate(self) -> None:
        """校验必填项；缺失抛 ConfigError 并记录错误日志。"""
        errors: list[str] = []
        if not self.platform.base_url:
            errors.append("platform.base_url 缺失")
        if not self.platform.user:
            errors.append("platform.user 缺失")
        if not self.platform.password:
            errors.append("platform.password 缺失")
        if not self.llm.base_url:
            errors.append("llm.base_url 缺失")
        if not self.llm.api_key:
            errors.append("llm.api_key 缺失")
        if not self.llm.model:
            errors.append("llm.model 缺失")
        if errors:
            msg = "；".join(errors)
            logger.error("配置校验失败: %s", msg)
            from .errors import ConfigError
            raise ConfigError(msg)

    # -------- 保存 / 热重载 --------
    def to_dict(self) -> dict:
        """将当前配置导出为可写回 yaml 的 dict。"""
        return {
            "platform": self.platform.__dict__.copy(),
            "llm": self.llm.__dict__.copy(),
            "rag": self.rag.__dict__.copy(),
            "health": self.health.__dict__.copy(),
            "update": self.update.__dict__.copy(),
            "ui": self.ui.__dict__.copy(),
            "knowledge_dir": self.knowledge_dir,
        }

    def save(self, path: "str | Path | None" = None) -> None:
        """写回配置文件（配置对话框保存用）。"""
        paths = Paths.instance()
        cfg_path = Path(path) if path else paths.config_file
        cfg_path.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("配置已保存: %s", cfg_path)

    def reload(self) -> "AppConfig":
        """重新从磁盘加载（热重载）。"""
        return AppConfig.load(self.config_file if hasattr(self, "config_file") else None)


# ----------------------------------------------------------------------
# 内置默认配置模板（当磁盘上连 example 都没有时的最后兜底）
# ----------------------------------------------------------------------
_DEFAULT_YAML = """\
platform:
  base_url: "http://10.1.1.201:8083"
  user: "<外部接口认证用户-管理员>"
  password: "<password>"
  verify_ssl: false
  # 平台前端会话（告警管理页同款接口）登录凭据；留空则回退旧告警接口
  frontend_user: "admin"
  frontend_password: ""
llm:
  provider: "deepseek"
  base_url: "https://api.deepseek.com/v1"
  api_key: "<key>"
  model: "deepseek-chat"
  temperature: 0.3
  timeout: 60
rag:
  top_k: 3
  use_vector: false
  similarity_threshold: 0.3
  ima_enabled: true
  ima_endpoint: ""
  ima_client_id: ""
  ima_api_key: ""
knowledge_dir: "knowledge"
health:
  alarm_refresh_sec: 30
  threshold_cpu: 85
  threshold_mem: 85
  max_concurrency: 5
update:
  source: "updates/version.json"
  remote_url: ""
  verify_checksum: false
ui:
  theme: "dark"
"""
