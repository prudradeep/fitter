"""Short-lived, user-scoped status for document ingestion requests."""

from __future__ import annotations

from threading import Lock
from time import monotonic
from uuid import UUID


_LOCK = Lock()
_STATUS: dict[str, dict[str, object]] = {}
_TTL_SECONDS = 600


def begin_ingestion_progress(progress_id: str | None, user_id: str) -> str | None:
    try:
        key = str(UUID(str(progress_id))) if progress_id else None
    except ValueError:
        return None
    if key is None:
        return None
    with _LOCK:
        now = monotonic()
        for old_key, item in list(_STATUS.items()):
            if now - float(item["updated_at"]) > _TTL_SECONDS:
                del _STATUS[old_key]
        _STATUS[key] = {
            "user_id": user_id,
            "source": "",
            "stage": "queued",
            "history": [],
            "updated_at": now,
        }
    return key


def update_ingestion_progress(progress_id: str | None, source: str, stage: str) -> None:
    if progress_id is None:
        return
    with _LOCK:
        item = _STATUS.get(progress_id)
        if item is not None:
            history = item["history"]
            if isinstance(history, list) and (not history or history[-1] != stage):
                history.append(stage)
            item.update(source=source, stage=stage, updated_at=monotonic())


def get_ingestion_progress(progress_id: str, user_id: str) -> dict[str, object] | None:
    with _LOCK:
        item = _STATUS.get(progress_id)
        if item is None or item["user_id"] != user_id:
            return None
        if monotonic() - float(item["updated_at"]) > _TTL_SECONDS:
            del _STATUS[progress_id]
            return None
        return {
            "source": str(item["source"]),
            "stage": str(item["stage"]),
            "history": list(item["history"]),
        }
