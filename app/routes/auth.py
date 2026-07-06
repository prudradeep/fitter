from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    clear_auth_cookie,
    hash_password,
    password_rule_errors,
    redirect_if_authenticated,
    set_auth_cookie,
    verify_password,
)
from app.database import get_db
from app.models import AppUser
from app.resource_paths import resource_path
from app.services.coverage import get_coverage_map_rows

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(resource_path("app/templates")))


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    redirect = redirect_if_authenticated(request, db)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
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
    user = db.scalar(select(AppUser).where(AppUser.email == email))

    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "page_title": "Dr Transition",
                "error": "Invalid email or password.",
                "email": email,
                "values": {},
                "initial_step": 1,
                "auth_mode": "login",
                "auth_open": True,
                "coverage_map_rows": get_coverage_map_rows(),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, user.id)
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    redirect = redirect_if_authenticated(request, db)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
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

    missing = [label for label, value in values.items() if not value]
    if missing or not password or not confirm_password:
        step = 2 if not any(field in missing for field in ["email", "name"]) and password else 1
        return _signup_error(request, values, "Please complete all signup fields.", step)
    if password != confirm_password:
        return _signup_error(request, values, "Passwords do not match.")
    password_errors = password_rule_errors(password)
    if password_errors:
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
        return _signup_error(request, values, "An account already exists for this email.")

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, user.id)
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookie(response)
    return response


def _signup_error(
    request: Request, values: dict[str, str], error: str, step: int = 1
) -> HTMLResponse:
    return templates.TemplateResponse(
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
        status_code=status.HTTP_400_BAD_REQUEST,
    )
