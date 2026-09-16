"""版本管理器（对应架构 T15 / update/version_manager.py）。

职责：
  - check_update()：读 update.source（默认本地 updates/version.json，
    字段 latest_version/download_url/notes；或配置 remote_url 远程），
    若最新版本高于内置 APP_VERSION 则返回 VersionInfo。
  - download(info)：将更新包下载到 updates/v{最新版本}/。
  - highest_local_version()：扫描 updates/v* 目录，返回语义版本最高者。

版本号读取来源（§7.7）：
  - 当前版本号：代码内置常量 APP_VERSION = "1.0.0"（与数据目录 v1.0.0 对应）。
  - 启动自动识别：扫描 updates/v* 取语义版本最高者作为本地可更新缓存。
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from core.logging_setup import get_logger
from core.models import VersionInfo
from core.paths import Paths


logger = get_logger(__name__)

# 内置当前版本号（与数据目录 v1.0.0 对应）
APP_VERSION = "1.0.0"

# 语义版本正则（支持 1 / 1.0 / 1.0.0）
_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*$")


def parse_version(v: str) -> tuple[int, int, int]:
    """解析语义版本为 (major, minor, patch) 元组，便于比较。"""
    m = _VERSION_RE.match(v or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def compare_version(a: str, b: str) -> int:
    """a > b 返回 1，a == b 返回 0，a < b 返回 -1。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa > pb:
        return 1
    if pa < pb:
        return -1
    return 0


class VersionManager:
    """版本检测 / 下载 / 目录识别。"""

    def __init__(self, source: str = "updates/version.json",
                 current: str = APP_VERSION, remote_url: str = "") -> None:
        self.source = source
        self.current_version = current
        self.remote_url = remote_url

    # ------------------------------------------------------------------
    def check_update(self) -> "VersionInfo | None":
        """检测新版本。返回 VersionInfo（有更新）或 None（已是最新/无源）。"""
        raw = self._load_source()
        if not raw:
            logger.debug("[Version] 无版本源或解析失败")
            return None
        latest = raw.get("latest_version") or raw.get("version") or ""
        if not latest:
            return None
        info = VersionInfo(
            current_version=self.current_version,
            latest_version=latest,
            download_url=raw.get("download_url", "") or raw.get("url", ""),
            notes=raw.get("notes", "") or raw.get("changelog", ""),
        )
        info.is_newer = compare_version(latest, self.current_version) > 0
        if info.is_newer:
            logger.info("[Version] 检测到新版本: %s -> %s", self.current_version, latest)
            return info
        logger.debug("[Version] 当前已是最新 (%s)", self.current_version)
        return None

    # ------------------------------------------------------------------
    def download(self, info: VersionInfo) -> Path:
        """下载更新包到 updates/v{latest_version}/，返回落地目录。"""
        target_dir = Paths.instance().updates_dir / f"v{info.latest_version}"
        target_dir.mkdir(parents=True, exist_ok=True)
        url = info.download_url
        if not url:
            logger.warning("[Version] 无 download_url，仅创建目录: %s", target_dir)
            return target_dir
        try:
            logger.info("[Version] 下载 %s -> %s", url, target_dir)
            resp = requests.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            file_name = url.rsplit("/", 1)[-1] or "update.pkg"
            out = target_dir / file_name
            with open(out, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info("[Version] 下载完成: %s", out)
            return target_dir
        except requests.RequestException as exc:
            logger.error("[Version] 下载失败: %s", exc)
            raise

    # ------------------------------------------------------------------
    def highest_local_version(self) -> str:
        """扫描 updates/v* 目录，返回语义版本最高的版本号字符串。"""
        updates_dir = Paths.instance().updates_dir
        best = ""
        best_tuple = (-1, -1, -1)
        for d in updates_dir.glob("v*"):
            if d.is_dir():
                v = d.name[1:]  # 去掉 'v' 前缀
                t = parse_version(v)
                if t > best_tuple:
                    best_tuple, best = t, v
        return best

    # ------------------------------------------------------------------
    def _load_source(self) -> dict:
        """读取版本源：remote_url 优先远程，否则按 source 路径。"""
        src = self.remote_url or self.source
        if src and (src.startswith("http://") or src.startswith("https://")):
            try:
                resp = requests.get(src, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("[Version] 远程版本源读取失败: %s", exc)
                return {}
        # 本地文件：source 相对 ROOT 或绝对路径
        p = Path(src)
        if not p.is_absolute():
            p = Paths.instance().resolve(src)
        if not p.exists():
            logger.debug("[Version] 本地版本源不存在: %s", p)
            return {}
        try:
            import json
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[Version] 本地版本源解析失败: %s", exc)
            return {}
