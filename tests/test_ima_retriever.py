"""IMA 检索适配层单元测试（mock HTTP，无需真实凭证）。

契约依据：ima-skills 知识库模块 ``knowledge-base/references/api.md``
+ 2026-09-14 对本账号真实平台的实测结论：
  - ``search_knowledge_base``：limit 上限 20（传 50 → code=51），返回 ``info_list``
  - ``search_knowledge``     ：返回 ``info_list``（media_id/title/highlight_content）
  - ``get_knowledge_list``   ：返回 ``knowledge_list``，``is_end``/``next_cursor`` 翻页
  - 线上字段是 ``kb_id``/``kb_name``（文档写 ``id``/``name``，两种都要兼容）
  - 文件类媒体拿不到正文，get_media_info 返回 220030；只有 weburl_ 能拿到 url
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai.ima_retriever import (
    ImaRetriever, _keywords,
    STATUS_OK, STATUS_EMPTY, STATUS_AUTH_FAILED,
    STATUS_NO_CRED, STATUS_DISABLED, STATUS_ERROR,
)

KB_LIST = "search_knowledge_base"
KB_SEARCH = "search_knowledge"
DOC_LIST = "get_knowledge_list"
MEDIA_INFO = "get_media_info"


def _fake_resp(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = ""
    return resp


def _ok(data: dict) -> MagicMock:
    return _fake_resp({"code": 0, "msg": "success", "data": data})


def _retriever(**kw) -> ImaRetriever:
    return ImaRetriever(
        endpoint="", enabled=True,
        client_id=kw.get("client_id", "cid"),
        api_key=kw.get("api_key", "ckey"),
    )


def _router(routes: dict, default: dict | None = None):
    """按 URL 片段路由的 requests.post 假实现。

    ``routes`` 的 value 可以是 fake response，也可以是 ``body -> response``。
    记录每次调用的 ``(url, body)`` 到 ``calls``（通过闭包变量访问）。
    """
    calls: list[tuple[str, dict]] = []

    def _post(url, headers=None, json=None, timeout=None, **kw):
        calls.append((url, dict(json or {})))
        for key, val in routes.items():
            if key in url:
                # 注意：MagicMock 也是 callable，必须排除掉，否则会把响应对象当工厂调用
                if callable(val) and not isinstance(val, MagicMock):
                    return val(dict(json or {}))
                return val
        return default if default is not None else _ok({})

    _post.calls = calls          # type: ignore[attr-defined]
    return _post


# ----------------------------------------------------------------------
# 基础失败安全
# ----------------------------------------------------------------------
def test_no_creds_returns_empty():
    assert ImaRetriever(enabled=True, client_id="", api_key="").retrieve("运维问题") == []
    assert ImaRetriever(enabled=False, client_id="cid", api_key="ckey").retrieve("q") == []


def test_auth_failure_returns_empty():
    with patch("requests.post", return_value=_fake_resp(
            {"code": 200002, "msg": "skill auth failed", "data": {}})):
        assert _retriever().retrieve("运维") == []


def test_non_json_response_returns_empty():
    resp = MagicMock(); resp.status_code = 200; resp.text = "<html>502</html>"
    resp.json.side_effect = ValueError("not json")
    with patch("requests.post", return_value=resp):
        assert _retriever().retrieve("q") == []


def test_network_failure_returns_empty():
    with patch("requests.post", side_effect=OSError("connection refused")):
        r = _retriever()
        assert r.retrieve("q") == []
    assert r.last_status == STATUS_ERROR


# ----------------------------------------------------------------------
# 契约回归：limit / 字段名 / 列表键
# ----------------------------------------------------------------------
def test_kb_enumeration_uses_limit_within_20():
    """回归：历史实现传 limit=50 → 真实平台报 code=51，必须 ≤20。"""
    post = _router({KB_LIST: _ok({"info_list": [], "is_end": True})})
    with patch("requests.post", post):
        _retriever().check_connection()
    body = [b for u, b in post.calls if KB_LIST in u][0]
    assert 1 <= body["limit"] <= 20
    assert body["query"] == "" and body["cursor"] == ""


def test_kb_parses_live_field_names_kb_id_kb_name():
    """回归：线上返回 kb_id/kb_name，历史实现只认 id/name → 枚举到 0 个。"""
    post = _router({KB_LIST: _ok({"info_list": [
        {"kb_id": "k1", "kb_name": "运维手册"},
        {"kb_id": "k2", "kb_name": "排障库"},
    ], "is_end": True})})
    with patch("requests.post", post):
        ok, msg = _retriever().check_connection()
    assert ok is True
    assert "2 个知识库" in msg and "运维手册" in msg and "排障库" in msg


def test_kb_parses_documented_field_names_id_name():
    """官方文档写 id/name，同样要能解析。"""
    post = _router({KB_LIST: _ok({"info_list": [
        {"id": "k1", "name": "文档库"}], "is_end": True})})
    with patch("requests.post", post):
        ok, msg = _retriever().check_connection()
    assert ok is True and "文档库" in msg


def test_kb_enumeration_paginates_with_cursor():
    """按官方游标规范翻页：is_end=False 时用 next_cursor 续拉。"""
    pages = [
        _ok({"info_list": [{"kb_id": "k1", "kb_name": "A"}],
             "is_end": False, "next_cursor": "C1"}),
        _ok({"info_list": [{"kb_id": "k2", "kb_name": "B"}], "is_end": True}),
    ]
    state = {"i": 0}

    def nxt(_body):
        r = pages[min(state["i"], 1)]
        state["i"] += 1
        return r

    post = _router({KB_LIST: nxt})
    with patch("requests.post", post):
        ok, msg = _retriever().check_connection()
    assert ok is True and "2 个知识库" in msg
    bodies = [b for u, b in post.calls if KB_LIST in u]
    assert bodies[0]["cursor"] == "" and bodies[1]["cursor"] == "C1"


# ----------------------------------------------------------------------
# search_knowledge：正文级命中（highlight_content）
# ----------------------------------------------------------------------
def test_search_uses_knowledge_base_id_and_highlight_content():
    def search(body):
        if body.get("knowledge_base_id") == "k1":
            return _ok({"info_list": [
                {"media_id": "m1", "title": "蓝屏处理",
                 "highlight_content": "重启并收集日志", "media_type": 3}]})
        return _ok({"info_list": []})

    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "运维手册"}],
                      "is_end": True}),
        KB_SEARCH: search,
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("蓝屏", top_k=3)

    assert len(chunks) == 1
    c = chunks[0]
    assert c.text == "重启并收集日志"
    assert c.source == "运维手册 › 蓝屏处理"
    assert c.is_title_hit is False
    assert c.score == 1.0                       # 正文级命中恒为最高分
    assert c.doc_id == "m1"
    # 检索请求必须带 knowledge_base_id（不是 kb_id）
    # 注意 search_knowledge 是 search_knowledge_base 的子串，必须精确匹配结尾
    sent = [b for u, b in post.calls if u.endswith("search_knowledge")][0]
    assert sent["knowledge_base_id"] == "k1" and sent["cursor"] == ""


def test_api_title_hit_becomes_bracketed_title():
    """无 highlight_content → 标题级命中，text 为《标题》且打上 is_title_hit。"""
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": [
            {"media_id": "m1", "title": "06-00 云桌面FAQ.docx", "highlight_content": ""}]}),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("云桌面", top_k=3)
    assert len(chunks) == 1
    assert chunks[0].text == "《06-00 云桌面FAQ.docx》"
    assert chunks[0].is_title_hit is True
    assert chunks[0].score == pytest.approx(0.85)


# ----------------------------------------------------------------------
# 本地标题召回（兜底 search_knowledge 的「整串匹配」缺口）
# ----------------------------------------------------------------------
def _docs_kb(*titles: str) -> MagicMock:
    return _ok({"knowledge_list": [{"media_id": f"m{i}", "title": t}
                                   for i, t in enumerate(titles)], "is_end": True})


def test_local_title_recall_for_long_question():
    """长问句服务端恒 0 命中，靠拆词在本地标题上召回。"""
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "手册库"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": []}),          # 服务端整串匹配 → 0
        DOC_LIST: _docs_kb(
            "H3C Workspace云桌面 安装部署指导-整本手册.pdf",
            "H3C License Server 安装指导.pdf",
            "H3C 无线控制器配置指导.pdf",
        ),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("云桌面 部署 指导", top_k=3)
    assert chunks, "本地标题召回应给出命中"
    assert "安装部署指导" in chunks[0].source
    assert chunks[0].is_title_hit is True


def test_boilerplate_words_do_not_create_false_hits():
    """模板词（高频出现在多数标题里）不携带区分度，不得靠它撞出命中。"""
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "手册库"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": []}),
        DOC_LIST: _docs_kb(*[f"H3C XX{i} 使用指南-整本手册.pdf" for i in range(8)]),
    })
    with patch("requests.post", post):
        r = _retriever()
        chunks = r.retrieve("使用 整本手册 指南", top_k=3)
    assert chunks == []
    assert r.last_status == STATUS_EMPTY


def test_single_chinese_char_token_is_ignored():
    """单个汉字（如「高」）不得用来撞「高可用」。"""
    assert "高" not in _keywords("主机 CPU 高")
    assert "cpu" in _keywords("主机 CPU 高")


def test_local_hit_below_floor_is_dropped():
    """只靠一个通用词撞上（覆盖率极低）→ 丢弃，宁可回退本地知识库。"""
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": []}),
        DOC_LIST: _docs_kb("H3C 高可用配置指导.pdf", *[f"无关文档{i}.pdf" for i in range(9)]),
    })
    with patch("requests.post", post):
        r = _retriever()
        # 关键词：主机/cpu/排查 都不在标题里，只剩一个弱命中 → 应被地板过滤
        assert r.retrieve("主机 CPU 使用率高怎么排查", top_k=3) == []
    assert r.last_status == STATUS_EMPTY


# ----------------------------------------------------------------------
# 排序 / 截断 / 去重 / 容错
# ----------------------------------------------------------------------
def test_content_hits_rank_above_title_hits_and_truncate():
    def search(body):
        if body.get("knowledge_base_id") == "k1":
            return _ok({"info_list": [
                {"media_id": "t1", "title": "只有标题", "highlight_content": ""}]})
        return _ok({"info_list": [
            {"media_id": "c1", "title": "有正文", "highlight_content": "正文片段"}]})

    post = _router({
        KB_LIST: _ok({"info_list": [
            {"kb_id": "k1", "kb_name": "A"}, {"kb_id": "k2", "kb_name": "B"}], "is_end": True}),
        KB_SEARCH: search,
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("q", top_k=1)
    assert len(chunks) == 1 and chunks[0].text == "正文片段"


def test_duplicate_copies_are_deduped_by_title():
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": []}),
        DOC_LIST: _docs_kb(
            "H3C License 使用指南.pdf",
            "H3C License 使用指南 (1).pdf",
        ),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("License 使用指南", top_k=5)
    assert len(chunks) == 1


def test_per_kb_biz_error_skips_but_keeps_others():
    post = _router({
        KB_LIST: _ok({"info_list": [
            {"kb_id": "k1", "kb_name": "坏库"}, {"kb_id": "k2", "kb_name": "好库"}],
            "is_end": True}),
        KB_SEARCH: lambda body: (
            _fake_resp({"code": 500123, "msg": "internal error", "data": {}})
            if body.get("knowledge_base_id") == "k1"
            else _ok({"info_list": [{"media_id": "m", "title": "网络中断",
                                     "highlight_content": "检查上行链路"}]})),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        r = _retriever()
        chunks = r.retrieve("q")
    assert len(chunks) == 1
    assert chunks[0].source == "好库 › 网络中断"
    assert r.last_status == STATUS_OK


# ----------------------------------------------------------------------
# 原文链接：只对网页/笔记类型解析
# ----------------------------------------------------------------------
def test_media_url_resolved_only_for_weburl():
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": [
            {"media_id": "weburl_abc", "title": "H3C 官网", "highlight_content": ""}]}),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
        MEDIA_INFO: _ok({"media_type": 2,
                         "url_info": {"url": "https://www.h3c.com/x", "headers": {}}}),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("H3C 官网", top_k=3)
    assert chunks[0].url == "https://www.h3c.com/x"


def test_media_url_skipped_for_file_types():
    """文件类 media（word_/pdf_/…）实测必失败，不应发起 get_media_info。"""
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": [
            {"media_id": "pdf_xyz", "title": "手册.pdf", "highlight_content": ""}]}),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        chunks = _retriever().retrieve("手册", top_k=3)
    assert chunks[0].url == ""
    assert not [u for u, _ in post.calls if MEDIA_INFO in u]


# ----------------------------------------------------------------------
# last_status：区分「未配置 / 鉴权失败 / 已连未命中 / 命中 / 异常」
# ----------------------------------------------------------------------
def test_last_status_disabled_and_no_cred():
    r = ImaRetriever(enabled=False, client_id="cid", api_key="ckey")
    assert r.retrieve("q") == [] and r.last_status == STATUS_DISABLED

    r2 = ImaRetriever(enabled=True, client_id="", api_key="")
    assert r2.retrieve("q") == [] and r2.last_status == STATUS_NO_CRED


def test_last_status_auth_failed_marks_auth_error():
    with patch("requests.post", return_value=_fake_resp(
            {"code": 200002, "msg": "skill auth failed", "data": {}})):
        r = _retriever()
        assert r.retrieve("运维") == []
    assert r.last_status == STATUS_AUTH_FAILED
    assert "200002" in r.last_error


def test_last_status_empty_when_no_hits():
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": []}),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        r = _retriever()
        assert r.retrieve("毫不相干的问题") == []
    assert r.last_status == STATUS_EMPTY


def test_last_status_ok_when_hit():
    post = _router({
        KB_LIST: _ok({"info_list": [{"kb_id": "k1", "kb_name": "KB"}], "is_end": True}),
        KB_SEARCH: _ok({"info_list": [
            {"media_id": "m", "title": "t", "highlight_content": "c"}]}),
        DOC_LIST: _ok({"knowledge_list": [], "is_end": True}),
    })
    with patch("requests.post", post):
        r = _retriever()
        assert len(r.retrieve("q")) == 1
    assert r.last_status == STATUS_OK


# ----------------------------------------------------------------------
# check_connection：配置界面「测试 IMA 连接」按钮
# ----------------------------------------------------------------------
def test_check_connection_no_cred():
    ok, msg = ImaRetriever(enabled=True, client_id="", api_key="").check_connection()
    assert ok is False and "未配置凭证" in msg


def test_check_connection_disabled():
    ok, msg = ImaRetriever(enabled=False, client_id="c", api_key="k").check_connection()
    assert ok is False and "未启用" in msg


def test_check_connection_auth_failed():
    with patch("requests.post", return_value=_fake_resp(
            {"code": 200002, "msg": "skill auth failed", "data": {}})):
        ok, msg = _retriever().check_connection()
    assert ok is False and "鉴权失败" in msg


def test_check_connection_http_401():
    resp = MagicMock(); resp.status_code = 401; resp.text = "unauthorized"
    resp.json.return_value = {}
    with patch("requests.post", return_value=resp):
        ok, msg = _retriever().check_connection()
    assert ok is False and "鉴权失败" in msg


def test_check_connection_reports_biz_error_detail():
    """契约性错误（如 limit 越界 code=51）要如实回报，不能被吞掉。"""
    with patch("requests.post", return_value=_fake_resp(
            {"code": 51, "msg": "value must be inside range (0, 20]", "data": {}})):
        ok, msg = _retriever().check_connection()
    assert ok is False and "51" in msg


def test_check_connection_success_zero_kbs():
    post = _router({KB_LIST: _ok({"info_list": [], "is_end": True})})
    with patch("requests.post", post):
        ok, msg = _retriever().check_connection()
    assert ok is True and "0 个" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
