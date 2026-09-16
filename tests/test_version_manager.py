"""update.version_manager 单元测试：版本号比较、check_update、highest_local_version、download 离线部分。

用 tmp_path + Paths.reset 隔离真实目录；远程源用 monkeypatch 替换 requests.get。
"""

import json

import requests

from core.models import VersionInfo
from core.paths import Paths
from update.version_manager import (
    APP_VERSION,
    VersionManager,
    compare_version,
    parse_version,
)


def test_parse_and_compare_version():
    assert parse_version("1.0.0") == (1, 0, 0)
    assert parse_version("2") == (2, 0, 0)
    assert parse_version("1.2") == (1, 2, 0)
    assert parse_version("garbage") == (0, 0, 0)
    assert compare_version("1.0.0", "1.1.0") == -1
    assert compare_version("2.0.0", "1.9.9") == 1
    assert compare_version("1.0.0", "1.0.0") == 0
    assert compare_version("1.0", "1.0.0") == 0
    assert APP_VERSION == "1.0.0"


def test_check_update_newer(tmp_path):
    Paths.reset(tmp_path)
    p = Paths.instance()
    (p.updates_dir / "version.json").write_text(
        json.dumps({"latest_version": "1.0.1", "download_url": "http://x/u.pkg",
                    "notes": "修复告警刷新"}),
        encoding="utf-8",
    )
    vm = VersionManager(source="updates/version.json", current="1.0.0")
    info = vm.check_update()
    assert isinstance(info, VersionInfo)
    assert info.latest_version == "1.0.1"
    assert info.is_newer is True
    assert info.notes == "修复告警刷新"


def test_check_update_none_when_current(tmp_path):
    Paths.reset(tmp_path)
    p = Paths.instance()
    (p.updates_dir / "version.json").write_text(
        json.dumps({"latest_version": "1.0.0"}), encoding="utf-8")
    vm = VersionManager(source="updates/version.json", current="1.0.0")
    assert vm.check_update() is None


def test_check_update_missing_source(tmp_path):
    Paths.reset(tmp_path)
    vm = VersionManager(source="updates/version.json", current="1.0.0")
    assert vm.check_update() is None  # 版本源文件不存在


def test_highest_local_version(tmp_path):
    Paths.reset(tmp_path)
    p = Paths.instance()
    for v in ["v1.0.0", "v1.0.1", "v1.2.0", "v1.0.10"]:
        (p.updates_dir / v).mkdir(parents=True)
    vm = VersionManager(current="1.0.0")
    assert vm.highest_local_version() == "1.2.0"


def test_check_update_remote(monkeypatch, tmp_path):
    Paths.reset(tmp_path)

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"latest_version": "2.0.0", "download_url": "http://x/u",
                    "notes": "major"}

    def fake_get(url, timeout=None):
        return FakeResp()

    monkeypatch.setattr(requests, "get", fake_get)
    vm = VersionManager(source="updates/version.json", current="1.0.0",
                        remote_url="http://remote/version.json")
    info = vm.check_update()
    assert info.latest_version == "2.0.0"
    assert info.is_newer is True


def test_download_no_url_creates_dir(tmp_path):
    Paths.reset(tmp_path)
    vm = VersionManager(current="1.0.0")
    info = VersionInfo(current_version="1.0.0", latest_version="1.0.1",
                      download_url="", notes="")
    d = vm.download(info)
    assert d.exists() and d.name == "v1.0.1"
