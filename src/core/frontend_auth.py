"""前端 Spring 会话登录（对应平台「/uis/spring_check」）。

用途
====

H3C Workspace 平台有两套认证域：

- **Digest 域**（``/vdi/rest/workspace/...``）：每次请求挑战式摘要认证，
  适用绝大多数 VDI 管理接口；本项目主采集走 ``DigestAuthHTTP``。
- **Spring Cookie 域**（``/vdi/warnManage/...``、``/vdi/vip/desk/...`` 等）：
  走平台前端的 ``JSESSIONID + AC_TOKEN`` cookie；只有前端登录后才能访问。
  平台告警管理页面（``/vdi/monitor/alarmManagement/realtimeAlarm``）用的
  是这一套。

本模块封装 Spring Cookie 域的登录 + 会话复用：

- ``POST /uis/spring_check?encrypt=...&name=...&password=...``
  —— 密码经 ``CryptoJS.DES.encrypt`` 等价加密（key 前 8 字节 = ``"hph3c_z0"``，
  mode=ECB、padding=Pkcs7）。
- 登录成功后由服务端 ``Set-Cookie: JSESSIONID=...; AC_TOKEN=...``，本类
  在 ``requests.Session`` 上保留，后续调用直接复用 cookie。
- 提供 ``FrontendSession.get(uri)``，与 ``DigestAuthHTTP.get`` 接口对称，
  便于 ``workspace_client._request(prefix="/vdi")`` 复用。

设计原则
========

- **每实例一 Session**（不是全局单例），由 ``H3CWorkspaceClient`` 持有。
- **懒登录**：首次调用时才登录，登录失败抛 ``AuthError``。
- **自动重登**：401 后自动重登一次，仍失败才抛错（适用于会话过期场景）。
- **连接池**：复用 ``DigestAuthHTTP`` 的池化策略——每线程一份 ``Session``。

依赖
====

- ``pycryptodome``：DES/ECB/Pkcs7。
- ``requests``：标准 HTTP 客户端。
"""

from __future__ import annotations

import base64
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .digest_auth import _CONN_RETRY
from .errors import AuthError
from .logging_setup import get_logger


logger = get_logger(__name__)


# 平台前端 CryptoJS.DES 密钥（解析自 ``static/js/app.*.js``）。
# CryptoJS.DES 会截前 8 字节；与前端等价。
_FRONTEND_DES_KEY = b"hph3c_z0"   # 12 字节 UTF-8 字符串的前 8 字节

# 登录接口路径与告警接口前缀（前端 chunk alarmManagement/realtimeAlarm.*.js）。
_LOGIN_PATH = "/uis/spring_check"
_LOGIN_NAME = "admin"


def des_encrypt_password(plaintext: str) -> str:
    """DES/ECB/Pkcs7 加密（等价 CryptoJS.DES.encrypt）。

    Args:
        plaintext: 明文密码。

    Returns:
        base64 字符串。前端 ``spring_check`` 接口要求 ``encrypt`` 参数。
    """
    # 本地导入避免顶层依赖 pycryptodome（没装时其他模块仍可用）
    from Crypto.Cipher import DES  # type: ignore

    cipher = DES.new(_FRONTEND_DES_KEY[:8], DES.MODE_ECB)
    pt = plaintext.encode("utf-8")
    pad_len = 8 - (len(pt) % 8)
    pt = pt + bytes([pad_len]) * pad_len
    return base64.b64encode(cipher.encrypt(pt)).decode("ascii")


