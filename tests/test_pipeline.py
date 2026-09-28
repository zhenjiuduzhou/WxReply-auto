from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from wxreply.engine import Engine
from wxreply.models import AppConfig, ChatConfig, Message
from wxreply.rules import RuleEvaluator
from wxreply.sender import GuardedSender, SendCancelled
from wxreply.source import DatabaseMessageSource
from wxreply.store import Store


CHAT = ChatConfig("wxid_test", "测试联系人", "private", True)


def msg(seq: int, local_id: int, content: str, self_sent: bool = False) -> Message:
    return Message(CHAT.username, "self" if self_sent else CHAT.username,
                   self_sent, "文本", content, seq, local_id, seq)


class RuleTests(unittest.TestCase):
    def test_enabled_contact_needs_no_keyword_but_only_incoming_text_qualifies(self):
        rules = RuleEvaluator()
        self.assertTrue(rules.eligible(CHAT, msg(1, 1, "你好")))
        self.assertFalse(rules.eligible(CHAT, msg(2, 2, "你好", self_sent=True)))
        self.assertFalse(rules.eligible(CHAT, msg(3, 3, "  ")))
        self.assertFalse(rules.eligible(CHAT, Message(CHAT.username, CHAT.username,
                                                     False, "图片", "图片", 4, 4, 4)))
        self.assertFalse(rules.eligible(ChatConfig("wxid_test", "测试联系人", "private", False),
                                        msg(5, 5, "你好")))


