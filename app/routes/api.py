import json
import re
import time
import zipfile
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
import httpx
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.auth import (
    get_current_user,
    hash_password,
    is_admin_user,
    password_rule_errors,
    require_admin_user,
    require_current_user,
    verify_password,
)
from app.config import get_settings
from app.database import get_db
from app.models import (
    AppUser,
    Country,
    EvidenceSubmission,
    KnowledgeChunk,
    KnowledgeDocument,
    Region,
    Sector,
    UserChatMessage,
    UserSession,
)
from app.schemas import ChatRequest, ChatResponse
from app.services.chat_session import session_store
from app.services.chat_service import ChatService
from app.services.document_text import (
    compact_text,
    extract_docx_text,
    extract_pdf_page_texts,
    html_to_text,
)
from app.services.hazard_effect_size import hazard_effect_size_rows
from app.services.hazard_ranking_service import HazardRankingService
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    SECTOR_PROMPT_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    extract_file_chunks,
    KnowledgeBaseService,
)
from app.services.knowledge_sync import KnowledgeSyncService, sync_status
from app.services.hazard_salience import country_hazard_salience
from app.services.sector_prompt_rag import SectorPromptRagService

router = APIRouter(prefix="/api", tags=["chat"])

SYNC_SCOPES = {MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, SECTOR_PROMPT_SCOPE}
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}


def require_sync_access(
    request: Request,
    authorization: str | None = Header(default=None),
    current_user: AppUser | None = Depends(get_current_user),
) -> None:
    settings = get_settings()
    expected = settings.central_sync_token.strip() or settings.central_api_token.strip()
    token = _bearer_token(authorization)
    rate_key = f"token:{token}" if expected and token == expected else _rate_limit_key(request, None, current_user)
    _check_rate_limit(
        "sync",
        rate_key,
        int(getattr(settings, "sync_rate_limit_per_minute", 120) or 0),
    )
    if expected:
        if token == expected:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sync token required")
    if is_admin_user(current_user):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sync access required")


def require_evidence_submit_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    expected = settings.central_evidence_token.strip() or settings.central_api_token.strip()
    token = _bearer_token(authorization)
    rate_key = f"token:{token}" if expected and token == expected else _rate_limit_key(request, None, None)
    _check_rate_limit(
        "evidence",
        rate_key,
        int(getattr(settings, "evidence_rate_limit_per_minute", 30) or 0),
    )
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Evidence submission token is not configured",
        )
    if token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Evidence token required")


def _bearer_token(authorization: str | None) -> str:
    scheme, _, token = str(authorization or "").partition(" ")
    if scheme.casefold() != "bearer":
        return ""
    return token.strip()


def _rate_limit_key(
    request: Request | None,
    authorization: str | None,
    current_user: AppUser | None,
) -> str:
    token = _bearer_token(authorization)
    if token:
        return f"token:{token}"
    if current_user is not None and getattr(current_user, "id", None):
        return f"user:{current_user.id}"
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return f"ip:{host or 'unknown'}"


def _check_rate_limit(kind: str, key: str, limit: int, window_seconds: int = 60) -> None:
    if limit <= 0:
        return
    now = time.monotonic()
    bucket_key = f"{kind}:{key}"
    hits = _RATE_LIMIT_BUCKETS.setdefault(bucket_key, [])
    cutoff = now - window_seconds
    hits[:] = [hit for hit in hits if hit >= cutoff]
    if len(hits) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sync requests; please try again shortly.",
        )
    hits.append(now)

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    payload = await _chat_payload(request, db, current_user.id)
    service = ChatService(db, user_id=current_user.id)
    return await service.handle_message(
        payload.message,
        payload.session_id,
        payload.validation_mode,
        payload.crowd_sourcing_enabled,
    )


