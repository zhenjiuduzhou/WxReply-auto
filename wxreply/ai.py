from __future__ import annotations

import os

import httpx

from .models import AppConfig, Message


class ReplyError(RuntimeError):
    pass


class ReplyGenerator:
    def __init__(self, config: AppConfig):
        self.config = config

    def available(self) -> bool:
        return bool(os.environ.get("WXREPLY_API_KEY") and self.config.model)

    def generate(self, history: list[Message]) -> str:
        key = os.environ.get("WXREPLY_API_KEY")
        if not key or not self.config.model:
            raise ReplyError("请配置 WXREPLY_API_KEY 和 model")
        messages = [{"role": "system", "content": self.config.system_prompt}]
        for item in history[-20:]:
            if item.kind == "文本" and item.content.strip():
                messages.append({
                    "role": "assistant" if item.is_self else "user",
                    "content": item.content[:3000],
                })
        if len(messages) < 2:
            raise ReplyError("没有可用的文字上下文")
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    f"{self.config.api_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": self.config.model, "messages": messages,
                          "temperature": 0.4},
                )
                response.raise_for_status()
                reply = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ReplyError(f"大模型调用失败：{type(exc).__name__}") from exc
        reply = str(reply or "").strip()
        if not reply or len(reply) > 1000:
            raise ReplyError("大模型回复为空或超过 1000 字")
        return reply
