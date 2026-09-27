from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .ai import ReplyError, ReplyGenerator
from .models import AppConfig, Message
from .rules import RuleEvaluator
from .sender import GuardedSender, SendCancelled
from .source import DatabaseMessageSource, SourceError, assert_environment
from .store import Store


log = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: AppConfig, data_dir: Path):
        self.config = config
        self.store = Store(data_dir / "state.db")
        self.source: DatabaseMessageSource | None = None
        self.generator = ReplyGenerator(config)
        self.rules = RuleEvaluator()
        self._stop = threading.Event()
        self._status = "启动中"
        self._pause_reason = ""
        self._status_lock = threading.Lock()
        self._chats = {chat.username: chat for chat in config.chats if chat.enabled}

    @property
    def status(self) -> str:
        with self._status_lock:
            return self._status

    def _set_status(self, value: str) -> None:
        with self._status_lock:
            self._status = value

    def stop(self) -> None:
        self._stop.set()

    def set_paused(self, paused: bool) -> None:
        self.store.set_paused(paused)
        self._pause_reason = "手动暂停" if paused else ""
        self._set_status("已手动暂停" if paused else "准备恢复")

    def _pause_with_reason(self, reason: str) -> None:
        self.store.set_paused(True)
        self._pause_reason = reason
        self._set_status(reason)

    def _connect(self, data_dir: Path) -> None:
        assert_environment(self.config.expected_wechat_version)
        self.source = DatabaseMessageSource(data_dir)
        for chat in self._chats.values():
            self.source.validate_chat(chat)

    def _poll(self) -> None:
        assert self.source is not None
        paused = self.store.is_paused()
        for chat in self._chats.values():
            checkpoint = self.store.checkpoint(chat.username)
            if checkpoint is None:
                existing = self.source.recent(chat.username, limit=200)
                if existing:
                    self.store.ingest(chat.username, existing, suppressed=True)
                else:
                    self.store.initialize_chat(chat.username, 0)
                continue
            incoming = self.source.read_since(chat.username, checkpoint)
            self.store.ingest(chat.username, incoming, suppressed=paused)

    @staticmethod
    def _row_message(row) -> Message:
        return Message(
            chat_id=row["chat_id"], sender_id=row["sender_id"],
            is_self=bool(row["is_self"]), kind=row["kind"],
            content=row["content"], sort_seq=row["sort_seq"],
            local_id=row["local_id"], created_at=row["created_at"],
        )

    def _process(self) -> None:
        assert self.source is not None
        for row in self.store.pending():
            if self.store.is_paused() or self._stop.is_set():
                return
            chat = self._chats.get(row["chat_id"])
            incoming = self._row_message(row)
            state = row["state"]
            if not chat or not self.rules.eligible(chat, incoming):
                self.store.set_state(row["event_id"], state, "ignored")
                continue
            if state == "queued":
                if not self.generator.available():
                    self._set_status("待配置大模型 API 密钥或 model")
                    return
                if not self.store.set_state(row["event_id"], "queued", "generating"):
                    continue
                try:
                    history = self.source.recent(chat.username, limit=20)
                    reply = self.generator.generate(history)
                except ReplyError as exc:
                    self.store.set_state(row["event_id"], "generating", "failed", detail=str(exc))
                    continue
                except Exception as exc:
                    self.store.set_state(row["event_id"], "generating", "failed",
                                         detail=f"生成异常：{type(exc).__name__}")
                    self._pause_with_reason(f"生成异常：{type(exc).__name__}")
                    log.exception("回复生成异常；自动回复已暂停")
                    return
                self.store.set_state(row["event_id"], "generating", "ready", reply=reply)
            else:
                reply = row["reply"]
            if not reply:
                self.store.set_state(row["event_id"], "ready", "failed", detail="拟回复为空")
                continue
            if self.store.is_paused():
                self.store.set_state(row["event_id"], "ready", "ignored", detail="自动回复暂停")
                return
            if not self.config.auto_send:
                self.store.set_state(row["event_id"], "ready", "drafted", detail="仅生成模式")
                continue
            if not self.store.set_state(row["event_id"], "ready", "sending"):
                continue
            try:
                GuardedSender(
                    self.source, self.config.expected_wechat_version,
                    should_cancel=lambda: self._stop.is_set() or self.store.is_paused(),
                ).send(
                    chat, incoming, reply
                )
            except SendCancelled as exc:
                self.store.set_state(row["event_id"], "sending", "superseded", detail=str(exc))
            except SourceError as exc:
                self.store.set_state(row["event_id"], "sending", "failed", detail=str(exc))
                self._pause_with_reason(f"发送条件失效：{exc}")
                return
            except Exception as exc:
                self.store.set_state(row["event_id"], "sending", "unknown", detail=str(exc))
                self._pause_with_reason(f"发送结果待核实：{type(exc).__name__}")
                log.exception("发送中断；自动回复已暂停")
                return
            else:
                self.store.set_state(row["event_id"], "sending", "confirmed")

    def run(self, data_dir: Path) -> None:
        self.store.recover()
        if self.store.is_paused():
            self.store.set_paused(True)
            self._set_status("已暂停，请在窗口或托盘恢复")
        if not self._chats:
            self._set_status("待配置启用的私聊")
            self._stop.wait()
            return
        while not self._stop.is_set():
            if self.source is None:
                if self.store.is_paused():
                    self._stop.wait(self.config.poll_interval_seconds)
                    continue
                try:
                    self._connect(data_dir)
                except Exception as exc:
                    self._pause_with_reason(f"连接失败：{type(exc).__name__}: {exc}")
                    log.exception("微信连接失败")
                    self._stop.wait(self.config.poll_interval_seconds)
                    continue
            try:
                assert_environment(self.config.expected_wechat_version)
                self._poll()
                self._process()
                if self.store.is_paused():
                    self._set_status(self._pause_reason or "已暂停；新消息只记录不回复")
                elif not self.generator.available() and self.store.pending():
                    self._set_status("待配置大模型 API 密钥或 model")
                elif self.config.auto_send:
                    self._set_status("运行中：自动发送")
                else:
                    self._set_status("运行中：仅生成")
            except Exception as exc:
                reason = f"读取异常：{type(exc).__name__}: {exc}"
                if self._pause_reason != reason:
                    log.exception("微信读取异常；自动回复已暂停")
                self._pause_with_reason(reason)
            self._stop.wait(self.config.poll_interval_seconds)