class FrontendSession:
    """平台前端 Spring 会话（cookie + AC_TOKEN）。

    Args:
        base_url: 平台根地址（如 ``http://10.1.1.201:8083``）。
        user: 前端登录用户名（实测均为 ``admin``）。
        password: 前端登录密码（实测与 digest 域密码不同；
            默认模板 ``WScloud@123456``）。
        timeout: 单请求超时（秒）。
        pool_connections / pool_maxsize: 连接池容量，与
            ``DigestAuthHTTP`` 对齐，保证 keep-alive 复用。
        verify_ssl: 是否校验证书。
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        timeout: float = 15.0,
        pool_connections: int = 8,
        pool_maxsize: int = 16,
        verify_ssl: bool = False,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.pool_connections = max(1, int(pool_connections))
        self.pool_maxsize = max(1, int(pool_maxsize))

        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()

    # ---- 连接池 ----
    def _session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = self._build_session()
            self._local.session = sess
        return sess

    def _build_session(self) -> requests.Session:
        sess = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.pool_connections,
            pool_maxsize=self.pool_maxsize,
            max_retries=_CONN_RETRY,
            pool_block=False,
        )
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        with self._sessions_lock:
            self._sessions.append(sess)
        return sess

    def close(self) -> None:
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        # 重置本地缓存，使下一次调用重建 Session（带新连接池）
        self._local = threading.local()

    # ---- 登录 ----
    def _login(self, sess: requests.Session) -> None:
        """执行 spring_check 登录，并把 cookie 留给 sess 复用。"""
        # 预热：GET /uis/login 触发 JSESSIONID 分配
        sess.get(f"{self.base_url}/uis/login", timeout=self.timeout,
                 verify=self.verify_ssl)
        encrypted = des_encrypt_password(self.password)
        r = sess.post(
            f"{self.base_url}{_LOGIN_PATH}",
            params={
                "name": self.user,
                "password": self.password,
                "encrypt": encrypted,
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        # spring_check 返 200 + JSON；online=false 表示密码错或账号不存在
        try:
            body = r.json()
        except ValueError as exc:
            raise AuthError(
                f"spring_check 响应非 JSON: {r.text[:200]}"
            ) from exc
        if not body.get("online"):
            raise AuthError(
                "前端登录失败: " + str(body.get("loginFailMessage")
                or body.get("failureMessage") or body)
            )
        # 校验 AC_TOKEN（部分新版本会强制校验）
        cookies = sess.cookies.get_dict()
        if not cookies.get("AC_TOKEN"):
            logger.warning("[FrontendSession] 登录成功但未拿到 AC_TOKEN cookie")
        logger.info("[FrontendSession] 前端登录成功 (cookies=%s)",
                    {k: v[:8] + "..." for k, v in cookies.items()})

    def _ensure_login(self, sess: requests.Session) -> None:
        """确保当前 sess 已登录；首次自动登录。"""
        if sess.cookies.get("JSESSIONID"):
            return
        self._login(sess)

    # ---- HTTP 出口 ----
    def _full_url(self, uri: str) -> str:
        return f"{self.base_url}/{str(uri).lstrip('/')}"

    def get(self, uri: str, params: dict | None = None,
            *, _retry_on_401: bool = True) -> requests.Response:
        """GET 请求（返回原始 Response，由调用方 .json()）。

        401 / 「未找到登录信息」 时自动重登一次再重试一次（最多一次），
        仍失败抛 ``AuthError``。
        """
        sess = self._session()
        self._ensure_login(sess)
        url = self._full_url(uri)
        resp = sess.get(url, params=params, timeout=self.timeout,
                        verify=self.verify_ssl)
        if _retry_on_401 and self._looks_unauth(resp):
            logger.info("[FrontendSession] 401，重登后重试 %s", uri)
            # 强制重登：清掉 cookie 再走一次
            sess.cookies.clear()
            self._login(sess)
            resp = sess.get(url, params=params, timeout=self.timeout,
                            verify=self.verify_ssl)
            if self._looks_unauth(resp):
                raise AuthError(
                    f"[FrontendSession] 重登后仍 401: {uri} -> {resp.text[:200]}"
                )
        return resp

    @staticmethod
    def _looks_unauth(resp: requests.Response) -> bool:
        if resp.status_code == 401:
            return True
        # 部分端点返 200 但 body 报「未找到登录信息」
        try:
            body = resp.json()
        except Exception:
            return False
        if isinstance(body, dict):
            msg = body.get("failureMessage") or body.get("loginFailMessage")
            if msg and ("未找到登录信息" in str(msg) or "请重新登录" in str(msg)):
                return True
        return False

    # ---- 上下文 ----
    def __enter__(self) -> "FrontendSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()