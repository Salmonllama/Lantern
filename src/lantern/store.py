"""Local conversation history and settings (standard library only)."""

import json
import os
import sqlite3
import time
from pathlib import Path

APP_ID = "lantern"


def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


class Settings:
    DEFAULTS = {"host": "http://127.0.0.1:11434", "model": ""}

    def __init__(self, path=None):
        self.path = Path(path) if path else config_path()
        self.values = dict(self.DEFAULTS)
        try:
            self.values.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values.get(key, self.DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.values[key] = value
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2))
        tmp.replace(self.path)


class Store:
    def __init__(self, path=None):
        self.db = sqlite3.connect(str(path or data_dir() / "history.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT,
                updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                conversation_id INTEGER NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created REAL NOT NULL
            );
            """
        )

    def new_conversation(self, title="New chat", model=""):
        cur = self.db.execute(
            "INSERT INTO conversations (title, model, updated) VALUES (?, ?, ?)",
            (title, model, time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def conversations(self):
        return self.db.execute(
            "SELECT id, title, model FROM conversations ORDER BY updated DESC"
        ).fetchall()

    def messages(self, conversation_id):
        rows = self.db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def add_message(self, conversation_id, role, content):
        now = time.time()
        self.db.execute(
            "INSERT INTO messages (conversation_id, role, content, created) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        self.db.execute(
            "UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation_id)
        )
        self.db.commit()

    def rename(self, conversation_id, title):
        self.db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        self.db.commit()

    def delete(self, conversation_id):
        self.db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self.db.commit()


def title_from(text, limit=40):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
