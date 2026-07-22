from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
import httpx
import re
from sqlalchemy.orm import Session

from app.auth import is_admin_user, require_current_user
from app.config import get_settings
from app.db.session import get_db
from app.models import AppUser, Prompt
from app.routes.request_limits import InvalidJsonPayload, RequestTooLarge, json_payload_error_response, read_limited_json
from app.services.prompt_loader import clear_prompt_caches
from app.services.prompt_store import prompt_metadata
from app.services.sync_service import SyncService

router = APIRouter(prefix="/api/sync", tags=["sync"])
settings = get_settings()


def require_sync_token(
    authorization: str | None = Header(default=None),
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not settings.sync_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync is not enabled.")
    bearer = ""
    if authorization and authorization.casefold().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    supplied = (x_sync_token or bearer or "").strip()
    client = SyncService(db).sync_client_for_token(supplied)
    if client is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sync token.")
    client["_token"] = supplied
    return client


@router.get("/status")
async def sync_status(
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service = SyncService(db, sync_token=str(sync_client.get("_token") or ""))
    service.ensure_schema()
    return {
        "enabled": True,
        "mode": settings.sync_mode,
        "device_id": service.device_id,
        "sync_client": _sync_client_status(sync_client),
        "server_to_client_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "client_to_server_knowledge_scopes": ["validated_evidence"],
        "admin_client_to_server_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
        "excluded_knowledge_scopes": ["temporary"],
        "knowledge_index_dirty_scopes": service.knowledge_index_dirty_scopes(),
        "tables": [table.name for table in service.sync_tables()],
    }


@router.post("/pull")
async def sync_pull(
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    can_sync_user_data = bool(sync_client.get("can_sync_user_data"))
    return SyncService(db, sync_token=str(sync_client.get("_token") or "")).export_bundle(
        include_app_users=can_sync_user_data,
        include_user_data=can_sync_user_data,
    )


@router.post("/push", response_model=None)
async def sync_push(
    request: Request,
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
):
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return payload
    result = SyncService(db, sync_token=str(sync_client.get("_token") or "")).apply_bundle(
        payload,
        sync_client=sync_client,
    )
    return {
        "error": False,
        "tables": result.tables,
        "inserted": result.inserted,
        "updated": result.updated,
        "skipped": result.skipped,
        "knowledge_scopes_dirty": list(result.knowledge_scopes_dirty),
        "prompts_dirty": result.prompts_dirty,
    }


@router.post("/exchange", response_model=None)
async def sync_exchange(
    request: Request,
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
):
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return payload
    service = SyncService(db, sync_token=str(sync_client.get("_token") or ""))
    admin_sync = service.admin_sync_allowed(payload, sync_client=sync_client)
    result = service.apply_bundle(payload, sync_client=sync_client)
    can_sync_user_data = bool(sync_client.get("can_sync_user_data"))
    bundle = service.export_bundle(
        include_app_users=can_sync_user_data,
        include_user_data=can_sync_user_data,
    )
    return {
        "error": False,
        "applied": {
            "tables": result.tables,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped,
            "knowledge_scopes_dirty": list(result.knowledge_scopes_dirty),
            "prompts_dirty": result.prompts_dirty,
        },
        "bundle": bundle,
    }


@router.post("/run")
async def sync_run(_: dict[str, object] = Depends(require_sync_token), db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return await SyncService(db).exchange_with_server()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": str(exc)}


@router.post("/prompts")
async def sync_prompt_create(
    request: Request,
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not bool(sync_client.get("can_manage_prompts")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Prompt management permission is required.")
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return {"error": True, "detail": payload.body.decode("utf-8", errors="replace")}
    prompt_key = _clean_prompt_key(payload.get("prompt_key"))
    if not prompt_key:
        return {"error": True, "detail": "Prompt key is required."}
    if db.query(Prompt.id).filter(Prompt.prompt_key == prompt_key).first():
        return {"error": True, "detail": "A prompt already exists for this key."}
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"error": True, "detail": "Prompt content is required."}
    category, model, display_name = prompt_metadata(prompt_key)
    prompt = Prompt(
        prompt_key=prompt_key,
        category=category,
        model=model,
        display_name=str(payload.get("display_name") or "").strip()[:255] or display_name,
        content=content,
        source_path=None,
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    clear_prompt_caches()
    return {"error": False, "prompt": _prompt_detail(prompt), "detail": "Prompt created."}


@router.patch("/prompts/{prompt_id}")
async def sync_prompt_update(
    prompt_id: str,
    request: Request,
    sync_client: dict[str, object] = Depends(require_sync_token),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not bool(sync_client.get("can_manage_prompts")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Prompt management permission is required.")
    payload = await _sync_payload_or_error(request)
    if isinstance(payload, JSONResponse):
        return {"error": True, "detail": payload.body.decode("utf-8", errors="replace")}
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"error": True, "detail": "Prompt content is required."}
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return {"error": True, "detail": "Prompt not found."}
    prompt.content = content
    db.commit()
    db.refresh(prompt)
    clear_prompt_caches()
    return {"error": False, "prompt": _prompt_detail(prompt), "detail": "Prompt updated."}


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


def _sync_client_status(sync_client: dict[str, object]) -> dict[str, object]:
    return {
        "id": sync_client.get("id"),
        "client_name": sync_client.get("client_name"),
        "user_email": sync_client.get("user_email") or "",
        "can_sync_main_kb": bool(sync_client.get("can_sync_main_kb")),
        "can_sync_sector_prompts": bool(sync_client.get("can_sync_sector_prompts")),
        "can_reindex_sector_prompts": bool(sync_client.get("can_reindex_sector_prompts")),
        "can_manage_prompts": bool(sync_client.get("can_manage_prompts")),
        "can_sync_validated_kb": bool(sync_client.get("can_sync_validated_kb")),
        "can_sync_user_data": bool(sync_client.get("can_sync_user_data")),
    }


def _prompt_summary(prompt: Prompt) -> dict[str, object]:
    return {
        "id": prompt.id,
        "prompt_key": prompt.prompt_key,
        "category": prompt.category,
        "model": prompt.model,
        "display_name": prompt.display_name,
        "source_path": prompt.source_path,
        "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None,
        "content_preview": prompt.content[:180],
    }


def _prompt_detail(prompt: Prompt) -> dict[str, object]:
    return {
        **_prompt_summary(prompt),
        "content": prompt.content,
    }


def _clean_prompt_key(value: object) -> str:
    prompt_key = str(value or "").strip().replace("\\", "/")
    if (
        not prompt_key
        or len(prompt_key) > 255
        or prompt_key.startswith("/")
        or ".." in prompt_key.split("/")
        or not re.fullmatch(r"[A-Za-z0-9._:/-]+", prompt_key)
    ):
        return ""
    return prompt_key
