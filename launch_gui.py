from __future__ import annotations

import traceback

from wxreply.data_directory import app_directory, project_directory
from wxreply.gui import main


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            directory = app_directory()
        except Exception:
            directory = project_directory() / "data"
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
