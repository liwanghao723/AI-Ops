"""异常定义与错误码映射（对应架构 §7.4）。

统一异常体系：
- WorkspaceAPIError(code, msg)：来自 H3C 外层 errorCode / failureMessage
- AuthError：401 / success=False 且为认证问题
- NetworkError：网络超时 / 连接失败
- LLMError：LLM 调用失败
- RetrievalError：检索失败
- ConfigError：配置校验/加载失败

所有 Worker 捕获后转 error(str) 信号，UI 以状态栏/弹窗提示，不崩溃。
"""

from __future__ import annotations


class H3COpsError(Exception):
    """所有本应用异常的基类。"""


class WorkspaceAPIError(H3COpsError):
    """H3C Workspace REST 接口错误。

    通常由 RpcResult.raise_if_fail() 抛出，携带平台返回的错误码与消息。
    """

    def __init__(self, code: int, message: str, *, http_status: int | None = None):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[WorkspaceAPI code={code}] {message}")


class AuthError(WorkspaceAPIError):
    """认证失败（401 / 摘要握手失败）。"""

    def __init__(self, message: str = "认证失败，请检查平台账号/密码"):
        super().__init__(code=401, message=message)


class NetworkError(H3COpsError):
    """网络层错误：连接失败 / 超时。"""

    def __init__(self, message: str = "网络连接失败或超时"):
        super().__init__(message)


class LLMError(H3COpsError):
    """大模型调用失败。"""

    def __init__(self, message: str = "大模型调用失败"):
        super().__init__(message)


class RetrievalError(H3COpsError):
    """知识检索失败。"""

    def __init__(self, message: str = "知识检索失败"):
        super().__init__(message)


class ConfigError(H3COpsError):
    """配置加载/校验失败。"""

    def __init__(self, message: str = "配置错误"):
        super().__init__(message)


def map_http_status(http_status: int, api_error_code: int = 0,
                    failure_message: str = "") -> type[H3COpsError] | None:
    """根据 HTTP 状态码映射异常类型（供 digest_auth 重试/抛错前判定）。

    返回异常类（未实例化）或 None（表示可重试，不抛错）。
    """
    if http_status == 401:
        return AuthError
    if http_status == 403:
        return AuthError
    if 400 <= http_status < 500 and http_status not in (401, 403):
        # 4xx（非 401/403）直接视为业务错误，由 WorkspaceAPIError 承载
        return WorkspaceAPIError
    if http_status >= 500:
        # 5xx 视为可重试，调用方决定是否抛 NetworkError
        return NetworkError
    return None


__all__ = [
    "H3COpsError",
    "WorkspaceAPIError",
    "AuthError",
    "NetworkError",
    "LLMError",
    "RetrievalError",
    "ConfigError",
    "map_http_status",
]
