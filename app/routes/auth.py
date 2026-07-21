from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    CSRF_COOKIE_NAME,
    clear_auth_cookie,
    hash_password,
    password_rule_errors,
    redirect_if_authenticated,
    set_auth_cookie,
    verify_password,
)
from app.config import get_settings
from app.db.session import get_db
from app.models import AppUser
from app.resource_paths import resource_path
from app.security import create_csrf_token, csrf_token_valid
from app.services.coverage import get_coverage_map_rows
from app.services.rate_limit import record_failed_attempt, reset_rate_limit, retry_after_seconds

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(resource_path("app/templates")))
settings = get_settings()


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    redirect = redirect_if_authenticated(request, db)
    if redirect is not None:
        return redirect
    return _auth_template_response(
        request,
        "login.html",
        {
            "page_title": "Dr Transition",
            "error": None,
            "email": "",
            "values": {},
            "initial_step": 1,
            "auth_mode": "login",
            "auth_open": False,
            "coverage_map_rows": get_coverage_map_rows(),
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    email = str(form.get("email") or "").strip().casefold()
    password = str(form.get("password") or "")
    rate_limit_key = _login_rate_limit_key(request, email)
    retry_after = retry_after_seconds(rate_limit_key, db)
    if retry_after is not None:
        return _login_error(
            request,
            email,
            f"Too many login attempts. Try again in {retry_after} seconds.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = db.scalar(select(AppUser).where(AppUser.email == email))

    if user is None or not verify_password(password, user.password_hash):
        retry_after = record_failed_attempt(
            rate_limit_key,
            max_attempts=settings.login_rate_limit_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
            lockout_seconds=settings.login_rate_limit_lockout_seconds,
            db=db,
        )
        if retry_after is not None:
            return _login_error(
                request,
                email,
                f"Too many login attempts. Try again in {retry_after} seconds.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return _login_error(request, email, "Invalid email or password.", status.HTTP_400_BAD_REQUEST)

    reset_rate_limit(rate_limit_key, db)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, user)
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    redirect = redirect_if_authenticated(request, db)
    if redirect is not None:
        return redirect
    return _auth_template_response(
        request,
        "signup.html",
        {
            "error": None,
            "email": "",
            "values": {},
            "initial_step": 1,
            "auth_mode": "signup",
            "auth_open": True,
            "coverage_map_rows": get_coverage_map_rows(),
        },
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    values = {
        "email": str(form.get("email") or "").strip().casefold(),
        "name": str(form.get("name") or "").strip(),
        "designation": str(form.get("designation") or "").strip(),
        "organisation_type": str(form.get("organisation_type") or "").strip(),
        "organisation_name": str(form.get("organisation_name") or "").strip(),
    }
    password = str(form.get("password") or "")
    confirm_password = str(form.get("confirm_password") or "")
    rate_limit_key = _signup_rate_limit_key(request, values["email"])
    retry_after = retry_after_seconds(rate_limit_key, db)
    if retry_after is not None:
        return _signup_error(
            request,
            values,
            f"Too many signup attempts. Try again in {retry_after} seconds.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    field_labels = {
        "email": "Email",
        "name": "Name",
        "designation": "Designation",
        "organisation_type": "Organisation type",
        "organisation_name": "Organisation name",
    }
    missing = [field_labels[key] for key, value in values.items() if not value]
    if not password:
        missing.append("Password")
    if not confirm_password:
        missing.append("Confirm password")
    if missing or not password or not confirm_password:
        retry_after = _record_signup_failure(rate_limit_key, db)
        if retry_after is not None:
            return _signup_lockout_error(request, values, retry_after)
        step = 2 if not any(label in missing for label in ["Email", "Name", "Password", "Confirm password"]) else 1
        return _signup_error(
            request,
            values,
            "Please complete: " + ", ".join(missing) + ".",
            step,
        )
    if password != confirm_password:
        retry_after = _record_signup_failure(rate_limit_key, db)
        if retry_after is not None:
            return _signup_lockout_error(request, values, retry_after)
        return _signup_error(request, values, "Passwords do not match.")
    password_errors = password_rule_errors(password)
    if password_errors:
        retry_after = _record_signup_failure(rate_limit_key, db)
        if retry_after is not None:
            return _signup_lockout_error(request, values, retry_after)
        return _signup_error(
            request,
            values,
            "Password must include: " + ", ".join(password_errors) + ".",
        )

    user = AppUser(
        email=values["email"],
        name=values["name"],
        password_hash=hash_password(password),
        designation=values["designation"],
        organisation_type=values["organisation_type"],
        organisation_name=values["organisation_name"],
    )
    db.add(user)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        retry_after = _record_signup_failure(rate_limit_key, db)
        if retry_after is not None:
            return _signup_lockout_error(request, values, retry_after)
        return _signup_error(request, values, "An account already exists for this email.")

    reset_rate_limit(rate_limit_key, db)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, user)
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookie(response)
    return response


def _signup_error(
    request: Request,
    values: dict[str, str],
    error: str,
    step: int = 1,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> HTMLResponse:
    return _auth_template_response(
        request,
        "signup.html",
        {
            "page_title": "Dr Transition",
            "error": error,
            "email": values.get("email", ""),
            "values": values,
            "initial_step": step,
            "auth_mode": "signup",
            "auth_open": True,
            "coverage_map_rows": get_coverage_map_rows(),
        },
        status_code=status_code,
    )


def _login_error(request: Request, email: str, error: str, status_code: int) -> HTMLResponse:
    return _auth_template_response(
        request,
        "login.html",
        {
            "page_title": "Dr Transition",
            "error": error,
            "email": email,
            "values": {},
            "initial_step": 1,
            "auth_mode": "login",
            "auth_open": True,
            "coverage_map_rows": get_coverage_map_rows(),
        },
        status_code=status_code,
    )


def _auth_template_response(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    set_csrf_cookie = False
    if settings.use_csrf_protection and not csrf_token_valid(csrf_token, settings):
        csrf_token = create_csrf_token(settings)
        set_csrf_cookie = True
    context = {**context, "csrf_token": csrf_token or ""}
    response = templates.TemplateResponse(
        request,
        template_name,
        context,
        status_code=status_code,
    )
    if set_csrf_cookie:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            max_age=settings.auth_cookie_max_age_seconds,
            httponly=False,
            samesite="lax",
            secure=settings.use_secure_auth_cookie,
        )
    return response


def _login_rate_limit_key(request: Request, email: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"login:{client}:{email or 'unknown'}"


def _signup_rate_limit_key(request: Request, email: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"signup:{client}:{email or 'unknown'}"


def _record_signup_failure(rate_limit_key: str, db: Session) -> int | None:
    return record_failed_attempt(
        rate_limit_key,
        max_attempts=settings.signup_rate_limit_attempts,
        window_seconds=settings.signup_rate_limit_window_seconds,
        lockout_seconds=settings.signup_rate_limit_lockout_seconds,
        db=db,
    )


def _signup_lockout_error(request: Request, values: dict[str, str], retry_after: int) -> HTMLResponse:
    return _signup_error(
        request,
        values,
        f"Too many signup attempts. Try again in {retry_after} seconds.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    )
