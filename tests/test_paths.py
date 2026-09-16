"""core.paths 单元测试：单例、子目录自动创建、环境变量覆盖、resolve。"""

from pathlib import Path

import core.paths as paths_mod
from core.paths import Paths


def test_paths_instance_is_singleton():
    a = Paths.instance()
    b = Paths.instance()
    assert a is b


def test_paths_subdirs_created_and_under_root():
    p = Paths.instance()
    for sub in (p.config_dir, p.knowledge_dir, p.logs_dir, p.updates_dir):
        assert sub.exists(), f"{sub} 应已自动创建"
        assert sub.parent == p.ROOT, f"{sub} 应位于 ROOT 下"
        assert sub.name in ("config", "knowledge", "logs", "updates")


def test_paths_resolve_appends_relative():
    p = Paths.instance()
    assert p.resolve("a/b.txt") == p.ROOT / "a" / "b.txt"


def test_paths_well_known_files():
    p = Paths.instance()
    assert p.config_file == p.config_dir / "app.yaml"
    assert p.config_example_file == p.config_dir / "app.yaml.example"
    assert p.version_file == p.updates_dir / "version.json"


def test_paths_env_override(monkeypatch, tmp_path):
    # 清空单例，让 instance() 重新读取环境变量
    monkeypatch.delenv("H3C_OPS_ROOT", raising=False)
    Paths._instance = None
    monkeypatch.setenv("H3C_OPS_ROOT", str(tmp_path))
    p = Paths.instance()
    assert p.ROOT == tmp_path.resolve()
    # 子目录仍按需创建
    assert p.knowledge_dir.exists()