@router.post("/stats-deep-dive", response_model=ChatResponse)
async def stats_deep_dive(
    payload: ChatRequest,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    service = ChatService(db, user_id=current_user.id)
    return await service.handle_stats_deep_dive_dialog(
        payload.message,
        payload.session_id,
        _validation_mode(payload.validation_mode),
        payload.crowd_sourcing_enabled,
    )


@router.post("/auto-user-message")
async def auto_user_message(
    payload: ChatRequest,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service = ChatService(db, user_id=current_user.id)
    return await service.generate_auto_user_message(
        payload.session_id,
        _validation_mode(payload.validation_mode),
        payload.crowd_sourcing_enabled,
    )


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
    service = ChatService(db, user_id=current_user.id)
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


@router.patch("/sessions/{session_key}")
async def rename_session(
    session_key: str,
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await request.json()
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
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await request.json()
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    if not verify_password(current_password, current_user.password_hash):
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
    db.commit()
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
        content = await file.read()
        try:
            result = await service.ingest_file(filename, content)
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"source": filename, "detail": str(exc)})
            continue
        if result.get("error"):
            failures.append({"source": filename, "detail": str(result.get("detail") or "Could not ingest file.")})
            continue
        total_chunks += int(result.get("chunks") or 0)
        results.append(result)
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
    payload = await request.json()
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
            result = await service.ingest_url(url, title if len(urls) == 1 else None)
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"source": url, "detail": str(exc)})
            continue
        if result.get("error"):
            failures.append({"source": url, "detail": str(result.get("detail") or "Could not ingest URL.")})
            continue
        total_chunks += int(result.get("chunks") or 0)
        results.append(result)
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
    payload = await request.json()
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
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    try:
        result = await SectorPromptRagService(db).rebuild()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": f"Could not reindex sector prompts: {exc}"}
    return {"error": bool(result.get("error")), **result}


@router.post("/sector-prompts/search")
async def sector_prompts_search(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    payload = await request.json()
    sector = str(payload.get("sector") or "").strip()
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"error": True, "detail": "Search query is required.", "results": []}
    try:
        results = await SectorPromptRagService(db).search(sector, query, limit=10)
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "detail": f"Could not search sector prompts: {exc}", "results": []}
    return {"error": False, "results": results}


