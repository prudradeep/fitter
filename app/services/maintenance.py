from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppRateLimit, KnowledgeDocument, LlmExchangeLog
from app.services.knowledge_base import TEMPORARY_KB_SCOPE


def cleanup_retained_data(db: Session, settings: Settings) -> dict[str, int]:
    return {
        "rate_limits": cleanup_rate_limits(db, settings.rate_limit_retention_days),
        "temporary_knowledge_documents": cleanup_temporary_knowledge(
            db,
            settings.temporary_knowledge_retention_hours,
        ),
        "llm_exchange_logs": cleanup_llm_exchange_logs(db, settings.llm_log_retention_days),
    }


def cleanup_rate_limits(db: Session, retention_days: int) -> int:
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).timestamp()
    result = db.execute(
        delete(AppRateLimit).where(
            AppRateLimit.locked_until <= cutoff_ts,
            AppRateLimit.window_started_at <= cutoff_ts,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def cleanup_temporary_knowledge(db: Session, retention_hours: int) -> int:
    cutoff = _naive_utc_now() - timedelta(hours=max(1, retention_hours))
    result = db.execute(
        delete(KnowledgeDocument).where(
            KnowledgeDocument.scope == TEMPORARY_KB_SCOPE,
            KnowledgeDocument.created_at < cutoff,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def cleanup_llm_exchange_logs(db: Session, retention_days: int) -> int:
    cutoff = _naive_utc_now() - timedelta(days=max(1, retention_days))
    result = db.execute(delete(LlmExchangeLog).where(LlmExchangeLog.created_at < cutoff))
    db.commit()
    return int(result.rowcount or 0)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
