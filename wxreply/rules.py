from __future__ import annotations

from .models import ChatConfig, Message


class RuleEvaluator:
    def eligible(self, chat: ChatConfig, message: Message) -> bool:
        return (
            chat.enabled
            and chat.chat_type == "private"
            and not chat.username.endswith("@chatroom")
            and not message.is_self
            and message.kind == "文本"
            and bool(message.content.strip())
        )
