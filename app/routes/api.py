import json
import re
from html import unescape
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.auth import hash_password, password_rule_errors, require_admin_user, require_current_user, set_auth_cookie, verify_password
from app.config import get_settings
from app.db.session import get_db
from app.models import AppUser, Country, Prompt, Region, Sector, UserChatMessage, UserMitigationMeasure, UserSession
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
from app.services.chat_parsers import parse_evaluation_answer
from app.services.audit_log import record_audit_event
from app.services.hazard_effect_size import hazard_effect_size_rows
from app.services.hazard_ranking_service import HazardRankingService
from app.services.knowledge_base import MAIN_KB_SCOPE, KnowledgeBaseService
from app.services.hazard_salience import country_hazard_salience
from app.services.prompt_loader import clear_prompt_caches
from app.services.prompt_store import list_prompts, prompt_metadata, seed_prompts_from_files_for_session
from app.services.rate_limit import record_failed_attempt, reset_rate_limit, retry_after_seconds
from app.services.report_export import REPORT_SCOPES, mitigation_report_pdf
from app.services.sector_prompt_rag import SectorPromptRagService, SECTOR_PROMPT_SCOPE
from app.services.sync_permissions import sync_client_permission_enabled

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()


def _is_admin_user(user: AppUser) -> bool:
    return str(user.role or "").strip().casefold() == "admin"


def _restore_options(options: object) -> list[Option]:
    if not isinstance(options, list):
        return []
    restored: list[Option] = []
    for index, option in enumerate(options):
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
            option_id = option.get("id")
        else:
            label = str(option or "").strip()
            option_id = None
        if label:
            restored.append(Option(id=option_id if option_id is not None else index, label=label))
    return restored


def _restore_other_options(options: object) -> list[str]:
    if not isinstance(options, list):
        return []
    return [str(option).strip() for option in options if str(option or "").strip()]


def _should_restore_persisted_current_prompt(
    chat_session,
    current_step: str,
) -> bool:
    saved_step = str(getattr(chat_session, "current_step", "") or "").strip()
    if not saved_step:
        return False
    phase = str(getattr(chat_session, "phase", "") or "").strip()
    return phase in {"", "wizard"}


def _session_data_needs_message_recovery(
    session_data: dict[str, object],
    messages: list[UserChatMessage],
) -> bool:
    if not messages:
        return False
    latest_bot = next(
        (message for message in reversed(messages) if message.role == "bot" and not message.is_error),
        None,
    )
    if latest_bot is not None:
        latest_phase = _recoverable_phase_from_bot_text(_plain_message_text(latest_bot.content))
        saved_phase = str(session_data.get("phase") or "").strip() if session_data else ""
        if latest_phase and saved_phase != latest_phase:
            return True
    if not session_data:
        return True
    has_context = any(
        str(session_data.get(key) or "").strip()
        for key in ("country", "country_id", "region", "region_id", "sector", "sector_id", "phase")
    )
    if not has_context:
        return True
    phase = str(session_data.get("phase") or "").strip()
    if phase not in {"", "wizard", "country"}:
        return False
    return any(_message_indicates_later_phase(message.content) for message in messages)


def _recoverable_phase_from_bot_text(text: str) -> str:
    if "System Inquiry Recorded" in text:
        return "system_inquiry_complete"
    return ""


def _message_indicates_later_phase(content: str) -> bool:
    text = _plain_message_text(content)
    indicators = (
        "Selected hazard:",
        "Mitigation measure:",
        "Question 1 of",
        "Question 2 of",
        "Use the score slider",
        "Target population",
        "Concept Comparision",
        "Concept Comparison",
        "System Inquiry Recorded",
    )
    return any(indicator in text for indicator in indicators)


def _plain_message_text(content: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(content or "")))).strip()


