"""知识管理与检索策略编排（对应架构 T8 / knowledge_manager.py）。

策略：
  1. 先 primary（IMA）检索；
  2. 若 primary 为空或抛异常，则 fallback（本地知识库）检索；
  3. 聚合返回 top-k 个 Chunk。

配置：rag.top_k（默认 3）、rag.ima_enabled、rag.ima_endpoint。
"""

from __future__ import annotations

from core.models import Chunk, SearchResult
from core.logging_setup import get_logger
from .retriever_base import Retriever
from .local_retriever import LocalRetriever
from .ima_retriever import ImaRetriever


logger = get_logger(__name__)


class KnowledgeManager:
    """检索策略编排：IMA 优先，本地知识库兜底。"""

    def __init__(self, primary: Retriever, fallback: Retriever) -> None:
        self.primary = primary
        self.fallback = fallback

    @classmethod
    def from_config(cls, cfg: "object") -> "KnowledgeManager":
        """依据 AppConfig 构建：IMA 为 primary，LocalRetriever 为 fallback。"""
        ima = ImaRetriever(
            endpoint=getattr(cfg.rag, "ima_endpoint", "") or "",
            enabled=getattr(cfg.rag, "ima_enabled", True),
            client_id=getattr(cfg.rag, "ima_client_id", "") or "",
            api_key=getattr(cfg.rag, "ima_api_key", "") or "",
        )
        local = LocalRetriever(
            use_vector=getattr(cfg.rag, "use_vector", False),
            similarity_threshold=getattr(cfg.rag, "similarity_threshold", 0.3),
        )
        return cls(primary=ima, fallback=local)

    def search(self, query: str, top_k: int = 3) -> SearchResult:
        """先 IMA 后本地，聚合返回 top-k，并标注命中来源。

        返回 ``SearchResult``：
          - origin == "ima"   且 kb_names 非空 → 命中 IMA 知识库（kb_names 为命中库名）
          - origin == "local"                → IMA 未命中，已用本地知识库兜底
          - origin == "none"                 → IMA 与本地均无命中

        ``ima_status`` 透传 IMA 步骤的结果（ok/empty/auth_failed/no_cred/disabled/error），
        供 UI 区分「未配置」「鉴权失败」「已连但未命中」，而非一律显示「IMA 未命中」。
        """
        ima_status = ""
        try:
            primary_res = self.primary.retrieve(query, top_k)
            ima_status = getattr(self.primary, "last_status", "") or ""
        except Exception as exc:
            logger.warning("[KM] primary 检索异常，回退本地: %s", exc)
            primary_res = []
            ima_status = "error"

        if primary_res:
            logger.info("[KM] IMA 命中 %d 条", len(primary_res))
            kb_names = self._collect_kb_names(primary_res)
            return SearchResult(
                chunks=primary_res[:top_k], origin="ima", kb_names=kb_names,
                ima_status=ima_status or "ok")

        # IMA 空 → 本地兜底
        try:
            fallback_res = self.fallback.retrieve(query, top_k)
        except Exception as exc:
            logger.error("[KM] 本地检索也失败: %s", exc)
            fallback_res = []
        if fallback_res:
            logger.info("[KM] 本地兜底命中 %d 条 (IMA 状态=%s)",
                        len(fallback_res), ima_status or "unknown")
            return SearchResult(chunks=fallback_res[:top_k], origin="local",
                                ima_status=ima_status)
        logger.info("[KM] IMA 与本地均无命中")
        return SearchResult(chunks=[], origin="none", ima_status=ima_status)

    @staticmethod
    def _collect_kb_names(chunks: "list[Chunk]") -> "list[str]":
        """从 Chunk.source（形如 \"知识库名 › 标题\"）提取去重的知识库名。"""
        names: list[str] = []
        for c in chunks:
            src = (c.source or "").strip()
            if "›" in src:
                kb = src.split("›", 1)[0].strip()
            else:
                kb = src
            if kb and kb not in names:
                names.append(kb)
        return names
