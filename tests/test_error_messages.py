"""ui/error_messages.py 单元测试（纯逻辑，可离线运行）。

覆盖：
- classify_error：402 / 401 / 403 / timeout / 连接失败 / 429 / 5xx / 404 模型不存在 / 未知兜底
- is_error_text：错误文本 True；含「错误」字样的正常回答必须 False（防误判）
- extract_raw_detail：从业务层包裹文本中抽出原始英文串
- split_sections：正文 / 建议分区拆分
- 边界：空串、None、超长文本、纯英文堆栈
"""

from __future__ import annotations

import pytest

from ui.error_messages import (
    classify_error,
    extract_raw_detail,
    is_error_text,
    split_sections,
)


# ---------------------------------------------------------------------------
# 工具：判定是否为中文标题/说明（不能把英文堆栈作为主体）
# ---------------------------------------------------------------------------
def _assert_chinese(title: str, msg: str) -> None:
    assert isinstance(title, str) and isinstance(msg, str)
    assert title.strip() and msg.strip()
    assert any("\u4e00" <= ch <= "\u9fff" for ch in title), f"标题非中文: {title}"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in msg), f"说明非中文: {msg}"
    # 友好文案不得以英文报错原文为主体
    for english in ("Client Error", "Traceback", "Payment Required",
                    "Unauthorized", "timed out", "Bad Gateway"):
        assert english not in title, f"标题泄漏英文堆栈: {title}"
        assert english not in msg, f"说明泄漏英文堆栈: {msg}"


# ---------------------------------------------------------------------------
# classify_error：各分类命中
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, kw",
    [
        ("⚠ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required "
         "for url: https://api.deepseek.com/v1/chat/completions）。",
         "余额"),
        ("⚠ 分析失败（402 payment required, insufficient balance）。", "余额"),
        ("⚠ 分析失败（账户余额不足，请充值后重试）。", "余额"),
        ("⚠ 分析失败（账户欠费已被停用）。", "余额"),
    ],
)
def test_classify_error_balance_402(text, kw):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert kw in title or kw in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（401 Unauthorized: invalid api key）。",
        "⚠ 分析失败（authentication failed）。",
        "⚠ 分析失败（鉴权失败，请检查凭据）。",
        "⚠ 分析失败（未授权访问）。",
    ],
)
def test_classify_error_auth_401(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "API Key" in title or "无权限" in title or "权限" in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（403 Forbidden）。",
        "⚠ 分析失败（forbidden: access denied）。",
    ],
)
def test_classify_error_forbidden_403(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "API Key" in title or "无权限" in title


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（HTTPSConnectionPool(host='api.deepseek.com', port=443): "
        "Read timed out. (read timeout=60)）。",
        "⚠ 分析失败（requests.exceptions.ReadTimeout: timed out）。",
        "⚠ 分析失败（网络连接失败，请重试）。",
    ],
)
def test_classify_error_timeout(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "连接" in title or "连接" in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（ConnectionError: connection refused）。",
        "⚠ 分析失败（ConnectionError: Failed to resolve 'api.deepseek.com'）。",
        "⚠ 分析失败（SSL: CERTIFICATE_VERIFY_FAILED）。",
    ],
)
def test_classify_error_connection(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "连接" in title or "网络" in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（429 Too Many Requests）。",
        "⚠ 分析失败（429 Client Error: Too Many Requests rate limit reached）。",
        "⚠ 分析失败（已被限流，请稍后）。",
    ],
)
def test_classify_error_rate_limit_429(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "频繁" in title or "限流" in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（500 Internal Server Error）。",
        "⚠ 分析失败（502 Bad Gateway）。",
        "⚠ 分析失败（503 Server Error: Service Unavailable）。",
    ],
)
def test_classify_error_server_5xx(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "不可用" in title or "可用" in msg


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（404 Not Found: model 'deepseek-reasoner-x' does not exist）。",
        "⚠ 分析失败（404 Client Error: Not Found for url: https://x/v1/chat）。",
        "⚠ 分析失败（模型不存在）。",
    ],
)
def test_classify_error_model_not_found_404(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert "模型" in title or "地址" in title


@pytest.mark.parametrize(
    "text",
    [
        "⚠ 分析失败（KeyError: 'choices'）。",
        "⚠ 分析失败（Unexpected none）。",
        "（未知的内部异常）",
    ],
)
def test_classify_error_default_fallback(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)
    assert title == "分析未成功完成"
    assert "未成功完成" in msg


@pytest.mark.parametrize("text", ["", None])
def test_classify_error_handles_empty(text):
    title, msg = classify_error(text)
    _assert_chinese(title, msg)


def test_classify_error_no_english_stacktrace_as_body():
    """回归核心诉求：402 场景不得把原始英文堆栈作为主体展示。"""
    raw = ("⚠ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required "
           "for url: https://api.deepseek.com/v1/chat/completions）。")
    title, msg = classify_error(raw)
    assert "余额不足" in title or "余额不足" in msg
    assert "Payment Required" not in title
    assert "Payment Required" not in msg


# ---------------------------------------------------------------------------
# is_error_text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "⚠ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required）。",
        "⚠️ 健康解读失败（Read timed out）。",
        "Traceback (most recent call last):\n  File \"a.py\", line 3\nValueError: boom",
        "⚠ 问答失败（Client Error: 404）。",
        "⚠ 失败（Server Error: 503）。",
        "LLM 请求失败: connection refused",
        "",
        None,
    ],
)
def test_is_error_text_true(text):
    assert is_error_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # 关键用例：正常回答中出现「错误」二字，不得误判为失败
        "本次巡检共发现 3 条错误日志，主要集中在 cvknode-02，建议清理后观察。",
        "该桌面的错误日志增长速率已趋于平稳，无需立即处理。",
        "错误集中出现在 02:00-03:00 的备份窗口，属正常现象。",
        # 其它正常回答
        "所有主机 CPU/内存均在阈值以下，运行正常。",
        "处理建议：\n1. 重启 agent\n2. 观察 30 分钟",
        "主机 cvknode-01 状态良好。",
    ],
)
def test_is_error_text_false_for_normal_answer(text):
    assert is_error_text(text) is False


