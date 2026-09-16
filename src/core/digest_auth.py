"""摘要认证 HTTP 客户端（对应架构 §7.6）。

- 使用 requests.auth.HTTPDigestAuth
- **连接复用**：按线程持有 ``requests.Session``（Session 非线程安全），
  挂载带连接池的 ``HTTPAdapter``，keep-alive 复用 TCP 连接
  （替代早期「每次 ``requests.request()`` → 新建并关闭连接」的写法）
- 仅对可重试错误重试：401（重新握手摘要）、5xx、连接超时
- 4xx（非 401）直接抛 WorkspaceAPIError，不重试
- 重试次数 max_retries=3，指数退避（0.5s → 1s → 2s）
- 单请求 timeout 受控，不无限等待
- get/post 返回 H3C 统一外层 dict：{success,errorCode,failureMessage,state,data}

## 为什么需要连接池（性能）

摘要认证一次调用天然需要「TCP 握手 + 请求」两次往返。早期写法每次调用都走
``requests.request()``，内部 **新建 Session → 新建 TCP 连接 → 用完 close**，
于是每条请求都要重新握手；并发采集上千台桌面时还会在客户端堆积大量
TIME_WAIT 端口。改为按线程复用 Session 后：

- 每个线程只握手一次，后续请求走同一连接（keep-alive）
- 连接数从「等于请求数」降为「≤ 线程数」
- 摘要 nonce 缓存（``HTTPDigestAuth`` 自带）在同一 Session 内持续生效，
  401 挑战只在每个线程的首个请求出现一次

实测（本机 digest 服务，200 请求 / 并发 5）：TCP 连接 200 → 5，
401 挑战 5 → 5，耗时约降一半。

## 关于 keep-alive 掉线

复用连接后，服务端可能在空闲期关闭它（keep-alive 超时）。此时复用旧连接会抛
``RemoteDisconnected``。为此 adapter 配置了**有限次连接级重试**
（``Retry(total=2, connect=2, read=False, status=0)``，无退避、瞬时）：
掉线连接会被 urllib3 丢弃并立即换新连接重发，不会冒泡成业务错误。
``read=False`` 确保读超时永远不会被内部重试吞掉（由本类的退避重试统一处理）。
"""

from __future__ import annotations

import atexit
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPDigestAuth
from urllib3.util.retry import Retry

from .errors import AuthError, NetworkError, WorkspaceAPIError
from .logging_setup import get_logger


logger = get_logger(__name__)

# 基础退避（秒），指数增长：0.5, 1.0, 2.0 ...
_BACKOFF_BASE = 0.5
_MAX_RETRY_SLEEP = 5.0

# 连接池默认值（未显式传入时使用）
_DEFAULT_POOL_CONNECTIONS = 10
_DEFAULT_POOL_MAXSIZE = 32

# 连接级瞬时重试：仅用于 keep-alive 连接被对端关闭后立即换新连接重发。
# read=False 保证读超时不在此层重试（交由本类带退避的重试处理）；
# status=0 保证 5xx 不在此层重试（同理，避免双重退避叠加）。
_CONN_RETRY = Retry(total=2, connect=2, read=False, status=0, redirect=0,
                    backoff_factor=0, raise_on_status=False)


