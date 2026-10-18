from __future__ import annotations

from wechatauto.db import WeChatDB


class AccountAwareWeChatDB(WeChatDB):
    """Resolve sender rowids in their own message shard, never in resource.db.

    The pinned upstream reader loses shard identity before formatting messages.
    Attach the username while its connection is still available, retaining the
    upstream content decoding, ordering and pagination behavior.
    """

    def _shard_rows(self, tables, sql_ext, params=(), order_ext="", per_shard_limit=None):
        rows = []
        for conn, table in tables:
            shard_rows = super()._shard_rows(
                [(conn, table)], sql_ext, params, order_ext, per_shard_limit
            )
            sender_ids = {row["real_sender_id"] for row in shard_rows}
            if not sender_ids:
                continue
            placeholders = ",".join("?" for _ in sender_ids)
            identities = dict(conn.execute(
                f"SELECT rowid, user_name FROM Name2Id WHERE rowid IN ({placeholders})",
                tuple(sender_ids),
            ))
            for row in shard_rows:
                raw = dict(row)
                raw["_sender_username"] = identities.get(row["real_sender_id"], "")
                rows.append(raw)
        return rows

    def _msg_row_to_dict(self, row) -> dict:
        # Bypass upstream's hardcoded self id and unrelated SenderName2Id lookup.
        content_row = dict(row)
        content_row["real_sender_id"] = None
        message = super()._msg_row_to_dict(content_row)
        username = row["_sender_username"]
        if message["type"] == "文本" and not username:
            raise RuntimeError("无法识别文字消息的发送者，请停止监听并检查微信数据库")
        message.update(
            sender_id=row["real_sender_id"],
            sender_username=username,
            is_self=bool(username) and username == self.wxid,
        )
        return message
