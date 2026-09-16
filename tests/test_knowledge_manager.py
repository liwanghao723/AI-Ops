"""ai.knowledge_manager 单元测试：IMA 优先、本地兜底、top-k 截断、空/异常处理。"""

from ai.knowledge_manager import KnowledgeManager
from core.models import Chunk, SearchResult


class FakeRetriever:
    def __init__(self, chunks=None, exc=None, last_status=""):
        self.chunks = chunks if chunks is not None else []
        self.exc = exc
        self.last_status = last_status
        self.calls = []

    def retrieve(self, query, top_k=3):
        self.calls.append((query, top_k))
        if self.exc is not None:
            raise self.exc
        return self.chunks


def test_ima_hit_returns_topk_and_skips_fallback():
    primary = FakeRetriever([
        Chunk("运维手册 › 蓝屏", "t", 1.0), Chunk("运维手册 › 重启", "t", 0.9),
        Chunk("排障库 › 网络", "t", 0.8), Chunk("d", "t", 0.7), Chunk("e", "t", 0.6),
    ])
    fb = FakeRetriever([Chunk("x", "local", 1.0)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q", top_k=3)
    assert isinstance(res, SearchResult)
    assert len(res.chunks) == 3
    assert {c.source for c in res.chunks} == {"运维手册 › 蓝屏", "运维手册 › 重启", "排障库 › 网络"}
    assert res.origin == "ima"
    assert "运维手册" in res.kb_names and "排障库" in res.kb_names
    assert fb.calls == []  # 命中后不调用兜底


def test_ima_empty_falls_back_to_local():
    primary = FakeRetriever([])
    fb = FakeRetriever([Chunk("x", "local", 1.0), Chunk("y", "local", 0.9)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q", top_k=3)
    assert len(res.chunks) == 2
    assert res.chunks[0].source == "x"
    assert res.origin == "local"
    assert fb.calls == [("q", 3)]


def test_ima_exception_falls_back_to_local():
    primary = FakeRetriever(exc=RuntimeError("ima down"))
    fb = FakeRetriever([Chunk("x", "local", 1.0)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q")
    assert res.chunks[0].source == "x"
    assert res.origin == "local"


def test_both_empty_returns_empty():
    km = KnowledgeManager(primary=FakeRetriever([]), fallback=FakeRetriever([]))
    res = km.search("q")
    assert res.chunks == []
    assert res.origin == "none"


def test_topk_truncation_on_fallback():
    primary = FakeRetriever([])
    fb = FakeRetriever([Chunk(f"s{i}", "t", 1.0) for i in range(5)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q", top_k=3)
    assert len(res.chunks) == 3
    assert res.origin == "local"


# ----------------------------------------------------------------------
# ima_status 透传：让 UI 分清「未配置 / 鉴权失败 / 已连未命中」
# ----------------------------------------------------------------------
def test_ima_status_passthrough_on_local_fallback():
    primary = FakeRetriever([], last_status="auth_failed")
    fb = FakeRetriever([Chunk("x", "local", 1.0)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q")
    assert res.origin == "local"
    assert res.ima_status == "auth_failed"


def test_ima_status_ok_on_hit():
    primary = FakeRetriever([Chunk("kb › t", "t", 1.0)], last_status="ok")
    km = KnowledgeManager(primary=primary, fallback=FakeRetriever([]))
    res = km.search("q")
    assert res.origin == "ima"
    assert res.ima_status == "ok"


def test_ima_status_error_on_primary_exception():
    primary = FakeRetriever(exc=RuntimeError("boom"))
    fb = FakeRetriever([Chunk("x", "local", 1.0)])
    km = KnowledgeManager(primary=primary, fallback=fb)
    res = km.search("q")
    assert res.origin == "local"
    assert res.ima_status == "error"


def test_ima_status_on_both_empty():
    km = KnowledgeManager(primary=FakeRetriever([], last_status="empty"),
                          fallback=FakeRetriever([]))
    res = km.search("q")
    assert res.origin == "none"
    assert res.ima_status == "empty"
