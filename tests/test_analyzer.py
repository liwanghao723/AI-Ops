"""ai.analyzer 单元测试：告警/健康 prompt 构造、级别映射、LLM 失败兜底、RAG 问答上下文。

用 FakeLLM / FakeKM / FakeWeb 替代真实大模型与检索，验证 prompt 含关键字段。
"""

from ai.analyzer import Analyzer, _level_name
from core.errors import LLMError
from core.models import Chunk, HealthRow, WarnInfoDTO, SearchResult


class FakeLLM:
    def __init__(self, return_value="AI回答"):
        self.return_value = return_value
        self.calls = []
        self.raise_error = None

    def chat(self, messages, temperature=0.3):
        self.calls.append((messages, temperature))
        if self.raise_error is not None:
            raise self.raise_error
        return self.return_value


class FakeKM:
    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else []
        self.calls = []

    def search(self, query, top_k=3):
        self.calls.append((query, top_k))
        return self.chunks


class FakeWeb:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=3):
        self.calls.append(query)
        return "联网结果"


def test_explain_alarm_builds_prompt_with_key_fields():
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    warn = WarnInfoDTO(id=1, eventName="主机CPU过高", eventDesc="CPU 97%",
                       eventLevel=1, eventSrc="host03", eventCount=3)
    out = a.explain_alarm(warn)
    assert out == "AI回答"
    msgs = llm.calls[0][0]
    assert msgs[0]["role"] == "system" and "资深" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert "主机CPU过高" in user
    assert "紧急" in user and "(1)" in user
    assert "host03" in user and "3" in user


def test_explain_alarm_level_mapping():
    llm2 = FakeLLM()
    Analyzer(llm=llm2).explain_alarm(WarnInfoDTO(eventName="x", eventLevel=2, eventDesc="d"))
    assert "重要" in llm2.calls[0][0][1]["content"]
    llm4 = FakeLLM()
    Analyzer(llm=llm4).explain_alarm(WarnInfoDTO(eventName="y", eventLevel=4, eventDesc="d"))
    assert "提示" in llm4.calls[0][0][1]["content"]


def test_level_name_maps_platform_caliber():
    """回归：分析器级别文案须与平台口径一致（1=紧急, 2=重要, 3=次要, 4=提示）。

    直接校验模块级 _level_name，避免仅靠 prompt 文本间接覆盖而漏掉回归。
    平台四级独立映射：eventLevel 1/2/3/4 分别对应 紧急/重要/次要/提示。
    """
    assert _level_name(1) == "紧急"
    assert _level_name(2) == "重要"
    assert _level_name(3) == "次要"
    assert _level_name(4) == "提示"
    # 未知级别回退为“级别N”，且不抛异常
    assert _level_name(9) == "级别9"


def test_explain_alarm_llm_error_fallback():
    llm = FakeLLM()
    llm.raise_error = LLMError("down")
    a = Analyzer(llm=llm)
    out = a.explain_alarm(WarnInfoDTO(eventName="过高告警", eventDesc="cpu", eventLevel=1))
    assert "失败" in out and "关键词" in out and "过高告警" in out


def test_explain_health_threshold_breach():
    """桌面级口径：vm_* 超阈值 → prompt 提示「桌面 CPU 利用率」并给出阈值。"""
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    row = HealthRow(title="桌面A", ip="1.1.1.1", os="Win10", status="running",
                   vm_cpu=92.0, vm_mem=70.0, vm_disk=30.0)
    a.explain_health(row)
    user = llm.calls[0][0][1]["content"]
    assert "92" in user and "CPU" in user and "超过阈值" in user
    assert "桌面" in user


def test_explain_health_normal():
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    row = HealthRow(title="桌面B", ip="1.1.1.1", os="Win10", status="running",
                   vm_cpu=10.0, vm_mem=20.0, vm_disk=30.0)
    a.explain_health(row)
    assert "运行正常" in llm.calls[0][0][1]["content"]


def test_explain_health_disk_breach_uses_cpu_threshold():
    """磁盘阈值复用 CPU 阈值（85%）。"""
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    row = HealthRow(title="桌面E", vm_cpu=10.0, vm_mem=10.0, vm_disk=95.0)
    a.explain_health(row)
    user = llm.calls[0][0][1]["content"]
    assert "磁盘" in user and "95" in user and "偏高" in user


