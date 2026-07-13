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
SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "token",
}


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
    if not settings.use_llm_logging:
        return

    if settings.include_llm_log_payloads:
        safe_request = _sanitize_payload(request, settings.llm_log_max_text_chars)
        safe_response = (
            _sanitize_payload(response, settings.llm_log_max_text_chars)
            if response is not None
            else None
        )
    else:
        safe_request = {"redacted": True, "reason": "LLM payload logging is disabled"}
        safe_response = {"redacted": True, "reason": "LLM payload logging is disabled"} if response is not None else None
    request_json = _bounded_json(safe_request, settings.llm_log_max_payload_chars)
    response_json = (
        _bounded_json(safe_response, settings.llm_log_max_payload_chars)
        if safe_response is not None
        else None
    )
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "request": safe_request,
        "response": safe_response,
        "error": error,
    }
    if settings.write_llm_log_to_db:
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
    if not settings.write_llm_log_to_file:
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


def _sanitize_payload(value: Any, max_text_chars: int) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.strip().casefold() in SENSITIVE_KEYS:
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = _sanitize_payload(item, max_text_chars)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item, max_text_chars) for item in value]
    if isinstance(value, str):
        if len(value) > max_text_chars:
            return value[:max_text_chars] + f"...[truncated {len(value) - max_text_chars} chars]"
        return value
    return value


def _bounded_json(value: Any, max_chars: int) -> str:
    payload = json.dumps(value, ensure_ascii=False, default=str)
    if len(payload) <= max_chars:
        return payload
    marker = f'...[truncated {len(payload) - max_chars} chars]"'
    return payload[: max(0, max_chars - len(marker))] + marker


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

        from app.db.session import SessionLocal

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
