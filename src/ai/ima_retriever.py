"""IMA 知识库检索适配层（对应架构 T7 / ima_retriever.py）。

调用 IMA OpenAPI（Base Path ``/openapi/wiki/v1``，官方契约见 ima-skills
知识库模块 ``knowledge-base/references/api.md``）：

  - ``search_knowledge_base``：搜索/枚举知识库（必填 query/cursor/limit，limit 1-20）
  - ``search_knowledge``     ：在某个知识库内**按标题**搜索，返回 info_list
  - ``get_knowledge_list``   ：浏览知识库内容（分页，limit 1-50）
  - ``get_media_info``       ：取媒体原文访问信息（仅网页/笔记类型可拿到 url）

凭证：client_id / api_key（来自配置 rag.ima_client_id / rag.ima_api_key）。
鉴权头（与官方 ima-skills 一致）：
  ima-openapi-clientid / ima-openapi-apikey / ima-openapi-ctx

失败安全：未启用 / 未配凭证 / 网络或鉴权异常 → 返回空列表，
由 KnowledgeManager 回退到本地知识库，不影响其他模块推进。
同时把失败原因写入 ``last_status``（disabled/no_cred/auth_failed/error/empty），
供 UI 区分「未配置 / 鉴权失败 / 已连但未命中」；``check_connection()``
供配置界面「测试 IMA 连接」按钮真实探测连通性。

检索策略（实测校正，2026-09-14）：
  1. 枚举账号下全部知识库（search_knowledge_base，空 query，分页）；
  2. 对每个知识库调用 search_knowledge（服务端标题检索）拿 ``highlight_content``；
  3. 同时用 get_knowledge_list 拉取该库文档清单（带 TTL 缓存），在本地做
     **关键词标题打分**补充召回——实测 search_knowledge 是「标题整串匹配」，
     像「主机 CPU 使用率高怎么排查」这类长问句会 0 命中，必须靠本地拆词兜底；
  4. 跨库合并去重后按相关度降序截断 top_k。
     正文级命中（highlight_content 非空）优先级恒高于标题级命中。

注意：IMA OpenAPI 对文件类媒体（word/pdf/ppt/excel）**不提供正文**
（get_media_info 返回 220030），只有网页/笔记类型能拿到 url。因此标题级
命中的 chunk 文本以《文档标题》形式给出，供模型引用，不伪装成正文。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from core.models import Chunk
from core.logging_setup import get_logger
from .retriever_base import Retriever


logger = get_logger(__name__)

DEFAULT_IMA_BASE = "https://ima.qq.com"
_API_LIST_KB = "openapi/wiki/v1/search_knowledge_base"
_API_SEARCH = "openapi/wiki/v1/search_knowledge"
_API_LIST_DOCS = "openapi/wiki/v1/get_knowledge_list"
_API_MEDIA_INFO = "openapi/wiki/v1/get_media_info"
_SKILL_CTX = "skill_version=h3c-ops-assistant/1.0.0"

# search_knowledge_base 的 limit 上限是 20（传 50 会报 code=51）
_KB_PAGE_LIMIT = 20
_MAX_KB_PAGES = 5              # 最多枚举 100 个知识库
_DOC_PAGE_LIMIT = 50           # get_knowledge_list 的 limit 1-50
_MAX_DOC_PAGES = 10            # 每个库最多 500 篇
_DOC_CACHE_TTL = 600           # 文档清单进程内缓存 10 分钟

# 「连接/检索状态」——供 UI 区分四种情形，避免把鉴权失败误显示成「未命中」
STATUS_OK = "ok"                    # 已连接且有命中
STATUS_EMPTY = "empty"              # 已连接，但本次查询无命中
STATUS_AUTH_FAILED = "auth_failed"  # 鉴权失败（Client ID / API Key 不正确）
STATUS_NO_CRED = "no_cred"          # 未填写 Client ID / API Key
STATUS_DISABLED = "disabled"        # IMA 未启用
STATUS_ERROR = "error"              # 网络/服务端异常

_AUTH_CODES = {401, 403, 200002}
_AUTH_KEYWORDS = ("auth", "unauthorized", "forbidden", "credential", "token", "鉴权")

# 关键词抽取：英文/数字串 + 连续中文串
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+#.\-]{1,}|[\u4e00-\u9fff]+")
# 重复上传产生的副本后缀：「xxx (1).pdf」→「xxx.pdf」
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)(?=\.[A-Za-z0-9]+$|$)")
_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "为何", "为什么", "哪些", "哪个", "多少", "是否",
    "可以", "能否", "需要", "应该", "以及", "并且", "如果", "那么", "这个", "那个",
    "请问", "一下", "我们", "他们", "你们", "问题", "方法", "介绍", "说明", "相关",
    "进行", "出现", "导致", "可能", "一般", "通常", "现在", "已经", "还是", "或者",
}

# 内容级命中固定高于标题级命中（保证排序稳定）
_CONTENT_SCORE = 1.0
# 服务端标题命中（精确子串匹配，信号强）
_API_TITLE_SCORE = 0.85
# 本地关键词命中区间（下限~上限）
_TITLE_SCORE_MIN = 0.40
_TITLE_SCORE_MAX = 0.80
# 相关度地板：低于该值的本地命中属于「只靠通用词撞上」，一律丢弃，
# 避免把《…使用指南》这类无关文档伪装成命中（宁可空手回退本地知识库）。
_MIN_TITLE_SCORE = 0.50
# 标题里出现频率过高的词（如「整本手册」「指导」）视为模板词，直接剔除
_DF_BOILERPLATE_RATIO = 0.5
_DF_MIN_DOCS = 5               # 文档数少于该值时不启用 IDF（样本太小）
_ASCII_TOKEN_WEIGHT = 2.0      # 英文/数字词更具体，权重加倍


class ImaError(RuntimeError):
    """IMA OpenAPI 调用异常。

    ``kind`` ∈ {network, http, json, biz, auth}：
      - network：连接层异常（超时/DNS/拒绝）；
      - http   ：HTTP 状态码 >= 400（非鉴权类）；
      - json   ：响应非 JSON 或结构异常；
      - biz    ：业务码非 0（非鉴权类）；
      - auth   ：鉴权失败（HTTP 401/403 或业务码 200002 / 含 auth 关键字）。
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    @staticmethod
    def looks_like_auth(code: Any, msg: str) -> bool:
        if isinstance(code, int) and code in _AUTH_CODES:
            return True
        low = (msg or "").lower()
        return any(k in low for k in _AUTH_KEYWORDS)


