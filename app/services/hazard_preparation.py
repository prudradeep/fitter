"""Run expensive sector hazard preparation outside chat request workers."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hazard-prep")
_lock = RLock()
_jobs: dict[str, dict[str, Any]] = {}


def start_hazard_preparation(
    job_key: str,
    session_data: dict[str, Any],
    user_id: str | None,
    is_admin: bool,
) -> str:
    with _lock:
        current = _jobs.get(job_key)
        if current and current["status"] in {"pending", "complete"}:
            return str(current["status"])
        if len(_jobs) >= 512:
            for completed_key in list(_jobs):
                if _jobs[completed_key]["status"] != "pending":
                    _jobs.pop(completed_key, None)
                    if len(_jobs) < 384:
                        break
        _jobs[job_key] = {"status": "pending", "future": None}
        future = _executor.submit(
            _prepare_hazard_listing, session_data, user_id, is_admin
        )
        _jobs[job_key]["future"] = future
        future.add_done_callback(lambda completed: _finish_job(job_key, completed))
        return "pending"


def hazard_preparation_status(job_key: str) -> str | None:
    with _lock:
        job = _jobs.get(job_key)
        return str(job["status"]) if job else None


async def wait_for_hazard_preparation(job_key: str) -> str | None:
    while True:
        status = hazard_preparation_status(job_key)
        if status != "pending":
            return status
        await asyncio.sleep(0.2)


def clear_hazard_preparation(job_key: str) -> None:
    with _lock:
        _jobs.pop(job_key, None)


def _finish_job(job_key: str, future: Future[None]) -> None:
    try:
        future.result()
        status = "complete"
    except Exception:
        logger.exception("Background hazard preparation failed job_key=%s", job_key)
        status = "failed"
    with _lock:
        if job_key in _jobs:
            _jobs[job_key]["status"] = status


def _prepare_hazard_listing(
    session_data: dict[str, Any], user_id: str | None, is_admin: bool
) -> None:
    from app.services.chat_service import ChatService
    from app.services.chat_session import ChatSession

    db = SessionLocal()
    try:
        service = ChatService(db, user_id=user_id, is_admin=is_admin)
        session = ChatSession(**session_data)
        asyncio.run(service._prepare_hazard_listing(session.session_key or "", session))
    finally:
        db.close()
