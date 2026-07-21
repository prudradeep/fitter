from urllib.parse import parse_qs, urlparse
import secrets

from fastapi import Request
from fastapi.responses import Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.auth import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME
from app.config import Settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FIELD_NAME = "csrf_token"

def csrf_serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt="dr-transition-csrf")


def create_csrf_token(settings: Settings) -> str:
    return csrf_serializer(settings).dumps({"nonce": secrets.token_urlsafe(32)})


def csrf_token_valid(token: str | None, settings: Settings) -> bool:
    if not token:
        return False
    try:
        payload = csrf_serializer(settings).loads(token)
    except BadSignature:
        return False
    return isinstance(payload, dict) and isinstance(payload.get("nonce"), str)


def csrf_origin_allowed(request: Request, settings: Settings) -> bool:
    if not settings.use_csrf_protection:
        return True
    if request.method.upper() not in UNSAFE_METHODS:
        return True
    if request.url.path in {"/login", "/signup"}:
        return True
    if AUTH_COOKIE_NAME not in request.cookies:
        return True

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin or referer
    if not candidate:
        return False

    return _origin(candidate) in allowed_csrf_origins(request, settings)


def apply_security_headers(response: Response, request: Request, settings: Settings) -> None:
    if settings.content_security_policy.strip():
        _set_header_if_missing(response, "Content-Security-Policy", settings.content_security_policy)
    _set_header_if_missing(response, "X-Content-Type-Options", "nosniff")
    if settings.referrer_policy.strip():
        _set_header_if_missing(response, "Referrer-Policy", settings.referrer_policy)
    if settings.permissions_policy.strip():
        _set_header_if_missing(response, "Permissions-Policy", settings.permissions_policy)
    if _request_is_https(request) and settings.strict_transport_security.strip():
        _set_header_if_missing(response, "Strict-Transport-Security", settings.strict_transport_security)


def _set_header_if_missing(response: Response, name: str, value: str) -> None:
    if name not in response.headers:
        response.headers[name] = value


def _request_is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().casefold() == "https"
    return request.url.scheme.casefold() == "https"


async def csrf_request_allowed(request: Request, settings: Settings) -> bool:
    if not csrf_origin_allowed(request, settings):
        return False
    if not settings.use_csrf_protection:
        return True
    if request.method.upper() not in UNSAFE_METHODS:
        return True
    if AUTH_COOKIE_NAME not in request.cookies:
        return True

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_token_valid(cookie_token, settings):
        return False

    submitted_token = request.headers.get(CSRF_HEADER_NAME)
    if not submitted_token:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")
        ):
            submitted_token = await _csrf_token_from_form_body(request, content_type)
    return bool(submitted_token and secrets.compare_digest(submitted_token, cookie_token))


async def _csrf_token_from_form_body(request: Request, content_type: str) -> str:
    body = await request.body()
    _replay_request_body(request, body)
    if content_type.startswith("application/x-www-form-urlencoded"):
        values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return values.get(CSRF_FIELD_NAME, [""])[0]
    if content_type.startswith("multipart/form-data"):
        marker = f'name="{CSRF_FIELD_NAME}"'.encode("utf-8")
        marker_index = body.find(marker)
        if marker_index < 0:
            return ""
        value_start = body.find(b"\r\n\r\n", marker_index)
        if value_start < 0:
            return ""
        value_start += 4
        value_end = body.find(b"\r\n--", value_start)
        if value_end < 0:
            value_end = len(body)
        return body[value_start:value_end].decode("utf-8", errors="replace").strip()
    return ""


def _replay_request_body(request: Request, body: bytes) -> None:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive


def allowed_csrf_origins(request: Request, settings: Settings) -> set[str]:
    allowed = {_origin(str(request.base_url).rstrip("/"))}
    allowed.update(_origin(origin) for origin in settings.cors_origin_list)
    return {origin for origin in allowed if origin}


def _origin(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
