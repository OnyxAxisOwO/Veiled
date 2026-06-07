import sqlite3
import json
import time
import uuid
from pathlib import Path
from .config import dpapi_encrypt, dpapi_decrypt
import base64


class Database:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                model TEXT,
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                role TEXT,
                content_enc BLOB,
                image_enc BLOB,
                timestamp REAL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, timestamp);
        """)
        self._conn.commit()

    def _encrypt(self, text: str) -> bytes:
        return base64.b64encode(dpapi_encrypt(text.encode("utf-8")))

    def _decrypt(self, data: bytes) -> str:
        return dpapi_decrypt(base64.b64decode(data)).decode("utf-8")

    def create_conversation(self, model: str = "") -> str:
        conv_id = str(uuid.uuid4())
        now = time.time()
        self._conn.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, "", model, now, now),
        )
        self._conn.commit()
        return conv_id

    def add_message(self, conversation_id: str, role: str, content: str, image_data: bytes = None):
        msg_id = str(uuid.uuid4())
        now = time.time()
        content_enc = self._encrypt(content)
        image_enc = base64.b64encode(dpapi_encrypt(image_data)) if image_data else None
        self._conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content_enc, image_enc, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, role, content_enc, image_enc, now),
        )
        row = self._conn.execute(
            "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row and not row["title"] and role == "user":
            title = content[:50]
            self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
        else:
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
        self._conn.commit()

    def delete_last_message(self, conversation_id: str):
        row = self._conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if row:
            self._conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
            self._conn.commit()

    def get_messages(self, conversation_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content_enc, image_enc, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()
        result = []
        for r in rows:
            msg = {
                "role": r["role"],
                "content": self._decrypt(r["content_enc"]),
                "timestamp": r["timestamp"],
            }
            if r["image_enc"]:
                msg["image"] = dpapi_decrypt(base64.b64decode(r["image_enc"]))
            result.append(msg)
        return result

    def list_conversations(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, model, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conversation_id: str):
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()

    def clear_conversation(self, conversation_id: str):
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._conn.commit()

    def cleanup_old(self, days: int):
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        conv_ids = [
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM conversations WHERE updated_at < ?", (cutoff,)
            ).fetchall()
        ]
        for cid in conv_ids:
            self.delete_conversation(cid)

    def close(self):
        self._conn.close()