def test_explain_health_skips_none_metrics():
    """关机桌面（vm_* 全 None）不参与阈值判定，提示无性能数据。"""
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    row = HealthRow(title="关机桌面", status="关机",
                   vm_cpu=None, vm_mem=None, vm_disk=None)
    a.explain_health(row)
    user = llm.calls[0][0][1]["content"]
    assert "超过阈值" not in user
    assert "运行正常" not in user, "无数据不得误报为运行正常"
    assert "暂无性能数据" in user
    assert user.count("-") >= 3, "三项指标均显示 '-'"


def test_explain_health_partial_none_only_judges_available():
    """部分指标为 None 时，仅对有数据的项做阈值判定。"""
    llm = FakeLLM()
    a = Analyzer(llm=llm)
    row = HealthRow(title="半数据桌面", vm_cpu=99.0, vm_mem=None, vm_disk=None)
    a.explain_health(row)
    user = llm.calls[0][0][1]["content"]
    assert "CPU" in user and "99" in user
    eval_part = user.split("评估结果")[1]
    assert "内存" not in eval_part and "磁盘" not in eval_part, "None 项不进入问题列表"
    assert "桌面自身指标：CPU 99% / 内存 - / 磁盘 -" in user


def test_explain_health_llm_error_fallback():
    llm = FakeLLM()
    llm.raise_error = LLMError("x")
    a = Analyzer(llm=llm)
    out = a.explain_health(HealthRow(vm_cpu=90.0, vm_mem=90.0, vm_disk=90.0))
    assert "失败" in out and "超过阈值" in out


def test_answer_with_km_context():
    llm = FakeLLM()
    km = FakeKM([Chunk(source="faq.md", text="重启服务可恢复", score=0.9)])
    a = Analyzer(llm=llm, km=km)
    out, banner, chunks = a.answer("如何恢复服务")
    assert out == "AI回答"
    user = llm.calls[0][0][1]["content"]
    assert "重启服务可恢复" in user
    assert "如何恢复服务" in user
    assert km.calls[0][1] == 3  # top_k=3
    assert "知识库" in banner
    assert len(chunks) == 1 and chunks[0].source == "faq.md"


def test_answer_no_km_falls_back_to_placeholder():
    llm = FakeLLM()
    a = Analyzer(llm=llm, km=None)
    a.answer("问题")
    assert "无相关命中" in llm.calls[0][0][1]["content"]


def test_answer_llm_error_falls_back_to_web():
    llm = FakeLLM()
    llm.raise_error = LLMError("x")
    web = FakeWeb()
    km = FakeKM([Chunk(source="s", text="t", score=1.0)])
    a = Analyzer(llm=llm, km=km, web=web)
    out, banner, chunks = a.answer("q")
    assert web.calls == ["q"]
    assert out == "联网结果"
    assert "联网" in banner
    assert chunks == []  # 联网兜底不带回填 chunks


def test_answer_source_banner_ima():
    llm = FakeLLM()
    # IMA 命中（origin=ima，kb_names 含「运维手册」）→ banner 含「IMA」与库名
    km = FakeKM([Chunk("运维手册 › 蓝屏", "重启", 0.9)])
    km.search = lambda q, top_k=3: SearchResult(
        chunks=[Chunk("运维手册 › 蓝屏", "重启", 0.9)],
        origin="ima", kb_names=["运维手册"])
    a = Analyzer(llm=llm, km=km)
    out, banner, chunks = a.answer("蓝屏怎么办")
    assert out == "AI回答"
    assert "IMA" in banner and "运维手册" in banner
    assert len(chunks) == 1 and chunks[0].source == "运维手册 › 蓝屏"


def test_answer_returns_chunks_with_url():
    llm = FakeLLM()
    km = FakeKM([Chunk(source="kb", text="片段", score=0.8, url="https://example.com/x")])
    a = Analyzer(llm=llm, km=km)
    _, _, chunks = a.answer("问题")
    assert chunks[0].url == "https://example.com/x"


