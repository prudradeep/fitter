import logging

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError

from app.auth import get_current_user
from app.config import get_settings
from app.database import (
    Base,
    engine,
    ensure_runtime_schema,
    validate_database_connection,
)
from app.models import AppUser
from app.resource_paths import resource_path
from app.routes.api import router as api_router
from app.routes.auth import router as auth_router
from app.services.coverage import get_coverage_rows

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, debug=settings.app_debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(resource_path("app/static"))), name="static")
templates = Jinja2Templates(directory=str(resource_path("app/templates")))

app.include_router(auth_router)
app.include_router(api_router)

@app.on_event("startup")
async def startup() -> None:
    validate_database_connection()
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()


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
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "coverage_rows": coverage_rows,
            "current_user": current_user,
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
