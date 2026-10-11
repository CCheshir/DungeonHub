"""Хранилище на SQLite: комнаты, персонажи, сообщения, летопись."""
import json
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    admin_token TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    chronicle TEXT NOT NULL DEFAULT '',
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
    room_id TEXT NOT NULL,
    player TEXT NOT NULL,
    character TEXT NOT NULL,
    sheet TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (room_id, player)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- action | ooc | roll | master | system
    author TEXT NOT NULL DEFAULT '',
    character TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "world": "",
    "situation": "",
    "plot": "",
    "style": "",
    "length": "medium",        # short | medium | long
    "mode": "narrative",       # narrative | mechanics
    "rules": "",
    "batch_seconds": 4,
    "open_timeout_minutes": 5,
    "temperature": 1.0,
    "model": "",               # пусто = модель из .env
}


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # --- комнаты ---
    def create_room(self, room_id: str, name: str, admin_token: str) -> None:
        self._exec(
            "INSERT INTO rooms (id, name, admin_token, settings, created_at) VALUES (?, ?, ?, ?, ?)",
            (room_id, name, admin_token, json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), time.time()),
        )

    def get_room(self, room_id: str) -> dict | None:
        room = self._one("SELECT * FROM rooms WHERE id = ?", (room_id,))
        if room:
            settings = dict(DEFAULT_SETTINGS)
            settings.update(json.loads(room["settings"] or "{}"))
            room["settings"] = settings
        return room

    def update_settings(self, room_id: str, settings: dict) -> None:
        self._exec("UPDATE rooms SET settings = ? WHERE id = ?", (json.dumps(settings, ensure_ascii=False), room_id))

    def update_room_name(self, room_id: str, name: str) -> None:
        self._exec("UPDATE rooms SET name = ? WHERE id = ?", (name, room_id))

    def set_chronicle(self, room_id: str, chronicle: str, summarized_upto: int | None = None) -> None:
        if summarized_upto is None:
            self._exec("UPDATE rooms SET chronicle = ? WHERE id = ?", (chronicle, room_id))
        else:
            self._exec(
                "UPDATE rooms SET chronicle = ?, summarized_upto = ? WHERE id = ?",
                (chronicle, summarized_upto, room_id),
            )

    # --- персонажи ---
    def upsert_character(self, room_id: str, player: str, character: str) -> None:
        self._exec(
            "INSERT INTO characters (room_id, player, character) VALUES (?, ?, ?) "
            "ON CONFLICT(room_id, player) DO UPDATE SET character = excluded.character",
            (room_id, player, character),
        )

    def set_sheet(self, room_id: str, player: str, sheet: str) -> None:
        self._exec("UPDATE characters SET sheet = ? WHERE room_id = ? AND player = ?", (sheet, room_id, player))

    def get_characters(self, room_id: str) -> list[dict]:
        return self._all("SELECT player, character, sheet FROM characters WHERE room_id = ? ORDER BY rowid", (room_id,))

    def remove_character(self, room_id: str, player: str) -> None:
        self._exec("DELETE FROM characters WHERE room_id = ? AND player = ?", (room_id, player))

    # --- сообщения ---
    def add_message(self, room_id: str, kind: str, content: str, author: str = "", character: str = "") -> dict:
        now = time.time()
        cur = self._exec(
            "INSERT INTO messages (room_id, kind, author, character, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (room_id, kind, author, character, content, now),
        )
        return {
            "id": cur.lastrowid, "kind": kind, "author": author,
            "character": character, "content": content, "created_at": now,
        }

    def recent_messages(self, room_id: str, limit: int = 200) -> list[dict]:
        rows = self._all(
            "SELECT id, kind, author, character, content, created_at FROM messages "
            "WHERE room_id = ? ORDER BY id DESC LIMIT ?",
            (room_id, limit),
        )
        return list(reversed(rows))

    def messages_after(self, room_id: str, after_id: int) -> list[dict]:
        return self._all(
            "SELECT id, kind, author, character, content, created_at FROM messages "
            "WHERE room_id = ? AND id > ? ORDER BY id",
            (room_id, after_id),
        )

    def last_message_id(self, room_id: str) -> int:
        row = self._one("SELECT MAX(id) AS m FROM messages WHERE room_id = ?", (room_id,))
        return (row or {}).get("m") or 0
