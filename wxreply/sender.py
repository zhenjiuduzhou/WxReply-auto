from __future__ import annotations

import time

from wechatauto.uia_driver import WeChatUIA

from .models import ChatConfig, Message
from .source import DatabaseMessageSource, SourceError, assert_environment


class SendUncertain(RuntimeError):
    pass


class SendCancelled(RuntimeError):
    pass


class GuardedSender:
    """One keyboard send only. Any ambiguous outcome stays unknown for review."""

    def __init__(self, source: DatabaseMessageSource, expected_version: str):
        self.source = source
        self.expected_version = expected_version

    def send(self, chat: ChatConfig, incoming: Message, reply: str) -> None:
        assert_environment(self.expected_version)
        self.source.validate_chat(chat)
        ui = WeChatUIA()
        if not ui.ensure_window():
            raise SourceError("微信 UI Automation 控件树不可用")
        if not ui.open_chat(chat.display_name) or ui.current_chat() != chat.display_name:
            raise SourceError("无法准确打开目标会话")
        latest = self.source.latest(chat.username)
        if latest is None or latest.event_id != incoming.event_id:
            raise SendCancelled("目标会话已有更新消息，取消本次回复")
        edit = ui._chat_input()
        if edit is None:
            raise SourceError("聊天输入框不可用")
        ui._paste_into(edit, reply, clear=True)
        if edit.GetValuePattern().Value != reply:
            raise SourceError("输入框内容与拟回复不一致，取消发送")
        if ui.current_chat() != chat.display_name:
            raise SendCancelled("发送前目标会话发生变化")
        latest = self.source.latest(chat.username)
        if latest is None or latest.event_id != incoming.event_id:
            raise SendCancelled("发送前出现新消息，取消本次回复")
        try:
            edit.SendKeys("{Enter}", waitTime=0.05)
        except Exception as exc:
            raise SendUncertain("发送动作异常，结果待核实") from exc
        for _ in range(10):
            rows = self.source.db.get_messages(chat.username, limit=20)
            if any(
                int(row["sort_seq"]) > incoming.sort_seq
                and row.get("sender_id") == 2
                and row.get("content") == reply
                for row in rows
            ):
                return
            time.sleep(1)
        raise SendUncertain("已执行发送，但数据库未确认，结果待核实")