def test_is_error_text_false_when_no_failure_marker():
    """仅有「失败」字样但不构成失败包裹时不应误判。"""
    assert is_error_text("本次备份任务失败率低于 1%") is False


# ---------------------------------------------------------------------------
# extract_raw_detail
# ---------------------------------------------------------------------------
def test_extract_raw_detail_from_business_wrapped_text():
    raw = ("⚠ 告警分析失败（LLM 请求失败: 402 Client Error: Payment Required "
           "for url: https://api.deepseek.com/v1/chat/completions）。")
    detail = extract_raw_detail(raw)
    assert "402 Client Error" in detail
    assert "Payment Required" in detail
    assert "api.deepseek.com" in detail
    # 不得把业务层中文包裹带出来
    assert "告警分析失败" not in detail
    assert "⚠" not in detail


def test_extract_raw_detail_nested_parenthesis():
    raw = ("⚠ 失败（requests.exceptions.HTTPError: 402 Client Error: "
           "Payment Required for url: https://api.a.com/v1/chat (id=1)）。")
    detail = extract_raw_detail(raw)
    assert "402 Client Error" in detail
    assert detail.endswith(")")


def test_extract_raw_detail_multiline_traceback_fallback():
    tb = ('Traceback (most recent call last):\n'
          '  File "a.py", line 3, in <module>\n'
          'ValueError: boom')
    detail = extract_raw_detail(tb)
    assert "Traceback" in detail
    assert "ValueError: boom" in detail


def test_extract_raw_detail_short_content_falls_back_to_whole():
    raw = "⚠ 失败（短）。"
    assert extract_raw_detail(raw) == raw.strip()


@pytest.mark.parametrize("text", ["", None])
def test_extract_raw_detail_empty(text):
    assert extract_raw_detail(text) == ""


def test_extract_raw_detail_very_long_text():
    payload = "E" * 20000
    raw = f"⚠ 失败（{payload}）。"
    detail = extract_raw_detail(raw)
    assert detail == payload


# ---------------------------------------------------------------------------
# split_sections
# ---------------------------------------------------------------------------
def test_split_sections_with_suggestion():
    text = ("主机 cvknode-01 CPU 持续偏高，已连续 30 分钟超过 85%。\n\n"
            "处理建议：\n1. 排查占用进程\n2. 考虑扩容")
    main, suggest = split_sections(text)
    assert "cvknode-01" in main
    assert "处理建议" not in main
    assert suggest.startswith("处理建议")
    assert "扩容" in suggest


@pytest.mark.parametrize(
    "head",
    ["处理建议：", "排查建议：", "解决建议：", "优化建议：", "建议", "参考", "## 参考"],
)
def test_split_sections_various_headers(head):
    text = f"CPU 偏高。\n\n{head}\n重启服务"
    main, suggest = split_sections(text)
    assert main == "CPU 偏高。"
    assert suggest


def test_split_sections_no_suggestion_keeps_full_body():
    text = "所有主机内存正常，无异常。"
    main, suggest = split_sections(text)
    assert main == text
    assert suggest == ""


def test_split_sections_head_at_first_or_last_line_not_split():
    """建议行位于首行或末行时，不切分（避免拆空）。"""
    assert split_sections("处理建议：\n重启") == ("处理建议：\n重启", "")
    assert split_sections("正文。\n建议") == ("正文。\n建议", "")


def test_split_sections_very_long_text():
    text = "A" * 5000 + "\n处理建议：\n重启"
    main, suggest = split_sections(text)
    assert main == "A" * 5000
    assert "重启" in suggest


@pytest.mark.parametrize("text", ["", None])
def test_split_sections_empty(text):
    assert split_sections(text) == ("", "")


# ---------------------------------------------------------------------------
# 已知缺陷登记（xfail：修复后自动转为 XPASS，不会阻断流水线）
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    reason="已知缺陷 DEF-01：402 规则含 'quota' 关键字且排在 429 规则之前，"
           "导致真实 429 限流错误被误判为「余额不足」(error_messages.py:18 / :29)",
    strict=False,
)
def test_defect_01_429_with_quota_keyword_should_not_be_balance():
    title, _ = classify_error("⚠ 分析失败（429 rate limit: quota exceeded）。")
    assert "余额" not in title


@pytest.mark.xfail(
    reason="已知缺陷 DEF-03：timeout 规则排在 5xx 规则之前，504 Gateway Timeout "
           "被判为「无法连接大模型服务」而非「大模型服务暂时不可用」"
           "(error_messages.py:25-26 优先于 :32)",
    strict=False,
)
def test_defect_03_504_should_be_server_unavailable():
    title, _ = classify_error("⚠ 分析失败（504 Gateway Timeout）。")
    assert "暂时不可用" in title


@pytest.mark.xfail(
    reason="已知缺陷 DEF-02：is_error_text 的兜底正则 '(失败|异常)[（(]' 过宽，"
           "正常回答「检测到 1 处异常（CPU 偏高）」被误判为失败，"
           "正文不会展示而走到错误卡片 (error_messages.py:54)",
    strict=False,
)
def test_defect_02_normal_answer_with_parenthesised_anomaly():
    assert is_error_text("检测到 1 处异常（CPU 使用率偏高）") is False
