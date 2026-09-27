from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from .models import Message


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    chat_id TEXT PRIMARY KEY, sort_seq INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    is_self INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sort_seq INTEGER NOT NULL,
                    local_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reply TEXT,
                    detail TEXT,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_pending ON events(state, chat_id, sort_seq);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def checkpoint(self, chat_id: str) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT sort_seq FROM checkpoints WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row[0]) if row else None

    def initialize_chat(self, chat_id: str, sort_seq: int) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO checkpoints VALUES (?, ?)", (chat_id, sort_seq))

    def ingest(self, chat_id: str, messages: list[Message], suppressed: bool = False) -> int:
        if not messages:
            return 0
        now = int(time.time())
        inserted = 0
        with self._connect() as db:
            for msg in messages:
                state = "ignored" if suppressed or msg.is_self or msg.kind != "文本" else "queued"
                cursor = db.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, chat_id, sender_id, is_self, kind, content,
                        sort_seq, local_id, created_at, state, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (msg.event_id, msg.chat_id, msg.sender_id, msg.is_self,
                     msg.kind, msg.content, msg.sort_seq, msg.local_id,
                     msg.created_at, state, now),
                )
                inserted += cursor.rowcount
            db.execute(
                """INSERT INTO checkpoints(chat_id, sort_seq) VALUES (?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET sort_seq=max(sort_seq, excluded.sort_seq)""",
                (chat_id, max(msg.sort_seq for msg in messages)),
            )
        return inserted

    def recover(self) -> None:
        with self._connect() as db:
            interrupted = db.execute(
                "UPDATE events SET state='unknown', detail='程序在发送期间中断，需人工核实' WHERE state='sending'"
            ).rowcount
            db.execute("UPDATE events SET state='queued' WHERE state='generating'")
            if interrupted:
                db.execute(
                    "INSERT INTO settings(key,value) VALUES ('paused','1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                db.execute(
                    "UPDATE events SET state='ignored', detail='发送中断，等待人工核实' "
                    "WHERE state IN ('queued','ready')"
                )

    def pending(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM events WHERE state IN ('queued', 'ready') ORDER BY created_at, sort_seq"
            ).fetchall()

    def set_state(self, event_id: str, expected: str, state: str,
                  reply: str | None = None, detail: str | None = None) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE events SET state=?, reply=COALESCE(?, reply), detail=?, updated_at=?
                   WHERE event_id=? AND state=?""",
                (state, reply, detail, int(time.time()), event_id, expected),
            )
            return cursor.rowcount == 1

    def is_paused(self) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(row and row[0] == "1")

    def set_paused(self, paused: bool) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES ('paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if paused else "0",),
            )
            if paused:
                db.execute(
                    "UPDATE events SET state='ignored', detail='自动回复暂停' WHERE state IN ('queued','ready')"
                )

    def recent(self, limit: int = 12) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT chat_id, state, content, reply, detail, updated_at FROM events ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
