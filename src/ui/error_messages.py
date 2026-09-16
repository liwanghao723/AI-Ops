"""AI 分析结果友好化（ui 层，不修改 ai/ 与 core/ 的行为）。

大模型调用失败时，业务层返回的是形如
「⚠️ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required for url: ...）。」
的原始英文文本，直接展示观感很差。这里在 **UI 层** 对文本做分类，
转换为一句中文说明，原始堆栈仅折叠在详情区中备查。
"""

from __future__ import annotations

import re

# 结果文本中出现这些标记即视为失败（业务层统一用「⚠️ …失败（…）」包裹）
_ERROR_MARKS = ("⚠", "Traceback", "Client Error", "Server Error", "LLM 请求失败")

# 规则顺序即优先级（下面的注释标明了依赖关系，调整顺序前请先跑 tests/test_error_messages.py）
_FRIENDLY: list[tuple[re.Pattern, str, str]] = [
    # 1) 429 限流：必须排在 402 之前——真实限流文案常带「quota」关键字，
    #    若被 402 规则先命中会误报成「余额不足」（DEF-01）
    (re.compile(r"\b429\b|rate[_ ]limit|too[_ ]many[_ ]requests|限流", re.I),
     "大模型请求过于频繁",
     "大模型请求过于频繁，已被限流，请稍后重试。"),
    # 2) 401/403 鉴权
    (re.compile(r"\b401\b|\b403\b|unauthorized|forbidden|invalid[_ ]api[_ ]key|"
                r"authentication|无权限|鉴权|未授权", re.I),
     "API Key 无效或无权限",
     "API Key 无效或无权限，请在「设置 → 大模型」中检查 api_key 与厂商配置。"),
    # 3) 402 余额（不收 quota 关键字，避免与 429 混淆）
    (re.compile(r"402|payment[_ ]required|insufficient[_ ]balance|余额|欠费", re.I),
     "大模型账户余额不足",
     "大模型账户余额不足，请充值或更换服务商（设置中可切换）。"),
    # 4) 5xx 服务端错误：必须排在 timeout 之前——504 Gateway Timeout 属服务端不可用，
    #    被 timeout 规则先命中会误报成「无法连接」（DEF-03）
    (re.compile(r"\b5\d\d\b|bad[_ ]gateway|service[_ ]unavailable|server[_ ]error", re.I),
     "大模型服务暂时不可用",
     "大模型服务暂时不可用，请稍后重试或更换服务商。"),
    # 5) 404 / 模型不存在
    (re.compile(r"\b404\b|not[_ ]found|模型不存在|model[_ ]not", re.I),
     "模型或接口地址不可用",
     "模型或接口地址不可用，请在「设置 → 大模型」中检查 model 与 base_url。"),
    # 6) 超时 / 连接失败
    (re.compile(r"timeout|timed[_ ]out|connectionerror|connection[_ ]refused|"
                r"max[_ ]retries|nameresolution|dns|ssl|proxy|网络|连接失败|无法连接", re.I),
     "无法连接大模型服务",
     "无法连接大模型服务，请检查网络或稍后重试。"),
]

_DEFAULT_TITLE = "分析未成功完成"
_DEFAULT_MSG = "本次分析未成功完成，请检查大模型配置或稍后重试。"


# 无 ⚠ 等明确标记时的兜底：只认「句首的 …失败（…）/ …异常（…）」句式。
# 不能放宽成任意位置的「(失败|异常)[（(]」——AI 正常回答极爱写
# 「检测到 1 处异常（CPU 使用率偏高）」，会被误判为失败（DEF-02）。
_FALLBACK_FAIL = re.compile(
    r"^\s*[⚠\s]*(?:分析|告警分析|健康解读|问答)?(?:失败|异常)\s*[（(]")


def is_error_text(text: str) -> bool:
    """判断分析结果文本是否为失败信息。

    优先以 ⚠ / Traceback / Client Error 等明确标记判定（业务层失败统一加该前缀），
    避免正常回答中出现「错误」「异常」字样被误判为失败。
    """
    if not text:
        return True
    if any(mark in text for mark in _ERROR_MARKS):
        return True
    return bool(_FALLBACK_FAIL.match(text))


def classify_error(text: str) -> tuple[str, str]:
    """返回 (中文标题, 友好说明)。未命中任何规则时返回默认文案。"""
    for pattern, title, msg in _FRIENDLY:
        if pattern.search(text or ""):
            return title, msg
    return _DEFAULT_TITLE, _DEFAULT_MSG


def extract_raw_detail(text: str) -> str:
    """从原文中抽取括号内的原始错误（失败原因），抽不到则整段返回。"""
    if not text:
        return ""
    m = re.search(r"[（(](.*?)[)）]\s*。?$", text, re.S)
    if m and len(m.group(1)) > 8:
        return m.group(1).strip()
    return text.strip()


# 「处理建议 / 参考」分区识别
_SUGGEST_HEAD = re.compile(
    r"^\s*(?:[#*\-\s]*)?(?:\d+[\.、)．]\s*)?"
    r"(处理建议|排查建议|解决建议|处置建议|优化建议|建议|参考(?:资料|来源|依据)?|"
    r"参考知识|整改措施)\s*[:：]?\s*$",
    re.I,
)


def split_sections(text: str) -> tuple[str, str]:
    """尝试把 AI 正文拆成 (分析正文, 建议/参考)；拆不出则建议为空串。"""
    if not text:
        return "", ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SUGGEST_HEAD.match(line) and i > 0 and i < len(lines) - 1:
            head = "\n".join(lines[:i]).strip()
            tail = "\n".join(lines[i:]).strip()
            if head and tail:
                return head, tail
    return text.strip(), ""
