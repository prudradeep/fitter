"""Persistent, server-local cache for Bedrock text embeddings."""

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path


def embedding_cache_key(model_id: str, text: str) -> str:
    digest = hashlib.sha256()
    digest.update(model_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def cached_embedding(path: str, model_id: str, text: str) -> list[float] | None:
    cache_path = Path(path)
    if not cache_path.is_file():
        return None
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        row = connection.execute(
            "SELECT embedding_json FROM bedrock_embeddings WHERE cache_key = ? AND model_id = ?",
            (embedding_cache_key(model_id, text), model_id),
        ).fetchone()
    if row is None:
        return None
    try:
        values = json.loads(row[0])
        if not isinstance(values, list) or not values:
            return None
        vector = [float(value) for value in values]
        return vector if all(math.isfinite(value) for value in vector) else None
    except (TypeError, ValueError):
        return None


def store_embedding(path: str, model_id: str, text: str, values: list[float]) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache_path, timeout=30)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bedrock_embeddings (
                    cache_key TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO bedrock_embeddings (cache_key, model_id, embedding_json) VALUES (?, ?, ?)",
                (embedding_cache_key(model_id, text), model_id, json.dumps(values, allow_nan=False)),
            )
