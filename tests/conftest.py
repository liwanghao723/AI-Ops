"""pytest 配置：把项目源码 src/ 加入 sys.path，并将 Paths 单例指向临时根目录。

所有测试共享一个临时数据根目录（_TEST_ROOT），避免写入真实项目目录
（config/knowledge/logs/updates）。通过 H3C_OPS_ROOT 环境变量 + Paths.reset
双重保证，使日志文件、子目录创建都落在临时区，互不污染。
"""

import os
import sys
import tempfile
from pathlib import Path

# ---- GUI 测试统一使用离屏平台，避免在打包/无显示器环境弹出真实窗口 ----
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- 将 src 加入模块搜索路径，使 `core`/`ai`/`update`/`workers` 可被导入 ----
ROOT = Path(__file__).resolve().parent.parent  # v1.0.0
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---- 准备隔离的测试根目录，并在导入 core 前注入环境变量 ----
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="h3c_ops_qa_"))
os.environ["H3C_OPS_ROOT"] = str(_TEST_ROOT)

import core.paths as _core_paths  # noqa: E402  (触发 core 包 __init__ -> 日志初始化)

# 确保单例指向临时根目录（即便环境变量被后续测试改动）
_core_paths.Paths.reset(_TEST_ROOT)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_paths():
    """每个测试前重置 Paths 单例到隔离根目录，保证用例互相独立。"""
    _core_paths.Paths.reset(_TEST_ROOT)
    yield
