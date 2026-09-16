"""真实 HTTP 层集成测试：摘要认证 + 连接池复用（不 mock 网络）。

用 ``http.server`` 在本机起一个真的 digest 认证服务，验证：

1. 摘要认证在 Session 复用下依然正确（服务端逐请求校验 response 摘要）；
2. N 次并发请求的 **TCP 连接数 ≤ 并发线程数**（而不是等于请求数）；
3. keep-alive 连接被对端「无声关闭」后，下一次请求能被内部连接级重试
   透明恢复（不冒泡成业务错误、不触发外层的退避 sleep）。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import core.digest_auth as digest_auth
from core.digest_auth import DigestAuthHTTP

USER = "pooluser"
PASS = "Pool@12345"
REALM = "pool-test"
NONCE = "00112233445566778899aabbccddeeff"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _parse_digest(header: str) -> dict:
    out = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,]+))', header[7:]):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3).strip()
    return out


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.connections = 0
        self.requests = 0
        self.challenges = 0
        self.bad_digest = 0
        # True 时：正常回 200，但随后**无声**关闭连接（模拟 keep-alive 空闲被踢）
        self.rude_close = False

    def bump(self, name):
        with self.lock:
            setattr(self, name, getattr(self, name) + 1)


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            state.bump("connections")

        def log_message(self, *a):
            pass

        def _send_json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _challenge(self):
            state.bump("challenges")
            body = b'{"success":false,"errorCode":401}'
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                f'Digest realm="{REALM}", nonce="{NONCE}", qop="auth", algorithm=MD5')
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.bump("requests")
            auth = self.headers.get("Authorization", "")
            if not auth.lower().startswith("digest "):
                return self._challenge()

            d = _parse_digest(auth)
            ha1 = _md5(f"{USER}:{REALM}:{PASS}")
            ha2 = _md5(f"GET:{d.get('uri', self.path)}")
            expect = _md5(f"{ha1}:{d.get('nonce')}:{d.get('nc')}:"
                          f"{d.get('cnonce')}:auth:{ha2}")
            if d.get("response") != expect or d.get("nonce") != NONCE:
                state.bump("bad_digest")
                return self._challenge()

            self._send_json(200, {"success": True, "errorCode": 0,
                                  "failureMessage": "", "state": 0, "data": {"ok": 1}})
            if state.rude_close:
                # 不回 Connection: close，客户端仍以为连接可用 → 下次请求会撞上
                # RemoteDisconnected，用以验证连接级自愈能力
                self.close_connection = True

    return Handler


@pytest.fixture()
def digest_server():
    state = _State()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/vdi/rest/workspace"
    try:
        yield base, state
    finally:
        srv.shutdown()
        srv.server_close()


def _client(base, **kw):
    return DigestAuthHTTP(base, USER, PASS, timeout=5.0, verify_ssl=False, **kw)


def test_digest_auth_works_over_reused_session(digest_server):
    """摘要认证必须在 Session 复用下保持正确（服务端逐请求校验摘要）。"""
    base, state = digest_server
    http = _client(base)
    try:
        for _ in range(5):
            out = http.get("/vms/queryVmList")
            assert out["success"] is True
            assert out["data"] == {"ok": 1}
    finally:
        http.close()
    assert state.bad_digest == 0
    # 401 挑战只应出现在线程首请求，不应每请求一次
    assert state.challenges <= 2
    assert state.requests == 5 + state.challenges


def test_connections_are_reused_across_requests(digest_server):
    """N 次并发请求的 TCP 连接数应远小于请求数（≈ 并发线程数）。"""
    base, state = digest_server
    n, conc = 40, 5
    http = _client(base)
    try:
        with ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(lambda _i: http.get("/hosts"), range(n)))
    finally:
        http.close()

    assert state.requests >= n
    assert state.connections <= conc + 1, (
        f"连接未被复用：{state.connections} 条连接 / {n} 次请求")
    assert state.connections < n / 2


def test_stale_keepalive_connection_is_recovered_transparently(
        digest_server, monkeypatch):
    """对端无声关闭 keep-alive 连接后，下一次请求应被内部重试透明恢复。"""
    base, state = digest_server
    http = _client(base)
    sleeps: list[float] = []
    monkeypatch.setattr(digest_auth.time, "sleep", lambda s: sleeps.append(s))
    try:
        assert http.get("/hosts")["success"] is True     # 建连、认证
        state.rude_close = True                          # 之后每次响应后静默断开
        for _ in range(3):
            out = http.get("/hosts")                     # 复用到死连接 → 应自愈
            assert out["success"] is True
    finally:
        http.close()
    # 自愈发生在本层连接级重试内，不应触发外层退避 sleep
    assert sleeps == [], f"走了外层退避重试（耗时不可控）：{sleeps}"


def test_close_releases_sockets(digest_server):
    """close() 后连接池被清空，可安全重复关闭。"""
    base, state = digest_server
    http = _client(base)
    http.get("/hosts")
    sessions = list(http._sessions)
    assert sessions
    http.close()
    http.close()
    assert http._sessions == []
    for s in sessions:
        # 已关闭：adapters 的连接池已被清空（不再持有任何 socket）
        assert all(len(a.poolmanager.pools) == 0 for a in s.adapters.values())


def test_no_sleep_when_all_requests_succeed(digest_server, monkeypatch):
    """全部成功时不应有任何重试等待（确认没引入额外开销）。"""
    base, _state = digest_server
    http = _client(base)
    sleeps: list[float] = []
    monkeypatch.setattr(digest_auth.time, "sleep", lambda s: sleeps.append(s))
    try:
        t0 = time.perf_counter()
        for _ in range(10):
            http.get("/hosts")
        elapsed = time.perf_counter() - t0
    finally:
        http.close()
    assert sleeps == []
    assert elapsed < 3.0
