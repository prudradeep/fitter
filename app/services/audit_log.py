import json
import logging
from typing import Any

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AppUser, AuditLog
from app.observability import current_request_id

logger = logging.getLogger(__name__)


def record_audit_event(
    db: Session,
    *,
    user: AppUser | None,
    action: str,
    request: Request | None = None,
    status: str = "success",
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        db.add(
            AuditLog(
                user_id=user.id if user else None,
                action=action[:120],
                status=status[:40],
                target_type=target_type[:80] if target_type else None,
                target_id=str(target_id)[:160] if target_id is not None else None,
                request_id=current_request_id() or None,
                ip_address=_client_host(request),
                user_agent=_user_agent(request),
                details=json.dumps(details or {}, ensure_ascii=False, default=str),
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Failed to write audit event action=%s user_id=%s request_id=%s",
            action,
            user.id if user else None,
            current_request_id() or "-",
        )


def _client_host(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host[:80]


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    user_agent = request.headers.get("user-agent")
    return user_agent[:255] if user_agent else None
