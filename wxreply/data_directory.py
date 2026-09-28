from __future__ import annotations

import sys
from pathlib import Path


def project_directory() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        return executable_dir.parent if executable_dir.name.lower() == "dist" else executable_dir
    return Path(__file__).resolve().parent.parent


def app_directory() -> Path:
    return project_directory() / "data"
