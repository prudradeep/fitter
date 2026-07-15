import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.auth import hash_password, password_rule_errors, require_admin_user, require_current_user, set_auth_cookie, verify_password
from app.config import get_settings
from app.db.session import get_db
from app.models import (
    AppUser,
    Country,
    KnowledgeChunk,
    KnowledgeDocument,
    Region,
    Sector,
    UserChatMessage,
    UserSession,
)
from app.routes.request_limits import (
    InvalidJsonPayload,
    RequestTooLarge,
    content_length as limited_content_length,
    json_payload_error_response,
    payload_too_large_response as limited_payload_too_large_response,
    read_limited_json,
    upload_too_large_response as limited_upload_too_large_response,
)
from app.schemas import ChatRequest, ChatResponse, Option
from app.services.chat_session import session_store
from app.services.chat_service import ChatService
from app.services.chat_options import POST_SECTOR_OPTIONS, option_list
from app.services.audit_log import record_audit_event
from app.services.hazard_effect_size import hazard_effect_size_rows
from app.services.hazard_ranking_service import HazardRankingService
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    SECTOR_PROMPT_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
    validated_scope_level,
)
from app.services.hazard_salience import country_hazard_salience
from app.services.rate_limit import record_failed_attempt, reset_rate_limit, retry_after_seconds
from app.services.sector_prompt_rag import SectorPromptRagService

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()


def _is_admin_user(user: AppUser) -> bool:
    return str(user.role or "").strip().casefold() == "admin"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    payload = await _chat_payload(request, db, current_user.id)
    session_key = _client_session_key(payload.session_id)
    session_data = _session_data_from_payload(
        {
            "session_id": session_key,
            "validation_mode": payload.validation_mode,
            "crowd_sourcing_enabled": payload.crowd_sourcing_enabled,
        }
    )
    user_session = _upsert_user_session(db, current_user.id, session_key, session_data)
    if payload.message.strip():
        _record_user_chat_message(db, user_session, "user", payload.message)
    return _client_state_chat_response(
        session_key,
        session_data,
        "Message persisted. Continue the local LLM workflow in the client app.",
    )


