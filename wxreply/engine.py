from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
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
        self._inflight: dict[str, tuple[Future[str], Message]] = {}
        self._last_latest: dict[str, str] = {}

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
                existing = self.source.recent(chat.username, limit=100)
                if existing:
                    self.store.ingest(chat.username, existing, suppressed=True)
                    self._last_latest[chat.username] = existing[-1].event_id
                else:
                    self.store.initialize_chat(chat.username, 0)
                continue
            latest = self.source.latest(chat.username)
            if latest is None or self._last_latest.get(chat.username) == latest.event_id:
                continue
            incoming = self.source.read_since(chat.username, checkpoint)
            self.store.ingest(chat.username, incoming, suppressed=paused)
            self._last_latest[chat.username] = latest.event_id

    @staticmethod
    def _row_message(row) -> Message:
        return Message(
            chat_id=row["chat_id"], sender_id=row["sender_id"],
            is_self=bool(row["is_self"]), kind=row["kind"],
            content=row["content"], sort_seq=row["sort_seq"],
            local_id=row["local_id"], created_at=row["created_at"],
        )

    def _finish_generations(self) -> None:
        assert self.source is not None
        for event_id, (future, incoming) in list(self._inflight.items()):
            if not future.done():
                continue
            del self._inflight[event_id]
            if self.store.is_paused() or self._stop.is_set():
                self.store.set_state(event_id, "generating", "ignored", detail="自动回复暂停")
                continue
            try:
                reply = future.result()
            except ReplyError as exc:
                self.store.set_state(event_id, "generating", "failed", detail=str(exc))
                continue
            except Exception as exc:
                self.store.set_state(event_id, "generating", "failed",
                                     detail=f"生成异常：{type(exc).__name__}")
                self._pause_with_reason(f"生成异常：{type(exc).__name__}")
                log.exception("回复生成异常；自动回复已暂停")
                return
            try:
                latest = self.source.latest(incoming.chat_id)
            except Exception as exc:
                self.store.set_state(event_id, "generating", "failed",
                                     detail=f"核对最新消息失败：{type(exc).__name__}")
                self._pause_with_reason(f"核对最新消息失败：{type(exc).__name__}")
                log.exception("模型完成后无法核对最新消息；自动回复已暂停")
                return
            if latest is None or latest.event_id != incoming.event_id:
                self.store.set_state(event_id, "generating", "superseded",
                                     detail="生成期间收到更新消息")
            else:
                self.store.set_state(event_id, "generating", "ready", reply=reply)

    def _schedule_generations(self, executor: ThreadPoolExecutor) -> None:
        assert self.source is not None
        rows = [row for row in self.store.pending() if row["state"] == "queued"]
        if not rows:
            return
        if not self.generator.available():
            self._set_status("待配置大模型 API 密钥或 model")
            return
        latest_by_chat = {}
        for row in rows:
            current = latest_by_chat.get(row["chat_id"])
            if current is None or (row["sort_seq"], row["local_id"]) > (
                current["sort_seq"], current["local_id"]
            ):
                latest_by_chat[row["chat_id"]] = row
        for row in rows:
            if row["event_id"] != latest_by_chat[row["chat_id"]]["event_id"]:
                self.store.set_state(row["event_id"], "queued", "superseded",
                                     detail="同一联系人有更新消息")
        busy_chats = {message.chat_id for _, message in self._inflight.values()}
        for row in sorted(latest_by_chat.values(), key=lambda item: (item["created_at"], item["sort_seq"])):
            if len(self._inflight) >= self.config.max_parallel_api_requests:
                break
            chat = self._chats.get(row["chat_id"])
            incoming = self._row_message(row)
            if not chat or not self.rules.eligible(chat, incoming):
                self.store.set_state(row["event_id"], "queued", "ignored")
                continue
            if chat.username in busy_chats:
                continue
            latest = self.source.latest(chat.username)
            if latest is None or latest.event_id != incoming.event_id:
                self.store.set_state(row["event_id"], "queued", "superseded",
                                     detail="目标会话已有更新消息")
                continue
            history = self.source.recent(chat.username, limit=20)
            if not self.store.set_state(row["event_id"], "queued", "generating"):
                continue
            try:
                future = executor.submit(self.generator.generate, history)
            except RuntimeError as exc:
                self.store.set_state(row["event_id"], "generating", "failed",
                                     detail=f"模型任务提交失败：{type(exc).__name__}")
                self._pause_with_reason("模型任务提交失败")
                return
            self._inflight[row["event_id"]] = future, incoming
            busy_chats.add(chat.username)

    def _send_ready(self) -> None:
        assert self.source is not None
        for row in self.store.pending():
            if row["state"] != "ready":
                continue
            if self.store.is_paused() or self._stop.is_set():
                return
            chat = self._chats.get(row["chat_id"])
            incoming = self._row_message(row)
            if not chat or not self.rules.eligible(chat, incoming):
                self.store.set_state(row["event_id"], "ready", "ignored")
                continue
            reply = row["reply"]
            if not reply:
                self.store.set_state(row["event_id"], "ready", "failed", detail="拟回复为空")
                continue
            latest = self.source.latest(chat.username)
            if latest is None or latest.event_id != incoming.event_id:
                self.store.set_state(row["event_id"], "ready", "superseded",
                                     detail="完成前收到更新消息")
                continue
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
            return  # 微信窗口只能由一个发送任务串行使用。

    def _process(self, executor: ThreadPoolExecutor) -> None:
        self._finish_generations()
        if self.store.is_paused() or self._stop.is_set():
            return
        self._schedule_generations(executor)
        self._send_ready()

    def run(self, data_dir: Path) -> None:
        self.store.recover()
        if self.store.is_paused():
            self.store.set_paused(True)
            self._set_status("已暂停，请在窗口或托盘恢复")
        if not self._chats:
            self._set_status("待配置启用的私聊")
            self._stop.wait()
            return
        last_prune = 0.0
        with ThreadPoolExecutor(max_workers=min(self.config.max_parallel_api_requests, len(self._chats)),
                                thread_name_prefix="wxreply-api") as executor:
            while not self._stop.is_set():
                if time.time() - last_prune >= 86400:
                    try:
                        deleted = self.store.prune(self.config.history_retention_days)
                        if deleted:
                            log.info("已清理 %s 条过期任务记录", deleted)
                    except Exception:
                        log.exception("清理过期任务记录失败")
                    last_prune = time.time()
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
                    self._process(executor)
                    if self.store.is_paused():
                        self._set_status(self._pause_reason or "已暂停；新消息只记录不回复")
                    elif not self.generator.available() and self.store.pending():
                        self._set_status("待配置大模型 API 密钥或 model")
                    elif self._inflight:
                        self._set_status(f"生成中：{len(self._inflight)} 个模型请求")
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
