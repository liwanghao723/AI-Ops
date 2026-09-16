"""联网搜索兜底（对应架构 T9 / web_search.py，§8.2 待定服务商）。

当前为占位实现：不崩溃、返回固定提示文本，并预留 provider 接口。
待明确服务商（SerpAPI / Bing / DuckDuckGo / 自建代理）后补全 _invoke()。
"""

from __future__ import annotations

from core.logging_setup import get_logger


logger = get_logger(__name__)

# 占位返回文本（当 RAG/模型都无法确定时，联网兜底也尚未可用）
_PLACEHOLDER = (
    "[联网搜索暂不可用：服务商未配置（见 §8.2）。"
    "请先在知识库中补充相关文档，或在配置中接入联网搜索 provider。]"
)


class WebSearch:
    """联网搜索兜底适配器（占位）。"""

    def __init__(self, provider: str = "", api_key: str = "") -> None:
        self.provider = provider
        self.api_key = api_key

    def search(self, query: str, top_k: int = 3) -> str:
        """返回联网汇总文本。占位实现：返回提示文本，绝不抛错。"""
        if not self.provider:
            logger.debug("[WebSearch] 未配置 provider，返回占位提示")
            return _PLACEHOLDER
        try:  # pragma: no cover - 待服务商明确后实现
            return self._invoke(query, top_k)
        except Exception as exc:
            logger.warning("[WebSearch] 调用失败，回退占位: %s", exc)
            return f"{_PLACEHOLDER}（错误：{exc}）"

    # TODO(§8.2): 按 provider 接入实际搜索 API
    def _invoke(self, query: str, top_k: int) -> str:
        raise NotImplementedError("联网搜索服务商未实现（§8.2）")