class StoreTests(unittest.TestCase):
    def test_identical_text_messages_remain_distinct_and_restart_dedupes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            store = Store(path)
            a, b = msg(10, 1, "触发"), msg(11, 2, "触发")
            self.assertEqual(store.ingest(CHAT.username, [a, b]), 2)
            self.assertEqual(Store(path).ingest(CHAT.username, [a, b]), 0)
            self.assertEqual(len(store.pending()), 2)
            self.assertEqual(store.checkpoint(CHAT.username), 11)

    def test_interrupted_send_becomes_unknown_and_never_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            store = Store(path)
            event = msg(10, 1, "触发")
            store.ingest(CHAT.username, [event])
            store.set_state(event.event_id, "queued", "generating")
            store.set_state(event.event_id, "generating", "ready", reply="回复")
            store.set_state(event.event_id, "ready", "sending")
            Store(path).recover()
            self.assertEqual(len(store.pending()), 0)
            self.assertEqual(store.recent()[0]["state"], "unknown")
            self.assertTrue(store.is_paused())

    def test_pause_discards_unsent_work(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            store.ingest(CHAT.username, [msg(10, 1, "触发")])
            store.set_paused(True)
            self.assertEqual(store.pending(), [])

    def test_prune_keeps_checkpoints_and_unresolved_work(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            old_done, old_unknown, old_queued = (
                msg(10, 1, "已完成"), msg(11, 2, "待核实"), msg(12, 3, "待处理")
            )
            store.ingest(CHAT.username, [old_done, old_unknown, old_queued])
            store.set_state(old_done.event_id, "queued", "drafted", reply="回复")
            store.set_state(old_unknown.event_id, "queued", "generating")
            store.set_state(old_unknown.event_id, "generating", "ready", reply="回复")
            store.set_state(old_unknown.event_id, "ready", "sending")
            store.set_state(old_unknown.event_id, "sending", "unknown")
            with store._connect() as db:
                db.execute("UPDATE events SET updated_at=1000")
            self.assertEqual(store.prune(90, now=1000 + 91 * 86400), 1)
            self.assertEqual(store.checkpoint(CHAT.username), 12)
            with store._connect() as db:
                states = {row[0] for row in db.execute("SELECT state FROM events")}
            self.assertEqual(states, {"unknown", "queued"})

    def test_prune_preserves_checkpoint_boundary_for_restart_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            boundary = msg(10, 1, "边界消息")
            store.ingest(CHAT.username, [boundary])
            store.set_state(boundary.event_id, "queued", "drafted", reply="回复")
            with store._connect() as db:
                db.execute("UPDATE events SET updated_at=1000")
            self.assertEqual(store.prune(90, now=1000 + 91 * 86400), 0)
            self.assertEqual(Store(store.path).ingest(CHAT.username, [boundary]), 0)


class SourceTests(unittest.TestCase):
    def test_read_since_pages_without_losing_equal_text_messages(self):
        source = DatabaseMessageSource.__new__(DatabaseMessageSource)
        rows = [
            {"sort_seq": seq, "local_id": seq, "sender_id": 3,
             "sender_username": CHAT.username, "type": "文本",
             "content": "触发", "create_time": seq}
            for seq in range(250, 0, -1)
        ]
        source.db = unittest.mock.Mock()
        source.db.get_messages.side_effect = lambda user, limit, offset: rows[offset:offset + limit]
        messages = source.read_since(CHAT.username, 0)
        self.assertEqual(len(messages), 250)
        self.assertTrue(all(call.kwargs["limit"] <= 100
                            for call in source.db.get_messages.call_args_list))
        self.assertEqual((messages[0].sort_seq, messages[-1].sort_seq), (1, 250))
        self.assertEqual(len({message.event_id for message in messages}), 250)


class _FakeSource:
    def __init__(self, latest: Message):
        self.current = latest

    def validate_chat(self, chat):
        pass

    def latest(self, chat_id):
        return self.current


class SenderTests(unittest.TestCase):
    @patch("wxreply.sender.assert_environment")
    @patch("wxreply.sender.WeChatUIA")
    def test_newer_message_cancels_before_keyboard_send(self, ui_class, environment):
        source = _FakeSource(msg(11, 2, "后来的消息"))
        ui = ui_class.return_value
        ui.ensure_window.return_value = True
        ui.open_chat.return_value = True
        ui.current_chat.return_value = CHAT.display_name
        with self.assertRaises(SendCancelled):
            GuardedSender(source, "4.1.13.65").send(CHAT, msg(10, 1, "触发"), "回复")
        ui._chat_input.assert_not_called()

    @patch("wxreply.sender.assert_environment")
    @patch("wxreply.sender.WeChatUIA")
    def test_verified_send_presses_enter_once(self, ui_class, environment):
        source = _FakeSource(msg(10, 1, "触发"))
        source.db = unittest.mock.Mock()
        source.db.get_messages.return_value = [
            {"sort_seq": 11, "sender_id": 2, "content": "回复"}
        ]
        ui = ui_class.return_value
        ui.ensure_window.return_value = True
        ui.open_chat.return_value = True
        ui.current_chat.return_value = CHAT.display_name
        ui._chat_input.return_value.GetValuePattern.return_value.Value = "回复"
        GuardedSender(source, "4.1.13.65").send(CHAT, msg(10, 1, "触发"), "回复")
        ui._chat_input.return_value.SendKeys.assert_called_once()

    @patch("wxreply.sender.assert_environment")
    @patch("wxreply.sender.WeChatUIA")
    def test_pause_before_enter_cancels_send(self, ui_class, environment):
        source = _FakeSource(msg(10, 1, "触发"))
        ui = ui_class.return_value
        ui.ensure_window.return_value = True
        ui.open_chat.return_value = True
        ui.current_chat.return_value = CHAT.display_name
        ui._chat_input.return_value.GetValuePattern.return_value.Value = "回复"
        with self.assertRaises(SendCancelled):
            GuardedSender(source, "4.1.13.65", should_cancel=lambda: True).send(
                CHAT, msg(10, 1, "触发"), "回复")
        ui._chat_input.return_value.SendKeys.assert_not_called()


class EngineTests(unittest.TestCase):
    def test_first_poll_ignores_existing_messages_then_queues_new(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig("4.1.13.65", 1, False, "https://example.com/v1",
                               "test-model", "system", (CHAT,))
            engine = Engine(config, Path(directory))
            engine.source = unittest.mock.Mock()
            engine.source.recent.return_value = [msg(10, 1, "触发")]
            engine.source.latest.return_value = msg(11, 2, "触发")
            engine.source.read_since.return_value = [msg(10, 1, "触发"), msg(11, 2, "触发")]
            engine._poll()
            self.assertEqual(engine.store.checkpoint(CHAT.username), 10)
            engine.source.recent.assert_called_with(CHAT.username, limit=100)
            self.assertEqual(engine.store.pending(), [])
            engine._poll()
            self.assertEqual(len(engine.store.pending()), 1)
            self.assertEqual(engine.store.checkpoint(CHAT.username), 11)
            engine._poll()
            engine.source.read_since.assert_called_once()

    def test_dry_run_creates_draft_without_sender(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig("4.1.13.65", 1, False, "https://example.com/v1",
                               "test-model", "system", (CHAT,))
            engine = Engine(config, Path(directory))
            event = msg(10, 1, "你好")
            engine.store.ingest(CHAT.username, [event])
            engine.source = unittest.mock.Mock()
            engine.source.recent.return_value = [event]
            engine.source.latest.return_value = event
            engine.generator = unittest.mock.Mock()
            engine.generator.available.return_value = True
            engine.generator.generate.return_value = "测试回复"
            with patch("wxreply.engine.GuardedSender") as sender:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    engine._process(executor)
                    for _ in range(100):
                        engine._process(executor)
                        if engine.store.recent()[0]["state"] == "drafted":
                            break
                        time.sleep(0.01)
            sender.assert_not_called()
            row = engine.store.recent()[0]
            self.assertEqual((row["state"], row["reply"]), ("drafted", "测试回复"))

    def test_different_contacts_generate_in_parallel(self):
        with tempfile.TemporaryDirectory() as directory:
            other = ChatConfig("wxid_other", "另一联系人", "private", True)
            first = msg(10, 1, "你好")
            second = Message(other.username, other.username, False, "文本", "在吗", 11, 2, 11)
            config = AppConfig("4.1.13.65", 1, False, "https://example.com/v1",
                               "test-model", "system", (CHAT, other),
                               max_parallel_api_requests=2)
            engine = Engine(config, Path(directory))
            engine.store.ingest(CHAT.username, [first])
            engine.store.ingest(other.username, [second])
            messages = {CHAT.username: first, other.username: second}
            engine.source = unittest.mock.Mock()
            engine.source.latest.side_effect = lambda chat_id: messages[chat_id]
            engine.source.recent.side_effect = lambda chat_id, limit: [messages[chat_id]]
            barrier = threading.Barrier(2)
            engine.generator = unittest.mock.Mock()
            engine.generator.available.return_value = True
            engine.generator.generate.side_effect = lambda history: (barrier.wait(timeout=2), "回复")[1]
            with ThreadPoolExecutor(max_workers=2) as executor:
                engine._process(executor)
                for _ in range(200):
                    engine._process(executor)
                    if all(row["state"] == "drafted" for row in engine.store.recent(limit=2)):
                        break
                    time.sleep(0.01)
            self.assertEqual(engine.generator.generate.call_count, 2)
            self.assertEqual({row["state"] for row in engine.store.recent(limit=2)}, {"drafted"})


if __name__ == "__main__":
    unittest.main()
