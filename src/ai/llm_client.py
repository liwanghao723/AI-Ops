"""LLM 客户端（对应架构 T5，按主理人决策实现）。

- 直接 requests POST 到 {base_url}/chat/completions（不依赖 openai SDK）
- 请求头 Authorization: Bearer {api_key}
- 请求体 {"model", "messages", "temperature"}
- 解析 response.json()["choices"][0]["message"]["content"]
- set_provider(name) 仅切换 base_url/model/api_key（来自配置）
- 失败抛 LLMError
"""

from __future__ import annotations

import requests

from core.errors import LLMError
from core.logging_setup import get_logger


logger = get_logger(__name__)

# 内置厂商预设（base_url 指向 /v1 兼容端点）
_PROVIDER_PRESETS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
}


class LLMClient:
    """OpenAI 兼容的大模型客户端（requests 裸调）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    # ---- 调用 ----
    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        """发起一次对话补全，返回 content 文本。失败抛 LLMError。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        try:
            logger.debug("[LLM] POST %s model=%s", url, self.model)
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip() if isinstance(content, str) else str(content)
        except requests.exceptions.RequestException as exc:
            logger.error("[LLM] 请求失败: %s", exc)
            raise LLMError(f"LLM 请求失败: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            logger.error("[LLM] 响应解析失败: %s", exc)
            raise LLMError(f"LLM 响应解析失败: {exc}") from exc

    # ---- 厂商切换 ----
    def set_provider(self, name: str, cfg: "object | None" = None) -> None:
        """切换厂商：仅改 base_url / model / api_key。

        name 命中预设时，先套用预设；若同时传入 cfg（含 base_url/api_key/model），
        以 cfg 中显式配置覆盖预设。
        """
        preset = _PROVIDER_PRESETS.get(name)
        if preset:
            self.base_url = preset["base_url"]
            self.model = preset["model"]
            logger.info("[LLM] 切换到厂商预设: %s", name)
        else:
            logger.warning("[LLM] 未知厂商 '%s'，保持当前配置", name)

        if cfg is not None:
            if getattr(cfg, "base_url", ""):
                self.base_url = cfg.base_url.rstrip("/")
            if getattr(cfg, "api_key", ""):
                self.api_key = cfg.api_key
            if getattr(cfg, "model", ""):
                self.model = cfg.model
        logger.info("[LLM] 当前端点=%s model=%s", self.base_url, self.model)