def _kb_id(kb: dict) -> str:
    """知识库 ID：线上返回 ``kb_id``，官方文档写 ``id``，兼容两者。"""
    for key in ("kb_id", "id", "knowledge_base_id"):
        v = kb.get(key)
        if v:
            return str(v)
    return ""


def _kb_name(kb: dict) -> str:
    """知识库名称：线上返回 ``kb_name``，官方文档写 ``name``，兼容两者。"""
    for key in ("kb_name", "name", "title"):
        v = kb.get(key)
        if v:
            return str(v)
    return ""


def _dedupe_by_title(chunks: list[Chunk]) -> list[Chunk]:
    """按「文档标题」去重（保留首次出现，即相关度最高的那条）。

    同一个文件被重复上传成「xxx.pdf」「xxx (1).pdf」时 media_id 不同，
    但标题实质相同，命中详情里并排出现两条属于噪声。
    """
    out: list[Chunk] = []
    seen: set[str] = set()
    for c in chunks:
        # 取 source 里「›」之后的文档标题部分，去掉「 (1)」这类副本后缀
        title = c.source.split("›")[-1].strip()
        key = _COPY_SUFFIX_RE.sub("", title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _keywords(query: str) -> list[str]:
    """把问句拆成检索关键词（英文整词 + 中文 ≥2 字片段），去掉停用词。

    单个汉字（如「高」）区分度太低，会误撞「高可用」这类标题，直接丢弃。
    """
    out: list[str] = []
    for raw in _TOKEN_RE.findall(query or ""):
        if raw[0].isascii():                      # 英文/数字串
            tok = raw.lower()
            if len(tok) >= 2 and tok not in _STOPWORDS:
                out.append(tok)
            continue
        if len(raw) < 2:                          # 单个汉字：无区分度
            continue
        if len(raw) <= 4:                          # 短中文串整体入列
            if raw not in _STOPWORDS:
                out.append(raw)
        for i in range(len(raw) - 1):              # 长中文串补 2-gram
            gram = raw[i:i + 2]
            if gram not in _STOPWORDS:
                out.append(gram)
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


class ImaRetriever(Retriever):
    """IMA 共享知识库检索（真实 OpenAPI 实现）。"""

    def __init__(
        self,
        endpoint: str = "",
        enabled: bool = True,
        client_id: str = "",
        api_key: str = "",
    ) -> None:
        # endpoint 即 IMA OpenAPI 基址；留空用默认
        self.base_url = (endpoint or "").strip() or DEFAULT_IMA_BASE
        self.enabled = enabled
        self.client_id = (client_id or "").strip()
        self.api_key = (api_key or "").strip()
        self._kb_cache: list[dict] | None = None
        self._kb_lock = threading.Lock()
        # 每个知识库的文档清单缓存：{kb_id: (fetched_at, [ {media_id,title}, ... ])}
        self._docs_cache: dict[str, tuple[float, list[dict]]] = {}
        # 最近一次 retrieve 的结果状态（供 KnowledgeManager / UI 读取）
        self.last_status: str = STATUS_DISABLED
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 3) -> list[Chunk]:
        """调用 IMA 检索；任何失败（未启用/未配凭证/异常/空）均返回空列表。

        同时把本次结果写入 ``last_status`` / ``last_error``，供上层区分
        「未配置」「鉴权失败」「已连但未命中」等情形。
        """
        self.last_status = STATUS_DISABLED
        self.last_error = ""
        if not self.enabled:
            logger.debug("[IMA] 未启用，跳过")
            self.last_status = STATUS_DISABLED
            return []
        if not self.client_id or not self.api_key:
            logger.debug("[IMA] 未配置 client_id/api_key，回退本地")
            self.last_status = STATUS_NO_CRED
            return []
        if not query or not query.strip():
            self.last_status = STATUS_EMPTY
            return []

        try:
            chunks = self._search_all(query.strip(), top_k)
        except ImaError as exc:
            self.last_status = (STATUS_AUTH_FAILED if exc.kind == "auth"
                                else STATUS_ERROR)
            self.last_error = exc.message
            logger.warning("[IMA] 检索失败(%s)，将回退本地知识库: %s",
                           exc.kind, exc.message)
            return []
        except Exception as exc:  # 失败安全：绝不抛错，交由上层回退
            self.last_status = STATUS_ERROR
            self.last_error = str(exc)
            logger.warning("[IMA] 检索异常，将回退本地知识库: %s", exc)
            return []
        self.last_status = STATUS_OK if chunks else STATUS_EMPTY
        return chunks

    def check_connection(self) -> "tuple[bool, str]":
        """测试 IMA 连通性（真实枚举知识库），供配置界面「测试 IMA 连接」调用。

        返回 ``(ok, message)``；``message`` 为可直接展示给用户的中文说明。
        """
        if not self.enabled:
            return False, "IMA 未启用：请把「IMA 启用」设为 true"
        if not self.client_id or not self.api_key:
            return False, "未配置凭证：请填写 Client ID 与 API Key（ima.qq.com/agent-interface 获取）"
        self._kb_cache = None  # 强制重新枚举，避免命中旧缓存
        try:
            kbs = self._list_kbs()
        except ImaError as exc:
            if exc.kind == "auth":
                return False, f"鉴权失败：{exc.message}（Client ID / API Key 不正确）"
            if exc.kind == "network":
                return False, f"网络异常：{exc.message}"
            return False, f"连接失败：{exc.message}"
        except Exception as exc:
            return False, f"连接异常：{exc}"

        if not kbs:
            return True, "连接成功，但该账号下暂无知识库（0 个）"
        names = "、".join(_kb_name(kb) or _kb_id(kb) for kb in kbs[:8])
        suffix = f" 等 {len(kbs)} 个" if len(kbs) > 8 else ""
        return True, f"连接成功，共 {len(kbs)} 个知识库：{names}{suffix}"

    # ------------------------------------------------------------------
    # 内部实现：HTTP
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "ima-openapi-clientid": self.client_id,
            "ima-openapi-apikey": self.api_key,
            "ima-openapi-ctx": _SKILL_CTX,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post(self, api_path: str, body: dict[str, Any], timeout: int = 15) -> dict:
        """POST 到 IMA OpenAPI，返回 data 字典；失败一律抛 ``ImaError``。"""
        import requests

        try:
            resp = requests.post(
                f"{self.base_url}/{api_path}",
                headers=self._headers(),
                json=body,
                timeout=timeout,
            )
        except Exception as exc:  # 连接层：超时 / DNS / 拒绝
            raise ImaError("network", f"{type(exc).__name__}: {exc}") from exc

        status = getattr(resp, "status_code", 0) or 0
        if status in (401, 403):
            raise ImaError("auth", f"HTTP {status} 鉴权失败")
        if status >= 400:
            body_txt = (getattr(resp, "text", "") or "")[:200]
            raise ImaError("http", f"HTTP {status}: {body_txt}")

        try:
            payload = resp.json()
        except Exception:
            raise ImaError(
                "json",
                f"响应非 JSON (HTTP {status}): {(getattr(resp, 'text', '') or '')[:200]}",
            )
        if not isinstance(payload, dict):
            raise ImaError("json", "响应结构异常（非对象）")
        code = payload.get("code")
        if code not in (0, None):
            msg = str(payload.get("msg") or payload.get("message") or "")
            kind = "auth" if ImaError.looks_like_auth(code, msg) else "biz"
            raise ImaError(kind, f"code={code} {msg}".strip())
        data = payload.get("data") or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _extract_list(data: dict, *extra_keys: str) -> list:
        """从 data 中取出列表字段（兼容官方键名与历史键名）。"""
        if not isinstance(data, dict):
            return []
        keys = (
            # 官方契约实际使用的键（实测）
            "info_list", "knowledge_list",
            # 历史 / 兼容键
            "list", "items", "knowledge_base_list", "search_results",
            "addable_knowledge_base_list",
        ) + tuple(extra_keys)
        for key in keys:
            v = data.get(key)
            if isinstance(v, list):
                return v
        return []

    def _post_paged(
        self, api_path: str, body: dict[str, Any], list_key: str,
        page_limit: int, max_pages: int, timeout: int = 15,
    ) -> list:
        """按官方「游标翻页」规范拉取全量列表（is_end / next_cursor）。"""
        out: list = []
        cursor = ""
        for _ in range(max_pages):
            payload = dict(body)
            payload["cursor"] = cursor
            payload["limit"] = page_limit
            data = self._post(api_path, payload, timeout=timeout)
            items = [x for x in self._extract_list(data) if isinstance(x, dict)]
            out.extend(items)
            if data.get("is_end") is True or not items:
                break
            nxt = data.get("next_cursor") or ""
            if not nxt or nxt == cursor:
                break
            cursor = nxt
        return out

    # ------------------------------------------------------------------
    # 内部实现：知识库 / 文档清单
    # ------------------------------------------------------------------
    def _list_kbs(self) -> list[dict]:
        """枚举全部知识库（带进程内缓存，避免每次检索重复枚举）。"""
        if self._kb_cache is not None:
            return self._kb_cache
        with self._kb_lock:
            if self._kb_cache is not None:
                return self._kb_cache
            kbs = self._post_paged(
                _API_LIST_KB, {"query": ""}, "info_list",
                _KB_PAGE_LIMIT, _MAX_KB_PAGES,
            )
            self._kb_cache = kbs
            logger.info("[IMA] 枚举到 %d 个知识库", len(kbs))
            return kbs

    def _list_docs(self, kb_id: str) -> list[dict]:
        """浏览某个知识库的文档清单（TTL 缓存），供本地标题召回使用。

        任何失败都返回空列表（不影响主检索链路）。
        """
        now = time.time()
        cached = self._docs_cache.get(kb_id)
        if cached and now - cached[0] < _DOC_CACHE_TTL:
            return cached[1]
        try:
            docs = self._post_paged(
                _API_LIST_DOCS, {"knowledge_base_id": kb_id}, "knowledge_list",
                _DOC_PAGE_LIMIT, _MAX_DOC_PAGES,
            )
        except ImaError as exc:
            if exc.kind == "auth":     # 鉴权失败是全局性的 → 上抛
                raise
            logger.warning("[IMA] 浏览知识库 %s 内容失败，跳过: %s", kb_id, exc.message)
            docs = []
        except Exception as exc:       # 失败安全
            logger.warning("[IMA] 浏览知识库 %s 内容异常，跳过: %s", kb_id, exc)
            docs = []
        self._docs_cache[kb_id] = (now, docs)
        return docs

    def _media_url(self, media_id: str) -> str:
        """仅对网页/笔记类型解析原文链接（文件类实测必失败，直接跳过省调用）。"""
        if not media_id.startswith(("weburl_", "note_")):
            return ""
        try:
            data = self._post(_API_MEDIA_INFO, {"media_id": media_id}, timeout=10)
        except Exception:
            return ""
        url_info = data.get("url_info") or {}
        if isinstance(url_info, dict):
            return str(url_info.get("url") or "").strip()
        return ""

    # ------------------------------------------------------------------
    # 内部实现：检索
    # ------------------------------------------------------------------
    def _search_kb(self, kb_id: str, kb_name: str, query: str) -> list[Chunk]:
        """服务端标题检索单个知识库；鉴权失败上抛，其他异常跳过该库。"""
        try:
            data = self._post(
                _API_SEARCH,
                {"query": query, "cursor": "", "knowledge_base_id": kb_id},
            )
        except ImaError as exc:
            if exc.kind == "auth":     # 鉴权失败是全局性的 → 直接上抛
                raise
            logger.warning("[IMA] 知识库「%s」检索失败，跳过: %s", kb_name, exc.message)
            return []
        out: list[Chunk] = []
        for hit in self._extract_list(data, "results"):
            chunk = self._hit_to_chunk(hit, kb_name, content_score=_CONTENT_SCORE)
            if chunk is not None:
                out.append(chunk)
        return out

    @staticmethod
    def _hit_to_chunk(hit: dict, kb_name: str, content_score: float) -> "Chunk | None":
        """把一条命中记录转成 Chunk。

        - ``highlight_content`` 非空 → 正文级命中（服务端内容匹配）；
        - 否则退化为标题级命中，文本形如《文档标题》，供模型引用。
        """
        title = str(hit.get("title") or hit.get("name") or "").strip()
        snippet = str(
            hit.get("highlight_content") or hit.get("content") or ""
        ).strip()
        if not title and not snippet:
            return None
        if snippet:
            text = snippet
            score = content_score
            is_title_hit = False
        else:
            text = f"《{title}》" if title else ""
            score = _API_TITLE_SCORE
            is_title_hit = True
        source = f"{kb_name} › {title}" if title else kb_name
        return Chunk(source=source, text=text, score=score,
                     url="", doc_id=str(hit.get("media_id") or ""),
                     is_title_hit=is_title_hit)

    def _match_titles(self, query: str, docs: list[dict]) -> list[tuple[float, dict]]:
        """本地标题关键词打分（IDF 加权）：返回 [(相关度, doc)]，按相关度降序。

        兜底实测缺口：``search_knowledge`` 是「整串标题匹配」，长问句
        （如「主机 CPU 使用率高怎么排查」）服务端恒返回 0，必须拆词后
        在本地标题上重打分。

        打分要点：
          - 剔除「模板词」：在某库标题中出现率 > 50% 的词（如
            「整本手册」「指导」「使用」）不携带区分度，直接忽略；
            否则「License 使用 FAQ」会被一堆「XX使用指南」抢走。
          - 英文/数字词权重加倍（``cpu``/``h3c``/``license`` 比中文
            2-gram 具体得多）。
          - 相关度 = 命中词权重 / 全部词权重（覆盖率），映射到
            [_TITLE_SCORE_MIN, _TITLE_SCORE_MAX]。
        """
        kws = _keywords(query)
        if not kws or not docs:
            return []
        pairs: list[tuple[dict, str]] = []          # (doc, 标题小写)，过滤空标题
        for d in docs:
            title = str(d.get("title") or d.get("name") or "").strip()
            if title:
                pairs.append((d, title.lower()))
        if not pairs:
            return []

        # 文档频率：某词出现在多少条标题里
        total = len(pairs)
        weights: dict[str, float] = {}
        for kw in kws:
            df = sum(1 for _, low_t in pairs if kw in low_t)
            if total >= _DF_MIN_DOCS and df / total > _DF_BOILERPLATE_RATIO:
                continue                      # 模板词：无区分度
            weights[kw] = _ASCII_TOKEN_WEIGHT if kw[0].isascii() else 1.0
        if not weights:
            return []

        total_w = sum(weights.values())
        low_q = (query or "").strip().lower()
        scored: list[tuple[float, dict]] = []
        for doc, low_t in pairs:
            matched_w = sum(w for kw, w in weights.items() if kw in low_t)
            if matched_w <= 0:
                continue
            ratio = matched_w / total_w
            if low_q and low_q in low_t:          # 整串命中额外加权
                ratio = min(1.0, ratio + 0.3)
            score = _TITLE_SCORE_MIN + (_TITLE_SCORE_MAX - _TITLE_SCORE_MIN) * ratio
            if score < _MIN_TITLE_SCORE:          # 只靠通用词撞上的，丢弃
                continue
            scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _search_all(self, query: str, top_k: int) -> list[Chunk]:
        kbs = self._list_kbs()
        if not kbs:
            logger.debug("[IMA] 未枚举到知识库，回退本地")
            return []

        merged: dict[str, Chunk] = {}       # media_id → chunk（正文级优先）
        for kb in kbs:
            kb_id, kb_name = _kb_id(kb), (_kb_name(kb) or _kb_id(kb) or "知识库")
            if not kb_id:
                continue

            # 1) 服务端标题检索（可能带 highlight_content 正文）
            for chunk in self._search_kb(kb_id, kb_name, query):
                self._merge(merged, chunk)

            # 2) 本地标题打分，补服务端「整串匹配」漏掉的召回
            for score, doc in self._match_titles(query, self._list_docs(kb_id)):
                mid = str(doc.get("media_id") or doc.get("folder_id") or "")
                if not mid or mid in merged:
                    continue
                title = str(doc.get("title") or doc.get("name") or "").strip()
                if not title:
                    continue
                merged[mid] = Chunk(
                    source=f"{kb_name} › {title}", text=f"《{title}》",
                    score=score, url="", doc_id=mid, is_title_hit=True,
                )

        chunks = sorted(merged.values(), key=lambda c: c.score, reverse=True)
        chunks = _dedupe_by_title(chunks)[:top_k]
        # 3) 只为网页/笔记类型补原文链接（文件类拿不到，不浪费调用）
        for chunk in chunks:
            if not chunk.url:
                chunk.url = self._media_url(chunk.doc_id)
        return chunks

    @staticmethod
    def _merge(merged: dict[str, Chunk], chunk: Chunk) -> None:
        """把 chunk 并入结果集；同一 media_id 保留得分更高（正文级）的那条。"""
        mid = chunk.doc_id or chunk.source
        old = merged.get(mid)
        if old is None or chunk.score > old.score:
            merged[mid] = chunk
