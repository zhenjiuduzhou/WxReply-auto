from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Message:
    chat_id: str
    sender_id: str
    is_self: bool
    kind: str
    content: str
    sort_seq: int
    local_id: int
    created_at: int

    @property
    def event_id(self) -> str:
        identity = "\x1f".join(
            map(str, (self.chat_id, self.sort_seq, self.local_id,
                      self.sender_id, self.kind, self.content))
        )
        return sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChatConfig:
    username: str
    display_name: str
    chat_type: str
    enabled: bool


@dataclass(frozen=True)
class AppConfig:
    expected_wechat_version: str
    poll_interval_seconds: float
    auto_send: bool
    api_base_url: str
    model: str
    system_prompt: str
    chats: tuple[ChatConfig, ...]
