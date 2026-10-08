"""Persistent, server-local cache for Bedrock chat answers."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


def chat_cache_key(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_chat(path: str, key: str, ttl_seconds: int) -> str | None:
    cache_path = Path(path)
    if not cache_path.is_file() or ttl_seconds <= 0:
        return None
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        row = connection.execute(
            "SELECT answer, created_at FROM bedrock_chat WHERE cache_key = ?", (key,)
        ).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0].strip():
        return None
    if time.time() - row[1] >= ttl_seconds:
        return None
    return row[0]


def store_chat(path: str, key: str, answer: str, ttl_seconds: int) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bedrock_chat (
                    cache_key TEXT PRIMARY KEY,
                    answer TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute("DELETE FROM bedrock_chat WHERE created_at <= ?", (time.time() - ttl_seconds,))
            connection.execute(
                "INSERT OR REPLACE INTO bedrock_chat (cache_key, answer, created_at) VALUES (?, ?, ?)",
                (key, answer, time.time()),
            )
