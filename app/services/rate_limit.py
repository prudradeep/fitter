from dataclasses import dataclass
from time import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppRateLimit


@dataclass
class RateLimitState:
    attempts: int = 0
    window_started_at: float = 0.0
    locked_until: float = 0.0


_RATE_LIMITS: dict[str, RateLimitState] = {}


def retry_after_seconds(key: str, db: Session | None = None) -> int | None:
    if db is not None:
        state = db.get(AppRateLimit, key)
        if state is None:
            return None
        remaining = state.locked_until - time()
        return max(1, int(remaining)) if remaining > 0 else None

    state = _RATE_LIMITS.get(key)
    if state is None:
        return None
    remaining = state.locked_until - time()
    return max(1, int(remaining)) if remaining > 0 else None


def record_failed_attempt(
    key: str,
    *,
    max_attempts: int,
    window_seconds: int,
    lockout_seconds: int,
    db: Session | None = None,
) -> int | None:
    now = time()
    if db is not None:
        state = db.scalar(select(AppRateLimit).where(AppRateLimit.key == key))
        if state is None:
            state = AppRateLimit(key=key, window_started_at=now)
            db.add(state)
            db.flush()
        if now - state.window_started_at > window_seconds:
            state.attempts = 0
            state.window_started_at = now
            state.locked_until = 0.0

        if state.locked_until > now:
            db.commit()
            return retry_after_seconds(key, db)

        state.attempts += 1
        if state.attempts >= max_attempts:
            state.locked_until = now + lockout_seconds
            db.commit()
            return retry_after_seconds(key, db)
        db.commit()
        return None

    state = _RATE_LIMITS.setdefault(key, RateLimitState(window_started_at=now))
    if now - state.window_started_at > window_seconds:
        state.attempts = 0
        state.window_started_at = now
        state.locked_until = 0.0

    if state.locked_until > now:
        return retry_after_seconds(key)

    state.attempts += 1
    if state.attempts >= max_attempts:
        state.locked_until = now + lockout_seconds
        return retry_after_seconds(key)
    return None


def reset_rate_limit(key: str, db: Session | None = None) -> None:
    if db is not None:
        state = db.get(AppRateLimit, key)
        if state is not None:
            db.delete(state)
            db.commit()
        return
    _RATE_LIMITS.pop(key, None)


def clear_rate_limits(db: Session | None = None) -> None:
    if db is not None:
        db.query(AppRateLimit).delete()
        db.commit()
    _RATE_LIMITS.clear()
