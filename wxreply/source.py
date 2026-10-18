from __future__ import annotations

import ctypes
from pathlib import Path

import psutil
import win32api

from .models import ChatConfig, Message
from .wechat_db import AccountAwareWeChatDB


class SourceError(RuntimeError):
    pass


def wechat_version() -> str | None:
    for process in psutil.process_iter(["name", "exe"]):
        if (process.info["name"] or "").lower() not in {"weixin.exe", "wechat.exe"}:
            continue
        path = process.info.get("exe")
        if not path:
            continue
        info = win32api.GetFileVersionInfo(path, "\\")
        number = info["ProductVersionMS"], info["ProductVersionLS"]
        return ".".join(str(x) for x in (
            number[0] >> 16, number[0] & 0xFFFF,
            number[1] >> 16, number[1] & 0xFFFF,
        ))
    return None


def desktop_is_unlocked() -> bool:
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.GetUserObjectInformationW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
    ]
    handle = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
    if not handle:
        return False
    try:
        size = ctypes.c_uint(0)
        buffer = ctypes.create_unicode_buffer(128)
        success = user32.GetUserObjectInformationW(
            handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(size)
        )
        return bool(success and buffer.value.lower() == "default")
    finally:
        user32.CloseDesktop(handle)


class DatabaseMessageSource:
    """Read-only view of synced WeChat messages; the upstream cache lives in data_dir."""

    PAGE_SIZE = 100
    MAX_PAGES = 20

    def __init__(self, data_dir: Path):
        workdir = data_dir / "upstream"
        workdir.mkdir(parents=True, exist_ok=True)
        self.db = AccountAwareWeChatDB(workdir=str(workdir))

    @staticmethod
    def _parse(chat_id: str, raw: dict) -> Message:
        is_self = raw.get("is_self") is True
        sender = "self" if is_self else raw.get("sender_username") or chat_id
        return Message(
            chat_id=chat_id,
            sender_id=str(sender),
            is_self=is_self,
            kind=str(raw.get("type") or ""),
            content=str(raw.get("content") or ""),
            sort_seq=int(raw["sort_seq"]),
            local_id=int(raw["local_id"]),
            created_at=int(raw["create_time"]),
        )

    def latest(self, chat_id: str) -> Message | None:
        rows = self.db.get_messages(chat_id, limit=1)
        return self._parse(chat_id, rows[0]) if rows else None

    def recent(self, chat_id: str, limit: int = 20) -> list[Message]:
        rows = self.db.get_messages(chat_id, limit=min(max(limit, 1), self.PAGE_SIZE))
        return [self._parse(chat_id, row) for row in reversed(rows)]

    def read_since(self, chat_id: str, checkpoint: int) -> list[Message]:
        collected: list[Message] = []
        reached_old = False
        for page in range(self.MAX_PAGES):
            rows = self.db.get_messages(chat_id, limit=self.PAGE_SIZE,
                                        offset=page * self.PAGE_SIZE)
            if not rows:
                reached_old = True
                break
            for row in rows:
                seq = int(row["sort_seq"])
                if seq < checkpoint:
                    reached_old = True
                    break
                collected.append(self._parse(chat_id, row))
            if reached_old or len(rows) < self.PAGE_SIZE:
                reached_old = True
                break
        if not reached_old:
            raise SourceError(f"{chat_id} 的新消息超过单轮读取上限，已暂停以防漏读")
        return list(reversed(collected))

    def validate_chat(self, chat: ChatConfig) -> None:
        if chat.chat_type != "private" or chat.username.endswith("@chatroom"):
            raise SourceError("第一版仅允许 private 聊天")
        if chat.username == "filehelper":
            return
        contacts = self.db.search_contact(chat.display_name)
        exact = [item for item in contacts if
                 (item.get("remark") or item.get("nick_name")) == chat.display_name]
        if len(exact) != 1 or exact[0].get("username") != chat.username:
            raise SourceError(f"{chat.display_name} 的备注名不能唯一对应配置的微信账号")


def assert_environment(expected_version: str) -> None:
    actual = wechat_version()
    if actual != expected_version:
        raise SourceError(f"微信版本为 {actual or '未运行'}，配置要求 {expected_version}")
    if not desktop_is_unlocked():
        raise SourceError("Windows 桌面已锁定或不可操作")
