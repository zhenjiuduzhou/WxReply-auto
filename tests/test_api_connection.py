from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from wxreply.ai import test_connection


class ApiConnectionTests(unittest.TestCase):
    @patch("wxreply.ai.httpx.Client")
    def test_valid_completion_uses_fixed_probe_only(self, client_class):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "连接成功"}}]}
        client = client_class.return_value.__enter__.return_value
        client.post.return_value = response

        result = test_connection("https://example.com/v1/", "model-a", "secret-a")

        self.assertTrue(result.ok)
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://example.com/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "model-a")
        self.assertEqual(kwargs["json"]["messages"], [
            {"role": "user", "content": "请只回复：连接成功"}
        ])
        self.assertNotIn("secret-a", result.message)

    @patch("wxreply.ai.httpx.Client")
    def test_auth_failure_reports_status_without_key(self, client_class):
        client_class.return_value.__enter__.return_value.post.return_value = Mock(status_code=401)
        result = test_connection("https://example.com/v1", "model-a", "secret-a")
        self.assertFalse(result.ok)
        self.assertIn("401", result.message)
        self.assertNotIn("secret-a", result.message)

    @patch("wxreply.ai.httpx.Client")
    def test_invalid_completion_is_not_reported_as_connected(self, client_class):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": []}
        client_class.return_value.__enter__.return_value.post.return_value = response
        self.assertFalse(test_connection("https://example.com/v1", "model-a", "secret-a").ok)
