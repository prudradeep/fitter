import asyncio
import logging
import random
from time import perf_counter

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError

from app.auth import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, get_current_user, require_admin_user
from app.config import get_settings
from app.db.migrations_runtime import repair_partial_installer_schema, run_runtime_migrations
from app.db.session import SessionLocal, validate_database_connection
from app.models import AppUser
from app.observability import (
    configure_logging,
    increment_metric,
    metrics_snapshot,
    new_request_id,
    set_request_id,
)
from app.resource_paths import resource_path
from app.routes.api import router as api_router
from app.routes.auth import router as auth_router
from app.routes.sync import router as sync_router
from app.security import apply_security_headers, create_csrf_token, csrf_request_allowed, csrf_token_valid
from app.services.coverage import get_coverage_rows
from app.services.sync_service import SyncService

settings = get_settings()

configure_logging(
    getattr(logging, settings.log_level.upper(), logging.INFO),
    structured=settings.structured_logs,
)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, debug=settings.app_debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(resource_path("app/static"))), name="static")
templates = Jinja2Templates(directory=str(resource_path("app/templates")))

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(sync_router)


@app.middleware("http")
async def log_api_request(request: Request, call_next):
    started_at = perf_counter()
    request_id = request.headers.get(settings.request_id_header) or new_request_id()
    set_request_id(request_id)
    if _sync_server_blocks_app_api(request.url.path):
        response = JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": True, "detail": "Endpoint is not available on this sync server."},
            headers={settings.request_id_header: request_id},
        )
        apply_security_headers(response, request, settings)
        return response
    if not await csrf_request_allowed(request, settings):
        increment_metric("csrf_rejected")
        response = JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": True, "detail": "Invalid CSRF token or request origin."},
            headers={settings.request_id_header: request_id},
        )
        apply_security_headers(response, request, settings)
        return response
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (perf_counter() - started_at) * 1000
        increment_metric("request_errors")
        logger.exception(
            "API request failed method=%s path=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (perf_counter() - started_at) * 1000
    response.headers[settings.request_id_header] = request_id
    apply_security_headers(response, request, settings)
    increment_metric("requests_total")
    if response.status_code >= 500:
        increment_metric("responses_5xx")
    elif response.status_code >= 400:
        increment_metric("responses_4xx")
    if _should_log_access(request.url.path):
        logger.info(
            "API request method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    if (
        settings.use_csrf_protection
        and AUTH_COOKIE_NAME in request.cookies
        and not csrf_token_valid(request.cookies.get(CSRF_COOKIE_NAME), settings)
    ):
        response.set_cookie(
            CSRF_COOKIE_NAME,
            create_csrf_token(settings),
            max_age=settings.auth_cookie_max_age_seconds,
            httponly=False,
            samesite="lax",
            secure=settings.use_secure_auth_cookie,
        )
    return response


def _should_log_access(path: str) -> bool:
    if not settings.access_log_enabled:
        return False
    if path in settings.access_log_suppressed_path_set:
        return False
    sample_rate = max(0.0, min(1.0, float(settings.access_log_sample_rate)))
    if sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    return random.random() < sample_rate


def _sync_server_blocks_app_api(path: str) -> bool:
    if not settings.sync_enabled:
        return False
    if str(settings.sync_mode or "").strip().casefold() != "server":
        return False
    if settings.sync_server_expose_app_apis:
        return False
    if path in {"/health", "/health/live", "/health/ready", "/metrics"}:
        return False
    return not path.startswith("/api/sync")


def _sync_server_disables_llm_services() -> bool:
    return (
        settings.sync_enabled
        and str(settings.sync_mode or "").strip().casefold() == "server"
        and not settings.sync_server_expose_app_apis
    )


@app.on_event("startup")
async def startup() -> None:
    validate_database_connection()
    if settings.database_auto_migrate:
        run_runtime_migrations()
    else:
        logger.info("Database auto-migration is disabled; checking installer schema health only")
        repair_partial_installer_schema()
    if _sync_server_disables_llm_services():
        logger.info("Sync-only server mode enabled; skipping LLM-dependent startup work")
        app.state.client_sync_task = None
        return
    app.state.client_sync_task = asyncio.create_task(_client_sync_loop_after_startup())


async def _client_sync_loop_after_startup() -> None:
    if not _client_sync_configured():
        app.state.client_sync_task = None
        return
    if settings.sync_auto_on_startup:
        await _run_configured_client_sync("startup")
    interval = int(settings.sync_interval_seconds or 0)
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        if not _client_sync_configured():
            continue
        await _run_configured_client_sync("interval")


async def _run_configured_client_sync(trigger: str) -> None:
    try:
        with SessionLocal() as db:
            result = await SyncService(db).exchange_with_server()
        if result.get("error"):
            logger.warning("Client sync failed trigger=%s detail=%s", trigger, result.get("detail"))
            return
        logger.info("Client sync completed trigger=%s result=%s", trigger, result)
    except Exception:
        logger.exception("Client sync failed trigger=%s", trigger)


def _client_sync_configured() -> bool:
    return (
        settings.sync_enabled
        and str(settings.sync_mode or "").strip().casefold() == "client"
        and bool(str(settings.sync_server_url or "").strip())
        and bool(str(settings.sync_api_token or "").strip())
    )


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"error": True, "detail": "Database service is temporarily unavailable."},
    )


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request, current_user: AppUser | None = Depends(get_current_user)
) -> Response:
    if current_user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    coverage_rows = get_coverage_rows()
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_token_valid(csrf_token, settings):
        csrf_token = create_csrf_token(settings)
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "coverage_rows": coverage_rows,
            "current_user": current_user,
            "csrf_token": csrf_token,
            "sync_enabled": bool(settings.sync_enabled),
        },
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=settings.auth_cookie_max_age_seconds,
        httponly=False,
        samesite="lax",
        secure=settings.use_secure_auth_cookie,
    )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    try:
        validate_database_connection()
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "service": settings.app_name, "database": "unavailable"},
        )
    return JSONResponse(
        content={"status": "ready", "service": settings.app_name, "database": "ok"},
    )


@app.get("/metrics")
async def metrics(current_user: AppUser = Depends(require_admin_user)) -> dict[str, object]:
    _ = current_user
    return {"metrics": metrics_snapshot()}
