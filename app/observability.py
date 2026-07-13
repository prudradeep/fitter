import contextvars
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_METRICS_LOCK = Lock()
_COUNTERS: Counter[str] = Counter()


def new_request_id() -> str:
    return uuid4().hex


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def current_request_id() -> str:
    return request_id_var.get()


def increment_metric(name: str, amount: int = 1) -> None:
    with _METRICS_LOCK:
        _COUNTERS[name] += amount


def metrics_snapshot() -> dict[str, int]:
    with _METRICS_LOCK:
        return dict(_COUNTERS)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int, *, structured: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if structured:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s"
            )
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
