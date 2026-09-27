from __future__ import annotations

import os
from pathlib import Path

import win32crypt


class ApiKeyStore:
    """Protect the API key with the current Windows user's DPAPI credentials."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 0)[1].decode("utf-8")

    def write(self, key: str) -> None:
        if not key:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = win32crypt.CryptProtectData(key.encode("utf-8"), "wxreply API key", None, None, None, 0)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)
