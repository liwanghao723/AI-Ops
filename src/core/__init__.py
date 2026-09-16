"""core 包：基础设施层（路径/配置/日志/认证/REST 客户端/数据类/异常）。

本模块不依赖 PyQt5，可独立 import 用于单元/契约测试。
"""

from .paths import Paths
from .errors import (
    AuthError, ConfigError, H3COpsError, LLMError,
    NetworkError, RetrievalError, WorkspaceAPIError,
)
from .models import (
    Chunk, HealthRow, HostBrief, HostPerf, RpcPagingResult, RpcResult,
    RsDomainSummary, VersionInfo, VmBrief, VmDetail, VmPerf, WarnInfoDTO,
)
from .digest_auth import DigestAuthHTTP
from .workspace_client import H3CWorkspaceClient
from .config import AppConfig

__all__ = [
    "Paths", "AppConfig",
    "H3COpsError", "WorkspaceAPIError", "AuthError", "NetworkError",
    "LLMError", "RetrievalError", "ConfigError",
    "RpcResult", "RpcPagingResult",
    "WarnInfoDTO", "VmBrief", "RsDomainSummary", "HostPerf", "HealthRow",
    "Chunk", "VmDetail", "VmPerf", "HostBrief", "VersionInfo",
    "DigestAuthHTTP", "H3CWorkspaceClient",
]
