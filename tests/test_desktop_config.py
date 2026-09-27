from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wxreply.config import load_config, save_config
from wxreply.models import AppConfig, ChatConfig
from wxreply.secrets import ApiKeyStore


class DesktopConfigTests(unittest.TestCase):
    def test_config_round_trip_preserves_contacts_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = AppConfig("4.1.13.65", 2.0, False, "https://example.com/v1",
                               "example-model", "测试提示词",
                               (ChatConfig("wxid_test", "测试备注", "private", True,
                                           ("关键词一", "关键词二")),))
            save_config(path, config)
            self.assertEqual(load_config(path), config)

    def test_api_key_is_encrypted_and_round_trips_for_windows_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api-key.dpapi"
            vault = ApiKeyStore(path)
            vault.write("example-secret-123")
            self.assertNotIn(b"example-secret-123", path.read_bytes())
            self.assertEqual(vault.read(), "example-secret-123")
            vault.write("")
            self.assertFalse(path.exists())
