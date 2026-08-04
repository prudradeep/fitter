# ruff: noqa: F401
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from pathlib import Path

from sqlalchemy import and_, func, or_, select

from app.llm import ask_llm_chat
from app.models import (
    EvaluationQuestion,
    MitigationMeasureExample,
    MitigationMeasurePolicy,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
    QuestionOption,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicTargetPopulation,
    UserHazard,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)
from app.schemas import ChatResponse, Option
from app.services.chat_formatters import (
    format_all_dgs,
    format_evaluation_answers,
    normalize_markdown_text,
)
from app.services.chat_json import parse_json_array, parse_json_object
from app.services.chat_options import (
    IMPLEMENTATION_READINESS_OPTIONS,
    MITIGATION_EVIDENCE_DECISION_OPTIONS,
    MITIGATION_EVIDENCE_INPUT_OPTIONS,
    MITIGATION_REVIEW_OPTIONS,
    SYSTEM_INQUIRY_COMPLETE_OPTIONS,
    SYSTEM_INQUIRY_FOLLOWUP_OPTIONS,
    SYSTEM_INQUIRY_INTRO_OPTIONS,
    SYSTEM_INQUIRY_OBSERVATION_OPTIONS,
    compact_for_match,
    exact_option_label,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    normalize_evidence_message,
    open_evidence_decision_action,
    parse_evaluation_answer,
)
from app.services.chat_session import ChatSession
from app.services.document_text import compact_text, extract_pdf_page_texts
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.hazard_ranking_service import slugify_hazard
from app.services.message_renderer import markdown_to_html, render_message
from app.services.mitigation_policy_formatting import (
    format_mitigation_reference_links,
    mitigation_reference_link_values,
    normalize_current_policy_measure_title,
    simplify_mitigation_implementation_summary,
)
from app.services.mitigation_text_rules import local_mitigation_clarification_error
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.system_inquiry_probe_library import (
    system_inquiry_library_version,
    system_inquiry_probe_library,
    system_inquiry_probe_record,
)
from app.services.system_inquiry_telemetry import enqueue_system_inquiry_telemetry

logger = logging.getLogger(__name__)

D23_CONCEPTUAL_REVIEW_PATH = Path(__file__).resolve().parents[2] / "kb" / "FITTER_D2.3_FINAL.pdf"
D23_CONCEPTUAL_REVIEW_START_PAGE = 26
D23_CONCEPTUAL_REVIEW_END_PAGE = 91
D23_CONCEPTUAL_REVIEW_MAX_EXCERPTS = 10
D23_CONCEPTUAL_REVIEW_MAX_CHARS = 9000


@lru_cache(maxsize=1)
def _d23_conceptual_review_page_texts_impl() -> tuple[tuple[int, str], ...]:
    if not D23_CONCEPTUAL_REVIEW_PATH.exists():
        return ()
    try:
        page_texts = extract_pdf_page_texts(D23_CONCEPTUAL_REVIEW_PATH.read_bytes())
    except Exception:
        logger.exception("Failed to read FITTER D2.3 conceptual review PDF")
        return ()

    selected: list[tuple[int, str]] = []
    for page_number in range(
        D23_CONCEPTUAL_REVIEW_START_PAGE,
        min(D23_CONCEPTUAL_REVIEW_END_PAGE, len(page_texts)) + 1,
    ):
        text = compact_text(page_texts[page_number - 1])
        if text:
            selected.append((page_number, text))
    return tuple(selected)
