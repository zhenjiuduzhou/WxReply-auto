from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from wxreply.data_directory import app_directory, project_directory


class DataDirectoryTests(unittest.TestCase):
    def test_source_run_uses_project_data_directory(self):
        self.assertEqual(app_directory(), Path(__file__).resolve().parent.parent / "data")

    def test_packaged_exe_uses_project_or_exe_directory(self):
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "executable", r"D:\codex\wx\dist\WxReply.exe"):
                self.assertEqual(project_directory(), Path(r"D:\codex\wx"))
            with patch.object(sys, "executable", r"D:\Apps\WxReply.exe"):
                self.assertEqual(project_directory(), Path(r"D:\Apps"))
