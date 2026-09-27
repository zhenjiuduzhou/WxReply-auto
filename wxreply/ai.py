from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from .models import AppConfig, Message


class ReplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str


def test_connection(base_url: str, model: str, key: str) -> ConnectionTestResult:
    """Exercise the same Chat Completions endpoint without sending chat history."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [
                    {"role": "user", "content": "请只回复：连接成功"}
                ]},
            )
        if response.status_code >= 400:
            hint = {
                401: "请检查 API 密钥",
                403: "请检查密钥权限",
                404: "请检查 API 地址和模型名称",
                429: "请求受限或额度不足",
            }.get(response.status_code, "请检查服务端配置")
            return ConnectionTestResult(False, f"连接失败：HTTP {response.status_code}；{hint}")
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty completion")
    except httpx.RequestError as exc:
        return ConnectionTestResult(False, f"网络连接失败：{type(exc).__name__}")
    except (ValueError, KeyError, IndexError, TypeError):
        return ConnectionTestResult(False, "连接失败：响应不是有效的 Chat Completions 结果")
    elapsed = time.perf_counter() - started
    return ConnectionTestResult(True, f"连接成功，模型已返回文字（{elapsed:.1f} 秒）")


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
