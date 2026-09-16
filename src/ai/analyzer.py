"""分析器（对应架构 T9 / analyzer.py）。

封装三类智能分析：
  - explain_alarm(warn)：告警含义解释 + 3 条建议
  - explain_health(row)：**桌面级**健康阈值解读（vm_cpu/vm_mem/vm_disk）
  - answer(query)：RAG 问答（IMA→本地→联网兜底）

依赖：LLMClient、KnowledgeManager、WebSearch（可选）。
"""

from __future__ import annotations

from core.errors import LLMError
from core.logging_setup import get_logger
from core.models import Chunk, HealthRow, WarnInfoDTO, SearchResult

_LEVEL_TEXT = {1: "紧急", 2: "重要", 3: "次要", 4: "提示"}

# 「本地兜底」banner：按 IMA 步骤的结果状态细分，避免把鉴权失败误报成「未命中」
#   key 为 SearchResult.ima_status（见 ai.ima_retriever 的 STATUS_* 常量）
_LOCAL_BANNER = {
    "auth_failed": "🔒 IMA 鉴权失败（Client ID / API Key 不正确），已回退本地知识库",
    "no_cred": "⚙️ IMA 未配置（缺 Client ID / API Key），已回退本地知识库",
    "disabled": "⚙️ IMA 未启用，已使用本地知识库",
    "error": "⚠️ IMA 连接异常，已回退本地知识库",
}

# 「无命中」banner：鉴权失败时额外提示，帮助用户定位是凭证问题而非真的没有内容
_NONE_BANNER = {
    "auth_failed": "🔒 IMA 鉴权失败，且本地知识库无相关命中，已直接由模型回答",
    "no_cred": "⚙️ IMA 未配置，本地知识库也无相关命中，已直接由模型回答",
    "error": "⚠️ IMA 连接异常，本地知识库也无相关命中，已直接由模型回答",
}


def _level_name(level: int) -> str:
    return _LEVEL_TEXT.get(level, f"级别{level}")


def _context_line(chunk) -> str:
    """把一条命中拼进给模型的上下文。

    IMA 的文档类媒体只能拿到标题（``is_title_hit=True``），必须显式标注，
    否则模型会误以为自己持有正文而编造内容。
    """
    if getattr(chunk, "is_title_hit", False):
        return (f"【来源 {chunk.source}】（IMA 知识库文档标题，平台未提供正文，"
                f"请仅作为「可进一步查阅的资料」引用，不要据此编造具体步骤）\n{chunk.text}")
    return f"【来源 {chunk.source}】\n{chunk.text}"