def _recover_session_data_from_messages(
    service: ChatService,
    user_session: UserSession,
    messages: list[UserChatMessage],
) -> dict[str, object]:
    recovered: dict[str, object] = {}
    user_messages = [message for message in messages if message.role == "user" and not message.is_error]
    country = None
    region = None
    sector = None
    for message in user_messages:
        country = service._match_country(_plain_message_text(message.content))
        if country is not None:
            recovered.update({"country_id": country.id, "country": country.name})
            break
    if country is not None:
        for message in user_messages:
            region = service._match_region(_plain_message_text(message.content), country.id)
            if region is not None:
                recovered.update({"region_id": region.id, "region": region.name})
                break
        for message in user_messages:
            sector = service._match_sector(_plain_message_text(message.content), country.id)
            if sector is not None:
                recovered.update({"sector_id": sector.id, "sector": sector.name})
                break

    selected_hazard_values = _latest_section_list_values(messages, "Selected hazard")
    selected_hazard = selected_hazard_values[0] if selected_hazard_values else None
    if selected_hazard:
        recovered["selected_hazard"] = selected_hazard
    mitigation_measure = _latest_user_prefixed_value(messages, "Mitigation measure")
    if mitigation_measure:
        recovered["mitigation_measure"] = mitigation_measure
        recovered["pending_mitigation_measure"] = mitigation_measure
    mitigation_record_details = _recover_mitigation_record_details(service, user_session.id)
    if mitigation_record_details:
        recovered.update(mitigation_record_details)
    target_population = _latest_target_population_values(messages)
    if target_population and not recovered.get("mitigation_target_population"):
        recovered["mitigation_target_population"] = target_population

    latest_bot = next(
        (message for message in reversed(messages) if message.role == "bot" and not message.is_error),
        None,
    )
    if latest_bot is None:
        return recovered

    latest_text = _plain_message_text(latest_bot.content)
    if "System Inquiry Recorded" in latest_text:
        recovered.update(
            {
                "phase": "system_inquiry_complete",
                "current_step": "system_inquiry_complete",
                "current_input_mode": "text",
                "current_options": [],
            }
        )
    elif re.search(r"\bQuestion\s+\d+\s+of\s+\d+\b", latest_text) and "score slider" in latest_text:
        recovered.update(_recover_evaluation_state(service, messages, latest_text))
    elif "Do you have evidence" in latest_text:
        recovered.update(
            {
                "phase": "mitigation_evidence_decision",
                "current_step": "mitigation_evidence_decision",
                "current_input_mode": "text",
            }
        )
    elif "Target population" in latest_text:
        recovered.update(
            {
                "phase": "mitigation_target_population_review",
                "current_step": "mitigation_target_population_review",
                "current_input_mode": "text",
            }
        )
    elif "Concept Comparision" in latest_text or "Concept Comparison" in latest_text:
        recovered.update(
            {
                "phase": "mitigation_review",
                "current_step": "mitigation_review",
                "current_input_mode": "text",
            }
        )
    elif "Mitigation measure" in latest_text:
        recovered.update(
            {
                "phase": "mitigation_measure",
                "current_step": "mitigation_measure",
                "current_input_mode": "text",
            }
        )
    return recovered


def _latest_user_prefixed_value(messages: list[UserChatMessage], label: str) -> str | None:
    prefix = f"{label}:"
    for message in reversed(messages):
        if message.role != "user" or message.is_error:
            continue
        text = _plain_message_text(message.content)
        if text.casefold().startswith(prefix.casefold()):
            value = text[len(prefix):].strip()
            return value or None
    return None


def _recover_mitigation_record_details(
    service: ChatService,
    user_session_id: str,
) -> dict[str, object]:
    row = service.db.scalar(
        select(UserMitigationMeasure)
        .where(UserMitigationMeasure.user_session_id == user_session_id)
        .order_by(desc(UserMitigationMeasure.created_at), desc(UserMitigationMeasure.id))
    )
    if row is None:
        return {}
    recovered: dict[str, object] = {
        "mitigation_record_id": row.id,
        "mitigation_measure": row.measure,
        "mitigation_reason": row.reason,
    }
    target_population = _json_string_list(row.target_population)
    if target_population:
        recovered["mitigation_target_population"] = target_population
    return recovered


def _json_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    values = parsed if isinstance(parsed, list) else str(parsed or "").split(",")
    labels: list[str] = []
    seen: set[str] = set()
    for item in values:
        label = _plain_message_text(str(item)).strip(" -.")
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            labels.append(label)
    return labels


def _latest_target_population_values(messages: list[UserChatMessage]) -> list[str]:
    return _latest_section_list_values(messages, "Target population")


