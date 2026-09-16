"""core.digest_auth 单元测试：401/4xx/5xx 处理、重试与指数退避、超时、URL 构造、
连接池复用（Session 线程隔离 / 池容量 / 关闭幂等）。

说明：用 monkeypatch 替换 ``DigestAuthHTTP._raw_request``（唯一网络出口），
不发起真实网络请求。真实 HTTP 层的连接复用行为见 ``test_digest_pool.py``。
"""

import threading
import time

import pytest
import requests
from urllib3.util.retry import Retry

from core.digest_auth import DigestAuthHTTP
from core.errors import AuthError, NetworkError, WorkspaceAPIError


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def _make_http(**kw):
    return DigestAuthHTTP("http://example.com/", "u", "p", timeout=1.0, **kw)


def patch_raw(monkeypatch, fake):
    """把底层网络出口替换为 fake(method, url, **kw)。"""
    def _fake(self, method, url, **kw):
        return fake(method, url, **kw)
    monkeypatch.setattr(DigestAuthHTTP, "_raw_request", _fake)


def test_401_raises_auth_error_no_retry(monkeypatch):
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        return FakeResponse(401, text="unauth")

    patch_raw(monkeypatch, fake_request)
    http = _make_http()
    with pytest.raises(AuthError):
        http.get("/x")
    # 401 不应重试，只调用一次
    assert len(calls) == 1


def test_4xx_non_401_raises_workspace_api_error(monkeypatch):
    patch_raw(monkeypatch, lambda *a, **k: FakeResponse(400, text="bad"))
    with pytest.raises(WorkspaceAPIError):
        _make_http().get("/x")


def test_200_normalizes_outer_dict(monkeypatch):
    patch_raw(monkeypatch,
              lambda *a, **k: FakeResponse(200, json_data={"data": {"k": 1}}))
    out = _make_http().get("/x")
    assert out["success"] is True
    assert out["errorCode"] == 0
    assert out["failureMessage"] == ""
    assert out["state"] == 0
    assert out["data"] == {"k": 1}


def test_200_non_json_raises(monkeypatch):
    patch_raw(monkeypatch,
              lambda *a, **k: FakeResponse(200, json_data=None, text="plain"))
    with pytest.raises(WorkspaceAPIError):
        _make_http().get("/x")


def test_retry_on_connection_error_then_success(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("boom")
        return FakeResponse(200, json_data={"data": "ok"})

    patch_raw(monkeypatch, fake_request)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    out = _make_http().get("/x")
    assert out["data"] == "ok"
    assert calls["n"] == 3
    # 退避序列 0.5s -> 1.0s
    assert sleeps == [0.5, 1.0]


def test_exhaust_retries_raises_network_error(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        raise requests.exceptions.Timeout("to")

    patch_raw(monkeypatch, fake_request)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(NetworkError):
        _make_http().get("/x")
    # max_retries=3 -> 4 次尝试，3 次退避
    assert calls["n"] == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_full_url_strips_trailing_slash(monkeypatch):
    captured = {}

    def fake_request(method, url, **kw):
        captured["url"] = url
        return FakeResponse(200, json_data={"data": 1})

    patch_raw(monkeypatch, fake_request)
    _make_http().get("/realtimeAlarms/list")
    assert captured["url"] == "http://example.com/realtimeAlarms/list"


def test_post_sends_json_body(monkeypatch):
    captured = {}

    def fake_request(method, url, **kw):
        captured["method"] = method
        captured["json"] = kw.get("json")
        return FakeResponse(200, json_data={"data": 1})

    patch_raw(monkeypatch, fake_request)
    _make_http().post("/vms/queryUuidsByVmUuids", json={"a": 1})
    assert captured["method"] == "POST"
    assert captured["json"] == {"a": 1}


def test_5xx_should_be_retried(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        return FakeResponse(503, text="down")

    patch_raw(monkeypatch, fake_request)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    http = _make_http(max_retries=3)
    with pytest.raises(NetworkError):
        http.get("/x")
    # 期望：5xx 触发指数退避重试，至少尝试 >1 次
    assert calls["n"] > 1


# ----------------------------------------------------------------------
# 连接池（Session 复用 / 线程隔离 / 池容量 / 关闭）
# ----------------------------------------------------------------------
def test_session_reused_within_same_thread():
    """同一线程重复调用必须复用同一个 Session（连接才能 keep-alive）。"""
    http = _make_http()
    try:
        assert http._session() is http._session()
    finally:
        http.close()


def test_session_is_thread_local():
    """不同线程必须持有各自独立的 Session（requests.Session 非线程安全）。"""
    http = _make_http()
    other = {}

    def worker():
        other["sess"] = http._session()

    try:
        main_sess = http._session()
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other["sess"] is not main_sess
    finally:
        http.close()


def test_session_mounts_pooled_adapter_with_expected_limits():
    """Session 必须挂载带连接池的 HTTPAdapter，且 read 超时不走内部静默重试。"""
    http = _make_http(pool_connections=7, pool_maxsize=13)
    try:
        sess = http._session()
        for scheme in ("http://", "https://"):
            adapter = sess.get_adapter(scheme + "example.com")
            assert isinstance(adapter, requests.adapters.HTTPAdapter)
            assert adapter._pool_connections == 7
            assert adapter._pool_maxsize == 13
            retry = adapter.max_retries
            assert isinstance(retry, Retry)
            assert retry.total == 2          # 仅有限次连接级瞬时重试
            assert retry.read is False       # 读超时不静默重试（交由外层退避）
    finally:
        http.close()


def test_close_closes_sessions_and_is_idempotent():
    """close() 必须真正关闭连接池，且可重复调用不报错。"""
    http = _make_http()
    sess = http._session()
    closed = []
    orig_close = sess.close

    def spy_close():
        closed.append(True)
        orig_close()

    sess.close = spy_close
    http.close()
    http.close()                 # 幂等
    assert len(closed) == 1
    assert http._sessions == []


def test_new_session_created_after_close():
    """close() 后再次取 Session 应能正常工作（不返回已关闭对象）。"""
    http = _make_http()
    try:
        first = http._session()
        http.close()
        second = http._session()
        assert second is not first
    finally:
        http.close()
