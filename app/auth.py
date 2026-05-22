import base64
import hashlib
import hmac
import re
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AppUser

AUTH_COOKIE_NAME = "dr_transition_auth"
AUTH_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
HASH_ITERATIONS = 210_000
PASSWORD_RULES = (
    "At least 8 characters",
    "One uppercase letter",
    "One lowercase letter",
    "One number",
    "One symbol",
)


def password_rule_errors(password: str) -> list[str]:
    checks = [
        (len(password) >= 8, PASSWORD_RULES[0]),
        (bool(re.search(r"[A-Z]", password)), PASSWORD_RULES[1]),
        (bool(re.search(r"[a-z]", password)), PASSWORD_RULES[2]),
        (bool(re.search(r"\d", password)), PASSWORD_RULES[3]),
        (bool(re.search(r"[^A-Za-z0-9]", password)), PASSWORD_RULES[4]),
    ]
    return [message for passed, message in checks if not passed]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def auth_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="dr-transition-auth")


def create_auth_token(user_id: int) -> str:
    return auth_serializer().dumps({"user_id": user_id})


def user_id_from_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        data = auth_serializer().loads(token, max_age=AUTH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    user_id = data.get("user_id") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, int) else None


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    auth_cookie: Annotated[str | None, Cookie(alias=AUTH_COOKIE_NAME)] = None,
) -> AppUser | None:
    user_id = user_id_from_token(auth_cookie)
    if user_id is None:
        return None
    return db.scalar(select(AppUser).where(AppUser.id == user_id))


def require_current_user(
    user: Annotated[AppUser | None, Depends(get_current_user)],
) -> AppUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def redirect_if_authenticated(request: Request, db: Session) -> RedirectResponse | None:
    user_id = user_id_from_token(request.cookies.get(AUTH_COOKIE_NAME))
    if user_id is None:
        return None
    user = db.scalar(select(AppUser.id).where(AppUser.id == user_id))
    if user is None:
        return None
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def set_auth_cookie(response: RedirectResponse, user_id: int) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_auth_token(user_id),
        max_age=AUTH_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME)
