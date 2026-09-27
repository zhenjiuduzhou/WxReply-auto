from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw

from .engine import Engine


def _icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 10, 58, 48), radius=12, fill="#21ad69")
    draw.polygon([(16, 44), (18, 58), (32, 46)], fill="#21ad69")
    draw.ellipse((18, 26, 23, 31), fill="white")
    draw.ellipse((30, 26, 35, 31), fill="white")
    draw.ellipse((42, 26, 47, 31), fill="white")
    return image


def _dialog(title: str, content: str) -> None:
    window = tk.Tk()
    window.withdraw()
    try:
        messagebox.showinfo(title, content, parent=window)
    finally:
        window.destroy()


def run_tray(engine: Engine, data_dir) -> None:
    worker = threading.Thread(target=engine.run, args=(data_dir,), daemon=True)
    worker.start()

    def show_status(icon, item):
        mode = "已暂停" if engine.store.is_paused() else "运行许可"
        _dialog("微信自动回复", f"{engine.status}\n模式：{mode}\n配置自动发送：{engine.config.auto_send}")

    def toggle_pause(icon, item):
        engine.set_paused(not engine.store.is_paused())
        icon.update_menu()

    def show_recent(icon, item):
        rows = engine.store.recent()
        lines = []
        for row in rows:
            lines.append(f"{row['chat_id']} · {row['state']}\n收到：{row['content'][:80]}")
            if row["reply"]:
                lines.append(f"拟回复：{row['reply'][:160]}")
            if row["detail"]:
                lines.append(f"说明：{row['detail'][:120]}")
            lines.append("")
        _dialog("最近消息", "\n".join(lines).strip() if lines else "暂无记录")

    def exit_app(icon, item):
        engine.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("查看状态", show_status, default=True),
        pystray.MenuItem(
            lambda item: "恢复自动回复" if engine.store.is_paused() else "暂停自动回复",
            toggle_pause,
        ),
        pystray.MenuItem("最近消息", show_recent),
        pystray.MenuItem("退出", exit_app),
    )
    icon = pystray.Icon("wxreply", _icon_image(), "微信自动回复", menu)
    icon.run()
    engine.stop()
    worker.join(timeout=5)
