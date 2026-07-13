import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def payload_too_large_response(
    request: Request,
    max_bytes: int,
    label: str,
) -> JSONResponse | None:
    length = content_length(request)
    if length is None or length <= max_bytes:
        return None
    return _too_large_response(max_bytes, label)


def upload_too_large_response(upload: object, max_bytes: int, label: str) -> JSONResponse | None:
    size = getattr(upload, "size", None)
    if not isinstance(size, int) or size <= max_bytes:
        return None
    return _too_large_response(max_bytes, label)


async def read_limited_json(
    request: Request,
    max_bytes: int,
    label: str,
) -> dict[str, Any]:
    too_large = payload_too_large_response(request, max_bytes, label)
    if too_large is not None:
        raise RequestTooLarge(label)

    body = await request.body()
    if len(body) > max_bytes:
        raise RequestTooLarge(label)
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidJsonPayload(label) from exc
    if not isinstance(payload, dict):
        raise InvalidJsonPayload(label)
    return payload


class RequestTooLarge(ValueError):
    def __init__(self, label: str) -> None:
        super().__init__(f"{label} is too large.")
        self.label = label


class InvalidJsonPayload(ValueError):
    def __init__(self, label: str) -> None:
        super().__init__(f"{label} must be a JSON object.")
        self.label = label


def json_payload_error_response(exc: ValueError, max_bytes: int | None = None) -> JSONResponse:
    if isinstance(exc, RequestTooLarge):
        return _too_large_response(max_bytes or 0, exc.label)
    return JSONResponse(
        status_code=400,
        content={"error": True, "detail": str(exc)},
    )


def _too_large_response(max_bytes: int, label: str) -> JSONResponse:
    max_mb = max(1, max_bytes // (1024 * 1024))
    return JSONResponse(
        status_code=413,
        content={
            "error": True,
            "detail": f"{label} is too large. Maximum size is {max_mb} MB.",
        },
    )