@router.get("/sync/manifest")
async def sync_manifest(
    _: None = Depends(require_sync_access),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    manifest: dict[str, object] = {}
    for scope in (MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, SECTOR_PROMPT_SCOPE):
        latest = db.scalar(
            select(func.max(KnowledgeDocument.sync_version)).where(
                KnowledgeDocument.scope == scope
            )
        )
        manifest[scope] = {"version": int(latest or 0)}
    return {"error": False, "scopes": manifest}


@router.get("/sync/knowledge/changes")
async def sync_knowledge_changes(
    scope: str = Query(...),
    since: int = Query(0, ge=0),
    cursor_version: int | None = Query(None, ge=0),
    cursor_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    country_id: int | None = Query(None),
    region_id: int | None = Query(None),
    sector_id: int | None = Query(None),
    _: None = Depends(require_sync_access),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if scope not in SYNC_SCOPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sync scope")
    filters = [
        KnowledgeDocument.scope == scope,
    ]
    if cursor_version is None:
        filters.append(KnowledgeDocument.sync_version > since)
    else:
        filters.append(
            or_(
                KnowledgeDocument.sync_version > cursor_version,
                and_(
                    KnowledgeDocument.sync_version == cursor_version,
                    KnowledgeDocument.id > cursor_id,
                ),
            )
        )
    if scope == VALIDATED_EVIDENCE_SCOPE:
        if country_id is not None:
            filters.append(KnowledgeDocument.country_id == country_id)
        if sector_id is not None:
            filters.append(KnowledgeDocument.sector_id == sector_id)
        if region_id is not None:
            filters.append(
                or_(
                    KnowledgeDocument.region_id == region_id,
                    KnowledgeDocument.region_id.is_(None),
                )
            )
        else:
            filters.append(KnowledgeDocument.region_id.is_(None))
    rows = db.scalars(
        select(KnowledgeDocument)
        .where(*filters)
        .order_by(KnowledgeDocument.sync_version, KnowledgeDocument.id)
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    latest = since
    documents: list[dict[str, object]] = []
    for document in rows:
        latest = max(latest, int(document.sync_version or 0))
        documents.append(_sync_document_payload(db, document))
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = {"version": int(last.sync_version or 0), "id": int(last.id or 0)}
    return {
        "error": False,
        "scope": scope,
        "latest_version": latest,
        "documents": documents,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "limit": limit,
    }


@router.get("/sync/status")
async def sync_status_endpoint(
    current_user: AppUser = Depends(require_current_user),
) -> dict[str, object]:
    _ = current_user
    return {"error": False, "status": sync_status()}


@router.post("/sync/pull")
async def sync_pull(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    payload = await request.json()
    try:
        return await KnowledgeSyncService(db).pull_all(
            country_id=_optional_int(payload.get("country_id")),
            region_id=_optional_int(payload.get("region_id")),
            sector_id=_optional_int(payload.get("sector_id")),
        )
    except httpx.HTTPError as exc:
        return {"error": True, "detail": f"Central sync failed: {exc}"}


@router.post("/sync/evidence/submit")
async def sync_evidence_submit(
    request: Request,
    _: None = Depends(require_evidence_submit_access),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await request.json()
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return {"error": True, "detail": "At least one evidence source is required.", "submitted": 0}
    submitted = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or source.get("source_uri") or "Submitted evidence").strip()
        source_type = str(source.get("source_type") or "evidence").strip()[:40] or "evidence"
        db.add(
            EvidenceSubmission(
                submitter_user_id=None,
                session_key=str(payload.get("session_key") or "") or None,
                country_id=_optional_int(payload.get("country_id")),
                region_id=_optional_int(payload.get("region_id")),
                sector_id=_optional_int(payload.get("sector_id")),
                source_type=source_type,
                source_uri=str(source.get("source_uri") or "") or None,
                title=_submission_title(payload, title)[:255],
                content=(str(source.get("content") or "")[:12000] or None),
                status="pending",
            )
        )
        submitted += 1
    db.commit()
    return {"error": False, "submitted": submitted}


@router.get("/sync/evidence/submissions")
async def sync_evidence_submissions(
    status_filter: str = Query("pending", alias="status"),
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    rows = db.scalars(
        select(EvidenceSubmission)
        .where(EvidenceSubmission.status == status_filter)
        .order_by(EvidenceSubmission.created_at, EvidenceSubmission.id)
    ).all()
    return {
        "error": False,
        "submissions": [
            {
                "id": row.id,
                "session_key": row.session_key,
                "country_id": row.country_id,
                "region_id": row.region_id,
                "sector_id": row.sector_id,
                "source_type": row.source_type,
                "source_uri": row.source_uri,
                "title": row.title,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.post("/sync/evidence/submissions/{submission_id}/approve")
async def sync_evidence_approve(
    submission_id: int,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found")
    if submission.status != "pending":
        return {"error": True, "detail": "Only pending submissions can be approved."}
    service = KnowledgeBaseService(
        db,
        submission.submitter_user_id,
        scope=VALIDATED_EVIDENCE_SCOPE,
        country_id=submission.country_id,
        region_id=submission.region_id,
        sector_id=submission.sector_id,
    )
    if submission.content and submission.content.strip():
        result = await service.ingest_text(
            submission.content,
            submission.title,
            "validated_user_evidence",
            submission.source_uri,
        )
    elif submission.source_uri and submission.source_uri.casefold().startswith(("http://", "https://")):
        result = await service.ingest_url(
            submission.source_uri,
            submission.title,
        )
    else:
        return {"error": True, "detail": "Submission has no readable content or URL."}
    submission.status = "approved"
    db.commit()
    return {"error": False, "submission_id": submission.id, "knowledge": result}


@router.post("/sync/evidence/submissions/{submission_id}/reject")
async def sync_evidence_reject(
    submission_id: int,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found")
    submission.status = "rejected"
    db.commit()
    return {"error": False, "submission_id": submission.id, "status": submission.status}


@router.delete("/knowledge/{document_id}")
async def knowledge_delete(
    document_id: int,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    try:
        deleted = await service.delete_document(document_id)
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": True, "deleted": False, "detail": f"Could not delete document: {exc}"}
    return {"error": not deleted, "deleted": deleted}


def _sync_document_payload(db: Session, document: KnowledgeDocument) -> dict[str, object]:
    deleted = document.deleted_at is not None
    chunks: list[dict[str, object]] = []
    if not deleted:
        chunk_rows = db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document.id)
            .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()
        chunks = [
            {
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "page_number": chunk.page_number,
            }
            for chunk in chunk_rows
        ]
    return {
        "sync_id": document.sync_id,
        "sync_version": int(document.sync_version or 0),
        "deleted": deleted,
        "title": document.title,
        "source_type": document.source_type,
        "source_uri": document.source_uri,
        "scope": document.scope,
        "country_id": document.country_id,
        "region_id": document.region_id,
        "sector_id": document.sector_id,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        "deleted_at": document.deleted_at.isoformat() if document.deleted_at else None,
        "chunks": chunks,
    }


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _submission_title(payload: dict[str, object], title: str) -> str:
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        return title
    return f"[client={client_id[:64]}] {title}"


async def _chat_payload(request: Request, db: Session, user_id: int) -> ChatRequest:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        payload = ChatRequest.model_validate(await request.json())
        payload.validation_mode = _validation_mode(payload.validation_mode)
        return payload

    form = await request.form()
    message = str(form.get("message") or "")
    session_id = str(form.get("session_id") or "") or None
    validation_mode = _validation_mode(str(form.get("validation_mode") or ""))
    crowd_sourcing_enabled = _truthy(form.get("crowd_sourcing_enabled"))
    evidence_parts: list[str] = []
    evidence_sources: list[dict[str, object]] = []
    session_row = (
        db.scalar(
            select(UserSession).where(
                UserSession.session_key == session_id,
                UserSession.user_id == user_id,
            )
        )
        if session_id
        else None
    )

    evidence_url = str(form.get("evidence_url") or "").strip()
    if evidence_url:
        evidence_parts.append(f"Evidence URL: {evidence_url}")
        evidence_sources.append(
            {
                "source_type": "url",
                "source_uri": evidence_url,
                "title": evidence_url,
            }
        )
        if session_id:
            temporary_service = KnowledgeBaseService(
                db,
                user_id,
                scope="temporary",
                session_key=session_id,
            )
            try:
                await temporary_service.ingest_url(
                    evidence_url,
                    evidence_url,
                    allow_lexical_only=True,
                )
            except (httpx.HTTPError, ValueError):
                pass

    evidence_file = form.get("evidence_file")
    filename = getattr(evidence_file, "filename", "")
    if isinstance(filename, str) and filename.strip():
        filename = filename.strip()
        if _allowed_evidence_file(filename) and hasattr(evidence_file, "read"):
            evidence_parts.append(f"Evidence file: {filename}")
            file_bytes = await evidence_file.read()
            file_content = ""
            if session_id:
                temporary_service = KnowledgeBaseService(
                    db,
                    user_id,
                    scope="temporary",
                    session_key=session_id,
                )
                try:
                    await temporary_service.ingest_file(
                        filename,
                        file_bytes,
                        allow_lexical_only=True,
                    )
                except (httpx.HTTPError, ValueError):
                    pass
            try:
                file_content = compact_text(" ".join(chunk.content for chunk in extract_file_chunks(filename, file_bytes)))
            except Exception:
                file_content = ""
            evidence_sources.append(
                {
                    "source_type": "file",
                    "source_uri": filename,
                    "title": filename,
                    "content": file_content[:12000],
                }
            )

    if evidence_sources and str(get_settings().app_mode).strip().casefold() == "cloud_client":
        try:
            await KnowledgeSyncService(db).submit_evidence(
                user_id=user_id,
                session_key=session_id,
                country_id=session_row.country_id if session_row else None,
                region_id=session_row.region_id if session_row else None,
                sector_id=session_row.sector_id if session_row else None,
                sources=evidence_sources,
            )
        except httpx.HTTPError:
            pass

    if evidence_parts:
        message = "\n".join([message.strip(), *evidence_parts]).strip()

    return ChatRequest(
        message=message,
        session_id=session_id,
        validation_mode=validation_mode,
        crowd_sourcing_enabled=crowd_sourcing_enabled,
    )


def _validation_mode(value: object) -> str:
    return "easy" if str(value or "").strip().casefold() == "easy" else "strict"


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


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


async def _extract_url_text(url: str) -> str:
    if not url.casefold().startswith(("http://", "https://")):
        return "Unable to extract evidence: URL must start with http:// or https://."

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Unable to extract evidence from URL: {exc}."

    content_type = response.headers.get("content-type", "").casefold()
    content = response.content
    if "pdf" in content_type or url.casefold().split("?", 1)[0].endswith(".pdf"):
        return _extract_pdf_text(content) or "Unable to extract readable text from PDF URL."
    if (
        "wordprocessingml.document" in content_type
        or url.casefold().split("?", 1)[0].endswith(".docx")
    ):
        return _extract_docx_text(content) or "Unable to extract readable text from DOCX URL."

    encoding = response.encoding or "utf-8"
    text = content.decode(encoding, errors="ignore")
    if "html" in content_type or "<html" in text[:500].casefold():
        text = html_to_text(text)
    return _compact_text(text)


def _extract_file_text(filename: str, content: bytes) -> str:
    lowered = filename.casefold()
    if lowered.endswith(".pdf"):
        return _extract_pdf_text(content) or "Unable to extract readable text from uploaded PDF."
    if lowered.endswith(".docx"):
        return _extract_docx_text(content) or "Unable to extract readable text from uploaded DOCX."
    if lowered.endswith((".md", ".txt")):
        return _compact_text(content.decode("utf-8", errors="ignore"))
    return "Unable to extract evidence: only PDF, DOCX, MD, and TXT files are supported."


def _extract_pdf_text(content: bytes) -> str:
    try:
        text = "\n".join(extract_pdf_page_texts(content))
    except Exception as exc:
        return f"Unable to extract evidence from PDF: {exc}."
    return _compact_text(text)


def _extract_docx_text(content: bytes) -> str:
    try:
        text = extract_docx_text(content)
    except (KeyError, zipfile.BadZipFile) as exc:
        return f"Unable to extract evidence from DOCX: {exc}."
    except ElementTree.ParseError as exc:
        return f"Unable to parse DOCX evidence: {exc}."
    return _compact_text(text)


def _compact_text(text: str) -> str:
    return compact_text(text)
