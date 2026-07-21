from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy.orm import Session

from app.auth import is_admin_user, require_current_user
from app.config import get_settings
from app.db.session import get_db
from app.models import AppUser
from app.routes.request_limits import InvalidJsonPayload, RequestTooLarge, json_payload_error_response, read_limited_json
from app.services.sync_service import SyncService

router = APIRouter(prefix="/api/sync", tags=["sync"])
settings = get_settings()


def require_sync_token(
    authorization: str | None = Header(default=None),
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
) -> None:
    if not settings.sync_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync is not enabled.")
    expected = str(settings.sync_api_token or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sync token is not configured.")
    bearer = ""
    if authorization and authorization.casefold().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    supplied = (x_sync_token or bearer or "").strip()
    if supplied != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sync token.")


@router.get("/status")
async def sync_status(_: None = Depends(require_sync_token), db: Session = Depends(get_db)) -> dict[str, object]:
    service = SyncService(db)
    service.ensure_schema()
    return {
        "enabled": True,
        "mode": settings.sync_mode,
        "device_id": service.device_id,
        "server_to_client_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "client_to_server_knowledge_scopes": ["validated_evidence"],
        "admin_client_to_server_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "excluded_knowledge_scopes": ["temporary"],
        "knowledge_index_dirty_scopes": service.knowledge_index_dirty_scopes(),
        "tables": [table.name for table in service.sync_tables()],
    }


@router.post("/pull")
async def sync_pull(_: None = Depends(require_sync_token), db: Session = Depends(get_db)) -> dict[str, object]:
    return SyncService(db).export_bundle()


@router.post("/push", response_model=None)
async def sync_push(
    request: Request,
    _: None = Depends(require_sync_token),
    db: Session = Depends(get_db),
):
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return payload
    result = SyncService(db).apply_bundle(payload)
    return {
        "error": False,
        "tables": result.tables,
        "inserted": result.inserted,
        "updated": result.updated,
        "skipped": result.skipped,
        "knowledge_scopes_dirty": list(result.knowledge_scopes_dirty),
    }


@router.post("/exchange", response_model=None)
async def sync_exchange(
    request: Request,
    _: None = Depends(require_sync_token),
    db: Session = Depends(get_db),
):
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return payload
    service = SyncService(db)
    admin_sync = service.admin_sync_allowed(payload)
    result = service.apply_bundle(payload)
    bundle = service.export_bundle(
        include_app_users=True,
        include_user_data=admin_sync,
    )
    return {
        "error": False,
        "applied": {
            "tables": result.tables,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped,
            "knowledge_scopes_dirty": list(result.knowledge_scopes_dirty),
        },
        "bundle": bundle,
    }


@router.post("/run")
async def sync_run(_: None = Depends(require_sync_token), db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return await SyncService(db).exchange_with_server()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": str(exc)}


@router.get("/client/status")
async def sync_client_status(
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    service = SyncService(db)
    configured = _client_sync_configured()
    return {
        "enabled": bool(settings.sync_enabled),
        "configured": configured,
        "mode": settings.sync_mode,
        "server_url": _redacted_server_url(),
        "device_id": service.device_id,
        "auto_on_startup": bool(settings.sync_auto_on_startup),
        "interval_seconds": int(settings.sync_interval_seconds or 0),
        "server_to_client_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "client_to_server_knowledge_scopes": ["validated_evidence"],
        "admin_client_to_server_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "excluded_knowledge_scopes": ["temporary"],
        "user_data_sync": service.user_data_sync_status(),
        "knowledge_index_dirty_scopes": service.knowledge_index_dirty_scopes() if configured else [],
    }


@router.post("/client/user-data")
async def sync_client_user_data(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return {"error": True, "detail": payload.body.decode("utf-8", errors="replace")}
    enabled = str(payload.get("enabled") or "").strip().casefold() in {"1", "true", "yes", "on"}
    status_data = SyncService(db).set_user_data_sync_enabled(enabled)
    return {"error": False, "user_data_sync": status_data}


@router.post("/client/run")
async def sync_client_run(
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    if not _client_sync_configured():
        return {
            "error": True,
            "detail": "Client sync is not configured. Set SYNC_ENABLED, SYNC_MODE=client, SYNC_SERVER_URL, and SYNC_API_TOKEN.",
        }
    try:
        admin_sync = is_admin_user(current_user)
        return await SyncService(db).exchange_with_server(
            include_admin_knowledge=admin_sync,
            admin_user_email=current_user.email if admin_sync else None,
            current_user_email=current_user.email,
        )
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": str(exc)}


async def _sync_payload_or_error(request: Request) -> dict[str, object] | JSONResponse:
    try:
        payload = await read_limited_json(request, settings.max_json_bytes * 20, "Sync payload")
    except (RequestTooLarge, InvalidJsonPayload) as exc:
        return json_payload_error_response(exc, settings.max_json_bytes * 20)
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": True, "detail": "Invalid sync payload."})
    return payload


def _client_sync_configured() -> bool:
    return (
        bool(settings.sync_enabled)
        and str(settings.sync_mode or "").strip().casefold() == "client"
        and bool(str(settings.sync_server_url or "").strip())
        and bool(str(settings.sync_api_token or "").strip())
    )


def _redacted_server_url() -> str:
    return str(settings.sync_server_url or "").strip().rstrip("/")