# ----------------------------------------------------------------------
# 本地兜底 banner 细分：把「IMA 鉴权失败」与「IMA 未命中」区分开
# ----------------------------------------------------------------------
def _km_with(origin, ima_status, chunks, kb_names=None):
    km = FakeKM()
    km.search = lambda q, top_k=3: SearchResult(
        chunks=chunks, origin=origin, ima_status=ima_status,
        kb_names=list(kb_names or []))
    return km


def test_answer_banner_local_auth_failed():
    llm = FakeLLM()
    km = _km_with("local", "auth_failed", [Chunk("faq.md", "重启", 0.9)])
    _, banner, chunks = Analyzer(llm=llm, km=km).answer("q")
    assert "鉴权失败" in banner and "本地" in banner
    assert len(chunks) == 1


def test_answer_banner_local_not_configured():
    llm = FakeLLM()
    km = _km_with("local", "no_cred", [Chunk("faq.md", "t", 0.9)])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "未配置" in banner


def test_answer_banner_local_ima_connected_no_hit():
    llm = FakeLLM()
    km = _km_with("local", "empty", [Chunk("faq.md", "t", 0.9)])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "IMA 未命中" in banner


def test_answer_banner_none_auth_failed():
    llm = FakeLLM()
    km = _km_with("none", "auth_failed", [])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "鉴权失败" in banner


# ----------------------------------------------------------------------
# IMA 标题级命中：平台不返回正文，必须如实标注，不得让模型当成正文
# ----------------------------------------------------------------------
def test_answer_ima_content_hit_banner_and_context():
    llm = FakeLLM()
    km = _km_with("ima", "ok", [Chunk("运维手册 › 蓝屏", "重启并收集日志", 1.0)])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "已基于 IMA 知识库检索" in banner and "仅命中标题" not in banner
    prompt = llm.calls[0][0][1]["content"]
    assert "重启并收集日志" in prompt
    assert "未提供正文" not in prompt


def test_answer_ima_title_only_hit_banner():
    llm = FakeLLM()
    km = _km_with("ima", "ok", [
        Chunk("手册库 › 安装部署指导.pdf", "《安装部署指导.pdf》",
              0.8, is_title_hit=True)], kb_names=["手册库"])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "仅命中标题" in banner and "手册库" in banner


def test_answer_ima_title_only_hit_context_is_marked():
    """标题级命中必须在 prompt 里显式标注「未提供正文」，避免模型编造。"""
    llm = FakeLLM()
    km = _km_with("ima", "ok", [
        Chunk("手册库 › 安装部署指导.pdf", "《安装部署指导.pdf》",
              0.8, is_title_hit=True)])
    Analyzer(llm=llm, km=km).answer("q")
    prompt = llm.calls[0][0][1]["content"]
    assert "未提供正文" in prompt and "不要据此编造" in prompt


def test_answer_ima_mixed_hits_uses_normal_banner():
    """只要有正文级命中，就按正常「已检索」文案，不降级为仅标题。"""
    llm = FakeLLM()
    km = _km_with("ima", "ok", [
        Chunk("手册库 › 有正文.pdf", "有效片段", 1.0),
        Chunk("手册库 › 只有标题.pdf", "《只有标题.pdf》", 0.8, is_title_hit=True)],
        kb_names=["手册库"])
    _, banner, _ = Analyzer(llm=llm, km=km).answer("q")
    assert "已基于 IMA 知识库检索" in banner


def test_extract_keywords():
    # 关键词提取按非字母/中文边界切分：连续中文会作为一个整词
    kws = Analyzer.extract_keywords("主机 CPU 告警 重启服务 失败")
    assert "主机" in kws
    assert "重启服务" in kws
    assert "失败" in kws
    # 停用词过滤：2 字停用词 “一个” 应被剔除
    kws2 = Analyzer.extract_keywords("一个 主机 重启")
    assert "一个" not in kws2
    assert "主机" in kws2
    # 上限 max_kw=8
    long_kw = Analyzer.extract_keywords("词语 苹果 香蕉 橙子 西瓜 柠檬 葡萄 芒果 草莓 菠萝 樱桃")
    assert len(long_kw) <= 8
