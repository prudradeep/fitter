import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.config import Settings

logger = logging.getLogger(__name__)

_LOG_LOCK = Lock()


def new_llm_request_id() -> str:
    return uuid4().hex


def log_llm_exchange(
    settings: Settings,
    *,
    request_id: str,
    provider: str,
    endpoint: str,
    model: str,
    request: dict[str, Any],
    response: Any = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    if not settings.llm_log_enabled:
        return

    request_json = json.dumps(request, ensure_ascii=False, default=str)
    response_json = json.dumps(response, ensure_ascii=False, default=str) if response is not None else None
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "request": request,
        "response": response,
        "error": error,
    }
    if settings.llm_log_to_db:
        _write_db_log(
            request_id=request_id,
            provider=provider,
            endpoint=endpoint,
            model=model,
            status_code=status_code,
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
            request_payload=request_json,
            response_payload=response_json,
            error=error,
        )
    if not settings.llm_log_to_file:
        return
    path = Path(settings.llm_log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
    except OSError:
        logger.exception("Failed to write LLM exchange log to %s", path)


def _write_db_log(
    *,
    request_id: str,
    provider: str,
    endpoint: str,
    model: str,
    status_code: int | None,
    duration_ms: float | None,
    request_payload: str,
    response_payload: str | None,
    error: str | None,
) -> None:
    try:
        from sqlalchemy import text

        from app.database import SessionLocal

        db = SessionLocal()
        try:
            db.execute(
                text(
                    """
                    INSERT INTO llm_exchange_logs (
                      request_id,
                      provider,
                      endpoint,
                      model,
                      status_code,
                      duration_ms,
                      request_payload,
                      response_payload,
                      error
                    ) VALUES (
                      :request_id,
                      :provider,
                      :endpoint,
                      :model,
                      :status_code,
                      :duration_ms,
                      :request_payload,
                      :response_payload,
                      :error
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "provider": provider,
                    "endpoint": endpoint,
                    "model": model,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "request_payload": request_payload,
                    "response_payload": response_payload,
                    "error": error,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to write LLM exchange log to database")
