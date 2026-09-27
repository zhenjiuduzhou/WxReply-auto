from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from wechatauto.param import WxParam

from .config import load_config
from .engine import Engine
from .models import ChatConfig
from .sender import GuardedSender
from .source import DatabaseMessageSource, assert_environment
from .tray import run_tray


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "wxreply"


def main(argv: list[str] | None = None) -> int:
    WxParam.ENABLE_FILE_LOGGER = False
    parser = argparse.ArgumentParser(description="Windows 微信自动回复")
    parser.add_argument("command", choices=[
        "run", "probe-db", "probe-send", "list-chats", "find-contact", "init"
    ])
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--data-dir", type=Path, default=_data_dir())
    parser.add_argument("--name", help="find-contact 使用的昵称或备注关键字")
    args = parser.parse_args(argv)

    if args.command == "init":
        if args.config.exists():
            parser.error(f"配置文件已存在：{args.config}")
        example = Path(__file__).with_name("default_config.json")
        args.config.write_bytes(example.read_bytes())
        print(f"已创建 {args.config}；请编辑联系人与模型设置。")
        return 0

    if not args.config.exists():
        parser.error(f"缺少 {args.config}；先运行 wxreply init")
    config = load_config(args.config)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=args.data_dir / "app.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )

    if args.command == "run":
        run_tray(Engine(config, args.data_dir), args.data_dir)
        return 0

    assert_environment(config.expected_wechat_version)
    source = DatabaseMessageSource(args.data_dir)
    if args.command == "probe-db":
        sessions = source.db.get_sessions(limit=3)
        readable = bool(sessions and source.db.get_messages(sessions[0]["username"], limit=1))
        print(f"本地数据库：{'可读取' if readable else '未发现可读消息'}；会话抽样：{len(sessions)}")
        return 0 if readable else 1

    if args.command == "list-chats":
        for session in source.db.get_sessions(limit=100):
            name = source.db.get_nickname(session["username"])
            print(f"{session['username']}\t{name}")
        return 0
    if args.command == "find-contact":
        if not args.name:
            parser.error("find-contact 需要 --name")
        for contact in source.db.search_contact(args.name):
            print(f"{contact['username']}\t{contact['remark'] or contact['nick_name']}")
        return 0

    # Explicit command only: sends one diagnostic message to the user's own File Transfer Assistant.
    chat = ChatConfig("filehelper", "文件传输助手", "private", True, ())
    source.validate_chat(chat)
    before = source.latest(chat.username)
    if before is None:
        print("文件传输助手没有可核对的基线消息，请先手动发送一条消息。")
        return 1
    text = "【自动回复程序通道验证】" + uuid.uuid4().hex[:8]
    GuardedSender(source, config.expected_wechat_version).send(chat, before, text)
    print("文件传输助手：发送并回读确认成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
