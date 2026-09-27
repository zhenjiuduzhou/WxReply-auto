from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk

import pystray
from wechatauto.param import WxParam

from .config import load_config, save_config
from .engine import Engine
from .models import AppConfig, ChatConfig
from .secrets import ApiKeyStore
from .source import DatabaseMessageSource
from .store import Store
from .tray import _icon_image


def app_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "wxreply"


def initial_config(path: Path) -> AppConfig:
    if path.exists():
        return load_config(path)
    old = Path.cwd() / "config.json"
    source = old if old.exists() else Path(__file__).with_name("default_config.json")
    config = load_config(source)
    save_config(path, config)
    return config


class ContactDialog(tk.Toplevel):
    def __init__(self, parent: "ReplyApp", existing: ChatConfig | None = None):
        super().__init__(parent)
        self.parent = parent
        self.result: ChatConfig | None = None
        self.title("编辑联系人" if existing else "添加联系人")
        self.geometry("580x380")
        self.transient(parent)
        self.grab_set()
        self.columnconfigure(1, weight=1)
        self.username = tk.StringVar(value=existing.username if existing else "")
        self.name = tk.StringVar(value=existing.display_name if existing else "")
        self.keywords = tk.StringVar(value="，".join(existing.keywords) if existing else "")
        self.enabled = tk.BooleanVar(value=existing.enabled if existing else True)
        self.search_term = tk.StringVar()
        ttk.Label(self, text="搜索微信联系人").grid(row=0, column=0, padx=16, pady=(16, 6), sticky="w")
        ttk.Entry(self, textvariable=self.search_term).grid(row=0, column=1, padx=8, pady=(16, 6), sticky="ew")
        ttk.Button(self, text="搜索", command=self._search).grid(row=0, column=2, padx=16, pady=(16, 6))
        self.matches = ttk.Treeview(self, columns=("id", "name"), show="headings", height=5)
        self.matches.heading("id", text="微信内部 ID")
        self.matches.heading("name", text="备注 / 昵称")
        self.matches.column("id", width=230)
        self.matches.column("name", width=250)
        self.matches.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=16)
        self.matches.bind("<<TreeviewSelect>>", self._choose_match)
        self.rowconfigure(1, weight=1)
        for row, label, variable in (
            (2, "微信内部 ID", self.username),
            (3, "准确备注 / 昵称", self.name),
            (4, "触发关键词（逗号分隔）", self.keywords),
        ):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=16, pady=8, sticky="w")
            ttk.Entry(self, textvariable=variable).grid(row=row, column=1, columnspan=2,
                                                          padx=(8, 16), pady=8, sticky="ew")
        ttk.Checkbutton(self, text="启用这个联系人", variable=self.enabled).grid(
            row=5, column=0, columnspan=2, padx=16, sticky="w"
        )
        buttons = ttk.Frame(self)
        buttons.grid(row=6, column=0, columnspan=3, pady=12)
        ttk.Button(buttons, text="保存", command=self._save).pack(side="left", padx=8)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=8)

    def _search(self) -> None:
        term = self.search_term.get().strip()
        if not term:
            messagebox.showinfo("搜索联系人", "请输入备注或昵称的一部分。", parent=self)
            return
        self.parent.async_contact_search(term, self)

    def show_matches(self, contacts: list[dict]) -> None:
        if not self.winfo_exists():
            return
        self.matches.delete(*self.matches.get_children())
        for contact in contacts[:50]:
            name = contact.get("remark") or contact.get("nick_name") or ""
            self.matches.insert("", "end", values=(contact.get("username", ""), name))
        if not contacts:
            messagebox.showinfo("搜索联系人", "没有找到匹配的联系人。", parent=self)

    def _choose_match(self, event=None) -> None:
        selected = self.matches.selection()
        if selected:
            username, name = self.matches.item(selected[0], "values")
            self.username.set(username)
            self.name.set(name)

    def _save(self) -> None:
        username = self.username.get().strip()
        name = self.name.get().strip()
        keywords = tuple(dict.fromkeys(
            word.strip() for word in self.keywords.get().replace("，", ",").split(",")
            if word.strip()
        ))
        if not username or not name:
            messagebox.showerror("联系人", "请填写微信内部 ID 和准确备注。", parent=self)
            return
        if username.endswith("@chatroom"):
            messagebox.showerror("联系人", "当前版本只支持私聊。", parent=self)
            return
        if self.enabled.get() and not keywords:
            messagebox.showerror("联系人", "启用联系人时，至少填写一个触发关键词。", parent=self)
            return
        self.result = ChatConfig(username, name, "private", self.enabled.get(), keywords)
        self.destroy()