class Analyzer:
    """告警/健康/问答分析器。"""

    def __init__(self, llm: "object", km: "object | None" = None,
                 web: "object | None" = None) -> None:
        self.llm = llm
        self.km = km
        self.web = web

    # ------------------------------------------------------------------
    # 关键词提取（F3-01）：简单去停用词的中英文 token 提取
    # ------------------------------------------------------------------
    @staticmethod
    def extract_keywords(text: str, max_kw: int = 8) -> list[str]:
        import re
        toks = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_]+", text or "")
        stop = {"的", "了", "和", "与", "及", "是", "在", "我", "你", "请", "该", "此", "一个", "出现", "发生"}
        seen: list[str] = []
        for t in toks:
            if t.lower() in stop:
                continue
            if t not in seen:
                seen.append(t)
            if len(seen) >= max_kw:
                break
        return seen

    # ------------------------------------------------------------------
    # 告警分析
    # ------------------------------------------------------------------
    def explain_alarm(self, warn: WarnInfoDTO) -> str:
        kw = " ".join(self.extract_keywords(f"{warn.eventName} {warn.eventDesc}"))
        system = "你是一名资深云桌面运维专家，请用简体中文、条理清晰地分析告警。"
        user = (
            f"告警名称：{warn.eventName}\n"
            f"告警描述：{warn.eventDesc}\n"
            f"告警级别：{_level_name(warn.eventLevel)}({warn.eventLevel})\n"
            f"事件来源：{warn.eventSrc}\n"
            f"触发次数：{warn.eventCount}\n\n"
            f"请先解释该告警的故障含义，再给出 3 条排查/解决建议（编号列出）。"
        )
        try:
            return self.llm.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                temperature=0.3,
            )
        except LLMError as exc:
            logger = get_logger(__name__)
            logger.error("[Analyzer] 告警分析失败: %s", exc)
            return f"⚠️ 告警分析失败（{exc}）。\n关键词：{kw}"

    # ------------------------------------------------------------------
    # 健康解读（桌面级口径：vm_cpu / vm_mem / vm_disk）
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_pct(value: float | None) -> str:
        """百分比格式化；None（关机/无数据）显示 "-"。"""
        return "-" if value is None else f"{value:.0f}%"

    def explain_health(self, row: HealthRow,
                       threshold_cpu: int = 85, threshold_mem: int = 85) -> str:
        """按**桌面自身**性能（2.27.31）解读健康度。

        ``vm_*`` 为 ``None``（关机 / 未采集）时跳过该项阈值判定，
        三项全为 None 时明确提示「无性能数据」，不误报为「运行正常」。
        """
        issues: list[str] = []
        if row.vm_cpu is not None and row.vm_cpu >= threshold_cpu:
            issues.append(f"桌面 CPU 利用率 {row.vm_cpu:.0f}% 超过阈值 {threshold_cpu}%")
        if row.vm_mem is not None and row.vm_mem >= threshold_mem:
            issues.append(f"桌面内存利用率 {row.vm_mem:.0f}% 超过阈值 {threshold_mem}%")
        if row.vm_disk is not None and row.vm_disk >= threshold_cpu:
            issues.append(f"桌面磁盘利用率 {row.vm_disk:.0f}% 偏高")
        has_data = any(v is not None for v in (row.vm_cpu, row.vm_mem, row.vm_disk))
        if not issues:
            if has_data:
                issues.append("当前各项利用率均在阈值内，运行正常")
            else:
                issues.append("该桌面暂无性能数据（可能处于关机状态），无法评估")
        summary = "；".join(issues)
        user = (
            f"云桌面：{row.title}（IP {row.ip}，系统 {row.os}，状态 {row.status}）\n"
            f"桌面自身指标：CPU {self._fmt_pct(row.vm_cpu)} / "
            f"内存 {self._fmt_pct(row.vm_mem)} / 磁盘 {self._fmt_pct(row.vm_disk)}\n"
            f"评估结果：{summary}\n\n请给出一句运维建议。"
        )
        try:
            return self.llm.chat(
                [{"role": "system", "content": "你是云桌面运维助手，回答简洁专业。"},
                 {"role": "user", "content": user}],
                temperature=0.3,
            )
        except LLMError as exc:
            logger = get_logger(__name__)
            logger.error("[Analyzer] 健康解读失败: %s", exc)
            return f"⚠️ 健康解读失败（{exc}）。\n评估：{summary}"

    # ------------------------------------------------------------------
    # RAG 问答（F3-02/03/04/05）：IMA→本地→联网兜底
    # ------------------------------------------------------------------
    def answer(self, query: str) -> "tuple[str, str, list[Chunk]]":
        """返回 (回答文本, 来源提示 banner)。

        banner 用于 UI 展示「本次回答基于哪个知识库」，涵盖：
          - IMA 命中：✅ 已基于 IMA 知识库检索（命中：KB1、KB2）
          - 本地兜底：📁 已基于本地知识库检索（IMA 未命中）
          - 本地兜底（IMA 异常细分）：🔒 鉴权失败 / ⚙️ 未配置 / ⚠️ 连接异常
          - 知识库无命中：ℹ️ 知识库无相关命中，已直接由模型回答
          - 联网兜底：🌐 已启用联网兜底（知识库与模型均无可用结果）
        """
        context = ""
        # banner 默认值（知识库无命中时由模型直接作答）
        banner = "ℹ️ 知识库无相关命中，已直接由模型回答"
        chunks: "list[Chunk]" = []
        if self.km is not None:
            keywords = self.extract_keywords(query)
            search_q = query if not keywords else " ".join(keywords)
            result = self.km.search(search_q, top_k=3)
            # 兼容旧 list 返回（测试替身）：统一为 SearchResult 形态
            ima_status = ""
            if isinstance(result, SearchResult):
                chunks, origin, kb_names = result.chunks, result.origin, result.kb_names
                ima_status = getattr(result, "ima_status", "") or ""
            else:
                chunks, origin, kb_names = (result or []), "unknown", []
            if chunks:
                context = "\n\n".join(_context_line(c) for c in chunks)
                if origin == "ima":
                    kbs = "、".join(kb_names) or "IMA"
                    # IMA OpenAPI 对文档类媒体只返回标题（拿不到正文），
                    # 此时必须如实告知，避免用户误以为模型读了整篇文档
                    if all(getattr(c, "is_title_hit", False) for c in chunks):
                        banner = f"✅ 已匹配 IMA 知识库文档（{kbs}，仅命中标题，正文请在 IMA 中查看）"
                    else:
                        banner = f"✅ 已基于 IMA 知识库检索（命中：{kbs}）"
                elif origin == "local":
                    # 区分 IMA 为何没命中：未配置 / 鉴权失败 / 已连未命中 / 异常
                    banner = _LOCAL_BANNER.get(
                        ima_status, "📁 已基于本地知识库检索（IMA 未命中）")
                elif origin == "none":
                    banner = _NONE_BANNER.get(
                        ima_status, "ℹ️ 知识库无相关命中，已直接由模型回答")
                else:  # unknown（测试替身返回 list）
                    banner = "📚 已基于知识库检索"
            else:
                banner = _NONE_BANNER.get(
                    ima_status, "ℹ️ 知识库无相关命中，已直接由模型回答")
        else:
            banner = "ℹ️ 未配置知识库，已直接由模型回答"

        prompt_ctx = (f"参考以下知识库内容回答问题：\n{context}\n\n" if context
                      else "（本地知识库无相关命中）\n")
        user = f"{prompt_ctx}用户问题：{query}\n请给出准确、可操作的回答。"

        try:
            text = self.llm.chat(
                [{"role": "system", "content": "你是 H3C 云桌面运维知识助手。"},
                 {"role": "user", "content": user}],
                temperature=0.3,
            )
            return text, banner, chunks
        except LLMError as exc:
            # LLM 失败 → 联网兜底（若有）
            logger = get_logger(__name__)
            logger.warning("[Analyzer] LLM 问答失败，尝试联网兜底: %s", exc)
            if self.web is not None:
                return (self.web.search(query),
                        "🌐 已启用联网兜底（知识库与模型均无可用结果）", [])
            return f"⚠️ 问答失败（{exc}）。", banner, []
