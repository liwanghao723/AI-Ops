"""core.config 单元测试：app.yaml 加载/默认值/字段校验/保存往返。"""

import pytest

from core.config import AppConfig
from core.errors import ConfigError

VALID = """
platform:
  base_url: "http://10.1.1.201:8083"
  user: "admin"
  password: "secret"
  verify_ssl: false
llm:
  provider: "deepseek"
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-xxx"
  model: "deepseek-chat"
  temperature: 0.3
  timeout: 60
rag:
  top_k: 3
  use_vector: false
health:
  alarm_refresh_sec: 30
  threshold_cpu: 85
update:
  source: "updates/version.json"
ui:
  theme: "dark"
"""


def test_load_full_config(tmp_path):
    f = tmp_path / "app.yaml"
    f.write_text(VALID, encoding="utf-8")
    cfg = AppConfig.load(f)
    assert cfg.platform.base_url == "http://10.1.1.201:8083"
    assert cfg.platform.user == "admin"
    assert cfg.platform.verify_ssl is False
    assert cfg.llm.api_key == "sk-xxx"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.rag.top_k == 3
    assert cfg.health.threshold_cpu == 85
    assert cfg.ui.theme == "dark"


def test_load_applies_defaults_for_missing_sections(tmp_path):
    f = tmp_path / "app.yaml"
    f.write_text("""
platform:
  base_url: "http://x"
  user: "u"
  password: "p"
llm:
  base_url: "http://llm"
  api_key: "k"
  model: "m"
""", encoding="utf-8")
    cfg = AppConfig.load(f)
    # 缺失分组使用 dataclass 默认值
    assert cfg.rag.top_k == 3
    assert cfg.health.alarm_refresh_sec == 30
    assert cfg.update.source == "updates/version.json"
    assert cfg.ui.theme == "dark"
    # knowledge_dir 顶层字段默认
    assert cfg.knowledge_dir == "knowledge"


def test_validate_missing_required_raises(tmp_path):
    f = tmp_path / "app.yaml"
    f.write_text("""
platform:
  base_url: "http://x"
  user: ""
  password: "p"
llm:
  base_url: "http://llm"
  api_key: "k"
  model: "m"
""", encoding="utf-8")
    with pytest.raises(ConfigError):
        AppConfig.load(f)


def test_save_roundtrip(tmp_path):
    f = tmp_path / "app.yaml"
    f.write_text(VALID, encoding="utf-8")
    cfg = AppConfig.load(f)
    cfg.llm.temperature = 0.7
    cfg.platform.user = "root"
    out = tmp_path / "out.yaml"
    cfg.save(out)
    cfg2 = AppConfig.load(out)
    assert cfg2.llm.temperature == 0.7
    assert cfg2.platform.user == "root"
    assert cfg2.llm.api_key == "sk-xxx"