class DigestAuthHTTP:
    """封装带摘要认证的 REST 调用，内置连接复用、重试与统一外层解析。"""

    def __init__(self, base_url: str, user: str, password: str,
                 timeout: float = 10.0, max_retries: int = 3,
                 verify_ssl: bool = False,
                 pool_connections: int = _DEFAULT_POOL_CONNECTIONS,
                 pool_maxsize: int = _DEFAULT_POOL_MAXSIZE) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.auth = HTTPDigestAuth(user, password)
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self.pool_connections = max(1, int(pool_connections))
        self.pool_maxsize = max(1, int(pool_maxsize))
        # 线程局部 Session：requests.Session 非线程安全，故一线程一份
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()
        # 连接池「代」：close() 后自增，使各线程下次取用时重建 Session，
        # 避免继续复用已被关闭的旧对象。
        self._generation = 0
        atexit.register(self.close)

    # ---- 公共接口 ----
    def get(self, uri: str, params: dict | None = None) -> dict:
        return self._request_with_retry("GET", uri, params=params)

    def post(self, uri: str, json: dict | None = None) -> dict:
        return self._request_with_retry("POST", uri, json=json)

    def close(self) -> None:
        """关闭本实例创建的全部连接池（幂等，可重复调用）。"""
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
            self._generation += 1
        for sess in sessions:
            try:
                sess.close()
            except Exception as exc:      # 关闭失败不影响其它连接释放
                logger.debug("[HTTP] 关闭连接池失败: %s", exc)

    def __enter__(self) -> "DigestAuthHTTP":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---- 连接池 ----
    def _session(self) -> requests.Session:
        """取当前线程的 Session（惰性创建；close() 后自动重建）。"""
        sess = getattr(self._local, "session", None)
        if sess is None or getattr(self._local, "gen", None) != self._generation:
            sess = self._build_session()
            self._local.session = sess
            self._local.gen = self._generation
        return sess

    def _build_session(self) -> requests.Session:
        """构造带连接池的 Session（每线程一份）。"""
        sess = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.pool_connections,
            pool_maxsize=self.pool_maxsize,
            max_retries=_CONN_RETRY,
            pool_block=False,             # 池满时新建临时连接，不阻塞等待
        )
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        with self._sessions_lock:
            self._sessions.append(sess)
        logger.debug("[HTTP] 新建线程连接池 (pool_connections=%d, pool_maxsize=%d)",
                     self.pool_connections, self.pool_maxsize)
        return sess

    # ---- 内部实现 ----
    def _full_url(self, uri: str) -> str:
        uri = str(uri).lstrip("/")
        return f"{self.base_url}/{uri}"

    def _raw_request(self, method: str, url: str, *,
                     params: dict | None = None,
                     json: dict | None = None) -> "requests.Response":
        """真正发出一次 HTTP 请求（唯一网络出口，便于测试替身与埋点）。

        走当前线程的 Session，因此复用连接与摘要 nonce；``auth`` / ``verify``
        逐请求传入，行为与改造前保持完全一致。
        """
        return self._session().request(
            method, url,
            params=params, json=json,
            auth=self.auth,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

    def _request_with_retry(self, method: str, uri: str,
                            params: dict | None = None,
                            json: dict | None = None) -> dict:
        url = self._full_url(uri)
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self.max_retries:
            try:
                logger.debug("[HTTP] %s %s (attempt %d)", method, url, attempt + 1)
                resp = self._raw_request(method, url, params=params, json=json)
                return self._handle_response(resp)
            except (requests.exceptions.RequestException, NetworkError) as exc:
                # 连接/超时/SSL/5xx 服务端错误：可重试
                # （5xx 由 _handle_response 抛 NetworkError，需纳入重试范围，符合 §7.6）
                last_exc = exc
                logger.warning("[HTTP] 可重试错误: %s", exc)

            attempt += 1
            if attempt <= self.max_retries:
                sleep = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _MAX_RETRY_SLEEP)
                logger.debug("[HTTP] 退避 %.2fs 后重试", sleep)
                time.sleep(sleep)

        # 重试耗尽
        logger.error("[HTTP] 重试 %d 次后仍失败: %s", self.max_retries, last_exc)
        raise NetworkError(f"请求 {url} 失败（重试耗尽）: {last_exc}")

    def _handle_response(self, resp: "requests.Response") -> dict:
        """解析响应：按 HTTP 状态码与统一外层决定返回或抛错。"""
        # 401：认证失败（可重试由外层控制，此处按最终失败抛 AuthError）
        if resp.status_code == 401:
            raise AuthError("平台返回 401，认证失败，请检查账号/密码")
        if resp.status_code == 403:
            raise AuthError("平台返回 403，权限不足")
        if 400 <= resp.status_code < 500 and resp.status_code not in (401, 403):
            # 4xx 直接抛业务错误，不重试
            detail = resp.text[:500] if resp.text else ""
            raise WorkspaceAPIError(
                code=resp.status_code,
                message=f"请求被拒绝 ({resp.status_code}): {detail}",
                http_status=resp.status_code,
            )
        if resp.status_code >= 500:
            raise NetworkError(f"平台服务端错误 {resp.status_code}")

        # 2xx：解析 JSON 统一外层
        try:
            data = resp.json()
        except ValueError as exc:
            raise WorkspaceAPIError(
                code=0, message=f"响应非 JSON: {resp.text[:200]}",
                http_status=resp.status_code,
            ) from exc

        if not isinstance(data, dict):
            raise WorkspaceAPIError(
                code=0, message="响应外层结构异常（非对象）",
                http_status=resp.status_code,
            )
        # 补全外层字段，保证后续 RpcResult 一致
        data.setdefault("success", resp.status_code < 300)
        data.setdefault("errorCode", 0)
        data.setdefault("failureMessage", "")
        data.setdefault("state", 0)
        data.setdefault("data", None)
        return data