@router.post("/stats-deep-dive", response_model=ChatResponse)
async def stats_deep_dive(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    payload = await _chat_request_from_json(request, "Stats deep-dive payload")
    session_key = _client_session_key(payload.session_id)
    session_data = _session_data_from_payload(
        {
            "session_id": session_key,
            "validation_mode": payload.validation_mode,
            "crowd_sourcing_enabled": payload.crowd_sourcing_enabled,
        }
    )
    user_session = _upsert_user_session(db, current_user.id, session_key, session_data)
    if payload.message.strip():
        _record_user_chat_message(db, user_session, "user", payload.message)
    return _client_state_chat_response(
        session_key,
        session_data,
        "Stats deep-dive input persisted. Run the local LLM workflow in the client app.",
    )


@router.post("/auto-user-message")
async def auto_user_message(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _chat_request_from_json(request, "Auto-user payload")
    session_key = _client_session_key(payload.session_id)
    return {
        "error": True,
        "session_id": session_key,
        "message": "",
        "detail": "Auto-user generation is a client-side local LLM workflow.",
    }


@router.get("/sessions")
async def sessions(
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, str | int | None]]]:
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == current_user.id)
        .order_by(desc(UserSession.updated_at))
    ).all()
    return {
        "sessions": [
            {
                "session_id": row.session_key,
                "title": row.title or "New policy session",
                "country": row.country_id,
                "region": row.region_id,
                "sector": row.sector_id,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]
    }


@router.post("/sessions/state")
async def save_session_state(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Session state payload")
    if isinstance(payload, JSONResponse):
        return payload
    session_key = _client_session_key(payload.get("session_id"))
    session_data = _session_data_from_payload({**payload, "session_id": session_key})
    user_session = _upsert_user_session(db, current_user.id, session_key, session_data)
    messages = _messages_from_payload(payload)
    for message in messages:
        _record_user_chat_message(
            db,
            user_session,
            str(message.get("role") or "user")[:20],
            str(message.get("content") or ""),
            bool(message.get("is_error")),
        )
    return {
        "error": False,
        "session_id": session_key,
        "title": user_session.title or "New policy session",
        "messages": len(messages),
    }


@router.post("/selections/advance")
async def advance_selection(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Selection payload")
    if isinstance(payload, JSONResponse):
        return payload
    message = str(payload.get("message") or "").strip()
    if not message:
        return {"error": False, "matched": False, "detail": "No selection provided."}
    session_key = _client_session_key(payload.get("session_id"))
    session_data = _session_data_from_payload({**payload, "session_id": session_key})
    step = str(payload.get("step") or session_data.get("phase") or "").strip().casefold()

    if step in {"", "client_state", "wizard", "country"} or not session_data.get("country_id"):
        response = _advance_country_selection(db, session_key, session_data, message)
    elif step == "region" and session_data.get("country_id"):
        response = _advance_region_selection(db, session_key, session_data, message)
    elif step == "sector" and session_data.get("country_id"):
        response = _advance_sector_selection(db, session_key, session_data, message)
    else:
        return {"error": False, "matched": False, "detail": "Current step is not a selection step."}

    if not response.get("matched"):
        return response
    user_session = _upsert_user_session(db, current_user.id, session_key, session_data)
    _record_user_chat_message(db, user_session, "user", message)
    response["session_id"] = session_key
    return response


@router.get("/hazard-salience")
async def hazard_salience(
    country: str | None = Query(default=None, max_length=120),
    sector: str | None = Query(default=None, max_length=120),
    current_user: AppUser = Depends(require_current_user),
) -> dict[str, object]:
    return {
        "threshold": "> 12",
        "formula": "mean_concern * pct_high_concern / 100",
        "salience": country_hazard_salience(country=country, sector=sector),
    }


@router.get("/hazard-effect-size")
async def hazard_effect_size(
    sector: str | None = Query(default=None, max_length=120),
    hazard: str | None = Query(default=None, max_length=180),
    min_or: float = Query(default=1.0, gt=0),
    current_user: AppUser = Depends(require_current_user),
) -> dict[str, object]:
    return {
        "formula": "mean(abs(log(OR_k))) for OR_k > min_or",
        "min_or": min_or,
        "effect_sizes": hazard_effect_size_rows(sector=sector, hazard=hazard, min_or=min_or),
    }


@router.get("/hazards/ranked")
async def ranked_hazards(
    country_id: int = Query(..., gt=0),
    region_id: int | None = Query(default=None, gt=0),
    sector_id: int = Query(..., gt=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    country = db.get(Country, country_id)
    sector = db.get(Sector, sector_id)
    region = db.get(Region, region_id) if region_id else None
    if country is None or sector is None:
        return {"error": True, "detail": "Country or sector not found.", "hazards": []}
    if region_id and region is None:
        return {"error": True, "detail": "Region not found.", "hazards": []}
    ranking_service = HazardRankingService(db)
    hazards = await ranking_service.rank_hazards(
        country=country,
        region=region,
        sector=sector,
    )
    return {
        "error": False,
        "country": country.name,
        "region": region.name if region else None,
        "sector": sector.name,
        "formula": "salience_score + effect_size_score + reach_score",
        "hazards": hazards,
    }


@router.get("/sessions/{session_key}")
async def restore_session(
    session_key: str,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_session = db.scalar(
        select(UserSession).where(
            UserSession.session_key == session_key,
            UserSession.user_id == current_user.id,
        )
    )
    if user_session is None:
        return {"error": True, "detail": "Session not found."}

    session_data = {}
    if user_session.session_data:
        try:
            session_data = json.loads(user_session.session_data)
        except json.JSONDecodeError:
            session_data = {}
    chat_session = session_store.put(session_key, session_data)
    chat_session.session_key = session_key
    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
    current_prompt = service._repeat_current_options(session_key, chat_session, "", False)
    service._attach_other_options(current_prompt, chat_session)
    messages = db.scalars(
        select(UserChatMessage)
        .where(UserChatMessage.user_session_id == user_session.id)
        .order_by(UserChatMessage.created_at, UserChatMessage.id)
    ).all()
    return {
        "error": False,
        "session_id": session_key,
        "title": user_session.title or "New policy session",
        "session": current_prompt.session.model_dump(),
        "step": current_prompt.step,
        "options": [option.model_dump() for option in current_prompt.options],
        "other_options": current_prompt.other_options,
        "input_mode": current_prompt.input_mode,
        "messages": [
            {
                "role": message.role,
                "content": service._chat_message_display_content(message.content),
                "is_error": message.is_error,
            }
            for message in messages
        ],
    }


@router.get("/sessions/{session_key}/export")
async def export_session(
    session_key: str,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_session = db.scalar(
        select(UserSession).where(
            UserSession.session_key == session_key,
            UserSession.user_id == current_user.id,
        )
    )
    if user_session is None:
        return {"error": True, "detail": "Session not found."}

    session_data: dict[str, object] = {}
    if user_session.session_data:
        try:
            parsed = json.loads(user_session.session_data)
            if isinstance(parsed, dict):
                session_data = parsed
        except json.JSONDecodeError:
            session_data = {}

    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
    rows = db.scalars(
        select(UserChatMessage)
        .where(UserChatMessage.user_session_id == user_session.id)
        .order_by(UserChatMessage.created_at, UserChatMessage.id)
    ).all()
    return {
        "error": False,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session": {
            "session_id": user_session.session_key,
            "title": user_session.title or "New policy session",
            "country_id": user_session.country_id,
            "region_id": user_session.region_id,
            "sector_id": user_session.sector_id,
            "created_at": user_session.created_at.isoformat() if user_session.created_at else None,
            "updated_at": user_session.updated_at.isoformat() if user_session.updated_at else None,
            "data": session_data,
        },
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": service._chat_message_display_content(message.content),
                "raw_content": (
                    message.content
                    if _is_admin_user(current_user)
                    else service._chat_message_display_content(message.content)
                ),
                "is_error": message.is_error,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in rows
        ],
    }


@router.post("/sessions/import")
async def import_session(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    too_large = payload_too_large_response(
        request,
        settings.max_session_import_bytes,
        "Session export",
    )
    if too_large is not None:
        return too_large

    form = await request.form()
    upload = form.get("file")
    filename = str(getattr(upload, "filename", "") or "").strip()
    if not filename.casefold().endswith(".json") or not hasattr(upload, "read"):
        return {"error": True, "detail": "Please choose an exported session JSON file."}
    too_large = upload_too_large_response(
        upload,
        settings.max_session_import_bytes,
        "Session export",
    )
    if too_large is not None:
        return too_large

    content = await upload.read()
    if len(content) > settings.max_session_import_bytes:
        return {"error": True, "detail": "Session export is too large to import."}
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": True, "detail": "Could not read exported session JSON."}
    if not isinstance(payload, dict):
        return {"error": True, "detail": "Invalid session export format."}

    exported_session = payload.get("session")
    if not isinstance(exported_session, dict):
        return {"error": True, "detail": "Export does not include session data."}
    session_data = exported_session.get("data")
    if not isinstance(session_data, dict):
        session_data = {}

    new_session_key = str(uuid4())
    session_data["session_key"] = new_session_key
    title = str(exported_session.get("title") or "Imported session").strip()[:220]
    user_session = UserSession(
        session_key=new_session_key,
        title=title or "Imported session",
        title_is_manual=True,
        session_data=json.dumps(session_data, default=str),
        user_id=current_user.id,
        country_id=_optional_int(exported_session.get("country_id") or session_data.get("country_id")),
        region_id=_optional_int(exported_session.get("region_id") or session_data.get("region_id")),
        sector_id=_optional_int(exported_session.get("sector_id") or session_data.get("sector_id")),
    )
    db.add(user_session)
    db.flush()

    imported_messages = 0
    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            content_value = str(item.get("raw_content") or item.get("content") or "").strip()
            if not content_value:
                continue
            role = str(item.get("role") or "user").strip().casefold()
            if role == "assistant":
                role = "bot"
            if role not in {"user", "bot", "assistant", "system"}:
                role = "user"
            db.add(
                UserChatMessage(
                    user_session_id=user_session.id,
                    role=role[:20],
                    content=content_value,
                    is_error=bool(item.get("is_error")),
                )
            )
            imported_messages += 1

    db.commit()
    session_store.put(new_session_key, session_data)
    record_audit_event(
        db,
        user=current_user,
        action="session.import",
        request=request,
        target_type="session",
        target_id=new_session_key,
        details={"title": user_session.title, "messages": imported_messages, "source_filename": filename},
    )
    return {
        "error": False,
        "session_id": new_session_key,
        "title": user_session.title,
        "messages": imported_messages,
    }


@router.patch("/sessions/{session_key}")
async def rename_session(
    session_key: str,
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Session rename")
    if isinstance(payload, JSONResponse):
        return payload
    title = str(payload.get("title") or "").strip()
    if not title:
        return {"error": True, "detail": "Session title is required."}

    user_session = db.scalar(
        select(UserSession).where(
            UserSession.session_key == session_key,
            UserSession.user_id == current_user.id,
        )
    )
    if user_session is None:
        return {"error": True, "detail": "Session not found."}

    user_session.title = title[:220]
    user_session.title_is_manual = True
    db.commit()
    return {
        "error": False,
        "session_id": session_key,
        "title": user_session.title,
    }


@router.patch("/profile/password")
async def change_password(
    request: Request,
    response: Response,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Password change")
    if isinstance(payload, JSONResponse):
        return payload
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm_password = str(payload.get("confirm_password") or "")
    rate_limit_key = _password_rate_limit_key(request, current_user.id)
    retry_after = retry_after_seconds(rate_limit_key, db)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={
                "error": True,
                "detail": f"Too many password change attempts. Try again in {retry_after} seconds.",
            },
        )

    if not verify_password(current_password, current_user.password_hash):
        retry_after = record_failed_attempt(
            rate_limit_key,
            max_attempts=settings.password_rate_limit_attempts,
            window_seconds=settings.password_rate_limit_window_seconds,
            lockout_seconds=settings.password_rate_limit_lockout_seconds,
            db=db,
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "detail": f"Too many password change attempts. Try again in {retry_after} seconds.",
                },
            )
        return {"error": True, "detail": "Current password is incorrect."}
    if new_password != confirm_password:
        return {"error": True, "detail": "New passwords do not match."}
    password_errors = password_rule_errors(new_password)
    if password_errors:
        return {
            "error": True,
            "detail": "Password must include: " + ", ".join(password_errors) + ".",
        }

    user = db.scalar(select(AppUser).where(AppUser.id == current_user.id))
    if user is None:
        return {"error": True, "detail": "User not found."}
    user.password_hash = hash_password(new_password)
    user.session_version = int(user.session_version or 1) + 1
    db.commit()
    db.refresh(user)
    set_auth_cookie(response, user)
    reset_rate_limit(rate_limit_key, db)
    record_audit_event(
        db,
        user=user,
        action="password.change",
        request=request,
        target_type="user",
        target_id=user.id,
    )
    return {"error": False, "detail": "Password updated."}


@router.get("/knowledge")
async def knowledge_documents(
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    return {"documents": service.list_documents()}


@router.post("/knowledge/upload")
async def knowledge_upload(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    too_large = payload_too_large_response(
        request,
        settings.max_upload_bytes,
        "Knowledge upload",
    )
    if too_large is not None:
        return too_large

    form = await request.form()
    files = [item for key in ("files", "file") for item in form.getlist(key)]
    if not files:
        return {"error": True, "detail": "Please choose one or more PDF, DOCX, MD, or TXT files."}

    _ = current_user
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    total_chunks = 0
    for file in files:
        filename = getattr(file, "filename", "")
        if not isinstance(filename, str) or not filename.strip() or not hasattr(file, "read"):
            failures.append({"source": "file", "detail": "Skipped an empty file field."})
            continue
        filename = filename.strip()
        if not filename.casefold().endswith((".pdf", ".docx", ".md", ".txt")):
            failures.append({"source": filename, "detail": "Supported file types are PDF, DOCX, MD, and TXT."})
            continue
        too_large = upload_too_large_response(file, settings.max_upload_bytes, "Knowledge upload")
        if too_large is not None:
            return too_large
        content = await file.read()
        try:
            result = await service.ingest_file(
                filename,
                content,
                allow_lexical_only=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"source": filename, "detail": str(exc)})
            continue
        if result.get("error"):
            failures.append({"source": filename, "detail": str(result.get("detail") or "Could not ingest file.")})
            continue
        total_chunks += int(result.get("chunks") or 0)
        results.append(result)
    record_audit_event(
        db,
        user=current_user,
        action="knowledge.upload",
        request=request,
        target_type="knowledge_document",
        details={
            "documents": [result.get("id") for result in results],
            "uploaded": len(results),
            "failed": len(failures),
            "chunks": total_chunks,
        },
    )
    return {
        "error": bool(failures) and not results,
        "detail": _knowledge_ingest_detail("file", len(results), total_chunks, failures),
        "documents": results,
        "failures": failures,
        "chunks": total_chunks,
    }


@router.post("/knowledge/url")
async def knowledge_url(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Knowledge URL payload")
    if isinstance(payload, JSONResponse):
        return payload
    urls = _knowledge_urls_from_payload(payload)
    title = str(payload.get("title") or "").strip() or None
    if not urls:
        return {"error": True, "detail": "At least one URL is required."}
    _ = current_user
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    total_chunks = 0
    for url in urls:
        try:
            result = await service.ingest_url(
                url,
                title if len(urls) == 1 else None,
                allow_lexical_only=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"source": url, "detail": str(exc)})
            continue
        if result.get("error"):
            failures.append({"source": url, "detail": str(result.get("detail") or "Could not ingest URL.")})
            continue
        total_chunks += int(result.get("chunks") or 0)
        results.append(result)
    record_audit_event(
        db,
        user=current_user,
        action="knowledge.url_import",
        request=request,
        target_type="knowledge_document",
        details={
            "documents": [result.get("id") for result in results],
            "urls": urls,
            "imported": len(results),
            "failed": len(failures),
            "chunks": total_chunks,
        },
    )
    return {
        "error": bool(failures) and not results,
        "detail": _knowledge_ingest_detail("URL", len(results), total_chunks, failures),
        "documents": results,
        "failures": failures,
        "chunks": total_chunks,
    }


@router.post("/knowledge/search")
async def knowledge_search(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Knowledge search payload")
    if isinstance(payload, JSONResponse):
        return payload
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"error": True, "detail": "Search query is required.", "results": []}
    _ = current_user
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    try:
        return {"error": False, "results": await service.search(query, 10)}
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": f"Could not search knowledge base: {exc}", "results": []}


@router.post("/sector-prompts/reindex")
async def sector_prompts_reindex(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = await SectorPromptRagService(db).rebuild()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": f"Could not reindex sector prompts: {exc}"}
    record_audit_event(
        db,
        user=current_user,
        action="sector_prompts.reindex",
        request=request,
        target_type="sector_prompts",
        details={"error": bool(result.get("error")), **result},
    )
    return {"error": bool(result.get("error")), **result}


@router.post("/sector-prompts/search")
async def sector_prompts_search(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    payload = await _json_payload_or_error(request, "Sector prompt search payload")
    if isinstance(payload, JSONResponse):
        return payload
    sector = str(payload.get("sector") or "").strip()
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"error": True, "detail": "Search query is required.", "results": []}
    try:
        results = await SectorPromptRagService(db).search(sector, query, limit=10)
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": f"Could not search sector prompts: {exc}", "results": []}
    return {"error": False, "results": results}


@router.get("/knowledge/sync/manifest")
async def knowledge_sync_manifest(
    scope: str = Query(default=MAIN_KB_SCOPE, max_length=40),
    country_id: int | None = Query(default=None, gt=0),
    region_id: int | None = Query(default=None, gt=0),
    sector_id: int | None = Query(default=None, gt=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    scope = _knowledge_sync_scope(scope)
    filters = _knowledge_sync_filters(
        scope,
        current_user=current_user,
        country_id=country_id,
        region_id=region_id,
        sector_id=sector_id,
    )
    rows = db.scalars(
        select(KnowledgeDocument)
        .where(*filters)
        .order_by(KnowledgeDocument.id)
    ).all()
    chunk_rows_by_document: dict[int, list[KnowledgeChunk]] = {}
    if rows:
        document_ids = [row.id for row in rows]
        chunks = db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id.in_(document_ids))
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()
        for chunk in chunks:
            chunk_rows_by_document.setdefault(chunk.document_id, []).append(chunk)
    max_id = max([0, *[row.id for row in rows]])
    documents = [
        {
            "id": row.id,
            "checksum": _knowledge_document_manifest_checksum(row, chunk_rows_by_document.get(row.id, [])),
            "updated_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {
        "error": False,
        "scope": scope,
        "cursor": max_id,
        "checksum": f"{scope}:{len(documents)}:{max_id}",
        "documents": documents,
    }


@router.get("/knowledge/sync")
async def knowledge_sync(
    scope: str = Query(default=MAIN_KB_SCOPE, max_length=40),
    since_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, gt=0, le=1000),
    include_chunks: bool = Query(default=True),
    country_id: int | None = Query(default=None, gt=0),
    region_id: int | None = Query(default=None, gt=0),
    sector_id: int | None = Query(default=None, gt=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    scope = _knowledge_sync_scope(scope)
    filters = _knowledge_sync_filters(
        scope,
        current_user=current_user,
        country_id=country_id,
        region_id=region_id,
        sector_id=sector_id,
    )
    rows = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.id > since_id, *filters)
        .order_by(KnowledgeDocument.id)
        .limit(limit)
    ).all()
    chunk_rows_by_document: dict[int, list[KnowledgeChunk]] = {}
    if include_chunks and rows:
        document_ids = [row.id for row in rows]
        chunks = db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id.in_(document_ids))
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()
        for chunk in chunks:
            chunk_rows_by_document.setdefault(chunk.document_id, []).append(chunk)
    next_cursor = max([since_id, *[row.id for row in rows]])
    return {
        "error": False,
        "scope": scope,
        "next_cursor": next_cursor,
        "has_more": len(rows) == limit,
        "deleted_document_ids": [],
        "documents": [
            _knowledge_document_sync_payload(
                row,
                chunk_rows_by_document.get(row.id, []) if include_chunks else [],
            )
            for row in rows
        ],
    }


@router.post("/validated-evidence/promote")
async def validated_evidence_promote(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Validated evidence payload")
    if isinstance(payload, JSONResponse):
        return payload
    title = str(payload.get("title") or "").strip()
    chunks_payload = payload.get("chunks")
    if not title:
        return {"error": True, "detail": "Validated evidence title is required."}
    if not isinstance(chunks_payload, list) or not chunks_payload:
        return {"error": True, "detail": "At least one validated evidence chunk is required."}

    country_id = _optional_int(payload.get("country_id"))
    region_id = _optional_int(payload.get("region_id"))
    sector_id = _optional_int(payload.get("sector_id"))
    source_type = str(payload.get("source_type") or "validated_user_evidence").strip()[:40]
    source_uri = str(payload.get("source_uri") or "").strip() or None
    scope_level = validated_scope_level(country_id, region_id)
    session_key = str(payload.get("session_key") or "").strip()[:64] or None
    validation_summary = str(payload.get("validation_summary") or "").strip()
    if validation_summary:
        title = f"{title} [{validation_summary[:80]}]"

    document = KnowledgeDocument(
        user_id=current_user.id,
        title=title[:255],
        source_type=source_type or "validated_user_evidence",
        source_uri=source_uri,
        scope=VALIDATED_EVIDENCE_SCOPE,
        session_key=session_key,
        scope_level=scope_level,
        country_id=country_id,
        region_id=region_id,
        sector_id=sector_id,
    )
    db.add(document)
    db.flush()

    chunks_added = 0
    for index, item in enumerate(chunks_payload):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        chunk_source_uri = str(item.get("source_uri") or "").strip() or source_uri
        chunk_source_type = str(item.get("source_type") or source_type).strip()[:40]
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id=current_user.id,
                chunk_index=_optional_int(item.get("chunk_index")) or index,
                content=content,
                source_type=chunk_source_type or "validated_user_evidence",
                source_uri=chunk_source_uri,
                page_number=_optional_int(item.get("page_number")),
                scope_level=scope_level,
                country_id=country_id,
                region_id=region_id,
                sector_id=sector_id,
            )
        )
        chunks_added += 1
    if not chunks_added:
        db.rollback()
        return {"error": True, "detail": "At least one validated evidence chunk must include content."}

    db.commit()
    db.refresh(document)
    stored_chunks = db.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document.id)
        .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
    ).all()
    record_audit_event(
        db,
        user=current_user,
        action="validated_evidence.promote",
        request=request,
        target_type="knowledge_document",
        target_id=document.id,
        details={
            "chunks": chunks_added,
            "country_id": country_id,
            "region_id": region_id,
            "sector_id": sector_id,
            "session_key": session_key,
        },
    )
    return {
        "error": False,
        "document": _knowledge_document_sync_payload(document, stored_chunks),
        "chunks": chunks_added,
        "version": document.created_at.isoformat() if document.created_at else None,
    }


@router.delete("/knowledge/{document_id}")
async def knowledge_delete(
    document_id: int,
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    try:
        deleted = await service.delete_document(document_id)
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "deleted": False, "detail": f"Could not delete document: {exc}"}
    if deleted:
        record_audit_event(
            db,
            user=current_user,
            action="knowledge.delete",
            request=request,
            target_type="knowledge_document",
            target_id=document_id,
        )
    return {"error": not deleted, "deleted": deleted}


async def _chat_payload(request: Request, db: Session, user_id: int) -> ChatRequest:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        payload = await _chat_request_from_json(request, "Chat payload")
        payload.validation_mode = _validation_mode(payload.validation_mode)
        return payload

    too_large = payload_too_large_response(
        request,
        settings.max_upload_bytes,
        "Evidence upload",
    )
    if too_large is not None:
        raise HTTPException(status_code=413, detail="Evidence upload is too large.")

    form = await request.form()
    message = str(form.get("message") or "")
    session_id = str(form.get("session_id") or "") or None
    validation_mode = _validation_mode(str(form.get("validation_mode") or ""))
    crowd_sourcing_enabled = _truthy(form.get("crowd_sourcing_enabled"))

    evidence_url = str(form.get("evidence_url") or "").strip()
    if evidence_url:
        raise HTTPException(
            status_code=400,
            detail="Temporary evidence must be handled by the client app before validation.",
        )

    evidence_file = form.get("evidence_file")
    filename = getattr(evidence_file, "filename", "")
    if isinstance(filename, str) and filename.strip():
        filename = filename.strip()
        if _allowed_evidence_file(filename) and hasattr(evidence_file, "read"):
            too_large = upload_too_large_response(
                evidence_file,
                settings.max_upload_bytes,
                "Evidence upload",
            )
            if too_large is not None:
                raise HTTPException(status_code=413, detail="Evidence upload is too large.")
            raise HTTPException(
                status_code=400,
                detail="Temporary evidence must be handled by the client app before validation.",
            )

    return ChatRequest(
        message=message,
        session_id=session_id,
        validation_mode=validation_mode,
        crowd_sourcing_enabled=crowd_sourcing_enabled,
    )


def _validation_mode(value: object) -> str:
    return "easy" if str(value or "").strip().casefold() == "easy" else "strict"


async def _json_payload_or_error(request: Request, label: str) -> dict[str, object] | JSONResponse:
    try:
        return await read_limited_json(request, settings.max_json_bytes, label)
    except (RequestTooLarge, InvalidJsonPayload) as exc:
        return json_payload_error_response(exc, settings.max_json_bytes)


async def _chat_request_from_json(request: Request, label: str) -> ChatRequest:
    try:
        payload_data = await read_limited_json(request, settings.max_json_bytes, label)
    except RequestTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidJsonPayload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = ChatRequest.model_validate(payload_data)
    payload.validation_mode = _validation_mode(payload.validation_mode)
    return payload


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _knowledge_urls_from_payload(payload: dict[str, object]) -> list[str]:
    values: list[str] = []
    raw_urls = payload.get("urls")
    if isinstance(raw_urls, list):
        values.extend(str(item or "") for item in raw_urls)
    raw_url = payload.get("url")
    if raw_url is not None:
        values.extend(re.split(r"[\n,]+", str(raw_url)))
    seen: set[str] = set()
    urls: list[str] = []
    for value in values:
        url = value.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _knowledge_sync_scope(scope: str) -> str:
    value = str(scope or "").strip()
    allowed = {MAIN_KB_SCOPE, SECTOR_PROMPT_SCOPE, VALIDATED_EVIDENCE_SCOPE}
    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Knowledge sync scope must be main, sector_prompt, or validated_evidence.",
        )
    return value


def _knowledge_sync_filters(
    scope: str,
    *,
    current_user: AppUser,
    country_id: int | None,
    region_id: int | None,
    sector_id: int | None,
) -> list[object]:
    filters: list[object] = [KnowledgeDocument.scope == scope]
    if scope in {MAIN_KB_SCOPE, SECTOR_PROMPT_SCOPE}:
        filters.append(KnowledgeDocument.user_id.is_(None))
        return filters

    filters.append(
        (KnowledgeDocument.user_id.is_(None)) | (KnowledgeDocument.user_id == current_user.id)
    )
    if country_id is not None:
        filters.append(
            (KnowledgeDocument.country_id == country_id)
            | (KnowledgeDocument.scope_level == "global")
            | (KnowledgeDocument.country_id.is_(None))
        )
    if region_id is not None:
        filters.append(
            (KnowledgeDocument.region_id == region_id)
            | (KnowledgeDocument.region_id.is_(None))
            | (KnowledgeDocument.scope_level == "global")
        )
    if sector_id is not None:
        filters.append((KnowledgeDocument.sector_id == sector_id) | (KnowledgeDocument.sector_id.is_(None)))
    return filters


def _knowledge_document_sync_payload(
    document: KnowledgeDocument,
    chunks: list[KnowledgeChunk],
) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "checksum": _knowledge_document_manifest_checksum(document, chunks),
        "source_type": document.source_type,
        "source_uri": document.source_uri,
        "scope": document.scope,
        "scope_level": document.scope_level,
        "session_key": document.session_key,
        "country_id": document.country_id,
        "region_id": document.region_id,
        "sector_id": document.sector_id,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "chunks": [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "page_number": chunk.page_number,
                "scope_level": chunk.scope_level,
                "country_id": chunk.country_id,
                "region_id": chunk.region_id,
                "sector_id": chunk.sector_id,
                "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
            }
            for chunk in chunks
        ],
    }


def _knowledge_document_manifest_checksum(document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> str:
    parts = [
        str(document.id),
        document.title or "",
        document.source_type or "",
        document.source_uri or "",
        document.created_at.isoformat() if document.created_at else "",
        str(document.country_id or ""),
        str(document.region_id or ""),
        str(document.sector_id or ""),
    ]
    for chunk in chunks:
        parts.extend(
            [
                str(chunk.id),
                str(chunk.chunk_index),
                chunk.content or "",
                chunk.source_type or "",
                chunk.source_uri or "",
                str(chunk.page_number or ""),
            ]
        )
    return "|".join(parts)


def _client_session_key(value: object) -> str:
    session_key = str(value or "").strip()
    return session_key[:64] if session_key else str(uuid4())


def _session_data_from_payload(payload: dict[str, object]) -> dict[str, object]:
    raw_session = payload.get("session")
    session_data = dict(raw_session) if isinstance(raw_session, dict) else {}
    raw_data = payload.get("data")
    if isinstance(raw_data, dict):
        session_data.update(raw_data)
    session_data["session_key"] = str(payload.get("session_id") or session_data.get("session_key") or "")
    for key in ("country_id", "region_id", "sector_id"):
        value = _optional_int(payload.get(key) if key in payload else session_data.get(key))
        if value is not None:
            session_data[key] = value
    for key in ("country", "region", "sector", "phase"):
        if key in payload and payload.get(key) is not None:
            session_data[key] = str(payload.get(key) or "").strip()
    session_data["validation_mode"] = _validation_mode(
        payload.get("validation_mode") or session_data.get("validation_mode")
    )
    session_data["crowd_sourcing_enabled"] = bool(
        payload.get("crowd_sourcing_enabled", session_data.get("crowd_sourcing_enabled", False))
    )
    return session_data


def _upsert_user_session(
    db: Session,
    user_id: int,
    session_key: str,
    session_data: dict[str, object],
) -> UserSession:
    user_session = db.scalar(select(UserSession).where(UserSession.session_key == session_key))
    if user_session is not None and user_session.user_id not in {None, user_id}:
        raise HTTPException(status_code=404, detail="Session not found.")
    if user_session is None:
        user_session = UserSession(
            session_key=session_key,
            user_id=user_id,
        )
        db.add(user_session)
    user_session.user_id = user_id
    title = str(session_data.get("title") or "").strip()
    if title:
        user_session.title = title[:220]
        user_session.title_is_manual = bool(session_data.get("title_is_manual", False))
    elif not user_session.title_is_manual:
        user_session.title = _session_title_from_data(session_data)
    user_session.country_id = _optional_int(session_data.get("country_id"))
    user_session.region_id = _optional_int(session_data.get("region_id"))
    user_session.sector_id = _optional_int(session_data.get("sector_id"))
    user_session.session_data = json.dumps(session_data, default=str)
    db.commit()
    db.refresh(user_session)
    session_store.put(session_key, session_data)
    return user_session


def _record_user_chat_message(
    db: Session,
    user_session: UserSession,
    role: str,
    content: str,
    is_error: bool = False,
) -> None:
    content = str(content or "").strip()
    if not content:
        return
    role = str(role or "user").strip().casefold()
    if role not in {"user", "bot", "assistant", "system"}:
        role = "user"
    db.add(
        UserChatMessage(
            user_session_id=user_session.id,
            role=role[:20],
            content=content,
            is_error=bool(is_error),
        )
    )
    db.commit()


def _messages_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        return [message for message in messages if isinstance(message, dict)]
    message = str(payload.get("message") or "").strip()
    if message:
        return [{"role": payload.get("role") or "user", "content": message}]
    return []


def _client_state_chat_response(
    session_key: str,
    session_data: dict[str, object],
    bot_message: str,
) -> ChatResponse:
    chat_session = session_store.put(session_key, session_data)
    chat_session.session_key = session_key
    return ChatResponse(
        session_id=session_key,
        step=str(session_data.get("phase") or "client_state"),
        bot_message=bot_message,
        options=[],
        session=chat_session.summary(),
        input_mode="client",
        error=False,
    )


def _advance_country_selection(
    db: Session,
    session_key: str,
    session_data: dict[str, object],
    message: str,
) -> dict[str, object]:
    countries = db.scalars(
        select(Country)
        .options(selectinload(Country.regions), selectinload(Country.sectors))
        .order_by(Country.name)
    ).all()
    country = _match_named_row(countries, message)
    if country is None:
        return {"error": False, "matched": False, "detail": "No country matched the selection."}
    session_data.update(
        {
            "country_id": country.id,
            "country": country.name,
            "region_id": None,
            "region": None,
            "sector_id": None,
            "sector": None,
        }
    )
    regions = sorted(country.regions, key=lambda row: row.name)
    if regions:
        session_data["phase"] = "region"
        return _selection_response(
            session_key,
            session_data,
            step="region",
            bot_message=f"Country set to {country.name}. Choose a region.",
            options=option_list(list(regions)),
        )
    session_data["region"] = "National scope"
    session_data["phase"] = "sector"
    sectors = sorted(country.sectors, key=lambda row: row.name)
    return _selection_response(
        session_key,
        session_data,
        step="sector",
        bot_message=f"Country set to {country.name}. National scope selected. Choose a sector.",
        options=option_list(list(sectors)),
    )


def _advance_region_selection(
    db: Session,
    session_key: str,
    session_data: dict[str, object],
    message: str,
) -> dict[str, object]:
    country = db.scalar(
        select(Country)
        .options(selectinload(Country.regions), selectinload(Country.sectors))
        .where(Country.id == _optional_int(session_data.get("country_id")))
    )
    if country is None:
        return {"error": False, "matched": False, "detail": "Country must be selected first."}
    region = _match_named_row(sorted(country.regions, key=lambda row: row.name), message)
    if region is None:
        return {"error": False, "matched": False, "detail": "No region matched the selection."}
    session_data.update(
        {
            "region_id": region.id,
            "region": region.name,
            "sector_id": None,
            "sector": None,
            "phase": "sector",
        }
    )
    sectors = sorted(country.sectors, key=lambda row: row.name)
    return _selection_response(
        session_key,
        session_data,
        step="sector",
        bot_message=f"Region set to {region.name}. Choose a sector.",
        options=option_list(list(sectors)),
    )


def _advance_sector_selection(
    db: Session,
    session_key: str,
    session_data: dict[str, object],
    message: str,
) -> dict[str, object]:
    country = db.scalar(
        select(Country)
        .options(selectinload(Country.sectors))
        .where(Country.id == _optional_int(session_data.get("country_id")))
    )
    if country is None:
        return {"error": False, "matched": False, "detail": "Country must be selected first."}
    sector = _match_named_row(sorted(country.sectors, key=lambda row: row.name), message)
    if sector is None:
        return {"error": False, "matched": False, "detail": "No sector matched the selection."}
    session_data.update(
        {
            "sector_id": sector.id,
            "sector": sector.name,
            "phase": "post_sector",
        }
    )
    return _selection_response(
        session_key,
        session_data,
        step="post_sector",
        bot_message=(
            f"Sector set to {sector.name}. You can start mitigation planning, add a new hazard, "
            "or refresh local hazard context."
        ),
        options=POST_SECTOR_OPTIONS,
    )


def _selection_response(
    session_key: str,
    session_data: dict[str, object],
    *,
    step: str,
    bot_message: str,
    options: list[Option],
) -> dict[str, object]:
    chat_session = session_store.put(session_key, session_data)
    chat_session.session_key = session_key
    return {
        "error": False,
        "matched": True,
        "session_id": session_key,
        "step": step,
        "bot_message": bot_message,
        "voice_summary": bot_message,
        "options": [option.model_dump() for option in options],
        "other_options": [],
        "session": chat_session.summary().model_dump(),
        "input_mode": "text",
        "input_values": {},
        "validation_details": None,
    }


def _match_named_row(rows: list[object], message: str) -> object | None:
    normalized = _normalize_selection_text(message)
    for row in rows:
        if _normalize_selection_text(getattr(row, "name", "")) == normalized:
            return row
    return None


def _normalize_selection_text(value: object) -> str:
    return " ".join(
        "".join(character.casefold() if character.isalnum() else " " for character in str(value or "")).split()
    )


def _session_title_from_data(session_data: dict[str, object]) -> str:
    parts = [
        str(session_data.get(key) or "").strip()
        for key in ("country", "region", "sector", "selected_hazard")
        if str(session_data.get(key) or "").strip()
    ]
    return " / ".join(parts[:4]) or "New policy session"


def payload_too_large_response(
    request: Request,
    max_bytes: int,
    label: str,
) -> JSONResponse | None:
    return limited_payload_too_large_response(request, max_bytes, label)


def upload_too_large_response(upload: object, max_bytes: int, label: str) -> JSONResponse | None:
    return limited_upload_too_large_response(upload, max_bytes, label)


def _content_length(request: Request) -> int | None:
    return limited_content_length(request)


def _password_rate_limit_key(request: Request, user_id: int) -> str:
    client = request.client.host if request.client else "unknown"
    return f"password:{client}:{user_id}"


def _knowledge_ingest_detail(
    source_label: str, ingested_count: int, chunks: int, failures: list[dict[str, str]]
) -> str:
    if ingested_count and failures:
        return f"Ingested {ingested_count} {source_label}(s) into {chunks} chunks; {len(failures)} failed."
    if ingested_count:
        return f"Ingested {ingested_count} {source_label}(s) into {chunks} chunks."
    return f"No {source_label}s were ingested."


def _allowed_evidence_file(filename: str) -> bool:
    return filename.casefold().endswith((".pdf", ".docx", ".md", ".txt"))
