from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import Mock, patch

from wxreply.models import ChatConfig, Message
from wxreply.sender import GuardedSender, SendUncertain
from wxreply.source import DatabaseMessageSource
from wxreply.wechat_db import AccountAwareWeChatDB


class MessageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.db = AccountAwareWeChatDB.__new__(AccountAwareWeChatDB)
        self.db.account = "wxid_me_abcd"
        self.db._sender_id_index = Mock(side_effect=AssertionError("wrong identity table"))

    def shard(self, identities, sender_id):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE Name2Id(user_name TEXT);
            CREATE TABLE Msg_test(local_id, local_type, real_sender_id, create_time,
                message_content, source, packed_info_data, compress_content, server_id, sort_seq);
        """)
        conn.executemany("INSERT INTO Name2Id(rowid,user_name) VALUES (?,?)", identities)
        conn.execute("INSERT INTO Msg_test VALUES (1,1,?,11,'reply','','',NULL,1,11)",
                     (sender_id,))
        return conn, "Msg_test"

    def messages(self, shards):
        return [self.db._msg_row_to_dict(row) for row in self.db._shard_rows(shards, "")]

    def test_self_id_four_is_recognized_and_id_two_can_be_another_user(self):
        own = self.messages([self.shard([(4, "wxid_me")], 4)])[0]
        other = self.messages([self.shard([(2, "wxid_other")], 2)])[0]
        self.assertTrue(own["is_self"])
        self.assertEqual(own["sender_username"], "wxid_me")
        self.assertFalse(other["is_self"])
        self.assertFalse(DatabaseMessageSource._parse("wxid_other", other).is_self)
        self.assertTrue(DatabaseMessageSource._parse("wxid_other", own).is_self)

    def test_sender_numbers_are_resolved_in_each_message_shard(self):
        rows = self.messages([
            self.shard([(4, "wxid_me")], 4),
            self.shard([(4, "wxid_other")], 4),
        ])
        self.assertEqual([row["is_self"] for row in rows], [True, False])

    def test_unresolved_text_sender_stops_processing(self):
        with self.assertRaisesRegex(RuntimeError, "发送者"):
            self.messages([self.shard([], 4)])


class SendConfirmationTests(unittest.TestCase):
    @patch("wxreply.sender.time.sleep")
    @patch("wxreply.sender.assert_environment")
    @patch("wxreply.sender.WeChatUIA")
    def test_confirmation_uses_account_identity_and_never_repeats_enter(self, ui_class, env, sleep):
        chat = ChatConfig("wxid_other", "contact", "private", True)
        incoming = Message(chat.username, chat.username, False, "文本", "hi", 10, 1, 10)
        ui = ui_class.return_value
        ui.ensure_window.return_value = ui.open_chat.return_value = True
        ui.current_chat.return_value = chat.display_name
        ui._chat_input.return_value.GetValuePattern.return_value.Value = "reply"
        for sender_id, is_self in [(4, True), (2, False)]:
            with self.subTest(sender_id=sender_id):
                source = Mock()
                source.latest.return_value = incoming
                source.db.get_messages.return_value = [
                    {"sort_seq": 11, "sender_id": sender_id, "is_self": is_self, "content": "reply"}
                ]
                ui._chat_input.return_value.SendKeys.reset_mock()
                if is_self:
                    GuardedSender(source, "test").send(chat, incoming, "reply")
                else:
                    with self.assertRaises(SendUncertain):
                        GuardedSender(source, "test").send(chat, incoming, "reply")
                ui._chat_input.return_value.SendKeys.assert_called_once()
