from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SystemInquiryTelemetryEvent

LOCAL_ONLY_KEYS = {
    "annotations",
    "held_observations",
    "candidate_audit",
    "observation_text",
    "question_text",
    "user_response",
    "followup_question",
    "followup_response",
    "summary",
    "context_snapshot",
}


def sanitize_system_inquiry_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep telemetry aggregate-only; free text and local audit records stay local."""

    cleaned = _strip_local_only(payload)
    if isinstance(cleaned, dict):
        cleaned["schema_version"] = int(cleaned.get("schema_version") or 1)
    return cleaned if isinstance(cleaned, dict) else {}


def enqueue_system_inquiry_telemetry(
    db: Session,
    payload: dict[str, Any],
) -> SystemInquiryTelemetryEvent | None:
    cleaned = sanitize_system_inquiry_telemetry(payload)
    if not cleaned:
        return None
    encoded = json.dumps(cleaned, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    event_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(SystemInquiryTelemetryEvent).where(
            SystemInquiryTelemetryEvent.event_key == event_key
        )
    )
    if existing is not None:
        return existing
    event = SystemInquiryTelemetryEvent(
        event_key=event_key,
        payload_json=encoded,
        status="queued",
    )
    db.add(event)
    db.flush()
    return event


def accept_system_inquiry_telemetry_batch(
    db: Session,
    events: list[dict[str, Any]],
) -> dict[str, int]:
    accepted = skipped = 0
    for event_payload in events:
        event = enqueue_system_inquiry_telemetry(db, event_payload)
        if event is None:
            skipped += 1
            continue
        accepted += 1
        event.status = "synced"
        event.synced_at = utc_now()
    db.commit()
    return {"accepted": accepted, "skipped": skipped}


async def push_queued_system_inquiry_telemetry(db: Session) -> dict[str, Any]:
    settings = get_settings()
    server_url = str(settings.sync_server_url or "").strip().rstrip("/")
    token = str(settings.sync_api_token or "").strip()
    if not server_url:
        raise ValueError("SYNC_SERVER_URL is not configured.")
    if not token:
        raise ValueError("SYNC_API_TOKEN is not configured.")

    rows = list(
        db.scalars(
            select(SystemInquiryTelemetryEvent)
            .where(SystemInquiryTelemetryEvent.status.in_(("queued", "failed")))
            .order_by(SystemInquiryTelemetryEvent.created_at.asc())
            .limit(100)
        )
    )
    events = [json.loads(row.payload_json) for row in rows]
    if not events:
        return {"error": False, "pushed": 0}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{server_url}/api/sync/system-inquiry-telemetry",
                json={"events": events},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
    except Exception as exc:
        for row in rows:
            row.status = "failed"
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = str(exc)[:1000]
        db.commit()
        raise

    synced_at = utc_now()
    for row in rows:
        row.status = "synced"
        row.synced_at = synced_at
        row.last_error = None
    db.commit()
    return {"error": False, "pushed": len(rows)}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _strip_local_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_local_only(item)
            for key, item in value.items()
            if str(key) not in LOCAL_ONLY_KEYS
        }
    if isinstance(value, list):
        return [_strip_local_only(item) for item in value]
    return value