def _latest_section_list_values(messages: list[UserChatMessage], label: str) -> list[str]:
    for message in reversed(messages):
        content = str(message.content or "")
        if label not in _plain_message_text(content):
            continue
        block = _labeled_section_block(content, label)
        labels = _html_list_labels(block)
        if labels:
            return labels
    return []


def _labeled_section_block(content: str, label: str) -> str:
    label_pattern = rf"{re.escape(label)}(?: identified)?\s*:?"
    match = re.search(label_pattern, content, re.IGNORECASE)
    if not match:
        return content
    block = content[match.end():]
    stop = re.search(
        r"(<div\b|<h[1-6]\b|Choose\s+|Concept Comparision|Concept Comparison|"
        r"Selected hazard\s*:|Target population\s*:|Reason\s*:|Mitigation measure\s*:)",
        block,
        re.IGNORECASE,
    )
    return block[: stop.start()] if stop else block


def _html_list_labels(content: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    matches = re.findall(r"<li\b[^>]*>(.*?)</li>", content, flags=re.IGNORECASE | re.DOTALL)
    if not matches:
        matches = re.findall(r"<strong\b[^>]*>(.*?)</strong>", content, flags=re.IGNORECASE | re.DOTALL)
    for item in matches:
        label = _plain_message_text(item).strip(" -.")
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            labels.append(label)
    return labels


def _recover_evaluation_state(
    service: ChatService,
    messages: list[UserChatMessage],
    latest_text: str,
) -> dict[str, object]:
    question_match = re.search(r"\bQuestion\s+(\d+)\s+of\s+(\d+)\b", latest_text)
    current_number = int(question_match.group(1)) if question_match else 1
    total = int(question_match.group(2)) if question_match else current_number
    questions = service._evaluation_questions()
    if not questions:
        questions = _fallback_evaluation_questions(latest_text, current_number, total)
    evaluation_index = max(0, min(current_number - 1, max(len(questions) - 1, 0)))
    return {
        "phase": "evaluation_question",
        "current_step": "evaluation_question",
        "current_input_mode": "evaluation_question",
        "current_options": [],
        "evaluation_questions": questions,
        "evaluation_index": evaluation_index,
        "evaluation_answers": _recover_evaluation_answers(messages, questions, evaluation_index),
    }


def _fallback_evaluation_questions(
    latest_text: str,
    current_number: int,
    total: int,
) -> list[dict[str, object]]:
    category = "Evaluation"
    category_match = re.search(r"^(.*?)\s+Question\s+\d+\s+of\s+\d+", latest_text)
    if category_match:
        category = category_match.group(1).strip() or category
    question_title = "Evaluation question"
    title_match = re.search(rf"\b{current_number}\.\s*(.+?)(?:\s*\(|\s+To what extent|$)", latest_text)
    if title_match:
        question_title = title_match.group(1).strip() or question_title
    questions: list[dict[str, object]] = []
    for index in range(max(total, current_number)):
        questions.append(
            {
                "id": f"recovered-evaluation-{index + 1}",
                "category": category,
                "chart_title": question_title if index + 1 == current_number else f"Question {index + 1}",
                "question": question_title if index + 1 == current_number else f"Question {index + 1}",
            }
        )
    return questions


def _recover_evaluation_answers(
    messages: list[UserChatMessage],
    questions: list[dict[str, object]],
    evaluation_index: int,
) -> list[dict[str, object]]:
    answers: list[dict[str, object]] = []
    evaluation_started = False
    for message in messages:
        text = _plain_message_text(message.content)
        if message.role == "bot" and re.search(r"\bQuestion\s+\d+\s+of\s+\d+\b", text):
            evaluation_started = True
            continue
        if not evaluation_started or message.role != "user" or message.is_error:
            continue
        score, reason, evidence = parse_evaluation_answer(text)
        if score is None or len(answers) >= evaluation_index or len(answers) >= len(questions):
            continue
        question = questions[len(answers)]
        answers.append(
            {
                "question_id": question["id"],
                "category": question["category"],
                "chart_title": question.get("chart_title") or question["question"],
                "question": question["question"],
                "score": score,
                "reason": reason,
                "evidence": evidence,
            }
        )
    return answers


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    payload = await _chat_payload(request, db, current_user.id)
    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
    return await service.handle_message(
        payload.message,
        payload.session_id,
        payload.validation_mode,
        payload.crowd_sourcing_enabled,
    )


@router.post("/stats-deep-dive", response_model=ChatResponse)
async def stats_deep_dive(
    request: Request,
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    payload = await _chat_request_from_json(request, "Stats deep-dive payload")
    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
    return await service.handle_stats_deep_dive_dialog(
        payload.message,
        payload.session_id,
        _validation_mode(payload.validation_mode),
        payload.crowd_sourcing_enabled,
    )


@router.post("/auto-user-message")
async def auto_user_message(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _chat_request_from_json(request, "Auto-user payload")
    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
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
    country_id: str = Query(..., min_length=1),
    region_id: str | None = Query(default=None, min_length=1),
    sector_id: str = Query(..., min_length=1),
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
    service = ChatService(
        db,
        user_id=current_user.id,
        is_admin=_is_admin_user(current_user),
    )
    messages = db.scalars(
        select(UserChatMessage)
        .where(UserChatMessage.user_session_id == user_session.id)
        .order_by(UserChatMessage.created_at, UserChatMessage.id)
    ).all()
    recovered_from_messages = False
    if _session_data_needs_message_recovery(session_data, messages):
        recovered = _recover_session_data_from_messages(service, user_session, messages)
        if recovered:
            session_data = {**session_data, **recovered}
            recovered_from_messages = True

    chat_session = session_store.put(session_key, session_data)
    chat_session.session_key = session_key
    if recovered_from_messages:
        service._ensure_user_session(session_key, chat_session)

    current_prompt = service._repeat_current_options(session_key, chat_session, "", False)
    if _should_restore_persisted_current_prompt(chat_session, current_prompt.step):
        current_prompt.step = chat_session.current_step
        current_prompt.input_mode = chat_session.current_input_mode or current_prompt.input_mode
        current_prompt.options = _restore_options(chat_session.current_options)
        current_prompt.other_options = _restore_other_options(chat_session.current_other_options)
    service._attach_other_options(current_prompt, chat_session)
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


@router.get("/sessions/{session_key}/report")
async def export_mitigation_report(
    session_key: str,
    scope: str = Query(default="current", max_length=40),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    user_session = db.scalar(
        select(UserSession).where(
            UserSession.session_key == session_key,
            UserSession.user_id == current_user.id,
        )
    )
    if user_session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if scope not in REPORT_SCOPES:
        raise HTTPException(status_code=400, detail="Unknown report scope.")

    session_data: dict[str, object] = {}
    if user_session.session_data:
        try:
            parsed = json.loads(user_session.session_data)
            if isinstance(parsed, dict):
                session_data = parsed
        except json.JSONDecodeError:
            session_data = {}
    try:
        report = mitigation_report_pdf(
            db,
            user_session,
            session_data,
            scope=scope,
            current_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=report.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )


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
        country_id=_optional_id(exported_session.get("country_id") or session_data.get("country_id")),
        region_id=_optional_id(exported_session.get("region_id") or session_data.get("region_id")),
        sector_id=_optional_id(exported_session.get("sector_id") or session_data.get("sector_id")),
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
    if not await _can_manage_main_knowledge(db, current_user):
        raise HTTPException(status_code=403, detail="Main knowledge sync permission is required.")
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
            result = await service.ingest_file(filename, content)
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
    if not await _can_manage_main_knowledge(db, current_user):
        raise HTTPException(status_code=403, detail="Main knowledge sync permission is required.")
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
            result = await service.ingest_url(url, title if len(urls) == 1 else None)
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
    if not await _can_reindex_sector_prompts(db, current_user):
        raise HTTPException(status_code=403, detail="Sector prompt reindex permission is required.")
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


@router.get("/settings/prompt-source")
async def prompt_source_setting(
    current_user: AppUser = Depends(require_admin_user),
) -> dict[str, object]:
    _ = current_user
    return {
        "error": False,
        "prompt_source": settings.prompt_source,
        "options": ["auto", "db", "file"],
    }


@router.patch("/settings/prompt-source")
async def prompt_source_update(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = await _json_payload_or_error(request, "Prompt source setting payload")
    if isinstance(payload, JSONResponse):
        return payload
    prompt_source = str(payload.get("prompt_source") or "").strip().casefold()
    if prompt_source not in {"auto", "db", "file"}:
        return {"error": True, "detail": "Prompt source must be auto, db, or file."}
    previous = settings.prompt_source
    settings.prompt_source = prompt_source
    clear_prompt_caches()
    record_audit_event(
        db,
        user=current_user,
        action="settings.prompt_source.update",
        request=request,
        target_type="settings",
        target_id="prompt_source",
        details={"previous": previous, "prompt_source": prompt_source},
    )
    return {
        "error": False,
        "prompt_source": settings.prompt_source,
        "detail": f"Prompt source set to {prompt_source}.",
    }


@router.get("/prompts")
async def prompts_list(
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    if _should_seed_prompts_from_files() and not db.scalar(select(Prompt.id).limit(1)):
        seed_prompts_from_files_for_session(db)
        db.commit()
    return {
        "error": False,
        "prompts": [_prompt_summary(row) for row in list_prompts(db)],
    }


@router.post("/prompts")
async def prompt_create(
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not await _can_manage_prompts(db, current_user):
        raise HTTPException(status_code=403, detail="Prompts are managed on the sync server.")
    payload = await _json_payload_or_error(request, "Prompt create payload")
    if isinstance(payload, JSONResponse):
        return payload
    if _is_sync_client_mode():
        return await _proxy_prompt_create_to_server(payload, db)
    prompt_key = _clean_prompt_key(payload.get("prompt_key"))
    if not prompt_key:
        return {"error": True, "detail": "Prompt key is required."}
    if db.scalar(select(Prompt.id).where(Prompt.prompt_key == prompt_key)):
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
    record_audit_event(
        db,
        user=current_user,
        action="prompts.create",
        request=request,
        target_type="prompt",
        target_id=prompt.prompt_key,
        details={"prompt_id": prompt.id, "category": prompt.category, "model": prompt.model},
    )
    return {"error": False, "prompt": _prompt_detail(prompt), "detail": "Prompt created."}


@router.get("/prompts/{prompt_id}")
async def prompt_detail(
    prompt_id: str,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ = current_user
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return {"error": True, "detail": "Prompt not found."}
    return {"error": False, "prompt": _prompt_detail(prompt)}


@router.patch("/prompts/{prompt_id}")
async def prompt_update(
    prompt_id: str,
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not await _can_manage_prompts(db, current_user):
        raise HTTPException(status_code=403, detail="Prompts are managed on the sync server.")
    payload = await _json_payload_or_error(request, "Prompt update payload")
    if isinstance(payload, JSONResponse):
        return payload
    if _is_sync_client_mode():
        return await _proxy_prompt_update_to_server(prompt_id, payload, db)
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
    invalidated_sector_prompt_chunks = _invalidate_sector_prompt_index(db, prompt)
    record_audit_event(
        db,
        user=current_user,
        action="prompts.update",
        request=request,
        target_type="prompt",
        target_id=prompt.prompt_key,
        details={
            "prompt_id": prompt.id,
            "category": prompt.category,
            "model": prompt.model,
            "invalidated_sector_prompt_chunks": invalidated_sector_prompt_chunks,
        },
    )
    return {"error": False, "prompt": _prompt_detail(prompt), "detail": "Prompt updated."}


@router.delete("/knowledge/{document_id}")
async def knowledge_delete(
    document_id: str,
    request: Request,
    current_user: AppUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not await _can_manage_main_knowledge(db, current_user):
        raise HTTPException(status_code=403, detail="Main knowledge sync permission is required.")
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


async def _chat_payload(request: Request, db: Session, user_id: str) -> ChatRequest:
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
            too_large = upload_too_large_response(
                evidence_file,
                settings.max_upload_bytes,
                "Evidence upload",
            )
            if too_large is not None:
                raise HTTPException(status_code=413, detail="Evidence upload is too large.")
            evidence_parts.append(f"Evidence file: {filename}")
            file_bytes = await evidence_file.read()
            if len(file_bytes) > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Evidence upload is too large.")
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


def _optional_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    value_text = str(value).strip()
    return value_text or None


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


def _password_rate_limit_key(request: Request, user_id: str) -> str:
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


def _should_seed_prompts_from_files() -> bool:
    return (
        settings.prompt_source != "file"
        and str(settings.sync_mode or "").strip().casefold() == "server"
    )


def _is_sync_client_mode() -> bool:
    return bool(settings.sync_enabled) and str(settings.sync_mode or "").strip().casefold() == "client"


async def _proxy_prompt_create_to_server(payload: dict[str, object], db: Session) -> dict[str, object]:
    response_payload = await _request_server_prompt_mutation("POST", "/api/sync/prompts", payload)
    prompt_data = response_payload.get("prompt")
    if isinstance(prompt_data, dict):
        _upsert_prompt_from_payload(prompt_data, db)
    return response_payload


async def _proxy_prompt_update_to_server(
    prompt_id: str,
    payload: dict[str, object],
    db: Session,
) -> dict[str, object]:
    response_payload = await _request_server_prompt_mutation(
        "PATCH",
        f"/api/sync/prompts/{prompt_id}",
        payload,
    )
    prompt_data = response_payload.get("prompt")
    if isinstance(prompt_data, dict):
        _upsert_prompt_from_payload(prompt_data, db)
    return response_payload


async def _request_server_prompt_mutation(
    method: str,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    server_url = str(settings.sync_server_url or "").strip().rstrip("/")
    token = str(settings.sync_api_token or "").strip()
    if not server_url or not token:
        raise HTTPException(status_code=503, detail="Client sync server and token are required for prompt management.")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{server_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        if response.status_code in {401, 403}:
            raise HTTPException(status_code=response.status_code, detail="Prompt management permission is required.")
        response.raise_for_status()
        response_payload = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not update prompt on sync server: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise HTTPException(status_code=502, detail="Invalid prompt response from sync server.")
    return response_payload


def _upsert_prompt_from_payload(data: dict[str, object], db: Session) -> Prompt | None:
    prompt_id = str(data.get("id") or "").strip()
    prompt_key = _clean_prompt_key(data.get("prompt_key"))
    content = str(data.get("content") or "").strip()
    if not prompt_id or not prompt_key or not content:
        return None
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        prompt = db.scalar(select(Prompt).where(Prompt.prompt_key == prompt_key))
    if prompt is None:
        prompt = Prompt(id=prompt_id)
        db.add(prompt)
    prompt.prompt_key = prompt_key
    prompt.category = str(data.get("category") or "custom").strip()[:80] or "custom"
    model = str(data.get("model") or "").strip()
    prompt.model = model[:120] or None
    prompt.display_name = str(data.get("display_name") or prompt_key).strip()[:255] or prompt_key
    prompt.source_path = str(data.get("source_path") or "").strip()[:500] or None
    prompt.content = content
    db.commit()
    db.refresh(prompt)
    clear_prompt_caches()
    return prompt


async def _can_manage_prompts(db: Session, user: AppUser) -> bool:
    if not bool(settings.sync_enabled):
        return True
    return await sync_client_permission_enabled(
        db,
        user,
        "can_manage_prompts",
        settings=settings,
    )


async def _can_reindex_sector_prompts(db: Session, user: AppUser) -> bool:
    return await sync_client_permission_enabled(
        db,
        user,
        "can_reindex_sector_prompts",
        settings=settings,
    )


async def _can_manage_main_knowledge(db: Session, user: AppUser) -> bool:
    return await sync_client_permission_enabled(
        db,
        user,
        "can_sync_main_kb",
        settings=settings,
    )


def _invalidate_sector_prompt_index(db: Session, prompt: Prompt) -> int:
    if prompt.category != "sector":
        return 0
    prompt_files_by_name = {
        "Energy_truth.txt": "energy",
        "Housing_truth.txt": "housing",
        "Transport_truth.txt": "transport",
        "Default_system_prompt.txt": "default",
    }
    sector_key = prompt_files_by_name.get(prompt.prompt_key)
    if not sector_key:
        return 0
    service = KnowledgeBaseService(db, None, scope=SECTOR_PROMPT_SCOPE)
    return service.delete_documents_by_source_uris(
        [SectorPromptRagService._source_uri(sector_key)]
    )


def _allowed_evidence_file(filename: str) -> bool:
    return filename.casefold().endswith((".pdf", ".docx", ".md", ".txt"))
