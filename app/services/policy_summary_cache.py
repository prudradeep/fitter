"""Short-lived local cache for complete generated policy summaries."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


def policy_summary_cache_key(request: dict[str, Any]) -> str:
    payload = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_policy_summary(path: str, key: str, ttl_seconds: int) -> str | None:
    cache_path = Path(path)
    if not cache_path.is_file() or ttl_seconds <= 0:
        return None
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        row = connection.execute(
            "SELECT summary, created_at FROM policy_summary_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0].strip():
        return None
    if time.time() - row[1] >= ttl_seconds:
        return None
    return row[0]


def store_policy_summary(path: str, key: str, summary: str, ttl_seconds: int) -> None:
    if ttl_seconds <= 0 or not summary.strip():
        return
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_summary_cache (
                    cache_key TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "DELETE FROM policy_summary_cache WHERE created_at <= ?",
                (now - ttl_seconds,),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO policy_summary_cache (cache_key, summary, created_at)
                VALUES (?, ?, ?)
                """,
                (key, summary, now),
            )
