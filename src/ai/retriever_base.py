"""检索器抽象基类（对应架构 T6 / retriever_base.py）。

所有检索适配层（本地/IMA）实现统一接口：
    retrieve(query, top_k) -> list[Chunk]
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import Chunk


class Retriever(ABC):
    """检索适配层接口。"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 3) -> list[Chunk]:
        """返回与 query 相关的知识片段（按相关度降序）。"""
        raise NotImplementedError
