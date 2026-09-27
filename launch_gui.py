from __future__ import annotations

import os
import traceback
from pathlib import Path

from wxreply.gui import main


if __name__ == "__main__":
    try:
        main()
    except Exception:
        directory = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "wxreply"
        directory.mkdir(parents=True, exist_ok=True)
        details = traceback.format_exc()
        (directory / "startup-error.log").write_text(details, encoding="utf-8")
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("微信自动回复启动失败", details[-1800:], parent=root)
            root.destroy()
        except Exception:
            pass
        raise
