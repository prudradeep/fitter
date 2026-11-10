"""Persistent, expiring cache for fixed frontend translations."""

import hashlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from collections.abc import Iterable


def _key(source_language: str, target_language: str, source_text: str) -> str:
    payload = "\0".join((source_language.casefold(), target_language.casefold(), source_text))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(path: str) -> sqlite3.Connection:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(cache_path, timeout=30)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS frontend_translation_cache (
            cache_key TEXT PRIMARY KEY,
            source_language TEXT NOT NULL,
            target_language TEXT NOT NULL,
            source_text TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    return connection


def get_frontend_translations(
    path: str,
    source_language: str,
    target_language: str,
    source_texts: Iterable[str],
) -> dict[str, str]:
    texts = list(dict.fromkeys(source_texts))
    if not texts:
        return {}
    now = time.time()
    keys = [_key(source_language, target_language, text) for text in texts]
    placeholders = ",".join("?" for _ in keys)
    with closing(_connect(path)) as connection:
        with connection:
            connection.execute(
                "DELETE FROM frontend_translation_cache WHERE expires_at <= ?",
                (now,),
            )
        rows = connection.execute(
            f"SELECT cache_key, source_text, translated_text FROM frontend_translation_cache "
            f"WHERE cache_key IN ({placeholders}) AND expires_at > ?",
            (*keys, now),
        ).fetchall()
    by_key = {row[0]: (row[1], row[2]) for row in rows}
    return {
        text: by_key[key][1]
        for text, key in zip(texts, keys)
        if key in by_key and by_key[key][0] == text
    }


def store_frontend_translations(
    path: str,
    source_language: str,
    target_language: str,
    translations: dict[str, str],
    ttl_seconds: int,
) -> None:
    if ttl_seconds <= 0 or not translations:
        return
    now = time.time()
    expires_at = now + ttl_seconds
    rows = [
        (
            _key(source_language, target_language, source_text),
            source_language.casefold(),
            target_language.casefold(),
            source_text,
            translated_text,
            expires_at,
        )
        for source_text, translated_text in translations.items()
        if source_text and translated_text.strip()
    ]
    if not rows:
        return
    with closing(_connect(path)) as connection:
        with connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO frontend_translation_cache
                    (cache_key, source_language, target_language, source_text, translated_text, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
