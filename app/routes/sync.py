from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
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
        "knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
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
    result = service.apply_bundle(payload)
    bundle = service.export_bundle()
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


async def _sync_payload_or_error(request: Request) -> dict[str, object] | JSONResponse:
    try:
        payload = await read_limited_json(request, settings.max_json_bytes * 20, "Sync payload")
    except (RequestTooLarge, InvalidJsonPayload) as exc:
        return json_payload_error_response(exc, settings.max_json_bytes * 20)
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": True, "detail": "Invalid sync payload."})
    return payload
