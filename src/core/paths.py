"""路径管理单例（对应架构 §7.1）。

所有文件读写只允许经过 Paths 单例，禁止在代码里硬编码绝对路径。
根目录默认：C:/Users/liwanghao/Desktop/AI工具文件/v1.0.0
可被环境变量 H3C_OPS_ROOT 覆盖，便于开发/测试。
"""

from __future__ import annotations

import os
from pathlib import Path


# 默认数据根目录（启动目录）。可由环境变量 H3C_OPS_ROOT 覆盖。
_DEFAULT_ROOT = Path(r"C:\Users\liwanghao\Desktop\AI工具文件\v1.0.0")


class Paths:
    """进程内唯一的路径管理单例。

    提供 config_dir / knowledge_dir / logs_dir / updates_dir 四个子目录属性，
    首次访问时按需创建（mkdir(parents=True, exist_ok=True)）。
    """

    _instance: "Paths | None" = None

    def __init__(self, root: Path) -> None:
        self.ROOT: Path = Path(root).resolve()
        # 子目录名（相对 ROOT）
        self._config_rel = "config"
        self._knowledge_rel = "knowledge"
        self._logs_rel = "logs"
        self._updates_rel = "updates"
        # 确保根目录存在
        self.ROOT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance(cls) -> "Paths":
        """返回单例；首次调用按环境变量或默认值初始化。"""
        if cls._instance is None:
            env = os.environ.get("H3C_OPS_ROOT")
            root = Path(env) if env else _DEFAULT_ROOT
            cls._instance = cls(root)
        return cls._instance

    @classmethod
    def reset(cls, root: Path) -> "Paths":
        """测试/重定位用：重建单例并清空缓存。"""
        cls._instance = cls(root)
        return cls._instance

    # ---- 子目录（按需创建） ----
    @property
    def config_dir(self) -> Path:
        p = self.ROOT / self._config_rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def knowledge_dir(self) -> Path:
        p = self.ROOT / self._knowledge_rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.ROOT / self._logs_rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def updates_dir(self) -> Path:
        p = self.ROOT / self._updates_rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---- 常用文件 ----
    @property
    def config_file(self) -> Path:
        return self.config_dir / "app.yaml"

    @property
    def config_example_file(self) -> Path:
        return self.config_dir / "app.yaml.example"

    @property
    def version_file(self) -> Path:
        return self.updates_dir / "version.json"

    def resolve(self, rel: str) -> Path:
        """将相对 ROOT 的路径解析为绝对 Path。"""
        return (self.ROOT / rel).resolve()
