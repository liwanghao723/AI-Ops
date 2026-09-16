"""本地知识库检索（对应架构 T6 / local_retriever.py）。

- 零依赖关键词检索：对 knowledge/ 下 .md/.txt 切块，按词重叠打分。
- 可选句子向量（use_vector=True 时尝试 import sentence-transformers，缺失不报错）。
- 通过 index_dir(path) 指定知识库目录（默认 Paths.knowledge_dir）。

检索策略：先按关键词打分，若开启向量则融合向量相似度（取 max）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.models import Chunk
from core.paths import Paths
from core.logging_setup import get_logger
from .retriever_base import Retriever


logger = get_logger(__name__)

# 中英文分词正则（简单按非字母数字/中文切分）
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")

# 切块参数
_CHUNK_CHARS = 800       # 每块最大字符数
_CHUNK_OVERLAP = 120     # 块间重叠字符数


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _chunk_text(text: str, size: int = _CHUNK_CHARS,
                overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按字符数滑动切块，保留一定重叠以维持上下文。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


class LocalRetriever(Retriever):
    """本地关键词/向量检索适配层。"""

    def __init__(self, use_vector: bool = False,
                 similarity_threshold: float = 0.3) -> None:
        self._dir: Path = Paths.instance().knowledge_dir
        self.use_vector = use_vector
        self.similarity_threshold = similarity_threshold
        self._vector_model = None
        if self.use_vector:
            self._try_load_vector_model()

    # ---- 目录管理 ----
    def index_dir(self, path: "str | Path") -> None:
        """指定知识库目录并重建索引。"""
        self._dir = Path(path)
        logger.info("[LocalRetriever] 知识库目录: %s", self._dir)

    # ---- 向量模型（可选）----
    def _try_load_vector_model(self) -> None:
        try:  # pragma: no cover - 可选依赖
            from sentence_transformers import SentenceTransformer
            self._vector_model = SentenceTransformer("shibing624/text2vec-base-chinese")
            logger.info("[LocalRetriever] 句向量模型已加载")
        except Exception as exc:  # 缺失或加载失败均不阻塞
            logger.warning("[LocalRetriever] 句向量不可用，回退关键词: %s", exc)
            self.use_vector = False
            self._vector_model = None

    # ---- 检索 ----
    def retrieve(self, query: str, top_k: int = 3) -> list[Chunk]:
        docs = self._load_documents()
        if not docs:
            logger.debug("[LocalRetriever] 知识库为空")
            return []

        q_tokens = set(_tokenize(query))
        results: list[Chunk] = []
        for fp, text in docs:
            file_url = "file:///" + str(fp.resolve()).replace("\\", "/")
            for ci, chunk in enumerate(_chunk_text(text)):
                score = self._score(query, q_tokens, chunk)
                results.append(Chunk(
                    source=f"{fp.name}#chunk{ci}",
                    text=chunk,
                    score=round(score, 4),
                    url=file_url,
                ))

        # 按分数降序，过滤低于阈值的（仅向量模式严格过滤；关键词模式放宽）
        results.sort(key=lambda c: c.score, reverse=True)
        if self.use_vector:
            results = [c for c in results if c.score >= self.similarity_threshold]
        return results[:top_k]

    # ---- 打分 ----
    def _score(self, query: str, q_tokens: set[str], chunk: str) -> float:
        if not q_tokens:
            return 0.0
        c_tokens = _tokenize(chunk)
        c_set = set(c_tokens)
        # 关键词重叠率（Jaccard）
        inter = len(q_tokens & c_set)
        keyword_score = inter / len(q_tokens) if q_tokens else 0.0
        if not self.use_vector or self._vector_model is None:
            return keyword_score
        # 向量融合：取关键词与向量相似度的加权
        try:  # pragma: no cover
            vec_q = self._vector_model.encode([query])[0]
            vec_c = self._vector_model.encode([chunk])[0]
            sim = float(vec_q @ vec_c / (norm(vec_q) * norm(vec_c) + 1e-9))
            return 0.7 * sim + 0.3 * keyword_score
        except Exception as exc:  # 回退
            logger.debug("[LocalRetriever] 向量打分失败回退: %s", exc)
            return keyword_score

    # ---- 文档加载 ----
    def _load_documents(self) -> "list[tuple[Path, str]]":
        if not self._dir.exists():
            return []
        docs: "list[tuple[Path, str]]" = []
        for ext in ("*.md", "*.txt"):
            for fp in sorted(self._dir.glob(ext)):
                try:
                    docs.append((fp, fp.read_text(encoding="utf-8", errors="ignore")))
                except OSError as exc:
                    logger.warning("[LocalRetriever] 读取失败 %s: %s", fp, exc)
        return docs


def norm(v):  # pragma: no cover - 仅向量模式使用
    import math
    return math.sqrt(float(sum(x * x for x in v)))
