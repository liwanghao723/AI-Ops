"""ai.llm_client 单元测试：set_provider 切换、chat 请求体构造、异常兜底。"""

import pytest
import requests

from core.errors import LLMError
from ai.llm_client import LLMClient


class FakeResp:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json


def test_chat_request_body_and_bearer(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp({"choices": [{"message": {"content": "  hello  "}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    c = LLMClient("https://api.deepseek.com/v1/", "sk-123", "deepseek-chat")
    out = c.chat([{"role": "user", "content": "hi"}], temperature=0.5)
    assert out == "hello"  # 内容已 strip
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-123"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["model"] == "deepseek-chat"
    assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["json"]["temperature"] == 0.5


def test_set_provider_preset():
    c = LLMClient("http://x", "k", "m")
    c.set_provider("deepseek")
    assert c.base_url == "https://api.deepseek.com/v1" and c.model == "deepseek-chat"
    c.set_provider("glm")
    assert c.model == "glm-4"
    c.set_provider("qwen")
    assert c.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_set_provider_unknown_keeps_current():
    c = LLMClient("http://x", "k", "m")
    c.set_provider("unknown-vendor")
    assert c.model == "m" and c.base_url == "http://x"


def test_set_provider_with_cfg_overrides():
    class Cfg:
        base_url = "http://cfg"
        api_key = "cfgkey"
        model = "cfgmodel"

    c = LLMClient("http://x", "k", "m")
    c.set_provider("deepseek", Cfg())
    assert c.base_url == "http://cfg" and c.api_key == "cfgkey" and c.model == "cfgmodel"


def test_chat_network_error_raises_llmerror(monkeypatch):
    def fake_post(*a, **k):
        raise requests.exceptions.ConnectionError("net")

    monkeypatch.setattr(requests, "post", fake_post)
    c = LLMClient("http://x", "k", "m")
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])


def test_chat_parse_error_raises_llmerror(monkeypatch):
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: FakeResp({"unexpected": 1}))
    c = LLMClient("http://x", "k", "m")
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])


def test_chat_http_500_raises_llmerror(monkeypatch):
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: FakeResp({"error": "x"}, status=500))
    c = LLMClient("http://x", "k", "m")
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])