class ReplyApp(tk.Tk):
    def __init__(self):
        super().__init__()
        WxParam.ENABLE_FILE_LOGGER = False
        self.data_dir = app_directory()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=self.data_dir / "app.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
        self.config_path = self.data_dir / "config.json"
        self.secret = ApiKeyStore(self.data_dir / "api-key.dpapi")
        self.config = initial_config(self.config_path)
        self.store = Store(self.data_dir / "state.db")
        self.chats = list(self.config.chats)
        self.engine: Engine | None = None
        self.worker: threading.Thread | None = None
        self.results: queue.Queue = queue.Queue()
        self.closing = False
        self.tray: pystray.Icon | None = None
        self.title("微信自动回复")
        self.geometry("1040x720")
        self.minsize(820, 580)
        self.configure(bg="#f4f7f5")
        self.protocol("WM_DELETE_WINDOW", self._hide)
        self._build()
        self._load_fields()
        self._refresh_contacts()
        self._start_tray()
        self.after(500, self._refresh)

    def _build(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 11))
        container = ttk.Frame(self, padding=18)
        container.pack(fill="both", expand=True)
        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="微信自动回复", style="Title.TLabel").pack(side="left")
        self.status_text = tk.StringVar(value="未启动")
        ttk.Label(header, textvariable=self.status_text, style="Status.TLabel").pack(side="right")
        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(controls, text="启动监听", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(controls, text="暂停", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(controls, text="保存配置", command=self.save)
        self.save_button.pack(side="right")
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        dashboard = ttk.Frame(notebook, padding=12)
        contacts = ttk.Frame(notebook, padding=12)
        settings = ttk.Frame(notebook, padding=12)
        notebook.add(dashboard, text="运行与任务")
        notebook.add(contacts, text="监听联系人")
        notebook.add(settings, text="模型与发送")
        self._build_dashboard(dashboard)
        self._build_contacts(contacts)
        self._build_settings(settings)

    def _build_dashboard(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="只处理启用的私聊、文字消息和包含关键词的新消息。首次监听会跳过旧消息。")\
            .pack(anchor="w", pady=(0, 10))
        columns = ("time", "chat", "state", "received")
        self.tasks = ttk.Treeview(frame, columns=columns, show="headings", height=13)
        for key, label, width in (("time", "时间", 145), ("chat", "会话 ID", 180),
                                  ("state", "状态", 110), ("received", "收到的消息", 440)):
            self.tasks.heading(key, text=label)
            self.tasks.column(key, width=width)
        self.tasks.pack(fill="both", expand=True)
        self.tasks.bind("<<TreeviewSelect>>", self._show_task)
        ttk.Label(frame, text="任务详情 / 拟回复").pack(anchor="w", pady=(12, 5))
        self.task_detail = tk.Text(frame, height=8, wrap="word", state="disabled",
                                   font=("Microsoft YaHei UI", 10))
        self.task_detail.pack(fill="x")
        self.task_rows = []

    def _build_contacts(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="添加联系人时可搜索微信备注。一个联系人可以设置多个关键词，用逗号分隔。")\
            .pack(anchor="w", pady=(0, 10))
        self.contacts = ttk.Treeview(frame, columns=("id", "name", "keywords", "enabled"),
                                     show="headings")
        for key, label, width in (("id", "微信内部 ID", 240), ("name", "备注 / 昵称", 180),
                                  ("keywords", "触发关键词", 300), ("enabled", "启用", 70)):
            self.contacts.heading(key, text=label)
            self.contacts.column(key, width=width)
        self.contacts.pack(fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=10)
        self.add_button = ttk.Button(buttons, text="添加联系人", command=self.add_contact)
        self.add_button.pack(side="left", padx=(0, 8))
        self.edit_button = ttk.Button(buttons, text="编辑", command=self.edit_contact)
        self.edit_button.pack(side="left", padx=8)
        self.delete_button = ttk.Button(buttons, text="删除", command=self.delete_contact)
        self.delete_button.pack(side="left", padx=8)

    def _build_settings(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)
        self.api_url = tk.StringVar()
        self.model = tk.StringVar()
        self.key = tk.StringVar()
        self.version = tk.StringVar()
        self.interval = tk.StringVar()
        self.auto_send = tk.BooleanVar()
        fields = (("API 地址", self.api_url), ("模型名称", self.model),
                  ("API 密钥（仅保存在本机）", self.key), ("微信版本", self.version),
                  ("轮询间隔（秒）", self.interval))
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, padx=8, pady=12, sticky="w")
            entry = ttk.Entry(frame, textvariable=variable, show="•" if variable is self.key else "")
            entry.grid(row=row, column=1, padx=8, pady=12, sticky="ew")
        ttk.Checkbutton(frame, text="允许无人值守自动发送（先用只生成模式验证）",
                        variable=self.auto_send).grid(row=5, column=0, columnspan=2,
                                                     padx=8, pady=12, sticky="w")
        ttk.Label(frame, text="系统提示词").grid(row=6, column=0, padx=8, pady=12, sticky="nw")
        self.prompt = tk.Text(frame, height=7, wrap="word", font=("Microsoft YaHei UI", 10))
        self.prompt.grid(row=6, column=1, padx=8, pady=12, sticky="nsew")
        frame.rowconfigure(6, weight=1)
        ttk.Label(frame, text="API 密钥使用 Windows DPAPI 加密保存；配置改动须停止监听后保存，再重新启动。")\
            .grid(row=7, column=0, columnspan=2, padx=8, pady=8, sticky="w")

    def _load_fields(self) -> None:
        self.api_url.set(self.config.api_base_url)
        self.model.set(self.config.model)
        self.version.set(self.config.expected_wechat_version)
        self.interval.set(str(self.config.poll_interval_seconds))
        self.auto_send.set(self.config.auto_send)
        self.prompt.insert("1.0", self.config.system_prompt)
        try:
            self.key.set(self.secret.read())
        except Exception as exc:
            messagebox.showerror("API 密钥", f"无法解密本机 API 密钥：{exc}", parent=self)

    def _refresh_contacts(self) -> None:
        self.contacts.delete(*self.contacts.get_children())
        for index, chat in enumerate(self.chats):
            self.contacts.insert("", "end", iid=str(index), values=(
                chat.username, chat.display_name, "、".join(chat.keywords),
                "是" if chat.enabled else "否",
            ))

    def _selected_contact(self) -> int | None:
        selected = self.contacts.selection()
        return int(selected[0]) if selected else None

    def add_contact(self) -> None:
        dialog = ContactDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            if any(chat.username == dialog.result.username for chat in self.chats):
                messagebox.showerror("联系人", "这个微信内部 ID 已在列表中。", parent=self)
                return
            self.chats.append(dialog.result)
            self._refresh_contacts()

    def edit_contact(self) -> None:
        index = self._selected_contact()
        if index is None:
            return
        dialog = ContactDialog(self, self.chats[index])
        self.wait_window(dialog)
        if dialog.result:
            if any(i != index and chat.username == dialog.result.username
                   for i, chat in enumerate(self.chats)):
                messagebox.showerror("联系人", "这个微信内部 ID 已在列表中。", parent=self)
                return
            self.chats[index] = dialog.result
            self._refresh_contacts()

    def delete_contact(self) -> None:
        index = self._selected_contact()
        if index is not None:
            del self.chats[index]
            self._refresh_contacts()

    def async_contact_search(self, term: str, dialog: ContactDialog) -> None:
        def task() -> None:
            try:
                source = DatabaseMessageSource(self.data_dir)
                contacts = source.db.search_contact(term)
                self.results.put((dialog, contacts, None))
            except Exception as exc:
                self.results.put((dialog, None, str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def save(self) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("保存配置", "请先停止监听，再保存并重新启动。", parent=self)
            return False
        try:
            interval = float(self.interval.get())
            if interval < 0.5:
                raise ValueError("轮询间隔不得小于 0.5 秒")
            candidate = replace(self.config,
                expected_wechat_version=self.version.get().strip(),
                poll_interval_seconds=interval,
                auto_send=self.auto_send.get(),
                api_base_url=self.api_url.get().strip().rstrip("/"),
                model=self.model.get().strip(),
                system_prompt=self.prompt.get("1.0", "end").strip(),
                chats=tuple(self.chats))
            if not candidate.expected_wechat_version or not candidate.api_base_url:
                raise ValueError("请填写微信版本和 API 地址")
            if any(chat.enabled and chat.chat_type != "private" for chat in candidate.chats):
                raise ValueError("当前版本仅支持私聊")
            if candidate.auto_send and not any(chat.enabled and chat.keywords for chat in candidate.chats):
                raise ValueError("自动发送前请启用至少一个设置了关键词的私聊")
            self.secret.write(self.key.get().strip())
            save_config(self.config_path, candidate)
            self.config = load_config(self.config_path)
            messagebox.showinfo("保存配置", "配置已保存。", parent=self)
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.save():
            return
        if not any(chat.enabled for chat in self.config.chats):
            messagebox.showinfo("启动监听", "请先启用至少一个私聊联系人。", parent=self)
            return
        key = self.key.get().strip()
        if not self.config.model or not key:
            messagebox.showerror("启动监听", "请在“模型与发送”页填写模型名称和 API 密钥。", parent=self)
            return
        os.environ["WXREPLY_API_KEY"] = key
        self.engine = Engine(self.config, self.data_dir)
        self.store = self.engine.store
        self.worker = threading.Thread(target=self.engine.run, args=(self.data_dir,), daemon=True)
        self.worker.start()
        self._set_running_controls(True)

    def _set_running_controls(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.pause_button.configure(state="normal" if running else "disabled")
        for button in (self.add_button, self.edit_button, self.delete_button):
            button.configure(state="disabled" if running else "normal")

    def toggle_pause(self) -> None:
        if self.engine and self.worker and self.worker.is_alive():
            self.engine.set_paused(not self.engine.store.is_paused())

    def stop(self) -> None:
        if self.engine and self.worker and self.worker.is_alive():
            self.engine.set_paused(True)
            self.engine.stop()
            self.status_text.set("正在停止；等待当前任务结束…")

    def _show_task(self, event=None) -> None:
        selected = self.tasks.selection()
        if not selected:
            return
        row = self.task_rows[int(selected[0])]
        detail = (f"状态：{row['state']}\n会话：{row['chat_id']}\n\n"
                  f"收到：\n{row['content']}\n\n"
                  f"拟回复：\n{row['reply'] or '无'}\n\n"
                  f"说明：{row['detail'] or '无'}")
        self.task_detail.configure(state="normal")
        self.task_detail.delete("1.0", "end")
        self.task_detail.insert("1.0", detail)
        self.task_detail.configure(state="disabled")

    def _refresh(self) -> None:
        while True:
            try:
                dialog, contacts, error = self.results.get_nowait()
            except queue.Empty:
                break
            if dialog.winfo_exists():
                if error:
                    messagebox.showerror("搜索失败", error, parent=dialog)
                else:
                    dialog.show_matches(contacts)
        if self.worker and not self.worker.is_alive() and self.engine:
            self._set_running_controls(False)
            self.worker = None
            self.engine = None
        if self.engine:
            pause = self.engine.store.is_paused()
            self.status_text.set(self.engine.status)
            self.pause_button.configure(text="恢复" if pause else "暂停")
        elif not self.closing:
            self.status_text.set("未启动")
        rows = self.store.recent(limit=100)
        signature = tuple((row["chat_id"], row["state"], row["updated_at"])
                          for row in rows)
        if getattr(self, "_task_signature", None) != signature:
            self._task_signature = signature
            self.task_rows = rows
            self.tasks.delete(*self.tasks.get_children())
            from datetime import datetime
            for index, row in enumerate(rows):
                when = datetime.fromtimestamp(row["updated_at"]).strftime("%m-%d %H:%M:%S")
                self.tasks.insert("", "end", iid=str(index), values=(
                    when, row["chat_id"], row["state"], row["content"][:90]))
        if self.closing:
            if not self.worker or not self.worker.is_alive():
                if self.tray:
                    self.tray.stop()
                self.destroy()
                return
        self.after(1000, self._refresh)

    def _start_tray(self) -> None:
        commands: queue.Queue[str] = queue.Queue()
        self.tray_commands = commands
        def show(icon, item):
            commands.put("show")
        def pause(icon, item):
            commands.put("pause")
        def quit_app(icon, item):
            commands.put("quit")
        self.tray = pystray.Icon("wxreply", _icon_image(), "微信自动回复",
            pystray.Menu(
                pystray.MenuItem("显示主窗口", show, default=True),
                pystray.MenuItem("暂停 / 恢复", pause),
                pystray.MenuItem("退出", quit_app),
            ))
        threading.Thread(target=self.tray.run, daemon=True).start()
        self.after(200, self._drain_tray)

    def _drain_tray(self) -> None:
        while True:
            try:
                command = self.tray_commands.get_nowait()
            except queue.Empty:
                break
            if command == "show":
                self.deiconify()
                self.lift()
                self.focus_force()
            elif command == "pause":
                self.toggle_pause()
            elif command == "quit":
                self._quit()
        if not self.closing:
            self.after(200, self._drain_tray)

    def _hide(self) -> None:
        self.withdraw()

    def _quit(self) -> None:
        self.closing = True
        self.deiconify()
        self.stop()


def main() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = ReplyApp()
    app.mainloop()
