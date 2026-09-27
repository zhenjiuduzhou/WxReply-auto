from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import asdict

from .models import AppConfig, ChatConfig


def load_config(path: Path) -> AppConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chats = tuple(
        ChatConfig(
            username=str(item["username"]).strip(),
            display_name=str(item["display_name"]).strip(),
            chat_type=str(item.get("chat_type", "private")),
            enabled=bool(item.get("enabled", False)),
            keywords=tuple(str(x).strip() for x in item.get("keywords", []) if str(x).strip()),
        )
        for item in raw.get("chats", [])
    )
    if len({chat.username for chat in chats}) != len(chats):
        raise ValueError("chats 中有重复的 username")
    if any(not chat.username or not chat.display_name for chat in chats):
        raise ValueError("每个聊天必须填写 username 和 display_name")
    if any(chat.chat_type not in {"private", "group"} for chat in chats):
        raise ValueError("chat_type 只能是 private 或 group")
    interval = float(raw.get("poll_interval_seconds", 2))
    if interval < 0.5:
        raise ValueError("poll_interval_seconds 不能小于 0.5")
    return AppConfig(
        expected_wechat_version=str(raw["expected_wechat_version"]),
        poll_interval_seconds=interval,
        auto_send=bool(raw.get("auto_send", False)),
        api_base_url=str(raw.get("api_base_url", "https://api.openai.com/v1")).rstrip("/"),
        model=str(raw.get("model", "")).strip(),
        system_prompt=str(raw.get("system_prompt", "你正在帮我拟写微信回复。只输出回复正文。")),
        chats=chats,
    )


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
