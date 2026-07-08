import json
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.auth import hash_password, password_rule_errors, require_admin_user, require_current_user, verify_password
from app.database import get_db
from app.models import AppUser, Country, Region, Sector, UserChatMessage, UserSession
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
from app.services.knowledge_base import MAIN_KB_SCOPE, KnowledgeBaseService
from app.services.hazard_salience import country_hazard_salience
from app.services.sector_prompt_rag import SectorPromptRagService

router = APIRouter(prefix="/api", tags=["chat"])

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
    current_user: AppUser = Depends(require_admin_user),
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

    service = ChatService(db, user_id=current_user.id)
    rows = db.scalars(
        select(UserChatMessage)
        .where(UserChatMessage.user_session_id == user_session.id)
        .order_by(UserChatMessage.created_at, UserChatMessage.id)
    ).all()
    return {
        "error": False,
        "exported_at": datetime.utcnow().isoformat() + "Z",
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
                "raw_content": message.content,
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
    form = await request.form()
    upload = form.get("file")
    filename = str(getattr(upload, "filename", "") or "").strip()
    if not filename.casefold().endswith(".json") or not hasattr(upload, "read"):
        return {"error": True, "detail": "Please choose an exported session JSON file."}

    content = await upload.read()
    if len(content) > 10 * 1024 * 1024:
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
    current_user: AppUser = Depends(require_admin_user),
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

    evidence_url = str(form.get("evidence_url") or "").strip()
    if evidence_url:
        evidence_parts.append(f"Evidence URL: {evidence_url}")
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
