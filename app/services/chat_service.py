import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape

from app.llm import ask_llm_chat
from app.config import get_settings
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    Country,
    EvaluationQuestion,
    MitigationMeasureExample,
    MitigationMeasurePolicy,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
    QuestionOption,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
    SystemHazardSocioDemographicTargetPopulation,
    EurostatPopulationCache,
    UserActivity,
    UserChatMessage,
    UserHazard,
    UserHazardSocioDemographic,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)
from app.schemas import ChatResponse, Option
from app.services.chat_formatters import (
    format_additional_dgs,
    format_all_dgs,
    format_evaluation_answers,
    format_hazards,
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_options import (
    ADD_DGS_OPTIONS,
    DG_REASON_EVIDENCE_OPTIONS,
    EVALUATION_CATEGORIES,
    HAZARD_ENTRY_OPTIONS,
    MITIGATION_DUPLICATE_OPTIONS,
    MITIGATION_REVIEW_OPTIONS,
    OTHER_NAV_OPTIONS,
    POST_SECTOR_OPTIONS,
    REASON_CONFIRMATION_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    best_fuzzy_label,
    compact_for_match,
    exact_option_label,
    fuzzy_score,
    match_option_label,
    normalize,
    normalize_for_match,
    option_list,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_duplicate_check_response,
    parse_entailment_response,
    parse_evaluation_answer,
    parse_grounded_claims_response,
    parse_grounded_validation_response,
    parse_hazard_input_review_response,
    parse_llm_hazard_list,
    parse_mitigation_reason,
    parse_mitigation_clarity_response,
    parse_reason_evidence,
    parse_validation_response,
)
from app.services.chat_session import ChatSession, session_store
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.knowledge_base import KnowledgeBaseService
from app.services.eurostat_service import EurostatService
from app.services.grounding_models import GroundingModelService
from app.services.hazard_ranking_service import HazardRankingService, slugify_hazard
from app.services.message_renderer import markdown_to_html, render_message
from app.services.profile_metadata import compact_profile_metadata
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger(__name__)


class ChatService:
    welcome_message = render_message("welcome.md")
    invalid_message = render_message("invalid_selection.md")
    fuzzy_rejected_message = render_message("fuzzy_rejected.md")
    mitigation_clarity_turn_cap = 5
    mitigation_support_label_user_evidence = "USER_EVIDENCE"
    mitigation_support_label_curated_knowledge_base = "CURATED_KNOWLEDGE_BASE"
    mitigation_grounding_dimensions = (
        "hazard_fit",
        "mechanism",
        "justification_soundness",
        "evidence_quality",
        "contraindications",
        "feasibility",
    )
    mitigation_critical_grounding_dimensions = (
        "hazard_fit",
        "justification_soundness",
    )
    mitigation_input_supported_dimensions = (
        "hazard_fit",
        "justification_soundness",
    )
    mitigation_clarity_dimensions = (
        "specificity",
        "justification_clarity",
        "evidence_identifiability",
    )
    mitigation_clarity_labels = {
        "specificity": "Specificity",
        "justification_clarity": "Justification clarity",
        "evidence_identifiability": "Evidence identifiability",
    }
    mitigation_clarity_fallback_questions = {
        "specificity": (
            "What specific action or intervention does the mitigation measure provide?",
            "Who will receive it, and under what circumstances?",
        ),
        "justification_clarity": (
            "How is the proposed measure expected to reduce the selected hazard's impact?",
            "Why is this measure appropriate for the identified affected group?",
        ),
        "evidence_identifiability": (
            "What is the title or source of the evidence you provided?",
            "Which finding or section of that source supports your input?",
        ),
    }
    mitigation_clarity_default_questions = (
        "Which part of the mitigation input needs to be interpreted more precisely?",
        "What specific meaning should be used during validation?",
    )
    mitigation_clarity_field_aliases = {
        "mitigation measure": "measure",
        "measure": "measure",
        "specificity": "measure",
        "justification": "justification",
        "reason": "justification",
        "mechanism": "justification",
        "evidence": "evidence",
        "source": "evidence",
        "citation": "evidence",
    }

    def __init__(self, db: Session, user_id: int | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.settings = get_settings()
        self.grounding_models = GroundingModelService()
        self.eurostat = EurostatService(db)
        self.hazard_ranking = HazardRankingService(db)

    async def handle_message(self, message: str, session_id: str | None) -> ChatResponse:
        clean_message = message.strip()

        if clean_message == "/reset":
            if not self._session_belongs_to_current_user(session_id):
                session_id = None
            if session_id:
                try:
                    KnowledgeBaseService(
                        self.db,
                        self.user_id,
                        scope="temporary",
                        session_key=session_id,
                    ).delete_temporary_documents()
                except Exception:
                    logger.exception("Failed to clear temporary evidence during session reset")
            current_session_id, session = session_store.reset(session_id)
            session.session_key = current_session_id
            response = self._country_step(
                current_session_id,
                session,
                await self._intro_message_from_llm(current_session_id),
            )
            self._attach_other_options(response, session)
            self._finalize_chat_response(current_session_id, session, response)
            return response

        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        self._ensure_user_session(current_session_id, session)

        response = await self._chat_response(current_session_id, session, clean_message)
        self._attach_other_options(response, session)
        if clean_message and not response.error:
            self._record_activity(current_session_id, session, "message_received", clean_message)
            self._record_chat_message(
                current_session_id,
                session,
                "user",
                self._chat_message_display_content(clean_message),
            )
        self._finalize_chat_response(current_session_id, session, response)
        return response

    async def handle_stats_deep_dive_dialog(
        self, message: str, session_id: str | None
    ) -> ChatResponse:
        clean_message = message.strip()
        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        self._ensure_user_session(current_session_id, session)

        if session.sector is None:
            return ChatResponse(
                session_id=current_session_id,
                step=session.phase,
                bot_message=self.invalid_message,
                options=[],
                session=session.summary(),
                error=True,
            )

        prompt = clean_message or (
            "Dive deeper into the statistical findings for the listed hazards. "
            "Summarise the most important results and affected groups."
        )
        response = await self._stats_deep_dive(
            current_session_id,
            session,
            prompt,
            history=session.stats_dialog_conversation or [],
            persist_history=False,
        )
        response.other_options = []
        return response

    async def generate_auto_user_message(self, session_id: str | None) -> dict[str, object]:
        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        self._ensure_user_session(current_session_id, session)

        current_response = self._repeat_current_options(current_session_id, session, "", False)
        self._attach_other_options(current_response, session)
        messages = self._recent_chat_messages_for_auto_user(current_session_id, limit=10)
        generated = await self._auto_user_message_from_llm(session, current_response, messages)
        return {
            "error": not bool(generated.strip()),
            "session_id": current_session_id,
            "message": generated.strip(),
            "detail": "" if generated.strip() else "Could not generate an auto user message.",
        }

    async def _chat_response(
        self, current_session_id: str, session: ChatSession, clean_message: str
    ) -> ChatResponse:
        if session.pending_fuzzy_option:
            fuzzy_response = await self._handle_pending_fuzzy_option(
                current_session_id, session, clean_message
            )
            if fuzzy_response is not None:
                return fuzzy_response

        other_nav_response = await self._handle_other_nav_action(
            current_session_id, session, clean_message
        )
        if other_nav_response is not None:
            return other_nav_response

        if not clean_message and not any([session.country, session.region, session.sector]):
            return self._country_step(
                current_session_id,
                session,
                await self._intro_message_from_llm(current_session_id),
            )

        if (
            clean_message
            and self._is_invalid_user_text(clean_message)
            and not self._could_be_fuzzy_selection(session, clean_message)
        ):
            return self._repeat_current_options(
                current_session_id,
                session,
                self._invalid_text_message(),
                True,
            )

        if session.country is None:
            return await self._select_country(current_session_id, session, clean_message)

        if session.region is None:
            return await self._select_region(current_session_id, session, clean_message)

        if session.sector is None:
            return await self._select_sector(current_session_id, session, clean_message)

        if session.phase == "hazards":
            return await self._handle_hazards_action(current_session_id, session, clean_message)

        if session.phase == "stats_deep_dive":
            return await self._handle_stats_deep_dive(current_session_id, session, clean_message)

        if session.phase == "add_hazard":
            return await self._capture_custom_hazard(
                current_session_id, session, clean_message
            )

        if session.phase == "add_hazard_evidence":
            return await self._validate_custom_hazard(current_session_id, session, clean_message)

        if session.phase == "target_population_question":
            return await self._handle_target_population_answer(
                current_session_id, session, clean_message
            )

        if session.phase == "hazard_profile_selection":
            return await self._handle_hazard_profile_selection(
                current_session_id, session, clean_message
            )

        if session.phase == "socio_demographic_review":
            return await self._handle_socio_demographic_review(
                current_session_id, session, clean_message
            )

        if session.phase == "reason_confirmation":
            return await self._handle_reason_confirmation(
                current_session_id, session, clean_message
            )

        if session.phase == "other_actions":
            return await self._handle_other_actions(
                current_session_id, session, clean_message
            )

        if session.phase == "add_dgs":
            return await self._capture_additional_dgs(
                current_session_id, session, clean_message
            )

        if session.phase == "dg_reason_evidence":
            return await self._validate_dgs_against_stats(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_measure":
            return await self._capture_mitigation_measure(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_duplicate_suggestion":
            return self._handle_mitigation_duplicate_suggestion(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_duplicate_report":
            return self._handle_mitigation_duplicate_report(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_reason":
            return await self._validate_mitigation_reason(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_clarity":
            return await self._handle_mitigation_clarity_answer(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_target_population":
            return await self._handle_mitigation_target_population(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_target_population_review":
            return await self._handle_mitigation_target_population_review(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_review":
            return await self._handle_mitigation_review(
                current_session_id, session, clean_message
            )

        if session.phase == "evaluation_question":
            return await self._handle_evaluation_answer(
                current_session_id, session, clean_message
            )

        if session.phase == "evaluation_complete":
            return await self._deep_dive(current_session_id, session, clean_message)

        if session.phase == "mitigation":
            return await self._deep_dive(current_session_id, session, clean_message)

        if not clean_message:
            return ChatResponse(
                session_id=current_session_id,
                step="complete",
                bot_message=render_message("already_complete.md"),
                options=[],
                session=session.summary(),
                error=False,
            )

        return await self._deep_dive(current_session_id, session, clean_message)

    def _country_step(
        self, session_id: str, session: ChatSession, bot_message: str, error: bool = False
    ) -> ChatResponse:
        countries = self.db.scalars(select(Country).order_by(Country.name)).all()
        return ChatResponse(
            session_id=session_id,
            step="country",
            bot_message=bot_message,
            options=option_list(list(countries)),
            session=session.summary(),
            error=error,
        )

    async def _handle_pending_fuzzy_option(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse | None:
        action = normalize(message)
        if action == normalize("Yes"):
            selected_option = session.pending_fuzzy_option
            session.pending_fuzzy_option = None
            if selected_option:
                if normalize(selected_option) == normalize("Dive deeper into statistical findings"):
                    return self._stats_deep_dive_dialog_step(session_id, session)
                return await self._chat_response(session_id, session, selected_option)

        if action == normalize("No"):
            session.pending_fuzzy_option = None
            return self._repeat_current_options(
                session_id,
                session,
                self.fuzzy_rejected_message,
                error=False,
            )

        if self._is_invalid_user_text(message):
            return ChatResponse(
                session_id=session_id,
                step="fuzzy_confirmation",
                bot_message=self._invalid_text_message(),
                options=REASON_CONFIRMATION_OPTIONS,
                session=session.summary(),
                error=True,
            )

        return ChatResponse(
            session_id=session_id,
            step="fuzzy_confirmation",
            bot_message=self.invalid_message,
            options=REASON_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _fuzzy_confirmation_step(
        self, session_id: str, session: ChatSession, option_label: str
    ) -> ChatResponse:
        session.pending_fuzzy_option = option_label
        return ChatResponse(
            session_id=session_id,
            step="fuzzy_confirmation",
            bot_message=render_message("fuzzy_confirmation.md", option=option_label),
            options=REASON_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_other_nav_action(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse | None:
        if normalize(message) not in {normalize(option) for option in OTHER_NAV_OPTIONS}:
            return None

        action = normalize(message)
        if action == normalize("Analyse another hazard in the same sector"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new hazard"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            self._clear_selected_hazard_context(session)
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Write hazard again"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            hazard_to_rewrite = session.accepted_custom_hazard or session.selected_hazard
            if hazard_to_rewrite and session.custom_hazards:
                session.custom_hazards = [
                    hazard
                    for hazard in session.custom_hazards
                    if normalize(hazard) != normalize(hazard_to_rewrite)
                ]
            self._clear_selected_hazard_context(session)
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Write mitigation measure again"):
            if session.selected_hazard is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            session.phase = "mitigation_measure"
            session.pending_mitigation_measure = None
            self._clear_mitigation_clarity_state(session)
            session.mitigation_measure = None
            session.mitigation_reason = None
            session.mitigation_record_id = None
            self._clear_mitigation_validation_state(session)
            session.evaluation_questions = None
            session.evaluation_index = 0
            session.evaluation_answers = None
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_measure_reason.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                    mitigation_examples=self._mitigation_measure_examples(session.sector_id),
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=False,
            )

        if action == normalize("Select another region"):
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self._clear_region_context(session)
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            return ChatResponse(
                session_id=session_id,
                step="region",
                bot_message=render_message(
                    "country_selected.md",
                    country=session.country or "your selected country",
                ),
                options=option_list(list(regions)),
                session=session.summary(),
                error=False,
            )

        if action == normalize("Choose a different sector"):
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self._clear_sector_context(session)
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=render_message(
                    "region_selected.md",
                    region=session.region or session.country or "your selected country",
                ),
                options=option_list(self._sectors_for_country(session.country_id)),
                session=session.summary(),
                error=False,
            )

        if action == normalize("Start over with a different country"):
            self._reset_session(session)
            return self._country_step(
                session_id,
                session,
                await self._intro_message_from_llm(session_id),
            )

        return None

    @staticmethod
    def _clear_sector_context(session: ChatSession) -> None:
        session.sector_id = None
        session.sector = None
        session.phase = "wizard"
        session.hazards = None
        session.hazard_profiles = None
        session.custom_hazards = None
        session.additional_hazards = None
        ChatService._clear_selected_hazard_context(session)

    @staticmethod
    def _clear_region_context(session: ChatSession) -> None:
        session.region_id = None
        session.region = None
        ChatService._clear_sector_context(session)

    @staticmethod
    def _clear_selected_hazard_context(session: ChatSession) -> None:
        session.pending_hazard = None
        session.selected_hazard = None
        session.selected_hazard_record_id = None
        session.socio_demographic_findings = None
        session.socio_demographic_profiles = None
        session.additional_dgs = None
        session.pending_additional_dgs = None
        session.additional_dg_answers = None
        session.stats_conversation = None
        session.dg_reason = None
        session.dg_evidence = None
        session.pending_mitigation_measure = None
        ChatService._clear_mitigation_clarity_state(session)
        session.suggested_mitigation_measure_id = None
        session.suggested_mitigation_measure_name = None
        session.mitigation_measure = None
        session.mitigation_reason = None
        session.mitigation_target_population = None
        session.mitigation_record_id = None
        ChatService._clear_mitigation_validation_state(session)
        session.evaluation_questions = None
        session.evaluation_index = 0
        session.evaluation_answers = None
        session.target_population_questions = None
        session.target_population_index = 0
        session.target_population_answers = None
        session.saved_target_population_answers = None
        session.accepted_custom_hazard = None
        session.accepted_custom_hazard_reason = None
        session.accepted_custom_hazard_evidence = None
        session.accepted_custom_hazard_record_id = None
        session.pending_fuzzy_option = None
        session.stats_dialog_conversation = None

    @staticmethod
    def _reset_session(session: ChatSession) -> None:
        fresh_session = ChatSession()
        for key, value in asdict(fresh_session).items():
            setattr(session, key, value)

    @staticmethod
    def _clear_mitigation_clarity_state(session: ChatSession) -> None:
        session.pending_mitigation_reason = None
        session.pending_mitigation_evidence = None
        session.pending_mitigation_clarity_dimension = None
        session.mitigation_clarity_turns = 0
        session.mitigation_clarification_history = None
        session.mitigation_frozen_inputs = None

    @staticmethod
    def _clear_mitigation_validation_state(session: ChatSession) -> None:
        session.mitigation_validation = None
        session.mitigation_grounded_synthesis = None

    def _clarity_validation_details(
        self,
        clarity: dict[str, object],
        session: ChatSession,
        active_dimension: str | None = None,
        clarification_questions: list[str] | None = None,
    ) -> dict[str, object]:
        dimensions = clarity.get("dimensions")
        return {
            "phase": "clarity",
            "title": "Mitigation clarification status",
            "dimensions": dimensions if isinstance(dimensions, dict) else {},
            "active_dimension": active_dimension,
            "clarification_questions": clarification_questions or [],
            "metrics": {
                "clarification_turn": session.mitigation_clarity_turns,
                "clarification_turn_cap": self.mitigation_clarity_turn_cap,
            },
            "checks": {
                "groundedness": "PENDING_INPUT_FREEZE",
                "reranker": "PENDING_INPUT_FREEZE",
                "entailment": "PENDING_INPUT_FREEZE",
            },
            "reason": str(clarity.get("reason") or "").strip(),
        }

    def _grounding_validation_details(
        self,
        session: ChatSession,
        validation: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        validation = validation or session.mitigation_validation
        if not isinstance(validation, dict):
            return None
        return {
            "phase": "grounding",
            "title": "Mitigation grounding status",
            "dimensions": validation.get("dimensions") or {},
            "metrics": {
                "outcome": validation.get("outcome"),
                "rubric_coverage": validation.get("rubric_coverage"),
                "retrieval_support": validation.get("retrieval_support"),
                "verdict_stability": validation.get("verdict_stability"),
                "sample_count": validation.get("sample_count"),
                "confidence_score": validation.get("confidence_score"),
            },
            "checks": {
                "support_corpus": validation.get("support_label"),
                "reranker": self.grounding_models.reranker_status,
                "entailment": self.grounding_models.nli_status,
            },
            "reason": str(validation.get("reason") or "").strip(),
        }

    def _attach_other_options(self, response: ChatResponse, session: ChatSession) -> None:
        self._apply_country_profile_count(response, session)
        main_options = {normalize(option.label) for option in response.options}
        response_specific_options = [
            option
            for option in (response.other_options or [])
            if normalize(option) not in main_options
        ]
        existing_options = {normalize(option) for option in response_specific_options}
        response.other_options = response_specific_options + [
            option
            for option in self._other_nav_options(session, response.step)
            if normalize(option) not in main_options
            and normalize(option) not in existing_options
        ]

    def _apply_country_profile_count(self, response: ChatResponse, session: ChatSession) -> None:
        response.session.affected_profile_count = session.eligible_hazard_profile_count()

    def _valid_sdp_variable_name(
        self, session: ChatSession, variable_name: str | None
    ) -> str:
        cleaned = normalize_markdown_text(str(variable_name or "")).strip().strip(".:;,- ")
        if not cleaned:
            return ""
        prefixed_match = re.match(
            r"^(?:PREDICTOR\s+)?[0-9]+[A-Z]\s*:\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if prefixed_match:
            cleaned = prefixed_match.group(1).strip().strip(".:;,- ")
            variable_token = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", cleaned)
            if variable_token:
                return variable_token.group(1)
        predictor_id = self._predictor_id_from_variable_name(cleaned)
        if predictor_id:
            return self._predictor_variable_name_from_prompt(session, predictor_id)
        return cleaned

    @staticmethod
    def _predictor_id_from_variable_name(variable_name: str) -> str | None:
        normalized = normalize_markdown_text(variable_name).strip().strip(".:;,- ")
        match = re.fullmatch(
            r"(?:PREDICTOR\s+)?([0-9]+[A-Z])",
            normalized,
            flags=re.IGNORECASE,
        )
        return match.group(1).upper() if match else None

    def _predictor_variable_name_from_prompt(
        self, session: ChatSession, predictor_id: str
    ) -> str:
        _ = session, predictor_id
        return ""

    @staticmethod
    def _other_nav_options(session: ChatSession, step: str) -> list[str]:
        options: list[str] = []
        if session.mitigation_measure or session.pending_mitigation_measure:
            options.append("Write mitigation measure again")
        if session.sector and session.selected_hazard:
            options.append("Analyse another hazard in the same sector")
        if session.sector and step != "sector":
            options.append("Add a new hazard")
        if session.sector and (
            session.accepted_custom_hazard
            or session.pending_hazard
            or (
                session.selected_hazard
                and session.custom_hazards
                and normalize(session.selected_hazard)
                in {normalize(hazard) for hazard in session.custom_hazards}
            )
        ):
            options.append("Write hazard again")
        if session.sector and step != "sector":
            options.append("Choose a different sector")
        if session.country and session.region_id is not None and step != "region":
            options.append("Select another region")
        if session.country:
            options.append("Start over with a different country")
        return options

    def _repeat_current_options(
        self,
        session_id: str,
        session: ChatSession,
        bot_message: str | None = None,
        error: bool = True,
    ) -> ChatResponse:
        message = bot_message or self.invalid_message
        if session.country is None:
            return self._country_step(session_id, session, message, error)

        if session.region is None:
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            return ChatResponse(
                session_id=session_id,
                step="region",
                bot_message=message,
                options=option_list(list(regions)),
                session=session.summary(),
                error=error,
            )

        if session.sector is None:
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=message,
                options=option_list(self._sectors_for_country(session.country_id)),
                session=session.summary(),
                error=error,
            )

        if session.phase == "hazards":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=POST_SECTOR_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "stats_deep_dive":
            return ChatResponse(
                session_id=session_id,
                step="stats_deep_dive",
                bot_message=message,
                options=STATS_DEEP_DIVE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "hazard_profile_selection":
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=message,
                options=self._hazard_options(session),
                session=session.summary(),
                error=error,
            )

        if session.phase == "socio_demographic_review":
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=message,
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "reason_confirmation":
            return ChatResponse(
                session_id=session_id,
                step="reason_confirmation",
                bot_message=message,
                options=REASON_CONFIRMATION_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "other_actions":
            return ChatResponse(
                session_id=session_id,
                step="complete",
                bot_message=message,
                options=self._primary_other_nav_options(session, "complete"),
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_dgs":
            question = self._current_target_population_question(session)
            if question is not None:
                return ChatResponse(
                    session_id=session_id,
                    step="add_dgs",
                    bot_message=message,
                    options=self._target_population_options(question),
                    session=session.summary(),
                    input_mode="target_population_multi",
                    error=error,
                )
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=message,
                options=ADD_DGS_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard_evidence":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "target_population_question":
            question = self._current_target_population_question(session)
            options = self._target_population_options(question) if question else []
            return ChatResponse(
                session_id=session_id,
                step="target_population_question",
                bot_message=message,
                options=options,
                session=session.summary(),
                error=error,
            )

        if session.phase == "dg_reason_evidence":
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=message,
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "mitigation_measure":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=error,
            )

        if session.phase == "mitigation_duplicate_suggestion":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_duplicate_suggestion",
                bot_message=message,
                options=MITIGATION_DUPLICATE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_duplicate_report":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_duplicate_report",
                bot_message=message,
                options=MITIGATION_DUPLICATE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_reason":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "mitigation_clarity":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=message,
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=error,
            )

        if session.phase == "mitigation_target_population":
            return self._mitigation_target_population_step(
                session_id,
                session,
                error_reason=message if error else None,
            )

        if session.phase == "mitigation_target_population_review":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_target_population_review",
                bot_message=message,
                options=self._mitigation_target_population_review_options(),
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_review":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=message,
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "evaluation_question":
            return ChatResponse(
                session_id=session_id,
                step="evaluation_question",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="evaluation_question",
                error=error,
            )

        if session.phase == "evaluation_complete":
            return ChatResponse(
                session_id=session_id,
                step="evaluation_complete",
                bot_message=message,
                options=[],
                session=session.summary(),
                error=error,
            )

        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=message,
            options=[],
            session=session.summary(),
            error=error,
        )

    @staticmethod
    def _invalid_text_message() -> str:
        return render_message(
            "input_validation_failed.md",
            reason=(
                "The input appears to contain gibberish, keyboard mashing, "
                "random characters, or unrecognizable text."
            ),
        )

    @staticmethod
    def _current_step(session: ChatSession) -> str:
        if session.country is None:
            return "country"
        if session.region is None:
            return "region"
        if session.sector is None:
            return "sector"
        if session.phase in {"add_hazard", "add_hazard_evidence"}:
            return "hazards"
        if session.phase == "dg_reason_evidence":
            return "socio_demographic_review"
        return session.phase or "complete"

    async def _select_country(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        country = self._match_country(message)
        if country is None:
            fuzzy_country = self._fuzzy_row_by_name(
                self.db.scalars(select(Country).order_by(Country.name)).all(),
                message,
            )
            if fuzzy_country is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_country.name)
            return self._country_step(session_id, session, self.invalid_message, True)

        session.country_id = country.id
        session.country = country.name
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "country_selected", country.name, step="country")
        regions = self.db.scalars(
            select(Region).where(Region.country_id == country.id).order_by(Region.name)
        ).all()
        if not regions:
            session.region_id = None
            session.region = "National scope"
            sectors = self._sectors_for_country(country.id)
            bot_message = await self._selection_message_from_llm(
                session,
                event="national_scope",
                fallback=render_message("national_scope.md", country=country.name),
            )
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=bot_message,
                options=option_list(sectors),
                session=session.summary(),
                error=False,
            )

        bot_message = await self._selection_message_from_llm(
            session,
            event="country_selected",
            fallback=render_message("country_selected.md", country=country.name),
        )
        return ChatResponse(
            session_id=session_id,
            step="region",
            bot_message=bot_message,
            options=option_list(list(regions)),
            session=session.summary(),
            error=False,
        )

    async def _select_region(self, session_id: str, session: ChatSession, message: str) -> ChatResponse:
        region = self._match_region(message, session.country_id)
        if region is None:
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            fuzzy_region = self._fuzzy_row_by_name(list(regions), message)
            if fuzzy_region is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_region.name)
            return ChatResponse(
                session_id=session_id,
                step="region",
                bot_message=self.invalid_message,
                options=option_list(list(regions)),
                session=session.summary(),
                error=True,
            )

        session.region_id = region.id
        session.region = region.name
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "region_selected", region.name, step="region")
        sectors = self._sectors_for_country(session.country_id)
        bot_message = await self._selection_message_from_llm(
            session,
            event="region_selected",
            fallback=render_message("region_selected.md", region=region.name),
        )
        return ChatResponse(
            session_id=session_id,
            step="sector",
            bot_message=bot_message,
            options=option_list(sectors),
            session=session.summary(),
            error=False,
        )

    async def _select_sector(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        sector = self._match_sector(message, session.country_id)
        if sector is None:
            fuzzy_sector = self._fuzzy_row_by_name(
                self._sectors_for_country(session.country_id),
                message,
            )
            if fuzzy_sector is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_sector.name)
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=self.invalid_message,
                options=option_list(self._sectors_for_country(session.country_id)),
                session=session.summary(),
                error=True,
            )

        session.sector_id = sector.id
        session.sector = sector.name
        session.phase = "hazards"
        self._normalize_stored_sdp_variable_names(session)
        hazard_items = self._stored_hazard_items_for_context(session_id, session)
        expected_hazard_count = await self._sector_prompt_rag_hazard_count(session.sector)
        invalid_cached_hazards = bool(hazard_items) and bool(
            expected_hazard_count and len(hazard_items) != expected_hazard_count
        )
        invalid_cached_profiles = bool(hazard_items) and not await self._sector_prompt_profiles_match_rag(
            session,
            hazard_items,
        )
        if not hazard_items or invalid_cached_hazards or invalid_cached_profiles:
            hazard_items = await self._refresh_hazards_and_profiles_from_llm(
                session_id,
                session,
                replace_sector_hazards=invalid_cached_hazards or invalid_cached_profiles,
            )
        session.hazards = [str(item["hazard"]) for item in hazard_items]
        session.hazard_profiles = {
            str(item["hazard"]): [
                profile
                for profile in item.get("profiles", [])
                if (
                    isinstance(profile, dict)
                    and str(profile.get("name") or "").strip()
                )
                or (isinstance(profile, str) and profile.strip())
            ]
            for item in hazard_items
            if item.get("profiles")
        }
        session.custom_hazards = self._saved_custom_hazards_for_context(session)
        session.additional_hazards = self._additional_hazards_for_context(session)
        self._hydrate_custom_hazard_profiles(session)
        self._filter_session_hazards_without_profiles(session)
        await self._rank_session_hazards(session)
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "sector_selected", sector.name, step="sector")
        return self._hazards_step(session_id, session)

    async def _rank_session_hazards(self, session: ChatSession) -> None:
        if not session.country_id or not session.sector_id or not session.hazards:
            return
        country = self.db.get(Country, session.country_id)
        sector = self.db.get(Sector, session.sector_id)
        region = self.db.get(Region, session.region_id) if session.region_id else None
        if country is None or sector is None:
            return
        try:
            ranked_rows = await self.hazard_ranking.rank_hazards(
                country=country,
                region=region,
                sector=sector,
                hazards=session.hazards,
            )
        except Exception:
            logger.exception("Failed to rank hazards")
            return
        if not ranked_rows:
            return
        ranked_by_name = {
            str(row["hazard"]): row
            for row in ranked_rows
            if int(row.get("total_predictors") or 0) > 0
        }
        ranked_names = [str(row["hazard"]) for row in ranked_rows if str(row["hazard"]) in ranked_by_name]
        session.hazards = ranked_names
        session.hazard_rankings = ranked_by_name
        await self._enrich_listed_hazard_profiles_with_population_context(session)
        try:
            refreshed_rows = await self.hazard_ranking.rank_hazards(
                country=country,
                region=region,
                sector=sector,
                hazards=session.hazards,
            )
        except Exception:
            logger.exception("Failed to recalculate hazard reach from population matches")
            return
        refreshed_by_name = {
            str(row["hazard"]): row
            for row in refreshed_rows
            if int(row.get("total_predictors") or 0) > 0
        }
        if refreshed_by_name:
            session.hazards = [
                str(row["hazard"])
                for row in refreshed_rows
                if str(row["hazard"]) in refreshed_by_name
            ]
            session.hazard_rankings = refreshed_by_name

    async def _enrich_listed_hazard_profiles_with_population_context(
        self,
        session: ChatSession,
    ) -> None:
        if not session.hazard_profiles:
            return
        enriched_profiles: dict[str, list[dict[str, str]]] = {}
        for hazard in session.hazards or []:
            profiles = self._stored_hazard_profiles(session, hazard)
            if not profiles:
                continue
            enriched_profiles[hazard] = await self._profiles_with_population_context(
                session,
                hazard,
                profiles,
            )
        for hazard in session.additional_hazards or []:
            profiles = self._stored_hazard_profiles(session, hazard)
            if not profiles:
                continue
            enriched_profiles[hazard] = await self._additional_profiles_with_population_context(
                session,
                hazard,
                profiles,
            )
        for hazard in session.custom_hazards or []:
            profiles = self._stored_hazard_profiles(session, hazard)
            if not profiles:
                continue
            enriched_profiles[hazard] = await self._additional_profiles_with_population_context(
                session,
                hazard,
                profiles,
            )
        if enriched_profiles:
            session.hazard_profiles = {
                **(session.hazard_profiles or {}),
                **enriched_profiles,
            }

    def _hazards_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        self._filter_session_hazards_without_profiles(session)
        session.phase = "hazards"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazards_overview.md",
                country=session.country,
                region=session.region,
                sector=session.sector,
                hazards=format_hazards(session),
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_hazards_action(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, POST_SECTOR_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, POST_SECTOR_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Refresh hazards and DGs"):
            hazard_items = await self._refresh_hazards_and_profiles_from_llm(
                session_id,
                session,
                replace_sector_hazards=True,
            )
            session.hazards = [str(item["hazard"]) for item in hazard_items]
            session.hazard_profiles = {
                str(item["hazard"]): [
                    profile
                    for profile in item.get("profiles", [])
                    if (
                        isinstance(profile, dict)
                        and str(profile.get("name") or "").strip()
                    )
                    or (isinstance(profile, str) and profile.strip())
                ]
                for item in hazard_items
                if item.get("profiles")
            }
            session.custom_hazards = self._saved_custom_hazards_for_context(session)
            session.additional_hazards = self._additional_hazards_for_context(session)
            self._hydrate_custom_hazard_profiles(session)
            self._filter_session_hazards_without_profiles(session)
            await self._rank_session_hazards(session)
            self._record_activity(session_id, session, "hazards_refreshed", session.sector or "")
            return self._hazards_step(session_id, session)

        if action == normalize("Dive deeper into statistical findings"):
            return self._stats_deep_dive_dialog_step(session_id, session)

        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=self.invalid_message,
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _stats_deep_dive_dialog_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        return ChatResponse(
            session_id=session_id,
            step="stats_deep_dive_dialog",
            bot_message="",
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_stats_deep_dive(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, STATS_DEEP_DIVE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, STATS_DEEP_DIVE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Refresh hazards and DGs"):
            hazard_items = await self._refresh_hazards_and_profiles_from_llm(
                session_id,
                session,
                replace_sector_hazards=True,
            )
            session.hazards = [str(item["hazard"]) for item in hazard_items]
            session.hazard_profiles = {
                str(item["hazard"]): [
                    profile
                    for profile in item.get("profiles", [])
                    if (
                        isinstance(profile, dict)
                        and str(profile.get("name") or "").strip()
                    )
                    or (isinstance(profile, str) and profile.strip())
                ]
                for item in hazard_items
                if item.get("profiles")
            }
            session.custom_hazards = self._saved_custom_hazards_for_context(session)
            session.additional_hazards = self._additional_hazards_for_context(session)
            self._hydrate_custom_hazard_profiles(session)
            self._filter_session_hazards_without_profiles(session)
            await self._rank_session_hazards(session)
            self._record_activity(session_id, session, "hazards_refreshed", session.sector or "")
            return self._hazards_step(session_id, session)

        if not message:
            return ChatResponse(
                session_id=session_id,
                step="stats_deep_dive",
                bot_message=await self._sector_briefing(session),
                options=STATS_DEEP_DIVE_OPTIONS,
                session=session.summary(),
                error=False,
            )

        return await self._stats_deep_dive(session_id, session, message)

    def _hazard_profile_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        self._filter_session_hazards_without_profiles(session)
        session.phase = "hazard_profile_selection"
        return ChatResponse(
            session_id=session_id,
            step="hazard_profile_selection",
            bot_message=render_message("mitigation_next.md"),
            options=self._hazard_options(session),
            session=session.summary(),
            error=False,
        )

    async def _handle_hazard_profile_selection(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        action = normalize(message)
        if action in {
            normalize("Show additional hazards"),
            normalize("Show hazards added by experts"),
        }:
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=(
                    "Choose one of the hazards added by experts from the selected "
                    "country-sector evidence."
                ),
                options=self._additional_hazard_selection_options(session),
                session=session.summary(),
                error=False,
            )
        if action == normalize("Show co-created hazards"):
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message="Choose one of the co-created hazards added by users.",
                options=self._custom_hazard_selection_options(session),
                session=session.summary(),
                error=False,
            )
        if action == normalize("Show listed hazards"):
            return self._hazard_profile_step(session_id, session)

        hazard = self._match_hazard(message, session)
        if hazard is None:
            fuzzy_hazard = self._fuzzy_hazard(message, session)
            if fuzzy_hazard is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_hazard)
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=self.invalid_message,
                options=self._hazard_options(session),
                session=session.summary(),
                error=True,
            )

        self._clear_selected_hazard_context(session)
        session.selected_hazard = hazard
        is_saved_custom_hazard = self._is_saved_custom_hazard(session, hazard)
        is_additional_hazard = self._is_additional_hazard(session, hazard)
        hazard_record = self._ensure_user_hazard(
            session_id,
            session,
            hazard,
            source=(
                "custom"
                if is_saved_custom_hazard
                else "additional"
                if is_additional_hazard
                else "system"
            ),
        )
        session.selected_hazard_record_id = hazard_record.id if hazard_record else None
        self._record_activity(session_id, session, "hazard_selected", hazard)
        session.phase = "socio_demographic_review"

        if is_saved_custom_hazard:
            session.saved_target_population_answers = self._target_population_answers_for_saved_hazard(
                session,
                hazard,
            )
            if not self._stored_hazard_profiles(session, hazard):
                profiles = self._target_population_profiles_for_saved_hazard(session, hazard)
                if profiles:
                    if session.hazard_profiles is None:
                        session.hazard_profiles = {}
                    session.hazard_profiles[hazard] = profiles
            return await self._hazard_profiles_response(session_id, session, hazard)

        return await self._hazard_profiles_response(session_id, session, hazard)

    async def _handle_socio_demographic_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SOCIO_DEMOGRAPHIC_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, SOCIO_DEMOGRAPHIC_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Add more DGs"):
            return self._start_additional_dg_questions(session_id, session)

        if action == normalize("Create Mitigation Measure"):
            return await self._create_mitigation_measure_step(session_id, session)

        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=self.invalid_message,
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=True,
        )

    async def _create_mitigation_measure_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        target_population_confirmed: bool = False,
    ) -> ChatResponse:
        if not target_population_confirmed:
            # Target groups are collected as free text during mitigation
            # clarification, after the measure and reason have been described.
            session.mitigation_target_population = None
        session.phase = "reason_confirmation"
        recommendations = await self._practical_policy_recommendations(session)
        return ChatResponse(
            session_id=session_id,
            step="reason_confirmation",
            bot_message=(
                markdown_to_html(recommendations)
                + "\n"
                + render_message("reason_confirmation.md")
            ),
            options=REASON_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_reason_confirmation(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, REASON_CONFIRMATION_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, REASON_CONFIRMATION_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Yes"):
            session.phase = "mitigation_measure"
            session.pending_mitigation_measure = None
            self._clear_mitigation_clarity_state(session)
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_measure_reason.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                    mitigation_examples=self._mitigation_measure_examples(session.sector_id),
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=False,
            )

        if action in {
            normalize("Adopt mitigation proposal suggested above"),
            normalize("Continue with current mitigation measure"),
        }:
            mitigation_measure = (
                str(session.suggested_new_policy_proposal or "").strip()
                or self._current_policy_mitigation_measure(session)
            )
            if not mitigation_measure:
                return ChatResponse(
                    session_id=session_id,
                    step="reason_confirmation",
                    bot_message=(
                        "I could not find a suggested mitigation proposal to adopt. "
                        "Choose **Yes** to write one manually."
                    ),
                    options=REASON_CONFIRMATION_OPTIONS,
                    session=session.summary(),
                    error=True,
                )
            self._clear_mitigation_clarity_state(session)
            self._clear_mitigation_validation_state(session)
            session.pending_mitigation_measure = mitigation_measure
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_measure_reason.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                    mitigation_measure=mitigation_measure,
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                input_values={"mitigation_measure": mitigation_measure},
                error=False,
            )

        if action == normalize("No"):
            session.phase = "other_actions"
            return ChatResponse(
                session_id=session_id,
                step="complete",
                bot_message=await self._other_actions_message_from_llm(session),
                options=self._primary_other_nav_options(session, "complete"),
                session=session.summary(),
                error=False,
            )

        return ChatResponse(
            session_id=session_id,
            step="reason_confirmation",
            bot_message=self.invalid_message,
            options=REASON_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _primary_other_nav_options(self, session: ChatSession, step: str) -> list[Option]:
        return [
            Option(id=index, label=label)
            for index, label in enumerate(self._other_nav_options(session, step), start=1)
        ]

    @staticmethod
    def _mitigation_clarity_options() -> list[Option]:
        return [Option(id=1, label="Write mitigation measure again")]

    @staticmethod
    def _mitigation_target_population_review_options() -> list[Option]:
        return [
            Option(id=1, label="Continue"),
            Option(id=2, label="Add more target population"),
        ]

    async def _other_actions_message_from_llm(self, session: ChatSession) -> str:
        options = self._other_nav_options(session, "complete")
        fallback = (
            "No problem. Choose another action below to continue working in Dr Transition."
        )
        context = """
You write short navigation prompts for Dr Transition.

The user has declined to create a mitigation measure after reviewing practical
recommendations. Invite them to continue with another useful action in the system.
Be concise, neutral, and encouraging. Do not recommend one option over another.
Return Markdown only.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    f"Country: {session.country or 'Not selected'}\n"
                    f"Region: {session.region or 'Not selected'}\n"
                    f"Sector: {session.sector or 'Not selected'}\n"
                    f"Selected hazard: {session.selected_hazard or 'Not selected'}\n\n"
                    "Available actions:\n"
                    + "\n".join(f"- {option}" for option in options)
                    + "\n\nAsk the user to choose another action below. Keep it to 1 or 2 sentences."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.4,
            max_tokens=160,
        )
        if is_llm_unavailable_response(response) or not response.strip():
            return markdown_to_html(fallback)
        return markdown_to_html(response.strip())

    async def _handle_other_actions(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._primary_other_nav_options(session, "complete")
        exact_label = exact_option_label(message, options)
        if exact_label is not None:
            response = await self._handle_other_nav_action(session_id, session, exact_label)
            if response is not None:
                return response

        fuzzy_label = match_option_label(message, options)
        if fuzzy_label is not None:
            return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)

        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=(
                self._invalid_text_message()
                if self._is_invalid_user_text(message)
                else self.invalid_message
            ),
            options=options,
            session=session.summary(),
            error=True,
        )

    async def _capture_mitigation_measure(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        mitigation_measure, _ = parse_mitigation_reason(message)
        mitigation_measure = mitigation_measure or message.strip()
        self._clear_mitigation_clarity_state(session)
        self._clear_mitigation_validation_state(session)
        if not mitigation_measure:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="`Mitigation measure:` is required.",
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )

        local_quality_reason = self._local_mitigation_measure_error(mitigation_measure)
        if local_quality_reason:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=local_quality_reason,
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )

        input_review = await self._validate_input_quality(
            session=session,
            purpose=(
                "a mitigation measure for reducing the selected hazard's "
                "negative impact on affected socio-demographic profiles"
            ),
            fields={
                "Mitigation measure": mitigation_measure,
            },
        )
        if input_review is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )
        if not input_review["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=str(input_review["reason"]),
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )

        local_duplicate = self._local_mitigation_duplicate_check(session, mitigation_measure)
        if local_duplicate is not None:
            return self._mitigation_duplicate_suggestion_step(
                session_id,
                session,
                mitigation_measure,
                local_duplicate,
            )

        duplicate_check = await self._semantic_mitigation_duplicate_check(
            session,
            mitigation_measure,
        )
        if duplicate_check is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )
        if duplicate_check["duplicate"]:
            return self._mitigation_duplicate_suggestion_step(
                session_id,
                session,
                mitigation_measure,
                duplicate_check,
            )

        session.pending_mitigation_measure = mitigation_measure
        session.phase = "mitigation_reason"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_reason",
            bot_message=render_message(
                "mitigation_measure_reason.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=format_all_dgs(session),
                mitigation_measure=mitigation_measure,
            ),
            options=[],
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    def _mitigation_duplicate_suggestion_step(
        self,
        session_id: str,
        session: ChatSession,
        proposed_measure: str,
        duplicate_check: dict[str, object],
    ) -> ChatResponse:
        session.pending_mitigation_measure = proposed_measure
        session.suggested_mitigation_measure_id = self._duplicate_mitigation_match_id(
            session,
            duplicate_check,
        )
        session.suggested_mitigation_measure_name = str(
            duplicate_check.get("match") or "the existing mitigation measure"
        ).strip()
        session.phase = "mitigation_duplicate_suggestion"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_duplicate_suggestion",
            bot_message=render_message(
                "mitigation_duplicate_suggestion.md",
                proposed_measure=proposed_measure,
                existing_measure=session.suggested_mitigation_measure_name,
                reason=str(duplicate_check.get("reason") or "").strip(),
            ),
            options=MITIGATION_DUPLICATE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _handle_mitigation_duplicate_suggestion(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_DUPLICATE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_DUPLICATE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Yes"):
            session.phase = "mitigation_duplicate_report"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_duplicate_report",
                bot_message=render_message(
                    "mitigation_existing_report.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    mitigation_measure=(
                        session.suggested_mitigation_measure_name
                        or "Existing mitigation measure"
                    ),
                    reason=self._suggested_mitigation_reason(session),
                    evaluation_report=self._suggested_mitigation_evaluation_report(session),
                ),
                options=MITIGATION_DUPLICATE_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("No"):
            return self._continue_pending_mitigation_reason_step(session_id, session)

        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _handle_mitigation_duplicate_report(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_DUPLICATE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_DUPLICATE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Yes"):
            return self._continue_pending_mitigation_reason_step(session_id, session)

        if action == normalize("No"):
            session.phase = "mitigation_measure"
            session.pending_mitigation_measure = None
            self._clear_mitigation_clarity_state(session)
            session.suggested_mitigation_measure_id = None
            session.suggested_mitigation_measure_name = None
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_measure_reason.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                    mitigation_examples=self._mitigation_measure_examples(session.sector_id),
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=False,
            )

        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _continue_pending_mitigation_reason_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "mitigation_reason"
        self._clear_mitigation_clarity_state(session)
        session.suggested_mitigation_measure_id = None
        session.suggested_mitigation_measure_name = None
        return ChatResponse(
            session_id=session_id,
            step="mitigation_reason",
            bot_message=render_message(
                "mitigation_measure_reason.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=format_all_dgs(session),
                mitigation_measure=session.pending_mitigation_measure
                or "Your proposed mitigation measure",
            ),
            options=[],
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    async def _validate_mitigation_reason(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        reason, evidence = parse_reason_evidence(message)
        if not reason:
            reason = self._plain_reason_from_unlabelled_message(message)
        mitigation_measure = session.pending_mitigation_measure or session.mitigation_measure
        if not mitigation_measure:
            session.phase = "mitigation_measure"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter a mitigation measure first.",
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=True,
            )

        if not reason:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="`Reason:` is required. Evidence URL and evidence file are optional.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if self._is_invalid_user_text(reason) or len(compact_for_match(reason)) < 8:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="The reason is too short or unclear. Please explain the mechanism in a little more detail.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        evidence_text = evidence or session.pending_mitigation_evidence or ""
        evidence_branch = self._has_user_supplied_evidence(evidence_text)
        if evidence_branch and not self._has_readable_evidence_content(evidence_text):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=(
                        "The evidence could not be read as supporting content. Please provide "
                        "a readable published source, such as a DOI/URL with extractable text "
                        "or a supported PDF/DOCX file."
                    ),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        clarity_response = await self._run_mitigation_clarity_track(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
        )
        if clarity_response is not None:
            return clarity_response

        frozen_inputs = session.mitigation_frozen_inputs or {}
        return await self._validate_frozen_mitigation_inputs(
            session_id,
            session,
            frozen_inputs.get("measure_description") or mitigation_measure,
            frozen_inputs.get("justification") or reason,
            frozen_inputs.get("evidence") or evidence_text,
        )

    @staticmethod
    def _plain_reason_from_unlabelled_message(message: str) -> str | None:
        stripped = message.strip()
        if not stripped:
            return None
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            return None
        evidence_markers = (
            "evidence:",
            "evidence url:",
            "evidence file:",
            "evidence content:",
            "temporary evidence",
        )
        if all(line.casefold().startswith(evidence_markers) for line in lines):
            return None
        if lines[0].casefold().startswith(("score:", "mitigation measure:", "mitigation:")):
            return None
        return ChatService._strip_wrapping_quotes(stripped)

    async def _handle_mitigation_clarity_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if not message.strip():
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message="Please answer the clarification questions so I can freeze the mitigation inputs.",
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )
        if self._is_invalid_user_text(message):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=self._invalid_text_message(),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )

        if session.pending_mitigation_clarity_dimension in {
            "target_population",
            "target_population_additional",
        }:
            input_review = {
                "valid": len(compact_for_match(message)) >= 3,
                "reason": "Please describe at least one target group in words.",
            }
        else:
            input_review = await self._validate_clarification_answer_quality(session, message)
        if input_review is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )
        if not input_review.get("valid"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=str(
                        input_review.get("reason")
                        or "Please answer with clear, meaningful text."
                    ),
                ),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )

        mitigation_measure = session.pending_mitigation_measure or ""
        reason = session.pending_mitigation_reason or ""
        evidence_text = session.pending_mitigation_evidence or ""
        if not mitigation_measure or not reason:
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter the mitigation reason again so I can restart the clarity check.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if session.pending_mitigation_clarity_dimension in {
            "target_population",
            "target_population_additional",
        }:
            matched_labels = await self._match_mitigation_target_population_answer(message)
            if not matched_labels:
                if (
                    session.pending_mitigation_clarity_dimension
                    == "target_population_additional"
                ):
                    session.pending_mitigation_clarity_dimension = None
                    return self._mitigation_target_population_review_step(
                        session_id,
                        session,
                        mitigation_measure,
                        reason,
                        evidence_text,
                        error_reason=(
                            "No valid target population group found. Choose **Continue** "
                            "to proceed with the current groups, or **Add more target "
                            "population** to try again."
                        ),
                    )
                return self._mitigation_target_population_clarification_step(
                    session_id,
                    session,
                    mitigation_measure,
                    reason,
                    evidence_text,
                    additional=session.pending_mitigation_clarity_dimension
                    == "target_population_additional",
                    error_reason=(
                        "No valid target population group found. Please try again."
                    ),
                )
            session.mitigation_target_population = self._merge_target_population_labels(
                session.mitigation_target_population or [],
                matched_labels,
            )
            self._append_mitigation_clarification_message(
                session,
                "user",
                "Target population answer: " + message.strip(),
            )
            session.pending_mitigation_clarity_dimension = None
            return self._mitigation_target_population_review_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
            )

        self._append_mitigation_clarification_message(session, "user", message)
        mitigation_measure, reason, evidence_text = self._merge_mitigation_clarification(
            mitigation_measure,
            reason,
            evidence_text,
            message,
            session.pending_mitigation_clarity_dimension,
        )
        clarity_response = await self._run_mitigation_clarity_track(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer=message,
        )
        if clarity_response is not None:
            return clarity_response

        frozen_inputs = session.mitigation_frozen_inputs or {}
        return await self._validate_frozen_mitigation_inputs(
            session_id,
            session,
            frozen_inputs.get("measure_description") or mitigation_measure,
            frozen_inputs.get("justification") or reason,
            frozen_inputs.get("evidence") or evidence_text,
        )

    @staticmethod
    def _merge_mitigation_clarification(
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str,
        clarity_dimension: str | None = None,
    ) -> tuple[str, str, str]:
        answer = clarification_answer.strip()
        if not answer:
            return mitigation_measure, reason, evidence_text
        fields = ChatService._clarification_fields(answer)
        if fields["measure"]:
            mitigation_measure = fields["measure"]
        if fields["justification"]:
            reason = f"{reason}\nClarification: {fields['justification']}".strip()
        if fields["evidence"]:
            evidence_text = f"{evidence_text}\n{fields['evidence']}".strip()
        if not any(fields.values()):
            mitigation_measure, reason, evidence_text = (
                ChatService._merge_unlabelled_mitigation_clarification(
                    mitigation_measure,
                    reason,
                    evidence_text,
                    answer,
                    clarity_dimension,
                )
            )
        return mitigation_measure, reason, evidence_text

    @staticmethod
    def _merge_unlabelled_mitigation_clarification(
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        answer: str,
        clarity_dimension: str | None,
    ) -> tuple[str, str, str]:
        clarification = f"Clarification: {answer}"
        if clarity_dimension == "specificity":
            mitigation_measure = f"{mitigation_measure}\n{clarification}".strip()
        elif clarity_dimension == "evidence_identifiability":
            evidence_text = f"{evidence_text}\n{clarification}".strip()
        else:
            reason = f"{reason}\n{clarification}".strip()
        return mitigation_measure, reason, evidence_text

    @classmethod
    def _clarification_fields(cls, answer: str) -> dict[str, str]:
        buffers: dict[str, list[str]] = {
            "measure": [],
            "justification": [],
            "evidence": [],
        }
        current: str | None = None
        for raw_line in answer.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^(?:\d+[.)]\s*)?([^:]+):\s*(.*)$", line)
            if match:
                key = cls.mitigation_clarity_field_aliases.get(
                    match.group(1).strip().casefold()
                )
                if key:
                    current = key
                    if match.group(2).strip():
                        buffers[key].append(match.group(2).strip())
                    continue
            if current:
                buffers[current].append(line)
        return {key: " ".join(parts).strip() for key, parts in buffers.items()}

    async def _run_mitigation_clarity_track(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str | None = None,
    ) -> ChatResponse | None:
        clarity = await self._assess_mitigation_clarity(
            session,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer,
        )
        if clarity is None or clarity.get("error"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if clarity.get("clear"):
            session.mitigation_frozen_inputs = self._frozen_mitigation_inputs(
                clarity,
                mitigation_measure,
                reason,
                evidence_text,
            )
            return None

        if self._can_freeze_after_mitigation_clarification(
            clarity,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer,
            session.pending_mitigation_clarity_dimension,
        ):
            session.mitigation_frozen_inputs = self._frozen_mitigation_inputs(
                clarity,
                mitigation_measure,
                reason,
                evidence_text,
            )
            return None

        if session.mitigation_clarity_turns >= self.mitigation_clarity_turn_cap:
            self._discard_temporary_evidence(session, evidence_text)
            session.phase = "mitigation_reason"
            self._clear_mitigation_clarity_state(session)
            clarity_reason = str(clarity.get("reason") or "").strip()
            revision_reason = (
                "I still cannot freeze an unambiguous version of the mitigation "
                "measure, justification, and evidence after the clarification limit. "
                "Please resubmit them with more concrete wording."
            )
            if clarity_reason:
                revision_reason = f"{revision_reason} Last clarity issue: {clarity_reason}"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=revision_reason,
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        unresolved_dimension = self._unresolved_mitigation_clarity_dimension(clarity)
        follow_up_questions = self._mitigation_clarification_questions(
            clarity,
            unresolved_dimension,
        )
        question_list = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(follow_up_questions, start=1)
        )
        dimension_label = self.mitigation_clarity_labels.get(
            unresolved_dimension,
            "Mitigation input",
        )
        session.phase = "mitigation_clarity"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        session.pending_mitigation_clarity_dimension = unresolved_dimension
        session.mitigation_clarity_turns += 1
        clarification_prompt = (
            f"Currently clarifying: {dimension_label}\n\n"
            f"Please answer these questions in one response:\n\n{question_list}"
        )
        self._append_mitigation_clarification_message(
            session,
            "assistant",
            clarification_prompt,
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=markdown_to_html(
                "### Clarification needed\n\n"
                f"**Currently clarifying: {dimension_label}**\n\n"
                f"Please answer these questions in one response:\n\n{question_list}\n\n"
                "I will use your answers only to clarify the inputs, not as evidence "
                "that the measure is supported."
            ),
            options=self._mitigation_clarity_options(),
            session=session.summary(),
            input_mode="textarea",
            error=False,
            validation_details=self._clarity_validation_details(
                clarity,
                session,
                unresolved_dimension,
                follow_up_questions,
            ),
        )

    def _mitigation_target_population_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        *,
        additional: bool = False,
        error_reason: str | None = None,
    ) -> ChatResponse:
        session.phase = "mitigation_clarity"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        session.pending_mitigation_clarity_dimension = (
            "target_population_additional" if additional else "target_population"
        )
        if additional:
            question = (
                "Share any additional target population this mitigation measure should "
                "support. Use open text; I will match it to the available target-population groups."
            )
            heading = "Add more target population"
        else:
            question = (
                "I could not identify a target population from the mitigation measure "
                "and reason. Which target groups or population is this mitigation measure "
                "intended to support? Describe every relevant group in your own words."
            )
            heading = "Target population needed"
        message = f"### {heading}\n\n{question}"
        if error_reason:
            message += f"\n\n> {error_reason}"
        self._append_mitigation_clarification_message(session, "assistant", question)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=markdown_to_html(message),
            options=self._mitigation_clarity_options(),
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    async def _ensure_mitigation_target_population_from_inputs(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> ChatResponse:
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        if session.mitigation_target_population is None:
            inferred = await self._infer_mitigation_target_population_from_inputs(
                mitigation_measure,
                reason,
            )
            if inferred:
                session.mitigation_target_population = inferred
            else:
                return self._mitigation_target_population_clarification_step(
                    session_id,
                    session,
                    mitigation_measure,
                    reason,
                    evidence_text,
                )
        return self._mitigation_target_population_review_step(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
        )

    async def _infer_mitigation_target_population_from_inputs(
        self,
        mitigation_measure: str,
        reason: str,
    ) -> list[str]:
        text = (
            f"Mitigation measure:\n{mitigation_measure.strip()}\n\n"
            f"Justification/reason:\n{reason.strip()}"
        )
        return await self._match_mitigation_target_population_answer(text)

    def _mitigation_target_population_review_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        *,
        error_reason: str | None = None,
    ) -> ChatResponse:
        session.phase = "mitigation_target_population_review"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        target_population = self._group_target_population_labels(
            session.mitigation_target_population or []
        )
        target_lines = "\n".join(f"- **{label}**" for label in target_population)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population_review",
            bot_message=markdown_to_html(
                "### Target population identified\n\n"
                "I identified these target-population groups from the mitigation information:\n\n"
                f"{target_lines or '- No target population matched.'}\n\n"
                f"{f'> {error_reason}\n\n' if error_reason else ''}"
                "Choose **Continue** to use these groups, or **Add more target population** "
                "to describe another group in open text."
            ),
            options=self._mitigation_target_population_review_options(),
            session=session.summary(),
            error=bool(error_reason),
        )

    async def _handle_mitigation_target_population_review(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(
            message,
            self._mitigation_target_population_review_options(),
        )
        if exact_label is None:
            fuzzy_label = match_option_label(
                message,
                self._mitigation_target_population_review_options(),
            )
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        mitigation_measure = session.pending_mitigation_measure or session.mitigation_measure or ""
        reason = session.pending_mitigation_reason or session.mitigation_reason or ""
        evidence_text = session.pending_mitigation_evidence or ""
        if not mitigation_measure or not reason:
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter the mitigation measure and reason again.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if action == normalize("Add more target population"):
            return self._mitigation_target_population_clarification_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
                additional=True,
            )

        if action == normalize("Continue"):
            if session.mitigation_validation and session.mitigation_grounded_synthesis:
                return await self._finalize_validated_mitigation(session_id, session)
            clarity_response = await self._run_mitigation_clarity_track(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
            )
            if clarity_response is not None:
                return clarity_response
            frozen_inputs = session.mitigation_frozen_inputs or {}
            return await self._validate_frozen_mitigation_inputs(
                session_id,
                session,
                frozen_inputs.get("measure_description") or mitigation_measure,
                frozen_inputs.get("justification") or reason,
                frozen_inputs.get("evidence") or evidence_text,
            )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population_review",
            bot_message=self.invalid_message,
            options=self._mitigation_target_population_review_options(),
            session=session.summary(),
            error=True,
        )

    @staticmethod
    def _merge_target_population_labels(
        existing: list[str],
        additions: list[str],
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for label in [*existing, *additions]:
            cleaned = str(label or "").strip()
            key = normalize(cleaned)
            if cleaned and key not in seen:
                seen.add(key)
                labels.append(cleaned)
        return labels

    @staticmethod
    def _group_target_population_labels(labels: list[str]) -> list[str]:
        grouped: dict[str, list[str]] = {}
        passthrough: list[str] = []
        for label in labels:
            cleaned = str(label or "").strip()
            if not cleaned:
                continue
            if ":" not in cleaned:
                passthrough.append(cleaned)
                continue
            question, answer = [part.strip() for part in cleaned.split(":", 1)]
            if not question or not answer:
                passthrough.append(cleaned)
                continue
            answers = grouped.setdefault(question, [])
            if normalize(answer) not in {normalize(existing) for existing in answers}:
                answers.append(answer)
        return [
            f"{question}: {', '.join(answers)}"
            for question, answers in grouped.items()
            if answers
        ] + passthrough

    @classmethod
    def _normalize_population_group_labels(cls, labels: list[str]) -> list[str]:
        normalized_labels: list[str] = []
        seen: set[str] = set()
        for label in labels:
            normalized = cls._normalize_population_group_label(label)
            key = normalize(normalized)
            if normalized and key not in seen:
                seen.add(key)
                normalized_labels.append(normalized)
        return normalized_labels

    @classmethod
    def _normalize_population_group_label(cls, label: object) -> str:
        raw = re.sub(r"\s+", " ", str(label or "")).strip(" .")
        if not raw:
            return ""
        question = ""
        answer = ""
        if ":" in raw:
            question, answer = [part.strip() for part in raw.split(":", 1)]
        question_key = normalize_for_match(question)
        answer_key = normalize_for_match(answer)
        full_key = normalize_for_match(raw)
        compact_key = compact_for_match(raw)

        mapped = cls._population_group_from_question_answer(question_key, answer_key)
        if mapped:
            return mapped

        phrase_map: tuple[tuple[tuple[str, ...], str], ...] = (
            (
                (
                    "households with repeated utility bill arrears",
                    "repeated utility bill arrears",
                    "utility bill arrears",
                    "utility arrears",
                    "arrears on utility bills",
                    "struggling to pay bills each month",
                    "high energy bills",
                    "issue high energy bills",
                    "energy affordability",
                ),
                "Households experiencing energy affordability challenges",
            ),
            (
                (
                    "living in a house with low energy efficiency",
                    "low energy efficiency",
                    "energy inefficient homes",
                    "energy inefficient housing",
                    "poorly insulated homes",
                    "poor insulation",
                ),
                "Residents of energy-inefficient homes",
            ),
            (
                (
                    "countries with higher electricity consumption",
                    "higher electricity consumption",
                    "electricity consumption",
                    "high energy consumption",
                ),
                "Residents of high-energy-consumption regions",
            ),
            (
                (
                    "countries with higher cold home pct",
                    "cold home pct",
                    "cold homes",
                    "inadequate heating",
                ),
                "Residents of cold or inadequately heated homes",
            ),
            (
                (
                    "countries with higher cost overburden",
                    "cost overburden",
                    "housing cost overburden",
                ),
                "Households facing housing-cost pressure",
            ),
            (
                (
                    "damp",
                    "mould",
                    "mold",
                    "leak",
                    "rot",
                    "home problems count",
                    "higher home problems count",
                ),
                "Residents of poor-quality housing",
            ),
            (
                ("low income", "income poor", "financially vulnerable"),
                "Low-income households",
            ),
            (
                ("medium income", "middle income"),
                "Middle-income households",
            ),
            (
                ("high income", "wealthy households"),
                "High-income households",
            ),
            (
                ("tenant", "tenants", "renters", "renting"),
                "Tenant households",
            ),
            (
                ("homeowner", "home owner", "home owners", "owner occupiers"),
                "Homeowner households",
            ),
            (
                ("unemployed", "jobless"),
                "Unemployed people",
            ),
            (
                ("retired", "retirees", "pensioners"),
                "Retired people",
            ),
            (
                ("women", "woman", "female"),
                "Women",
            ),
            (
                ("non binary", "nonbinary"),
                "Non-binary people",
            ),
            (
                ("people with disabilities", "disabled people", "long term condition", "chronic illness"),
                "People with disabilities or long-term conditions",
            ),
            (
                ("rural residents", "rural area", "remote communities"),
                "Rural residents",
            ),
            (
                ("urban residents", "urban area", "city residents"),
                "Urban residents",
            ),
        )
        for phrases, canonical in phrase_map:
            for phrase in phrases:
                phrase_key = normalize_for_match(phrase)
                phrase_compact = compact_for_match(phrase)
                if (
                    phrase_key and phrase_key in full_key
                    or phrase_compact and phrase_compact in compact_key
                ):
                    return canonical

        if question_key.startswith("countries with higher"):
            remainder = re.sub(
                r"(?i)^countries with higher\s+",
                "",
                raw,
            ).strip()
            if remainder:
                descriptor = cls._population_region_descriptor(remainder)
                return f"Residents of {descriptor} regions"
        if full_key.startswith("countries with higher"):
            remainder = re.sub(
                r"(?i)^countries with higher\s+",
                "",
                raw,
            ).strip()
            if remainder:
                descriptor = cls._population_region_descriptor(remainder)
                return f"Residents of {descriptor} regions"

        return cls._people_centric_label(raw)

    @staticmethod
    def _population_group_from_question_answer(question_key: str, answer_key: str) -> str:
        if not question_key:
            return ""
        mappings: dict[tuple[str, str], str] = {
            ("level of income", "low income"): "Low-income households",
            ("level of income", "medium income"): "Middle-income households",
            ("level of income", "high income"): "High-income households",
            ("living in a house with low energy efficiency", "yes"): (
                "Residents of energy-inefficient homes"
            ),
            ("tenancy status", "tenant"): "Tenant households",
            ("tenancy status", "homeowner"): "Homeowner households",
            ("economic status", "unemployed"): "Unemployed people",
            ("economic status", "employed"): "Employed people",
            ("economic status", "retired"): "Retired people",
            ("gender", "woman"): "Women",
            ("gender", "male"): "Men",
            ("gender", "non binary"): "Non-binary people",
            ("disability of long term condition", "yes"): (
                "People with disabilities or long-term conditions"
            ),
            ("location of residency", "urban area"): "Urban residents",
            ("location of residency", "suburban area"): "Suburban residents",
            ("location of residency", "rural area"): "Rural residents",
            ("need of a car to perform daily activities", "yes"): "Car-dependent residents",
            ("care responsibility as the main activity", "yes remunerated"): "Paid carers",
            ("care responsibility as the main activity", "yes non remunerated"): "Unpaid carers",
            ("eu citizenship", "no"): "Non-EU citizens",
            ("eu citizenship", "yes"): "EU citizens",
            ("level of education", "no formal education"): "People with no formal education",
            ("level of education", "primary"): "People with primary education",
            ("level of education", "secondary"): "People with secondary education",
            ("level of education", "further normal education"): (
                "People with further or higher education"
            ),
            ("age range", "18"): "Children and young people",
            ("age range", "25 35"): "Young adults",
            ("age range", "35 65"): "Working-age adults",
            ("age range", "65"): "Older adults",
        }
        mapped = mappings.get((question_key, answer_key))
        if mapped:
            return mapped
        if answer_key in {"yes", "no"}:
            descriptor = ChatService._humanize_population_fragment(question_key)
            return f"People affected by {descriptor}" if descriptor else ""
        if answer_key:
            return ChatService._people_centric_label(
                ChatService._humanize_population_fragment(answer_key)
            )
        return ""

    @staticmethod
    def _population_region_descriptor(value: str) -> str:
        fragment = ChatService._humanize_population_fragment(value)
        fragment = re.sub(r"\b(pct|percentage|rate|count)\b", "", fragment, flags=re.I)
        fragment = re.sub(r"\s+", " ", fragment).strip().lower()
        if not fragment:
            return "higher-risk"
        return fragment.replace(" ", "-")

    @staticmethod
    def _humanize_population_fragment(value: str) -> str:
        words = normalize_for_match(value)
        replacements = {
            "electricity consumption": "electricity consumption",
            "cold home": "cold homes",
            "cost overburden": "housing cost pressure",
            "home problems count": "home-quality problems",
        }
        for old, new in replacements.items():
            words = words.replace(old, new)
        return re.sub(r"\s+", " ", words).strip()

    @staticmethod
    def _people_centric_label(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .")
        if not cleaned:
            return ""
        lower = cleaned.casefold()
        people_prefixes = (
            "people",
            "households",
            "residents",
            "families",
            "workers",
            "tenants",
            "homeowners",
            "women",
            "men",
            "children",
            "older adults",
            "young adults",
            "students",
            "carers",
            "businesses",
            "communities",
        )
        if lower.startswith(people_prefixes):
            return cleaned[:1].upper() + cleaned[1:]
        if any(term in lower for term in ("household", "family", "families")):
            return cleaned[:1].upper() + cleaned[1:]
        if "business" in lower or "sme" in lower:
            return cleaned[:1].upper() + cleaned[1:]
        return f"People in {cleaned[:1].lower() + cleaned[1:]}"

    @staticmethod
    def _append_mitigation_clarification_message(
        session: ChatSession,
        role: str,
        content: str,
    ) -> None:
        clean_content = content.strip()
        if not clean_content:
            return
        history = session.mitigation_clarification_history or []
        history.append({"role": role, "content": clean_content})
        session.mitigation_clarification_history = history

    @staticmethod
    def _mitigation_clarification_history_block(
        session: ChatSession,
        clarification_answer: str | None = None,
    ) -> str:
        history = [
            entry
            for entry in (session.mitigation_clarification_history or [])
            if isinstance(entry, dict)
            and str(entry.get("role") or "").strip()
            and str(entry.get("content") or "").strip()
        ]
        latest_answer = (clarification_answer or "").strip()
        if latest_answer and not any(
            entry.get("role") == "user"
            and str(entry.get("content") or "").strip() == latest_answer
            for entry in history
        ):
            history.append({"role": "user", "content": latest_answer})
        if not history:
            return "None yet"
        return "\n\n".join(
            f"{str(entry['role']).strip().title()}:\n{str(entry['content']).strip()}"
            for entry in history
        )

    @classmethod
    def _unresolved_mitigation_clarity_dimension(
        cls,
        clarity: dict[str, object],
    ) -> str | None:
        dimensions = clarity.get("dimensions")
        if not isinstance(dimensions, dict):
            return None
        return next(
            (
                dimension
                for dimension in cls.mitigation_clarity_dimensions
                if dimensions.get(dimension) == "NEEDS_CLARIFICATION"
            ),
            None,
        )

    @classmethod
    def _mitigation_clarification_questions(
        cls,
        clarity: dict[str, object],
        unresolved_dimension: str | None,
    ) -> list[str]:
        raw_questions = clarity.get("follow_up_questions")
        questions = [
            str(question).strip()
            for question in raw_questions
            if str(question).strip()
        ] if isinstance(raw_questions, list) else []
        if not questions:
            legacy_question = str(clarity.get("follow_up_question") or "").strip()
            if legacy_question:
                questions.append(legacy_question)

        fallback_questions = cls.mitigation_clarity_fallback_questions.get(
            unresolved_dimension,
            cls.mitigation_clarity_default_questions,
        )
        for question in fallback_questions:
            if len(questions) >= 2:
                break
            if question not in questions:
                questions.append(question)
        return questions[:3]

    def _can_freeze_after_mitigation_clarification(
        self,
        clarity: dict[str, object],
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str | None,
        answered_dimension: str | None = None,
    ) -> bool:
        dimensions = clarity.get("dimensions")
        if not isinstance(dimensions, dict):
            return False

        unresolved = {
            key
            for key, value in dimensions.items()
            if value != "CLEAR"
        }
        allowed_unresolved = answered_dimension or "justification_clarity"
        if unresolved != {allowed_unresolved}:
            return False

        clarification = (clarification_answer or "").strip()
        if not clarification and "Clarification:" in reason:
            clarification = reason.rsplit("Clarification:", 1)[1].strip()
        if not clarification:
            return False
        if self._is_invalid_user_text(clarification):
            return False
        if len(compact_for_match(clarification)) < 12:
            return False
        if len(compact_for_match(mitigation_measure)) < 8:
            return False
        if len(compact_for_match(reason)) < 30:
            return False
        if evidence_text and self._is_invalid_user_text(evidence_text):
            return False
        if answered_dimension == "evidence_identifiability" and not evidence_text:
            return False
        return True

    @staticmethod
    def _frozen_mitigation_inputs(
        clarity: dict[str, object],
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> dict[str, str]:
        frozen = clarity.get("frozen_inputs")
        if not isinstance(frozen, dict):
            frozen = {}
        return {
            "measure_description": str(
                frozen.get("measure_description") or mitigation_measure
            ).strip(),
            "justification": str(frozen.get("justification") or reason).strip(),
            # Evidence is an immutable source reference/content payload. Do not
            # let the clarity model replace an absent value with "Not provided"
            # or rewrite uploaded-document identifiers.
            "evidence": evidence_text.strip(),
        }

    @staticmethod
    def _normalized_mitigation_evidence(evidence: str | None) -> str:
        clean_evidence = str(evidence or "").strip()
        if clean_evidence.casefold() in {
            "none",
            "none yet",
            "not provided",
            "no evidence",
            "no evidence provided",
            "n/a",
        }:
            return ""
        return clean_evidence

    async def _validate_frozen_mitigation_inputs(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> ChatResponse:
        evaluated_inputs = {
            "measure_description": mitigation_measure.strip(),
            "justification": reason.strip(),
            "evidence": self._normalized_mitigation_evidence(evidence_text),
        }
        mitigation_measure = evaluated_inputs["measure_description"]
        reason = evaluated_inputs["justification"]
        evidence_text = evaluated_inputs["evidence"]
        session.mitigation_frozen_inputs = evaluated_inputs
        session.phase = "mitigation_reason"
        evidence_branch = self._has_user_supplied_evidence(evidence_text)
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text

        validation = await self._validate_mitigation_against_stats(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            evidence=evidence_text,
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        validation["evaluated_inputs"] = evaluated_inputs.copy()
        outcome = str(validation.get("outcome") or ("PASS" if validation["valid"] else "REJECT"))
        if outcome == "REJECT":
            self._discard_temporary_evidence(session, evidence_text)
            session.pending_mitigation_evidence = None
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_rejected.md",
                    reason=self._mitigation_outcome_reason(validation, "REJECT"),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
                validation_details=self._grounding_validation_details(session, validation),
            )
        if outcome == "ABSTAIN":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_abstained.md",
                    reason=self._mitigation_outcome_reason(validation, "ABSTAIN"),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                input_values={
                    "reason": reason,
                    "evidence_url": self._evidence_url(evidence_text),
                },
                error=False,
                validation_details=self._grounding_validation_details(session, validation),
            )

        synthesis = await self._grounded_mitigation_synthesis(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            validation=validation,
        )
        if synthesis is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if evidence_branch:
            self._promote_temporary_evidence(
                session,
                target_scope="quarantined",
                provenance="validated_user_evidence",
            )
            await self._admit_inline_evidence_to_quarantine(
                session,
                evidence_text,
                validation,
            )
        session.mitigation_measure = mitigation_measure
        session.mitigation_reason = reason
        session.mitigation_validation = validation
        session.mitigation_grounded_synthesis = synthesis
        session.pending_mitigation_measure = None
        self._clear_mitigation_clarity_state(session)
        return await self._ensure_mitigation_target_population_from_inputs(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
        )

    async def _finalize_validated_mitigation(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        if session.selected_hazard_record_id is None and session.selected_hazard:
            hazard_record = self._ensure_user_hazard(
                session_id,
                session,
                session.selected_hazard,
                source="system",
            )
            session.selected_hazard_record_id = hazard_record.id if hazard_record else None
        session.mitigation_record_id = self._store_mitigation_measure(
            session.selected_hazard_record_id,
            session.mitigation_measure or "",
            session.mitigation_reason or "",
            session.mitigation_target_population,
        )
        self._record_activity(
            session_id,
            session,
            "mitigation_measure_validated",
            session.mitigation_measure or "",
        )
        return await self._mitigation_review_step(session_id, session)

    def _mitigation_target_population_labels(self, session: ChatSession) -> list[str]:
        if session.sector_id is None or not session.selected_hazard:
            return []
        rows = self.db.execute(
            select(
                SystemHazardSocioDemographic.profile,
                SystemHazardSocioDemographic.variable_name,
                SystemHazardSocioDemographic.explanation,
                SystemHazardSocioDemographic.statistical_basis,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .outerjoin(
                SystemHazardSocioDemographicTargetPopulation,
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id
                == SystemHazardSocioDemographic.id,
            )
            .outerjoin(
                QuestionOption,
                QuestionOption.id
                == SystemHazardSocioDemographicTargetPopulation.question_option_id,
            )
            .outerjoin(
                EvaluationQuestion,
                and_(
                    EvaluationQuestion.id == QuestionOption.question_id,
                    EvaluationQuestion.active.is_(True),
                    EvaluationQuestion.category == "target_population",
                ),
            )
            .where(
                SystemHazard.sector_id == session.sector_id,
                func.lower(SystemHazard.name) == session.selected_hazard.casefold(),
                SystemHazardSocioDemographic.sector_id == session.sector_id,
            )
            .order_by(
                SystemHazardSocioDemographic.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
        ).all()
        if not rows:
            stored_profile_labels = self._target_population_labels_from_stored_profiles(session)
            if stored_profile_labels:
                return stored_profile_labels
            selected_labels = self._selected_target_population_labels(session)
            return selected_labels or self._selected_hazard_profile_names_for_venn(session)

        labels: list[str] = []
        seen: set[str] = set()
        labels_by_profile: dict[str, list[str]] = {}
        profile_names: dict[str, str] = {}
        excluded_profile_keys = self._selected_hazard_or_below_one_profile_keys(session)
        for row in rows:
            profile_name = str(row.profile or "").strip()
            if not profile_name:
                continue
            if normalize(profile_name) in excluded_profile_keys:
                continue
            variable_name = str(row.variable_name or "").strip()
            if self._system_profile_has_or_below_one_effect(
                session,
                variable_name,
                profile_name,
            ):
                continue
            if self._profile_has_odds_ratio_below_one(
                {
                    "name": profile_name,
                    "profile": profile_name,
                    "variable_name": variable_name,
                    "explanation": str(row.explanation or ""),
                    "statistical_basis": str(row.statistical_basis or ""),
                }
            ):
                continue
            profile_key = normalize(profile_name)
            profile_names.setdefault(profile_key, profile_name)
            if row.question and row.option:
                profile_labels = labels_by_profile.setdefault(profile_key, [])
                label = f"{row.question}: {row.option}"
                if normalize(label) not in {normalize(item) for item in profile_labels}:
                    profile_labels.append(label)

        for profile_key, profile_name in profile_names.items():
            profile_labels = labels_by_profile.get(profile_key) or [profile_name]
            for label in profile_labels:
                key = normalize(label)
                if key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    def _target_population_labels_from_stored_profiles(self, session: ChatSession) -> list[str]:
        if not session.selected_hazard:
            return []
        labels: list[str] = []
        seen: set[str] = set()
        for profile in self._stored_hazard_profiles(session, session.selected_hazard):
            profile_labels = profile.get("target_population_labels")
            values = profile_labels if isinstance(profile_labels, list) else []
            for value in values:
                label = str(value or "").strip()
                key = normalize(label)
                if label and key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    def _selected_hazard_or_below_one_profile_keys(self, session: ChatSession) -> set[str]:
        if not session.selected_hazard:
            return set()
        return {
            normalize(str(profile.get("name") or profile.get("profile") or ""))
            for profile in self._stored_hazard_profiles(session, session.selected_hazard)
            if self._profile_has_odds_ratio_below_one(profile)
        }

    def _system_profile_has_or_below_one_effect(
        self,
        session: ChatSession,
        variable_name: str,
        profile_name: str,
    ) -> bool:
        if not session.sector or not session.selected_hazard:
            return False
        candidates = self._effect_predictor_candidates(variable_name, profile_name)
        if not candidates:
            return False
        hazard_key = slugify_hazard(session.selected_hazard)
        for row in hazard_predictor_effect_rows(sector=session.sector, min_or=0.0):
            row_hazard = slugify_hazard(str(row.get("hazard") or ""))
            if row_hazard != hazard_key:
                continue
            predictor = normalize_for_match(str(row.get("predictor") or ""))
            if not predictor:
                continue
            if not any(
                predictor == candidate
                or predictor.startswith(f"{candidate} ")
                or candidate.startswith(f"{predictor} ")
                for candidate in candidates
            ):
                continue
            try:
                return float(row.get("odds_ratio") or 0) < 1
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _effect_predictor_candidates(variable_name: str, profile_name: str) -> set[str]:
        candidates: set[str] = set()
        for value in (variable_name, profile_name):
            cleaned = str(value or "").strip()
            if not cleaned:
                continue
            candidates.add(normalize_for_match(cleaned))
            if ":" in cleaned:
                question, answer = [part.strip() for part in cleaned.split(":", 1)]
                if question:
                    candidates.add(normalize_for_match(question))
                if question and answer:
                    candidates.add(normalize_for_match(f"{question} {answer}"))
                    candidates.add(normalize_for_match(f"{question}__{answer}"))
        return {candidate for candidate in candidates if candidate}

    def _selected_hazard_profile_names_for_venn(self, session: ChatSession) -> list[str]:
        if not session.selected_hazard:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for profile in self._stored_hazard_profiles(session, session.selected_hazard):
            if self._profile_has_odds_ratio_below_one(profile):
                continue
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            key = normalize(name)
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        return names

    def _affected_profile_target_population_labels(self, session: ChatSession) -> list[str]:
        return (
            self._mitigation_target_population_labels(session)
            or self._selected_target_population_labels(session)
        )

    def _mitigation_target_population_options(self, session: ChatSession) -> list[Option]:
        # Kept for restoring legacy sessions; mitigation no longer uses the
        # target-population quick-select dialog.
        return []

    @staticmethod
    def _selected_target_population_labels(session: ChatSession) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for answer in session.target_population_answers or []:
            question = str(answer.get("question") or "Target population").strip()
            selected = answer.get("selected")
            values = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for value in values:
                option = str(value).strip()
                label = f"{question}: {option}" if question and option else option
                key = normalize(label)
                if label and key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    async def _match_mitigation_target_population_answer(self, answer: str) -> list[str]:
        rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, QuestionOption.id)
        ).all()
        if not rows:
            return []

        allowed_ids = {int(row.id) for row in rows}
        option_catalogue = "\n".join(
            f"- {int(row.id)} | {row.question}: {row.option}" for row in rows
        )
        context = """
You extract mitigation target-population groups from free text.

First map groups to the fixed target-population option list when an option is
explicitly stated or clearly implied. Then, if the text contains a valid target
population group that is not represented by the fixed option list, include that
group in additional_groups.

Valid target population groups are recognizable groups of people, households,
businesses, institutions, communities, or places that could be targeted by a
mitigation measure. Do not include policy actions, hazards, mechanisms, vague
concepts, evidence sources, or random/unrecognizable text. Do not invent
characteristics. Do not repeat an additional group already covered by a selected
option.

Return JSON only in this form:
{"option_ids": [1, 2], "additional_groups": ["small businesses"]}

Use empty arrays when no valid target population group is found.
""".strip()
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Target-group answer:\n{answer.strip()}\n\n"
                        f"Available options:\n{option_catalogue}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=220,
        )
        matched_ids: set[int] = set()
        additional_groups: list[str] = []
        if not is_llm_unavailable_response(response):
            try:
                parsed = json.loads(self._extract_json_object(response))
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = {}
            raw_ids = parsed.get("option_ids") if isinstance(parsed, dict) else []
            if isinstance(raw_ids, list):
                for raw_id in raw_ids:
                    try:
                        option_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if option_id in allowed_ids:
                        matched_ids.add(option_id)
            raw_groups = parsed.get("additional_groups") if isinstance(parsed, dict) else []
            if isinstance(raw_groups, list):
                for raw_group in raw_groups:
                    group = re.sub(r"\s+", " ", str(raw_group or "")).strip()
                    if self._is_valid_custom_target_population_group(group):
                        additional_groups.append(group)

        matched_ids.update(self._fallback_target_population_option_ids(answer, rows))
        labels = [
            f"{row.question}: {row.option}"
            for row in rows
            if int(row.id) in matched_ids
        ]
        return self._merge_target_population_labels(labels, additional_groups)

    @staticmethod
    def _is_valid_custom_target_population_group(group: str) -> bool:
        cleaned = re.sub(r"\s+", " ", str(group or "")).strip(" .,:;")
        if len(cleaned) < 3:
            return False
        if len(cleaned) > 120:
            return False
        normalized = normalize_for_match(cleaned)
        compact_key = compact_for_match(cleaned)
        if len(normalized) < 3:
            return False
        if re.fullmatch(r"(.)\1{3,}", normalized):
            return False
        invalid_terms = {
            "none",
            "no",
            "noadditional",
            "notapplicable",
            "n/a",
            "na",
            "policy",
            "mitigation",
            "hazard",
            "measure",
            "evidence",
            "targetpopulation",
            "noadditionaltargetpopulation",
            "notargetpopulation",
            "noneidentified",
        }
        if normalized in {normalize_for_match(term) for term in invalid_terms}:
            return False
        compact_invalid_terms = {compact_for_match(term) for term in invalid_terms}
        if compact_key in compact_invalid_terms:
            return False
        if compact_key.startswith("noadditional") or compact_key.startswith("notarget"):
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))

    @staticmethod
    def _fallback_target_population_option_ids(answer: str, rows: list[object]) -> set[int]:
        text = f" {normalize_for_match(answer)} "
        phrase_map: dict[tuple[str, str], tuple[str, ...]] = {
            ("age range", "18"): ("children", "child", "minors", "under 18", "youth"),
            ("age range", "25 35"): ("young adults", "aged 25 35", "25 to 35"),
            ("age range", "35 65"): ("middle aged", "working age", "aged 35 65", "35 to 65"),
            ("age range", "65"): ("older", "older adults", "older people", "elderly", "seniors", "over 65"),
            ("living in a house with low energy efficiency", "yes"): ("energy inefficient homes", "low energy efficiency", "poorly insulated", "cold homes"),
            ("gender", "woman"): ("women", "woman", "female"),
            ("gender", "male"): ("men", "man", "male"),
            ("gender", "non binary"): ("non binary", "nonbinary"),
            ("need of a car to perform daily activities", "yes"): ("car dependent", "car reliance", "need a car"),
            ("level of education", "no formal education"): ("no formal education",),
            ("level of education", "primary"): ("primary education",),
            ("level of education", "secondary"): ("secondary education",),
            ("level of education", "further normal education"): ("further education", "higher education"),
            ("location of residency", "urban area"): ("urban residents", "urban areas", "city residents"),
            ("location of residency", "suburban area"): ("suburban residents", "suburban areas"),
            ("location of residency", "rural area"): ("rural residents", "rural areas", "remote communities"),
            ("economic status", "employed"): ("employed people", "workers"),
            ("economic status", "unemployed"): ("unemployed", "jobless"),
            ("economic status", "retired"): ("retired people", "retirees", "pensioners"),
            ("care responsibility as the main activity", "yes remunerated"): ("paid carers", "paid caregivers"),
            ("care responsibility as the main activity", "yes non remunerated"): ("unpaid carers", "unpaid caregivers", "informal carers"),
            ("eu citizenship", "yes"): ("eu citizens",),
            ("eu citizenship", "no"): ("non eu citizens", "non eu migrants"),
            ("disability of long term condition", "yes"): ("people with disabilities", "disabled people", "long term condition", "chronic illness"),
            ("level of income", "low income"): ("low income", "income poor", "financially vulnerable"),
            ("level of income", "medium income"): ("middle income", "medium income"),
            ("level of income", "high income"): ("high income", "wealthy households"),
            ("tenancy status", "homeowner"): ("homeowners", "home owners", "owner occupiers"),
            ("tenancy status", "tenant"): ("tenants", "renters", "people who rent", "renting", "rent", "rented housing"),
        }
        matched: set[int] = set()
        for row in rows:
            key = (
                normalize_for_match(str(row.question)),
                normalize_for_match(str(row.option)),
            )
            if any(
                f" {normalize_for_match(phrase)} " in text
                for phrase in phrase_map.get(key, ())
            ):
                matched.add(int(row.id))
        return matched

    def _mitigation_target_population_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        options = self._mitigation_target_population_options(session)
        if not options:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message="No target population selection is required.",
                options=[],
                session=session.summary(),
                error=False,
            )
        session.phase = "mitigation_target_population"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population",
            bot_message=render_message(
                "mitigation_target_population.md",
                error_reason=error_reason or "",
            ),
            options=options,
            session=session.summary(),
            input_mode="target_population_multi",
            error=bool(error_reason),
        )

    async def _handle_mitigation_target_population(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        return await self._create_mitigation_measure_step(
            session_id,
            session,
        )

    async def _handle_mitigation_target_population_batch(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        raw_json = message.split(":", 1)[1].strip()
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, list):
            return self._mitigation_target_population_step(
                session_id, session, error_reason="Please submit valid target-population selections."
            )

        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        labels: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                question_id = int(item.get("question_id"))
            except (TypeError, ValueError):
                continue
            question = questions_by_id.get(question_id)
            answers = item.get("answers")
            if question is None or not isinstance(answers, list):
                continue
            allowed = {
                normalize(str(option)): str(option)
                for option in question.get("options", [])
            }
            for answer in answers:
                option = allowed.get(normalize(str(answer)))
                if not option:
                    continue
                label = f"{question['question']}: {option}"
                key = normalize(label)
                if key not in seen:
                    seen.add(key)
                    labels.append(label)

        if not labels:
            return self._mitigation_target_population_step(
                session_id,
                session,
                error_reason="Select at least one target-population option in the dialog.",
            )
        session.mitigation_target_population = labels
        return await self._create_mitigation_measure_step(
            session_id, session, target_population_confirmed=True
        )

    @staticmethod
    def _mitigation_target_population_text(session: ChatSession) -> str:
        return ", ".join(session.mitigation_target_population or []) or "Not specified"

    async def _mitigation_review_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "mitigation_review"
        answer = session.mitigation_grounded_synthesis or await self._mitigation_review_response(
            session,
            (
                "Provide a concise conclusion about the validated mitigation measure. "
                "Include related statistical context, affected groups, expected strengths, "
                "and limitations. Do not ask evaluation questions yet."
            ),
        )
        self._update_mitigation_review_details(
            session,
            answer,
            self._mitigation_target_affected_groups_json(session),
        )
        affected_target_populations = self._normalize_population_group_labels(
            self._affected_profile_target_population_labels(session)
        )
        mitigation_target_populations = self._normalize_population_group_labels(
            session.mitigation_target_population or []
        )
        affected_target_population_display = self._group_target_population_labels(
            affected_target_populations
        )
        mitigation_target_population_display = self._group_target_population_labels(
            mitigation_target_populations
        )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_review",
            bot_message=(
                render_message(
                    "mitigation_review.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    reason=session.mitigation_reason or "Not provided",
                    target_population=", ".join(
                        mitigation_target_population_display
                    ),
                    affected_target_population_json=json.dumps(
                        affected_target_populations,
                        ensure_ascii=False,
                    ),
                    mitigation_target_population_json=json.dumps(
                        mitigation_target_populations,
                        ensure_ascii=False,
                    ),
                    affected_target_populations=affected_target_population_display,
                    mitigation_target_populations=mitigation_target_population_display,
                    show_target_population_venn=bool(
                        affected_target_populations and mitigation_target_populations
                    ),
                    review=answer,
                )
            ),
            options=MITIGATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=False,
            validation_details=self._grounding_validation_details(session),
        )

    async def _handle_mitigation_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_REVIEW_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_REVIEW_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label

        if normalize(exact_label or "") == normalize("Move to next step"):
            return self._start_evaluation_questions(session_id, session)

        local_reason = None
        if self._is_invalid_user_text(message):
            local_reason = (
                "The question appears to contain gibberish, keyboard mashing, "
                "or unrecognizable text."
            )
        elif len(compact_for_match(message)) < 4:
            local_reason = "The question is too short to understand."
        if local_reason:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=local_reason,
                ),
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                input_mode="mitigation_review",
                error=True,
            )

        input_review = await self._validate_input_quality(
            session=session,
            purpose=(
                "A follow-up question or request about the already validated "
                "mitigation measure and its reasoning."
            ),
            fields={"Follow-up question": message},
        )
        if input_review is not None and not input_review.get("valid"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=str(input_review.get("reason") or "Please rewrite the question."),
                ),
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                input_mode="mitigation_review",
                error=True,
            )

        answer = await self._mitigation_review_response(session, message)
        if session.stats_conversation is None:
            session.stats_conversation = []
        session.stats_conversation.extend(
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ]
        )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_review",
            bot_message=markdown_to_html(answer),
            options=MITIGATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _start_evaluation_questions(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.evaluation_questions = self._evaluation_questions()
        session.evaluation_index = 0
        session.evaluation_answers = []

        if not session.evaluation_questions:
            session.phase = "evaluation_complete"
            self._promote_temporary_evidence(session)
            return ChatResponse(
                session_id=session_id,
                step="evaluation_complete",
                bot_message=render_message(
                    "mitigation_recorded.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    reason=session.mitigation_reason or "Not provided",
                ),
                options=[],
                session=session.summary(),
                error=False,
            )

        session.phase = "evaluation_question"
        return self._evaluation_question_step(session_id, session)

    async def _handle_evaluation_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        score, reason, evidence = parse_evaluation_answer(message)
        if score is None:
            return self._evaluation_question_step(
                session_id,
                session,
                error_reason="Please provide a score from 1 to 10.",
            )

        question = self._current_evaluation_question(session)
        if question is None:
            return self._evaluation_complete_step(session_id, session)

        if reason or evidence:
            evidence_text = self._evaluation_evidence_text(evidence)
            input_review = await self._validate_input_quality(
                session=session,
                purpose=(
                    "an optional evaluation reason and optional evidence supporting "
                    "the selected mitigation score"
                ),
                fields=self._reason_evidence_quality_fields(reason or "", evidence_text),
            )
            if input_review is None:
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=(
                        "I could not validate the reason and evidence because the "
                        "local LLM is unavailable. Please try this question again."
                    ),
                )
            if not input_review["valid"]:
                self._discard_temporary_evidence(session, evidence or "")
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=str(input_review["reason"]),
                )

            validation = await self._validate_evaluation_answer_against_stats(
                session=session,
                question=question,
                score=score,
                reason=reason or "",
                evidence=evidence_text or "",
            )

            if validation is None:
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=(
                        "I could not validate the reason and evidence because the "
                        "local LLM is unavailable. Please try this question again."
                    ),
                )

            if not validation["valid"]:
                self._discard_temporary_evidence(session, evidence or "")
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=str(validation["reason"]),
                )

        if session.evaluation_answers is None:
            session.evaluation_answers = []
        session.evaluation_answers.append(
            {
                "question_id": question["id"],
                "category": question["category"],
                "chart_title": question.get("chart_title") or question["question"],
                "question": question["question"],
                "score": score,
                "reason": reason,
                "evidence": self._evaluation_evidence_text(evidence),
            }
        )
        self._store_question_response(
            session_id,
            session,
            question_id=int(question["id"]),
            category=str(question["category"]),
            response_text=str(score),
            score=score,
            reason=reason,
            evidence=self._evaluation_evidence_text(evidence),
            hazard_id=session.selected_hazard_record_id,
            mitigation_measure_id=session.mitigation_record_id,
        )
        self._record_activity(
            session_id,
            session,
            "evaluation_question_answered",
            f"{question['category']}: {question['question']} -> {score}",
        )
        session.evaluation_index += 1

        if session.evaluation_index >= len(session.evaluation_questions or []):
            return self._evaluation_complete_step(session_id, session)

        return self._evaluation_question_step(session_id, session)

    async def _capture_additional_dgs(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if message.strip() == "Quick Select Target Population":
            return self._additional_dg_question_step(session_id, session)
        if message.strip().startswith("TARGET_POPULATION_BATCH:"):
            return await self._handle_additional_dg_batch(session_id, session, message)

        question = self._current_target_population_question(session)
        if question is None:
            return await self._finalize_additional_dg_questions(session_id, session)

        options = self._target_population_options(question)
        selected_labels = self._target_population_selected_labels(message, options)
        if not selected_labels:
            return self._additional_dg_question_step(
                session_id,
                session,
                error_reason="Please choose one or more listed socio-demographic options.",
            )

        if any(normalize(label) == normalize("Skip all") for label in selected_labels):
            session.target_population_index = len(session.target_population_questions or [])
            return await self._finalize_additional_dg_questions(session_id, session)

        if any(normalize(label) == normalize("Skip") for label in selected_labels):
            session.target_population_index += 1
            if session.target_population_index >= len(session.target_population_questions or []):
                return await self._finalize_additional_dg_questions(session_id, session)
            return self._additional_dg_question_step(session_id, session)

        self._record_additional_dg_answer(session_id, session, question, selected_labels)
        session.target_population_index += 1
        if session.target_population_index >= len(session.target_population_questions or []):
            return await self._finalize_additional_dg_questions(session_id, session)

        return self._additional_dg_question_step(session_id, session)

    async def _validate_dgs_against_stats(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, DG_REASON_EVIDENCE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, DG_REASON_EVIDENCE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        skipped_reason_evidence = normalize(exact_label or message) == normalize("Skip")
        if skipped_reason_evidence:
            message = ""

        reason, evidence = parse_reason_evidence(message)
        if reason or evidence:
            input_review = await self._validate_input_quality(
                session=session,
                purpose=(
                    "an optional reason and optional evidence explaining why the listed "
                    "socio-demographic profiles are severely affected by the selected hazard"
                ),
                fields=self._reason_evidence_quality_fields(reason or "", evidence),
            )
            if input_review is None:
                return ChatResponse(
                    session_id=session_id,
                    step="socio_demographic_review",
                    bot_message=render_message("dg_validation_unavailable.md"),
                    options=DG_REASON_EVIDENCE_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )
            if not input_review["valid"]:
                return ChatResponse(
                    session_id=session_id,
                    step="socio_demographic_review",
                    bot_message=render_message(
                        "dg_validation_failed.md",
                        sector=session.sector,
                        reason=str(input_review["reason"]),
                    ),
                    options=DG_REASON_EVIDENCE_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )

        validation = await self._validate_dgs_context_against_stats(
            session=session,
            reason=reason or "",
            evidence=evidence or "",
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message("dg_validation_unavailable.md"),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if not validation["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_validation_failed.md",
                    sector=session.sector,
                    reason=validation["reason"],
                ),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        session.dg_reason = reason
        session.dg_evidence = evidence
        pending_dgs = session.pending_additional_dgs or []
        if pending_dgs:
            pending_profile_details = {
                normalize(str(profile.get("name") or "")): profile
                for profile in self._target_population_profiles_from_answers(
                    session.additional_dg_answers or [],
                    session.selected_hazard or "the selected hazard",
                )
                if profile.get("name")
            }
            if session.additional_dgs is None:
                session.additional_dgs = []
            self._extend_unique_profiles(session.additional_dgs, pending_dgs)
            for dg in pending_dgs:
                profile_detail = pending_profile_details.get(normalize(dg), {})
                self._store_socio_demographic(
                    session,
                    session.selected_hazard_record_id,
                    dg,
                    source="user_validated",
                    variable_name=str(profile_detail.get("variable_name") or "") or None,
                    explanation=str(profile_detail.get("explanation") or "") or None,
                    statistical_basis=str(profile_detail.get("statistical_basis") or "") or None,
                    metadata=profile_detail or None,
                    reason=reason or None,
                    evidence=evidence or None,
                )
            session.pending_additional_dgs = None
        self._record_activity(
            session_id,
            session,
            "socio_demographics_validated",
            reason or "Validated without user-provided reason.",
        )
        if skipped_reason_evidence:
            session.phase = "socio_demographic_review"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=markdown_to_html(
                    "The selected socio-demographic profiles were validated and added.\n\n"
                    f"### Complete socio-demographic profile summary for {session.selected_hazard or 'the selected hazard'}\n\n"
                    f"{format_all_dgs(session)}\n\n"
                    "You can continue with the selected hazard options."
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=False,
            )
        session.phase = "mitigation_measure"
        session.pending_mitigation_measure = None
        self._clear_mitigation_clarity_state(session)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_measure",
            bot_message=render_message(
                "mitigation_measure_reason.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=format_all_dgs(session),
                mitigation_examples=self._mitigation_measure_examples(session.sector_id),
            ),
            options=[],
            session=session.summary(),
            input_mode="mitigation_measure",
            error=False,
        )

    async def _capture_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        hazard = message.strip()
        if not hazard:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        existing_hazard = self._match_hazard(hazard, session)
        if existing_hazard is not None:
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason="This appears to already be covered by an existing hazard.",
                    suggestions=f"- **{existing_hazard}**",
                    has_suggestions=True,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        hazard_review = await self._review_custom_hazard_input(session, hazard)
        if hazard_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=(
                    "I could not review this hazard for clarity and overlap because "
                    "the local LLM is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        if not hazard_review["valid"]:
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=hazard_review["reason"],
                    suggestions=self._format_hazard_suggestions(hazard_review),
                    has_suggestions=self._has_hazard_suggestions(hazard_review),
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        session.pending_hazard = hazard
        session.phase = "add_hazard_evidence"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_reason_evidence.md",
                hazard=hazard,
                matching_hazards=self._format_hazard_suggestions(hazard_review),
                has_matching_hazards=self._has_hazard_suggestions(hazard_review),
            ),
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    async def _validate_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        reason, evidence = parse_reason_evidence(message)
        if not reason:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason="`Reason:` is required. Evidence URL and evidence file are optional.",
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        input_review = await self._validate_input_quality(
            session=session,
            purpose=(
                "a reason and optional evidence explaining why the proposed hazard "
                "is a negative impact or risk created by twin-transition policies "
                "for the selected country, region, and sector"
            ),
            fields=self._reason_evidence_quality_fields(reason, evidence),
        )
        if input_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
        if not input_review["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=str(input_review["reason"]),
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        validation = await self._validate_hazard_against_stats(
            session=session,
            hazard=session.pending_hazard or "",
            reason=reason,
            evidence=evidence or "",
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if not validation["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=validation["reason"],
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if session.custom_hazards is None:
            session.custom_hazards = []
        if session.pending_hazard:
            session.custom_hazards.append(session.pending_hazard)
        accepted_hazard = session.pending_hazard or "New hazard"
        session.pending_hazard = None
        session.accepted_custom_hazard = accepted_hazard
        session.accepted_custom_hazard_reason = reason
        session.accepted_custom_hazard_evidence = evidence or "Not provided"
        hazard_record = self._ensure_user_hazard(
            session_id,
            session,
            accepted_hazard,
            source="custom",
            reason=reason,
            evidence=evidence or None,
        )
        session.accepted_custom_hazard_record_id = hazard_record.id if hazard_record else None
        session.selected_hazard_record_id = session.accepted_custom_hazard_record_id
        self._record_activity(session_id, session, "custom_hazard_added", accepted_hazard)

        target_population_step = self._start_target_population_questions(session_id, session)
        if target_population_step is not None:
            return target_population_step

        return await self._custom_hazard_added_step(session_id, session)

    async def _stats_deep_dive(
        self,
        session_id: str,
        session: ChatSession,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        persist_history: bool = True,
    ) -> ChatResponse:
        context, messages = await self._build_stats_deep_dive_messages(session, user_message, history)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=900,
        )

        next_messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": answer},
        ]
        if persist_history:
            if session.stats_conversation is None:
                session.stats_conversation = []
            session.stats_conversation.extend(next_messages)
        else:
            if session.stats_dialog_conversation is None:
                session.stats_dialog_conversation = []
            session.stats_dialog_conversation.extend(next_messages)

        return ChatResponse(
            session_id=session_id,
            step="stats_deep_dive",
            bot_message=markdown_to_html(answer),
            options=STATS_DEEP_DIVE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _deep_dive(
        self, session_id: str, session: ChatSession, user_message: str
    ) -> ChatResponse:
        context, messages = await self._build_deep_dive_messages(session, user_message)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=900,
        )
        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=markdown_to_html(answer),
            options=[],
            session=session.summary(),
            error=False,
        )

    async def _socio_demographic_response(
        self, session_id: str, session: ChatSession, user_message: str
    ) -> ChatResponse:
        context, messages = await self._build_deep_dive_messages(session, user_message)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=900,
        )
        answer = self._strip_practical_sections(answer)
        session.socio_demographic_findings = answer
        session.socio_demographic_profiles = self._extract_socio_demographic_profiles(answer)
        profiles_to_store = [
            {"name": profile, "profile": profile, "source": "llm"}
            for profile in (session.socio_demographic_profiles or [answer])
        ]
        is_custom_hazard = self._is_saved_custom_hazard(
            session, session.selected_hazard or ""
        ) or normalize(session.selected_hazard or "") == normalize(
            session.accepted_custom_hazard or ""
        )
        if is_custom_hazard:
            for profile in profiles_to_store:
                self._store_socio_demographic(
                    session,
                    session.selected_hazard_record_id,
                    str(profile.get("name") or ""),
                    source="llm",
                    metadata=profile,
                )
        else:
            system_hazard = self._ensure_system_hazard(
                session,
                session.selected_hazard or "Selected hazard",
            )
            if system_hazard is not None:
                for profile in profiles_to_store:
                    self._store_system_socio_demographic(session, system_hazard.id, profile)
        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=(
                markdown_to_html(answer)
                + "\n"
                + render_message("socio_demographic_next.md")
            ),
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _hazard_profiles_response(
        self, session_id: str, session: ChatSession, hazard: str
    ) -> ChatResponse:
        profiles = self._stored_hazard_profiles(session, hazard)
        user_profiles = self._stored_user_hazard_profiles(session, hazard)
        is_additional_hazard = self._is_additional_hazard(session, hazard)
        if not profiles:
            profiles = await self._get_hazard_profiles_from_llm(session, hazard)
            if profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = profiles
        display_profiles = (
            await self._additional_profiles_with_population_context(session, hazard, profiles)
            if is_additional_hazard
            else await self._profiles_with_population_context(session, hazard, profiles)
        )
        display_user_profiles = await self._profiles_with_population_context(
            session,
            hazard,
            user_profiles,
        )
        answer = self._format_hazard_profiles_markdown(
            hazard,
            display_profiles,
            user_profiles=display_user_profiles,
        )
        session.socio_demographic_findings = self._format_hazard_profiles_markdown(
            hazard,
            display_profiles,
        )
        session.socio_demographic_profiles = [
            profile["name"] for profile in profiles if profile.get("name")
        ]
        session.additional_dgs = [
            profile["name"] for profile in user_profiles if profile.get("name")
        ] or None
        is_custom_hazard = self._is_saved_custom_hazard(session, hazard) or (
            normalize(hazard) == normalize(session.accepted_custom_hazard or "")
        )
        if is_custom_hazard:
            for profile in profiles:
                self._store_socio_demographic(
                    session,
                    session.selected_hazard_record_id,
                    profile["name"],
                    source="llm",
                    variable_name=profile.get("variable_name"),
                    explanation=profile.get("explanation"),
                    statistical_basis=profile.get("statistical_basis"),
                    metadata=profile,
                )
        elif not is_additional_hazard:
            system_hazard = self._ensure_system_hazard(session, hazard)
            if system_hazard is not None:
                for profile in profiles:
                    self._store_system_socio_demographic(session, system_hazard.id, profile)
        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=(
                markdown_to_html(answer)
                + "\n"
                + render_message("socio_demographic_next.md")
            ),
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _negative_impact_reasons(self, session: ChatSession) -> str:
        context, messages = await self._build_deep_dive_messages(
            session,
            (
                f"From the sector system prompt, identify the reasons given for the "
                f"negative impact of the selected hazard '{session.selected_hazard}'. "
                "Use only the loaded statistical context. Focus on why this hazard can "
                "harm the affected socio-demographic profiles, not on mitigation. "
                "Answer in Markdown with a short heading and concise bullets. If the "
                "prompt does not identify clear reasons, say that explicitly."
            ),
        )
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=650,
        )

    async def _practical_policy_recommendations(self, session: ChatSession) -> str:
        matched_examples = self._matched_mitigation_measure_examples(session)
        context, messages = await self._build_deep_dive_messages(
            session,
            (
                f"For the selected hazard '{session.selected_hazard}', provide practical "
                "considerations. Use the matched "
                "mitigation-measure examples for the same sector, hazard, and affected "
                "profiles as the main policy-design evidence when they are available; "
                "synthesize them into implementation guidance instead of copying them verbatim. "
                "Use the loaded sector statistical context to explain why those "
                "implementation examples fit the affected profiles.\n\n"
                "Socio-demographic profiles:\n"
                f"{format_all_dgs(session)}\n\n"
                "Selected target populations/groups:\n"
                f"{self._mitigation_target_population_text(session)}\n\n"
                "Matched mitigation-measure examples:\n"
                f"{matched_examples or '- No matching examples were found for this sector, hazard, and profile set.'}\n\n"
                "Answer in Markdown with one short section only: Practical "
                "Considerations. Start the section with two concise sentences explaining "
                "that it highlights implementation issues, design trade-offs, and "
                "profile-specific concerns to consider before creating a mitigation "
                "measure. Then keep bullets concise and do not create a final "
                "mitigation measure yet. Do not include a Current Policy "
                "Implementation section; it will be rendered separately from the "
                "database examples."
            ),
        )
        practical_considerations = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=750,
        )
        practical_considerations = self._ensure_practical_considerations_intro(
            practical_considerations
        )
        session.practical_considerations = self._extract_practical_consideration_items(
            practical_considerations
        )
        current_policy = self._current_policy_implementations_section(session)
        new_policy_suggestions = await self._new_policy_suggestions_section(session)
        session.suggested_new_policy_proposal = self._extract_suggested_policy_proposal(
            new_policy_suggestions
        )
        return "\n\n".join(
            section.strip()
            for section in (
                practical_considerations,
                current_policy,
                new_policy_suggestions,
            )
            if section and section.strip()
        )

    async def _mitigation_review_response(self, session: ChatSession, user_message: str) -> str:
        context, messages = await self._build_mitigation_review_messages(session, user_message)
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.35,
            max_tokens=1050,
        )

    async def _sector_briefing(self, session: ChatSession) -> str:
        sector_stats = await self._sector_prompt_rag_context(
            session,
            "sector statistical summary hazards confirmed predictors target population",
            limit=8,
        )
        return render_message(
            "deep_dive_intro.md",
            country=session.country,
            region=session.region,
            sector=session.sector,
            sector_stats=sector_stats,
        )

    async def _intro_message_from_llm(self, session_id: str) -> str:
        stats = self._user_visit_stats(session_id)
        fallback = self._intro_fallback(stats)
        if stats.get("user_type") == "first_time":
            return fallback
        context = """
You write short, positive welcome messages for Dr Transition.

Use the user's visit/session pattern to choose the greeting:
- First-time user: greet them warmly as a new user.
- Returning user: greet them warmly as a returning user and acknowledge momentum.

Use positive language only. Keep the message professional, encouraging, and concise.
Do not mention negative concepts, limitations, or missing activity. Do not invent
statistics beyond the provided session facts. Return Markdown only.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Write the intro message shown before country selection.\n\n"
                    f"Previous sessions by this user: {stats['previous_sessions']}\n"
                    f"Active visit days: {stats['active_visit_days']}\n"
                    f"Sessions in the last 30 days: {stats['recent_sessions_30_days']}\n"
                    f"Last visit label: {stats['last_visit_label']}\n"
                    f"User type: {stats['user_type']}\n\n"
                    "Required content:\n"
                    "- Mention Dr Transition.\n"
                    "- Invite the user to start by selecting their country.\n"
                    "- Keep it to 2 or 3 short sentences.\n"
                    "- Use only positive wording."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.45,
            max_tokens=220,
        )
        if is_llm_unavailable_response(response) or not response.strip():
            return fallback
        return markdown_to_html(response.strip())

    def _user_visit_stats(self, current_session_id: str) -> dict[str, object]:
        if self.user_id is None:
            return {
                "previous_sessions": 0,
                "active_visit_days": 0,
                "recent_sessions_30_days": 0,
                "last_visit_label": "first visit",
                "user_type": "first_time",
            }

        try:
            sessions = self.db.scalars(
                select(UserSession)
                .where(
                    UserSession.user_id == self.user_id,
                    UserSession.session_key != current_session_id,
                )
                .order_by(UserSession.updated_at.desc())
            ).all()
        except Exception:
            logger.exception("Failed to calculate user visit stats")
            sessions = []

        previous_sessions = len(sessions)
        dates = {
            (row.updated_at or row.created_at).date()
            for row in sessions
            if row.updated_at or row.created_at
        }
        now = datetime.now(timezone.utc)
        recent_sessions = 0
        for row in sessions:
            timestamp = row.updated_at or row.created_at
            if timestamp is None:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if (now - timestamp).days <= 30:
                recent_sessions += 1

        last_visit_label = "first visit"
        if sessions:
            timestamp = sessions[0].updated_at or sessions[0].created_at
            if timestamp is not None:
                last_visit_label = timestamp.date().isoformat()

        return {
            "previous_sessions": previous_sessions,
            "active_visit_days": len(dates),
            "recent_sessions_30_days": recent_sessions,
            "last_visit_label": last_visit_label,
            "user_type": "returning" if previous_sessions else "first_time",
        }

    def _intro_fallback(self, stats: dict[str, object]) -> str:
        previous_sessions = int(stats.get("previous_sessions") or 0)
        if previous_sessions:
            message = (
                "Welcome back to **Dr Transition**. "
                f"You have built momentum across {previous_sessions} prior session"
                f"{'' if previous_sessions == 1 else 's'}, and we can continue shaping "
                "a thoughtful Twin-Transition analysis together.\n\n"
                "**Let's start with your country.** Our research currently covers:"
            )
        else:
            message = render_message("welcome.md")
        return markdown_to_html(message)

    @staticmethod
    def _format_pending_additional_dgs(session: ChatSession) -> str:
        pending = session.pending_additional_dgs or []
        if not pending:
            return "- No pending custom socio-demographic profiles."
        return "\n".join(f"- {dg}." for dg in pending)

    async def _selection_message_from_llm(
        self,
        session: ChatSession,
        *,
        event: str,
        fallback: str,
    ) -> str:
        context = """
You write short, professional wizard messages for Dr Transition.

Keep the meaning similar to the existing app message, but make it personalized,
polished, and concise. Do not add analysis, hazard facts, or future-step details.
Return Markdown only.
""".strip()
        next_step = "region selection" if event == "country_selected" else "sector selection"
        if event == "national_scope":
            next_step = "sector selection using national scope"
        wording_rule = (
            "After country selection, explicitly ask the user to select a region from the list. "
            "Use the word region."
            if event == "country_selected"
            else (
                "Use the word sector for the next choice. Do not use industry, field, "
                "domain, or any other synonym for sector."
            )
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Event: {event}\n"
                    f"Country: {session.country or 'Not selected'}\n"
                    f"Region: {session.region or 'Not selected'}\n"
                    f"Next step: {next_step}\n\n"
                    f"Baseline message:\n{fallback}\n\n"
                    f"Wording rule: {wording_rule}\n\n"
                    "Write one or two sentences. Keep it warm, professional, and direct. "
                    "Ask for the exact next selection named in the wording rule. "
                    "Do not wrap the message in quotation marks."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.35,
            max_tokens=140,
        )
        if is_llm_unavailable_response(response) or not response.strip():
            return fallback
        return markdown_to_html(self._normalize_sector_wording(response.strip()))

    @staticmethod
    def _normalize_sector_wording(message: str) -> str:
        replacements = {
            "preferred industry": "preferred sector",
            "industry for analysis": "sector for analysis",
            "industry": "sector",
            "field for analysis": "sector for analysis",
            "field": "sector",
            "domain for analysis": "sector for analysis",
            "domain": "sector",
        }
        normalized = message
        for old, new in replacements.items():
            normalized = re.sub(rf"\b{re.escape(old)}\b", new, normalized, flags=re.IGNORECASE)
        return ChatService._strip_wrapping_quotes(normalized)

    @staticmethod
    def _strip_wrapping_quotes(message: str) -> str:
        stripped = message.strip()
        quote_pairs = {
            '"': '"',
            "'": "'",
            "“": "”",
            "‘": "’",
        }
        if len(stripped) >= 2 and stripped[0] in quote_pairs:
            closing = quote_pairs[stripped[0]]
            if stripped[-1] == closing:
                return stripped[1:-1].strip()
        return stripped

    def _mitigation_reason_prompt(
        self, session: ChatSession, error_reason: str | bool | None = None
    ) -> str:
        prompt = render_message(
            "mitigation_measure_reason.md",
            hazard=session.selected_hazard or "the selected hazard",
            dgs=format_all_dgs(session),
            mitigation_examples=self._mitigation_measure_examples(session.sector_id),
        )
        if isinstance(error_reason, str) and error_reason.strip():
            return (
                render_message(
                    "mitigation_validation_failed.md",
                    reason=error_reason.strip(),
                )
                + "\n"
                + prompt
            )
        return prompt

    async def _build_mitigation_review_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} {format_all_dgs(session)} "
                f"{self._mitigation_target_population_text(session)} "
                f"{session.mitigation_measure or ''} {session.mitigation_reason or ''} {user_message}"
            ),
            limit=8,
        )
        knowledge_context = await self._mitigation_knowledge_context(
            session,
            session.mitigation_measure or "",
            session.mitigation_reason or "",
        )
        examples = self._mitigation_measure_examples(session.sector_id)
        context = f"""
You are Dr Transition — an expert research assistant specialising in
the social and distributional impacts of Twin-Transition policies (the simultaneous digital and
green transitions) in Europe.

Your role is EDUCATIONAL: you help users LEARN how to think about policy design, hazards, and
mitigation measures. You do NOT draft policies for them. You guide them to discover important
concepts themselves.

Tone: warm, encouraging, intellectually rigorous. Use plain language. Avoid jargon unless you
explain it. Always ground your responses in the research knowledge provided to you.

Never skip steps or volunteer
information for a future step before the user reaches it.

{self._scope_instruction(session)}

Sector-prompt RAG excerpts:
{sector_context}

Retrieved knowledge-base excerpts:
{knowledge_context or "- No relevant knowledge-base excerpts were found."}
""".strip()

        history = list(session.stats_conversation or [])[-8:]
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": (
                    "Session context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n"
                    f"- Selected hazard: {session.selected_hazard or 'Not selected'}\n\n"
                    "Selected target populations/groups:\n"
                    f"{self._mitigation_target_population_text(session)}\n\n"
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    "Validated mitigation measure:\n"
                    f"{session.mitigation_measure or 'Not provided'}\n\n"
                    "Validated mitigation reason:\n"
                    f"{session.mitigation_reason or 'Not provided'}\n\n"
                    "Relevant mitigation measure examples:\n"
                    f"{examples or '- No sector-specific examples are available.'}\n\n"
                    "YOUR TASK — write an encouraging, educational evaluation (around "
                    "200-300 words) that:\n\n"
                    "1. ACKNOWLEDGE what is valuable or insightful about their proposal — "
                    "be specific and genuine.\n"
                    "2. CONNECT their idea to relevant concepts from the research knowledge "
                    "provided. Name specific concepts, mechanisms, or groups where applicable.\n"
                    "3. GENTLY EXPAND their thinking by pointing out one dimension or group "
                    "they may not have considered, suggesting how their measure could be "
                    "strengthened or made more targeted, or noting a potential unintended "
                    "consequence to watch for.\n"
                    "4. If appropriate, REFERENCE how a similar approach appears in the "
                    "research examples.\n"
                    "5. Discard proposals that are conceptually wrong.\n"
                    "6. Discard all concepts different from twin transition policies.\n"
                    "7. If user reasoning lacks deep thinking, END with an encouraging "
                    "reflection question that deepens their thinking.\n"
                    "8. Only ask reflective question if necessary.\n\n"
                    "Tone: to the point, collegial, intellectually stimulating. Use plain "
                    "language. Do NOT simply list the example measures. Do NOT be dismissive. "
                    "This is an educational conversation. Do NOT include headers or bullet "
                    "points — write in flowing paragraphs."
                ),
            },
            *history,
            {
                "role": "user",
                "content": (
                    f"User message:\n{user_message}\n\n"
                    "Answer in flowing paragraphs. Stay grounded in the statistical context, "
                    "the retrieved knowledge, the examples, and the validated mitigation "
                    "measure/reason."
                ),
            },
        ]
        return context, messages

    def _evaluation_question_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        question = self._current_evaluation_question(session)
        if question is None:
            return self._evaluation_complete_step(session_id, session)

        message = render_message(
            "evaluation_question.md",
            category=question["category"],
            question=question["question"],
            current=session.evaluation_index + 1,
            total=len(session.evaluation_questions or []),
            error_reason=error_reason or "",
        )
        return ChatResponse(
            session_id=session_id,
            step="evaluation_question",
            bot_message=message,
            options=[],
            session=session.summary(),
            input_mode="evaluation_question",
            error=bool(error_reason),
        )

    def _evaluation_complete_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        session.phase = "evaluation_complete"
        self._promote_temporary_evidence(session)
        return ChatResponse(
            session_id=session_id,
            step="evaluation_complete",
            bot_message=render_message(
                "evaluation_complete.md",
                hazard=session.selected_hazard or "the selected hazard",
                mitigation_measure=session.mitigation_measure or "Not provided",
                reason=session.mitigation_reason or "Not provided",
                answers=format_evaluation_answers(
                    session,
                    self._historical_evaluation_series(session),
                ),
            ),
            options=[],
            session=session.summary(),
            error=False,
        )

    def _start_target_population_questions(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse | None:
        questions = self._target_population_questions()
        if not questions:
            session.target_population_questions = []
            session.target_population_answers = []
            return None

        session.target_population_questions = questions
        session.target_population_answers = []
        session.target_population_index = 0
        session.phase = "target_population_question"
        return self._target_population_question_step(session_id, session)

    def _start_additional_dg_questions(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        questions = self._target_population_questions()
        session.target_population_questions = questions
        session.additional_dg_answers = []
        session.target_population_index = 0
        session.phase = "add_dgs"
        if not questions:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message="No socio-demographic questions are configured. You can continue to mitigation planning.",
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=True,
            )
        return self._additional_dg_question_step(session_id, session)

    def _additional_dg_question_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        question = self._current_target_population_question(session)
        if question is None:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=self.invalid_message,
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=True,
            )

        return ChatResponse(
            session_id=session_id,
            step="add_dgs",
            bot_message=render_message(
                "target_population_question.md",
                hazard=session.selected_hazard or "the selected hazard",
                question=question["question"],
                current=session.target_population_index + 1,
                total=len(session.target_population_questions or []),
                error_reason=error_reason or "",
            ),
            options=self._target_population_options(question),
            session=session.summary(),
            input_mode="target_population_multi",
            error=bool(error_reason),
        )

    async def _handle_additional_dg_batch(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        raw_json = message.split(":", 1)[1].strip()
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return self._additional_dg_question_step(
                session_id,
                session,
                error_reason="Please submit valid socio-demographic selections.",
            )
        if not isinstance(payload, list):
            return self._additional_dg_question_step(
                session_id,
                session,
                error_reason="Please submit valid socio-demographic selections.",
            )

        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if "id" in question
        }
        recorded_any = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                question_id = int(item.get("question_id"))
            except (TypeError, ValueError):
                continue
            question = questions_by_id.get(question_id)
            if question is None:
                continue
            raw_answers = item.get("answers")
            if not isinstance(raw_answers, list):
                continue
            options = self._target_population_options(question)
            selected = self._target_population_selected_labels(
                "\n".join(str(answer) for answer in raw_answers),
                options,
            )
            selected = [
                label
                for label in selected
                if normalize(label) not in {normalize("Skip"), normalize("Skip all")}
            ]
            if not selected:
                continue
            self._record_additional_dg_answer(session_id, session, question, selected)
            recorded_any = True

        if not recorded_any:
            return self._additional_dg_question_step(
                session_id,
                session,
                error_reason="Please select at least one socio-demographic option.",
            )

        session.target_population_index = len(session.target_population_questions or [])
        return await self._finalize_additional_dg_questions(session_id, session)

    def _record_additional_dg_answer(
        self,
        session_id: str,
        session: ChatSession,
        question: dict[str, object],
        selected_labels: list[str],
    ) -> None:
        if session.additional_dg_answers is None:
            session.additional_dg_answers = []
        answer_text = ", ".join(selected_labels)
        session.additional_dg_answers.append(
            {
                "question_id": int(question["id"]),
                "question": str(question["question"]),
                "answer": answer_text,
            }
        )
        for selected in selected_labels:
            question_option_id = self.db.scalar(
                select(QuestionOption.id).where(
                    QuestionOption.question_id == int(question["id"]),
                    QuestionOption.option == selected,
                )
            )
            self._store_question_response(
                session_id,
                session,
                question_id=int(question["id"]),
                category="additional_target_population",
                response_text=selected,
                question_option_id=question_option_id,
                hazard_id=session.selected_hazard_record_id,
            )
        self._record_activity(
            session_id,
            session,
            "additional_dg_question_answered",
            f"{question['question']} -> {answer_text}",
        )

    async def _finalize_additional_dg_questions(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        profiles = [
            profile["name"]
            for profile in self._target_population_profiles_from_answers(
                session.additional_dg_answers or [],
                session.selected_hazard or "the selected hazard",
            )
            if profile.get("name")
        ]
        if not profiles:
            session.phase = "socio_demographic_review"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message="No additional socio-demographic profiles were selected.",
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=False,
            )

        local_duplicate_check = self._match_existing_dg(session, profiles)
        if local_duplicate_check is not None:
            session.phase = "socio_demographic_review"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_duplicate.md",
                    duplicates=self._format_duplicate_dgs(local_duplicate_check),
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=True,
            )

        duplicate_check = await self._semantic_dg_duplicate_check(session, profiles)
        if duplicate_check is None:
            session.phase = "socio_demographic_review"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=(
                    "I could not check whether these socio-demographic profiles are "
                    "already covered because the local LLM is unavailable. Please try again."
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=True,
            )
        if duplicate_check["duplicate"]:
            session.phase = "socio_demographic_review"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_duplicate.md",
                    duplicates=self._format_duplicate_dgs(duplicate_check),
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=True,
            )

        session.pending_additional_dgs = []
        self._extend_unique_profiles(session.pending_additional_dgs, profiles)
        self._record_activity(
            session_id,
            session,
            "socio_demographics_added",
            ", ".join(profiles),
        )
        session.phase = "dg_reason_evidence"
        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=render_message(
                "dg_reason_evidence.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=self._format_pending_additional_dgs(session),
            ),
            options=DG_REASON_EVIDENCE_OPTIONS,
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    def _target_population_question_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        question = self._current_target_population_question(session)
        if question is None:
            return self._custom_hazard_added_step_sync(session_id, session)

        options = self._target_population_options(question)
        return ChatResponse(
            session_id=session_id,
            step="target_population_question",
            bot_message=render_message(
                "target_population_question.md",
                hazard=session.accepted_custom_hazard or "the new hazard",
                question=question["question"],
                current=session.target_population_index + 1,
                total=len(session.target_population_questions or []),
                error_reason=error_reason or "",
            ),
            options=options,
            session=session.summary(),
            input_mode="target_population_multi",
            error=bool(error_reason),
        )

    async def _handle_target_population_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if message.strip() == "Quick Select Target Population":
            return self._target_population_question_step(session_id, session)
        if message.strip().startswith("TARGET_POPULATION_BATCH:"):
            return await self._handle_target_population_batch(session_id, session, message)

        question = self._current_target_population_question(session)
        if question is None:
            await self._synthesize_target_population_profile(session)
            return await self._custom_hazard_added_step(session_id, session)

        options = self._target_population_options(question)
        selected_labels = self._target_population_selected_labels(message, options)

        if not selected_labels:
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please choose one or more listed target population options.",
            )

        if any(normalize(label) == normalize("Skip all") for label in selected_labels):
            session.target_population_index += 1
            session.target_population_index = len(session.target_population_questions or [])
            await self._synthesize_target_population_profile(session)
            return await self._custom_hazard_added_step(session_id, session)

        if any(normalize(label) == normalize("Skip") for label in selected_labels):
            session.target_population_index += 1
            if session.target_population_index >= len(session.target_population_questions or []):
                await self._synthesize_target_population_profile(session)
                return await self._custom_hazard_added_step(session_id, session)
            return self._target_population_question_step(session_id, session)

        self._record_target_population_answer(session_id, session, question, selected_labels)
        session.target_population_index += 1

        if session.target_population_index >= len(session.target_population_questions or []):
            await self._synthesize_target_population_profile(session)
            return await self._custom_hazard_added_step(session_id, session)

        return self._target_population_question_step(session_id, session)

    async def _handle_target_population_batch(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        raw_json = message.split(":", 1)[1].strip()
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please submit valid target population selections.",
            )

        if not isinstance(payload, list):
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please submit valid target population selections.",
            )

        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if "id" in question
        }
        recorded_any = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                question_id = int(item.get("question_id"))
            except (TypeError, ValueError):
                continue
            question = questions_by_id.get(question_id)
            if question is None:
                continue
            raw_answers = item.get("answers")
            if not isinstance(raw_answers, list):
                continue
            options = self._target_population_options(question)
            selected = self._target_population_selected_labels(
                "\n".join(str(answer) for answer in raw_answers),
                options,
            )
            selected = [
                label
                for label in selected
                if normalize(label) not in {normalize("Skip"), normalize("Skip all")}
            ]
            if not selected:
                continue
            self._record_target_population_answer(session_id, session, question, selected)
            recorded_any = True

        if not recorded_any:
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please select at least one target population option.",
            )

        session.target_population_index = len(session.target_population_questions or [])
        await self._synthesize_target_population_profile(session)
        return await self._custom_hazard_added_step(session_id, session)

    def _record_target_population_answer(
        self,
        session_id: str,
        session: ChatSession,
        question: dict[str, object],
        selected_labels: list[str],
    ) -> None:
        if session.target_population_answers is None:
            session.target_population_answers = []
        answer_text = ", ".join(selected_labels)
        question_id = int(question["id"])
        session.target_population_answers = [
            answer
            for answer in session.target_population_answers
            if int(answer.get("question_id") or 0) != question_id
        ]
        session.target_population_answers.append(
            {
                "question_id": question_id,
                "question": str(question["question"]),
                "answer": answer_text,
                "selected": list(selected_labels),
            }
        )
        hazard_id = session.accepted_custom_hazard_record_id or session.selected_hazard_record_id
        if hazard_id is not None:
            self.db.execute(
                delete(UserQuestionResponse).where(
                    UserQuestionResponse.user_hazard_id == hazard_id,
                    UserQuestionResponse.question_id == question_id,
                    UserQuestionResponse.category == "target_population",
                )
            )
            self.db.commit()
        for selected in selected_labels:
            question_option_id = self.db.scalar(
                select(QuestionOption.id).where(
                    QuestionOption.question_id == question_id,
                    QuestionOption.option == selected,
                )
            )
            self._store_question_response(
                session_id,
                session,
                question_id=question_id,
                category="target_population",
                response_text=selected,
                question_option_id=question_option_id,
                hazard_id=hazard_id,
            )
        self._record_activity(
            session_id,
            session,
            "target_population_question_answered",
            f"{question['question']} -> {answer_text}",
        )

    def _prepare_custom_hazard_added_profiles(self, session: ChatSession) -> str:
        session.phase = "hazards"
        self._set_custom_hazard_profiles_from_target_population(session)
        accepted_hazard = session.accepted_custom_hazard or "New hazard"
        stored_profiles = self._stored_hazard_profiles(session, accepted_hazard)
        profile_items = stored_profiles or [
            {"name": profile, "profile": profile}
            for profile in (session.socio_demographic_profiles or [])
        ]
        hazard_record_id = (
            session.accepted_custom_hazard_record_id or session.selected_hazard_record_id
        )
        self._clear_target_population_profiles(hazard_record_id)
        for profile in profile_items:
            self._store_socio_demographic(
                session,
                hazard_record_id,
                str(profile.get("name") or profile.get("profile") or ""),
                source="target_population",
                variable_name=str(profile.get("variable_name") or "") or None,
                explanation=str(profile.get("explanation") or "") or None,
                statistical_basis=str(profile.get("statistical_basis") or "") or None,
                metadata=profile,
            )
        return accepted_hazard

    def _custom_hazard_added_step_sync(self, session_id: str, session: ChatSession) -> ChatResponse:
        accepted_hazard = self._prepare_custom_hazard_added_profiles(session)
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_added.md",
                hazard=accepted_hazard,
                reason=session.accepted_custom_hazard_reason or "Not provided",
                evidence=session.accepted_custom_hazard_evidence or "Not provided",
                target_population_answers=self._format_target_population_answers(session),
                hazards=format_hazards(session),
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _custom_hazard_added_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        accepted_hazard = self._prepare_custom_hazard_added_profiles(session)
        profiles = self._stored_hazard_profiles(session, accepted_hazard)
        enriched_profiles = await self._additional_profiles_with_population_context(
            session,
            accepted_hazard,
            profiles,
        )
        if enriched_profiles:
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[accepted_hazard] = enriched_profiles
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_added.md",
                hazard=accepted_hazard,
                reason=session.accepted_custom_hazard_reason or "Not provided",
                evidence=session.accepted_custom_hazard_evidence or "Not provided",
                target_population_answers=self._format_target_population_answers(session),
                hazards=format_hazards(session),
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _clear_target_population_profiles(self, hazard_id: int | None) -> None:
        if hazard_id is None:
            return
        try:
            self.db.execute(
                delete(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.user_hazard_id == hazard_id,
                    UserHazardSocioDemographic.source == "target_population",
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear prior target-population profiles")

    def _current_target_population_question(self, session: ChatSession) -> dict[str, object] | None:
        questions = session.target_population_questions or []
        if session.target_population_index < 0 or session.target_population_index >= len(questions):
            return None
        return questions[session.target_population_index]

    @staticmethod
    def _target_population_options(question: dict[str, object]) -> list[Option]:
        labels = [str(label) for label in question.get("options", [])]
        options = [Option(id=index, label=label) for index, label in enumerate(labels, start=1)]
        return [
            *options,
            Option(id=len(options) + 1, label="Skip"),
            Option(id=len(options) + 2, label="Skip all"),
            Option(id=len(options) + 3, label="Quick Select Target Population"),
        ]

    @staticmethod
    def _target_population_selected_labels(message: str, options: list[Option]) -> list[str]:
        raw_parts = [
            part.strip()
            for part in re.split(r"[;\n]+", message)
            if part.strip()
        ]
        if not raw_parts:
            raw_parts = [message.strip()] if message.strip() else []

        selected: list[str] = []
        seen: set[str] = set()
        for part in raw_parts:
            label = exact_option_label(part, options) or match_option_label(part, options)
            if label is None:
                continue
            key = normalize(label)
            if key in seen:
                continue
            seen.add(key)
            selected.append(label)
        return selected

    def _target_population_questions(self) -> list[dict[str, object]]:
        rows = self.db.scalars(
            select(EvaluationQuestion)
            .options(selectinload(EvaluationQuestion.options))
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, EvaluationQuestion.id)
        ).all()

        questions: list[dict[str, object]] = []
        for row in rows:
            options = [item.option for item in sorted(row.options, key=lambda item: item.id)]
            if not options:
                continue
            questions.append(
                {
                    "id": row.id,
                    "question": normalize_markdown_text(row.question),
                    "options": options,
                }
            )
        return questions

    @staticmethod
    def _format_target_population_answers(session: ChatSession) -> str:
        if not session.target_population_answers:
            return "- No target population questions were configured."

        lines: list[str] = []
        for answer in session.target_population_answers:
            lines.append(f"- **{answer['question']}**")
            lines.append(f"  - {answer['answer']}")
        return "\n".join(lines)

    async def _synthesize_target_population_profile(self, session: ChatSession) -> None:
        hazard = session.accepted_custom_hazard
        answers = session.target_population_answers or []
        if not hazard or not answers:
            return
        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        answers_by_id = {
            int(answer.get("question_id") or 0): answer
            for answer in answers
            if int(answer.get("question_id") or 0) > 0
        }
        all_options_selected = bool(questions_by_id)
        structured_answers: list[dict[str, object]] = []
        for question_id, question in questions_by_id.items():
            answer = answers_by_id.get(question_id, {})
            available = [str(item) for item in question.get("options", [])]
            stored_selected = answer.get("selected")
            selected = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [
                    option
                    for option in available
                    if normalize(option)
                    in normalize(str(answer.get("answer") or ""))
                ]
            )
            if {normalize(item) for item in selected} != {normalize(item) for item in available}:
                all_options_selected = False
            structured_answers.append(
                {
                    "question": str(answer.get("question") or "").strip(),
                    "selected": selected,
                    "available": available,
                }
            )
        required_title = "General Population" if all_options_selected else ""
        context = """
You summarize target-population selections for a policy hazard.
Return only a JSON object with `title` and `description`.
Create exactly one inclusive socio-demographic profile. Do not list every option.
The title must be short and the description must be one plain-English sentence.
Do not invent characteristics that were not selected.
""".strip()
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Hazard: {hazard}\n"
                        f"Required title: {required_title or 'Choose a concise title'}\n"
                        f"All options selected: {all_options_selected}\n"
                        "Selections:\n"
                        + json.dumps(structured_answers, ensure_ascii=False)
                    ),
                }
            ],
            temperature=0,
            max_tokens=180,
        )
        title = required_title
        description = ""
        if not is_llm_unavailable_response(response):
            try:
                parsed = json.loads(response.strip())
            except json.JSONDecodeError:
                start = response.find("{")
                end = response.rfind("}")
                try:
                    parsed = json.loads(response[start : end + 1]) if start >= 0 and end > start else {}
                except json.JSONDecodeError:
                    parsed = {}
            if isinstance(parsed, dict):
                if not required_title:
                    title = str(parsed.get("title") or "").strip()
                description = str(parsed.get("description") or "").strip()
        selected_labels = [
            label
            for answer in structured_answers
            for label in answer.get("selected", [])
            if str(label).strip()
        ]
        if not title:
            title = "Selected Target Population"
        if not description:
            description = (
                "The hazard is considered across the general population without restricting it "
                "to a particular socio-demographic group."
                if all_options_selected
                else "This profile summarizes the selected target groups: "
                + ", ".join(str(label) for label in selected_labels[:6])
                + "."
            )
        title = re.sub(r"\s+", " ", normalize_markdown_text(title)).strip("`*_ #.-")[:120]
        description = re.sub(
            r"\s+", " ", normalize_markdown_text(description)
        ).strip("`*_ #")
        first_sentence = re.match(r"^(.+?[.!?])(?:\s|$)", description)
        if first_sentence:
            description = first_sentence.group(1)
        description = description[:260]
        profile = {
            "name": title,
            "profile": title,
            "variable_name": "generalized_target_population",
            "explanation": description,
            "statistical_basis": "LLM synthesis of user-selected target-population responses.",
            "source": "target_population",
        }
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = [profile]
        session.socio_demographic_profiles = [title]

    def _set_custom_hazard_profiles_from_target_population(self, session: ChatSession) -> None:
        hazard = session.accepted_custom_hazard
        if not hazard:
            return
        existing_profiles = self._stored_hazard_profiles(session, hazard)
        if any(
            profile.get("variable_name") == "generalized_target_population"
            for profile in existing_profiles
        ):
            return
        profiles = self._target_population_profiles_from_answers(
            session.target_population_answers or [],
            hazard,
        )
        if not profiles:
            return
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = profiles
        session.socio_demographic_profiles = [profile["name"] for profile in profiles]

    def _hydrate_custom_hazard_profiles(self, session: ChatSession) -> None:
        for hazard in session.custom_hazards or []:
            if self._stored_hazard_profiles(session, hazard):
                continue
            stored_profiles = self._stored_user_hazard_profiles(session, hazard)
            if stored_profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = stored_profiles
                continue
            profiles = self._target_population_profiles_for_saved_hazard(session, hazard)
            if not profiles:
                continue
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[hazard] = profiles

    @staticmethod
    def _filter_session_hazards_without_profiles(session: ChatSession) -> None:
        system_hazards = [
            hazard
            for hazard in (session.hazards or [])
            if ChatService._stored_hazard_profiles(session, hazard)
        ]
        custom_hazards = [
            hazard
            for hazard in (session.custom_hazards or [])
            if ChatService._stored_hazard_profiles(session, hazard)
        ]
        additional_hazards = [
            hazard
            for hazard in (session.additional_hazards or [])
            if ChatService._stored_hazard_profiles(session, hazard)
        ]
        session.hazards = system_hazards
        session.custom_hazards = custom_hazards
        session.additional_hazards = additional_hazards

        allowed = {
            normalize(hazard)
            for hazard in [*system_hazards, *custom_hazards, *additional_hazards]
        }
        session.hazard_profiles = {
            hazard: profiles
            for hazard, profiles in (session.hazard_profiles or {}).items()
            if normalize(str(hazard)) in allowed
        }
        session.hazard_rankings = {
            hazard: ranking
            for hazard, ranking in (session.hazard_rankings or {}).items()
            if normalize(str(hazard)) in allowed
        } or None

    def _target_population_profiles_for_saved_hazard(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []

        rows = self.db.execute(
            select(
                EvaluationQuestion.question,
                UserQuestionResponse.response_text,
            )
            .join(UserHazard, UserHazard.id == UserQuestionResponse.user_hazard_id)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
            .where(
                UserSession.country_id == session.country_id,
                UserHazard.sector_id == session.sector_id,
                UserHazard.region_id.is_(None)
                if session.region_id is None
                else UserHazard.region_id == session.region_id,
                UserHazard.source == "custom",
                UserHazard.name == hazard,
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
        ).all()
        answers = [
            {
                "question": normalize_markdown_text(question),
                "answer": str(response or "").strip(),
                "selected": [str(response or "").strip()],
            }
            for question, response in rows
            if str(response or "").strip()
        ]
        return self._target_population_profiles_from_answers(answers, hazard)

    @staticmethod
    def _target_population_profiles_from_answers(
        answers: list[dict[str, object]],
        hazard: str,
    ) -> list[dict[str, str]]:
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for answer in answers:
            question = str(answer.get("question") or "").strip()
            answer_text = str(answer.get("answer") or "").strip()
            if not question or not answer_text:
                continue
            stored_selected = answer.get("selected")
            labels = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [item.strip() for item in answer_text.split(",") if item.strip()]
            )
            for label in labels:
                name = ChatService._target_population_profile_name(question, label)
                key = normalize(name)
                if not name or key in seen:
                    continue
                seen.add(key)
                profiles.append(
                    {
                        "name": name[:120],
                        "profile": name[:120],
                        "variable_name": question[:160],
                        "explanation": f"Selected as a target population for {hazard}."[:260],
                        "statistical_basis": "User-selected socio-demographic question response.",
                        "source": "target_population",
                    }
                )
        return profiles

    @staticmethod
    def _target_population_profile_name(question: str, label: str) -> str:
        normalized_question = question.strip().rstrip(".")
        normalized_label = label.strip()
        label_key = normalize(normalized_label)
        if label_key == normalize("Yes"):
            return normalized_question
        if label_key == normalize("No"):
            return f"Not {normalized_question[:1].lower()}{normalized_question[1:]}"
        if "age" in normalize_for_match(normalized_question):
            return f"Age {normalized_label}"
        if normalize_for_match(normalized_question) in {"gender", "sex"}:
            return normalized_label
        return f"{normalized_question}: {normalized_label}"

    def _current_evaluation_question(
        self, session: ChatSession
    ) -> dict[str, str | int] | None:
        questions = session.evaluation_questions or []
        if session.evaluation_index < 0 or session.evaluation_index >= len(questions):
            return None
        return questions[session.evaluation_index]

    def _evaluation_questions(self) -> list[dict[str, str | int]]:
        rows = self.db.scalars(
            select(EvaluationQuestion)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category.in_(EVALUATION_CATEGORIES),
            )
            .order_by(EvaluationQuestion.category.desc(), EvaluationQuestion.sort_order)
        ).all()
        category_order = {category: index for index, category in enumerate(EVALUATION_CATEGORIES)}
        sorted_rows = sorted(
            rows,
            key=lambda row: (category_order.get(row.category, 99), row.sort_order, row.id),
        )
        return [
            {
                "id": row.id,
                "category": row.category,
                "chart_title": row.chart_title or normalize_markdown_text(row.question),
                "question": normalize_markdown_text(row.question),
            }
            for row in sorted_rows
        ]

    @staticmethod
    def _extend_unique_profiles(existing: list[str], new_profiles: list[str]) -> None:
        for profile in new_profiles:
            if any(ChatService._profiles_are_similar(profile, existing_profile) for existing_profile in existing):
                continue
            existing.append(profile)

    @staticmethod
    def _match_existing_dg(session: ChatSession, new_profiles: list[str]) -> dict[str, object] | None:
        existing = ChatService._selected_hazard_profile_names(session)
        for profile in new_profiles:
            for existing_profile in existing:
                if ChatService._profiles_are_similar(profile, existing_profile):
                    return {
                        "duplicate": True,
                        "match": existing_profile,
                        "reason": "The proposed profile is the same as, or very similar to, an existing profile.",
                        "duplicates": [
                            {
                                "profile": profile,
                                "match": existing_profile,
                                "reason": "Similar profile already exists.",
                            }
                        ],
                    }
        return None

    @staticmethod
    def _profiles_are_similar(left: str, right: str) -> bool:
        left_key = normalize_for_match(left)
        right_key = normalize_for_match(right)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        left_compact = compact_for_match(left)
        right_compact = compact_for_match(right)
        if left_compact and right_compact and (
            left_compact in right_compact or right_compact in left_compact
        ):
            return True
        left_words = ChatService._profile_similarity_words(left_key)
        right_words = ChatService._profile_similarity_words(right_key)
        if not left_words or not right_words:
            return False
        overlap = len(left_words & right_words)
        smaller_overlap = overlap / max(1, min(len(left_words), len(right_words)))
        larger_overlap = overlap / max(1, max(len(left_words), len(right_words)))
        return smaller_overlap >= 0.85 or (smaller_overlap >= 0.7 and larger_overlap >= 0.5)

    @staticmethod
    def _profile_similarity_words(value: str) -> set[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "for",
            "in",
            "is",
            "of",
            "the",
            "to",
            "with",
        }
        words: set[str] = set()
        for word in value.split():
            if word in stop_words or len(word) <= 2:
                continue
            if len(word) > 4 and word.endswith("ies"):
                word = word[:-3] + "y"
            elif len(word) > 4 and word.endswith("s"):
                word = word[:-1]
            words.add(word)
        return words

    @staticmethod
    def _selected_hazard_profile_names(session: ChatSession) -> list[str]:
        profiles: list[str] = []
        if session.socio_demographic_profiles:
            profiles.extend(session.socio_demographic_profiles)
        elif session.socio_demographic_findings:
            profiles.extend(ChatService._extract_socio_demographic_profiles(session.socio_demographic_findings))

        selected_hazard = session.selected_hazard or session.accepted_custom_hazard
        if selected_hazard:
            stored_profiles = ChatService._stored_hazard_profiles(session, selected_hazard)
            profiles.extend(profile["name"] for profile in stored_profiles if profile.get("name"))

        profiles.extend(session.additional_dgs or [])

        deduped: list[str] = []
        seen: set[str] = set()
        for profile in profiles:
            key = normalize(profile)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(profile)
        return deduped

    @staticmethod
    def _format_selected_hazard_profiles_for_duplicate_check(session: ChatSession) -> str:
        profiles = ChatService._selected_hazard_profile_names(session)
        if not profiles:
            return "- No socio-demographic profiles have been identified for the selected hazard yet."
        return "\n".join(f"- {profile}" for profile in profiles)

    @staticmethod
    def _format_duplicate_dgs(duplicate_check: dict[str, object]) -> str:
        duplicates = duplicate_check.get("duplicates")
        if isinstance(duplicates, list) and duplicates:
            lines: list[str] = []
            for item in duplicates:
                if not isinstance(item, dict):
                    continue
                profile = str(item.get("profile") or "Proposed profile")
                match = str(item.get("match") or "an existing profile")
                reason = str(item.get("reason") or "").strip()
                line = f"- **{profile}** already appears to be covered by **{match}**"
                if reason:
                    line += f": {reason}"
                lines.append(line)
            if lines:
                return "\n".join(lines)

        match = str(duplicate_check.get("match") or "an existing profile")
        reason = str(duplicate_check.get("reason") or "").strip()
        line = f"- The proposed profile already appears to be covered by **{match}**"
        if reason:
            line += f": {reason}"
        return line

    @staticmethod
    def _format_hazard_suggestions(review: dict[str, object]) -> str:
        suggestions = review.get("suggestions")
        if isinstance(suggestions, list):
            lines = [f"- **{item}**" for item in suggestions if str(item).strip()]
            if lines:
                return "\n".join(lines)
        return ""

    @staticmethod
    def _reason_evidence_quality_fields(
        reason: str, evidence: str | None
    ) -> dict[str, str]:
        fields = {"Reason": reason}
        if evidence and evidence.strip():
            fields["Evidence URL or file content"] = evidence
        return fields

    @staticmethod
    def _evaluation_evidence_text(evidence: str | None) -> str | None:
        if not evidence or not evidence.strip():
            return None

        lines = [line.strip() for line in evidence.splitlines() if line.strip()]
        if not lines:
            return None

        content_lines = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.casefold().startswith("evidence content:")
            and line.split(":", 1)[1].strip()
        ]
        if content_lines:
            source_lines = [
                line
                for line in lines
                if line.casefold().startswith(("evidence url:", "evidence file:"))
            ]
            return "\n".join([*source_lines, *content_lines]).strip()

        return "\n".join(lines)

    @staticmethod
    def _temporary_evidence_document_ids(evidence: str | None) -> list[int]:
        if not evidence:
            return []
        return [
            int(match)
            for match in re.findall(
                r"Temporary evidence document ID:\s*(\d+)",
                evidence,
                flags=re.IGNORECASE,
            )
        ]

    def _discard_temporary_evidence(self, session: ChatSession, evidence: str | None) -> None:
        document_ids = self._temporary_evidence_document_ids(evidence)
        if not document_ids or not session.session_key:
            return
        try:
            KnowledgeBaseService(
                self.db,
                self.user_id,
                scope="temporary",
                session_key=session.session_key,
            ).delete_temporary_documents(document_ids)
        except Exception:
            logger.exception("Failed to discard rejected temporary evidence")

    def _promote_temporary_evidence(
        self,
        session: ChatSession,
        *,
        target_scope: str = "main",
        provenance: str | None = None,
    ) -> None:
        if not session.session_key:
            return
        try:
            KnowledgeBaseService(
                self.db,
                self.user_id,
                scope="temporary",
                session_key=session.session_key,
            ).promote_temporary_documents(
                target_scope=target_scope,
                provenance=provenance,
            )
        except Exception:
            logger.exception("Failed to promote temporary evidence")

    @staticmethod
    def _has_hazard_suggestions(review: dict[str, object]) -> bool:
        suggestions = review.get("suggestions")
        return isinstance(suggestions, list) and any(str(item).strip() for item in suggestions)

    @staticmethod
    def _local_similar_hazards(hazard: str, existing_hazards: list[str]) -> list[str]:
        query = normalize_for_match(hazard)
        compact_query = compact_for_match(hazard)
        if not query or not compact_query:
            return []

        query_words = ChatService._hazard_similarity_words(query)
        matches: list[str] = []
        for existing in existing_hazards:
            existing_normalized = normalize_for_match(existing)
            compact_existing = compact_for_match(existing)
            if not existing_normalized or not compact_existing:
                continue

            existing_words = ChatService._hazard_similarity_words(existing_normalized)
            overlap = len(query_words & existing_words) / max(1, len(query_words))
            reverse_overlap = len(query_words & existing_words) / max(1, len(existing_words))
            is_contained = compact_query in compact_existing or compact_existing in compact_query
            if (
                is_contained
                or overlap >= 0.75
                or reverse_overlap >= 0.75
                or fuzzy_score(hazard, existing) >= 0.82
            ):
                matches.append(existing)

        return list(dict.fromkeys(matches))

    @staticmethod
    def _hazard_similarity_words(value: str) -> set[str]:
        words: set[str] = set()
        for word in value.split():
            if len(word) <= 2:
                continue
            if len(word) > 3 and word.endswith("ies"):
                word = word[:-3] + "y"
            elif len(word) > 3 and word.endswith("s"):
                word = word[:-1]
            words.add(word)
        return words

    @staticmethod
    def _extract_socio_demographic_profiles(markdown_text: str) -> list[str]:
        profiles: list[str] = []
        seen: set[str] = set()
        for raw_line in markdown_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            bullet_match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", line)
            if bullet_match is None:
                continue

            profile = bullet_match.group(1).strip()
            if ChatService._is_statistical_basis_line(profile):
                continue
            profile = re.sub(r"\*\*(.*?)\*\*", r"\1", profile)
            profile = re.sub(r"__(.*?)__", r"\1", profile)
            profile = profile.strip("`*_ ")
            if ChatService._is_statistical_basis_line(profile):
                continue
            for separator in (":", " - ", " – ", " — "):
                if separator in profile:
                    profile = profile.split(separator, 1)[0].strip()
                    break
            profile = profile.strip(" .;,-")
            if not profile or len(profile) > 180:
                continue
            if profile.casefold().startswith(
                (
                    "socio-demographic",
                    "statistical basis",
                    "basis",
                    "reason",
                    "evidence",
                )
            ):
                continue
            key = normalize(profile)
            if key not in seen:
                seen.add(key)
                profiles.append(profile)
        return profiles

    @staticmethod
    def _parse_hazard_profile_items(response: str) -> list[dict[str, str]]:
        if is_llm_unavailable_response(response):
            return []
        try:
            parsed = json.loads(response.strip())
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, list):
            try:
                parsed = json.loads(ChatService._extract_json_array(response))
            except json.JSONDecodeError:
                parsed = None
        if not isinstance(parsed, list):
            return []

        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in parsed:
            variable_name = ""
            statistical_basis = ""
            source = "sector_prompt"
            if isinstance(item, str):
                name = item.strip().strip("`*_ ")
                explanation = ""
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("profile") or "").strip().strip("`*_ ")
                explanation = str(
                    item.get("explanation")
                    or item.get("reason")
                    or item.get("description")
                    or ""
                ).strip().strip("`*_ ")
                variable_name = str(
                    item.get("variable_name")
                    or item.get("variable")
                    or item.get("predictor")
                    or ""
                ).strip().strip("`*_ ")
                statistical_basis = str(
                    item.get("statistical_basis")
                    or item.get("basis")
                    or item.get("statistical_evidence")
                    or ""
                ).strip().strip("`*_ ")
                source = str(item.get("source") or "sector_prompt").strip().strip("`*_ ")
            else:
                continue
            if not name:
                continue
            key = normalize(name)
            if key in seen:
                continue
            seen.add(key)
            profile_item = {
                "name": name[:120],
                "profile": name[:120],
                "explanation": explanation[:260],
                "variable_name": variable_name[:160],
                "statistical_basis": statistical_basis[:600],
                "source": source[:40] if source else "sector_prompt",
            }
            if isinstance(item, dict):
                metadata = compact_profile_metadata(item)
                if metadata:
                    profile_item["metadata"] = metadata
            profiles.append(profile_item)
        return profiles[:12]

    @staticmethod
    def _extract_json_array(value: str) -> str:
        start = value.find("[")
        end = value.rfind("]")
        if start != -1 and end != -1 and end > start:
            return value[start : end + 1]
        return "[]"

    async def _profiles_with_population_context(
        self,
        session: ChatSession,
        hazard: str,
        profiles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not profiles:
            return profiles
        population_profiles = self._ranking_population_profiles(session, hazard)
        if not population_profiles:
            return []
        cached_matches: dict[str, list[dict[str, object]]] = {}
        blocked_profiles: set[str] = set()
        profiles_needing_match: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name or self._profile_has_odds_ratio_below_one(profile):
                continue
            if self._profile_population_match_blocked(session, hazard, profile):
                blocked_profiles.add(normalize(name))
                continue
            cached_match = self._matched_population_profile_from_db(
                session,
                hazard,
                profile,
                population_profiles,
            )
            if cached_match:
                cached_matches[normalize(name)] = cached_match
            else:
                profiles_needing_match.append(profile)
        llm_matches = (
            await self._match_population_profiles_with_llm(
                profiles_needing_match,
                population_profiles,
            )
            if profiles_needing_match
            else {}
        )
        enriched: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name or self._profile_has_odds_ratio_below_one(profile):
                continue
            if normalize(name) in blocked_profiles:
                continue
            matches = self._merge_population_profile_matches(
                cached_matches.get(normalize(name), []),
                llm_matches.get(normalize(name), []),
                self._deterministic_population_profile_matches(
                    name,
                    population_profiles,
                ),
            )
            percentages = self._population_context_percentages(matches) if matches else None
            if percentages is None:
                self._record_profile_population_match_failure(session, hazard, profile)
                continue
            self._store_matched_profile_population_references(
                session,
                hazard,
                profile,
                matches,
            )
            updated = dict(profile)
            updated["regional_population_pct"] = percentages[0]
            updated["national_population_pct"] = percentages[1]
            updated.pop("population_context", None)
            enriched.append(updated)
        return enriched

    async def _additional_profiles_with_population_context(
        self,
        session: ChatSession,
        hazard: str,
        profiles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not profiles:
            return profiles
        country = self.db.get(Country, session.country_id) if session.country_id else None
        sector = self.db.get(Sector, session.sector_id) if session.sector_id else None
        region = self.db.get(Region, session.region_id) if session.region_id else None
        if country is None or sector is None:
            return profiles
        country_name = str(country.name or "").strip()
        region_name = str((region.name if region else country.name) or "").strip()
        sector_name = str(sector.name or "").strip()
        if not country_name or not region_name or not sector_name:
            return profiles

        enriched: list[dict[str, str]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                enriched.append(profile)
                continue
            lookup_labels = self._additional_profile_population_lookup_labels(profile)
            if not lookup_labels:
                enriched.append(profile)
                continue
            population_profiles: list[dict[str, object]] = []
            for label in lookup_labels:
                try:
                    prevalence = await self.eurostat.get_prevalence(
                        label,
                        country_code=country_name,
                        nuts_code=region_name,
                        sector=sector_name,
                        hazard=hazard,
                        confirmed_predictor_category=label,
                    )
                except Exception:
                    logger.exception(
                        "Failed to fetch Eurostat population for additional hazard profile"
                    )
                    continue
                if prevalence is None:
                    continue
                population_profiles.append(
                    {
                        "name": label,
                        "eurostat_population_cache_id": prevalence.get(
                            "eurostat_population_cache_id"
                        ),
                        "population_pct": prevalence.get("population_pct"),
                        "national_population_pct": prevalence.get("national_population_pct"),
                        "source": prevalence.get("source"),
                        "dataset": prevalence.get("dataset"),
                        "geo": prevalence.get("geo"),
                    }
                )
            percentages = (
                self._population_context_percentages(population_profiles)
                if population_profiles
                else None
            )
            if percentages is None:
                enriched.append(profile)
                continue
            updated = dict(profile)
            updated["regional_population_pct"] = percentages[0]
            updated["national_population_pct"] = percentages[1]
            updated["population_context"] = population_profiles
            updated["population_source"] = "Eurostat"
            updated["population_lookup_labels"] = lookup_labels
            enriched.append(updated)
        return enriched

    @staticmethod
    def _additional_profile_population_lookup_labels(
        profile: dict[str, object],
    ) -> list[str]:
        labels: list[str] = []
        raw_labels = profile.get("target_population_labels")
        if isinstance(raw_labels, list):
            labels.extend(str(label).strip() for label in raw_labels if str(label).strip())
        if not labels:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if name:
                labels.append(name)
        seen: set[str] = set()
        deduped: list[str] = []
        for label in labels:
            key = normalize(label)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(label)
        return deduped

    @staticmethod
    def _profile_has_odds_ratio_below_one(profile: dict[str, object]) -> bool:
        candidates: list[object] = [profile]
        metadata = profile.get("metadata")
        if isinstance(metadata, dict):
            candidates.append(metadata)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("odds_ratio", "or", "OR"):
                if candidate.get(key) is None:
                    continue
                try:
                    return float(candidate[key]) < 1
                except (TypeError, ValueError):
                    pass
        text_value = " ".join(
            str(profile.get(key) or "")
            for key in ("explanation", "statistical_basis", "basis")
        )
        ratio_matches = re.finditer(
            r"(?i)(?:odds\s+ratio|\bOR\b)\s*(?:=|:|is|<|>|<=|>=)?\s*(\d+(?:\.\d+)?)",
            text_value,
        )
        for ratio_match in ratio_matches:
            try:
                if float(ratio_match.group(1)) < 1:
                    return True
            except (TypeError, ValueError):
                continue
        normalized_text = normalize_for_match(text_value)
        basis_text = normalize_for_match(
            " ".join(
                str(profile.get(key) or "")
                for key in ("statistical_basis", "basis")
            )
        )
        if "decreases" in basis_text or "direction decreases" in basis_text:
            return True
        return any(
            marker in normalized_text
            for marker in ("protective", "lower odds", "lower concern")
        )

    async def _match_population_profiles_with_llm(
        self,
        profiles: list[dict[str, str]],
        population_profiles: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        profile_items = [
            {
                "name": str(profile.get("name") or profile.get("profile") or "").strip(),
                "explanation": str(profile.get("explanation") or "").strip(),
            }
            for profile in profiles
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        candidate_names = [
            str(profile.get("name") or "").strip()
            for profile in population_profiles
            if str(profile.get("name") or "").strip()
        ]
        if not profile_items or not candidate_names:
            return {}
        context = """
You match socio-demographic profile labels for Dr Transition.

Return only exact JSON. Match a displayed profile to a population profile only
when they refer to the same people, household group, income group, age group,
education group, or dwelling group. Return every compatible category. If there
is no clear match, return an empty matched_profiles array.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Displayed profiles with explanations:\n"
                    + json.dumps(profile_items, ensure_ascii=False)
                    + "\n\nPopulation profiles:\n"
                    + json.dumps(candidate_names, ensure_ascii=False)
                    + "\n\nReturn ONLY a JSON array like:\n"
                    '[{"profile": "displayed profile", '
                    '"matched_profiles": ["population profile 1", "population profile 2"]}]'
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=500,
        )
        try:
            parsed = json.loads(response.strip())
        except json.JSONDecodeError:
            try:
                parsed = json.loads(self._extract_json_array(response))
            except json.JSONDecodeError:
                return {}
        if not isinstance(parsed, list):
            return {}
        population_by_name = {
            normalize(str(profile.get("name") or "")): profile
            for profile in population_profiles
            if str(profile.get("name") or "").strip()
        }
        matches: dict[str, list[dict[str, object]]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            profile_name = str(item.get("profile") or "").strip()
            raw_matched_names = item.get("matched_profiles")
            if not isinstance(raw_matched_names, list):
                legacy_name = item.get("matched_profile")
                raw_matched_names = [legacy_name] if legacy_name is not None else []
            if not profile_name:
                continue
            matched_profiles = [
                population_by_name[normalize(str(matched_name or "").strip())]
                for matched_name in raw_matched_names
                if normalize(str(matched_name or "").strip()) in population_by_name
            ]
            if matched_profiles:
                matches[normalize(profile_name)] = matched_profiles
        return matches

    @staticmethod
    def _ranking_population_profiles(
        session: ChatSession,
        hazard: str,
    ) -> list[dict[str, object]]:
        rankings = session.hazard_rankings or {}
        ranking = rankings.get(hazard)
        if ranking is None:
            hazard_key = normalize(hazard)
            for stored_hazard, stored_ranking in rankings.items():
                if normalize(str(stored_hazard)) == hazard_key:
                    ranking = stored_ranking
                    break
        if not isinstance(ranking, dict):
            return []
        profiles = ranking.get("profiles")
        if not isinstance(profiles, list):
            return []
        return [
            profile
            for profile in profiles
            if isinstance(profile, dict)
            and str(profile.get("name") or "").strip()
            and profile.get("population_pct") is not None
        ]

    def _matched_population_profile_from_db(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
        population_profiles: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if session.sector_id is None:
            return []
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return []
        cache_ids: set[int] = set()
        for population_profile in population_profiles:
            try:
                cache_id = int(population_profile.get("eurostat_population_cache_id") or 0)
            except (TypeError, ValueError):
                continue
            if cache_id > 0:
                cache_ids.add(cache_id)
        if not cache_ids:
            return []
        system_hazard = self.db.scalar(
            select(SystemHazard).where(
                SystemHazard.sector_id == session.sector_id,
                SystemHazard.name == hazard,
            )
        )
        if system_hazard is None:
            return []
        system_profile = self.db.scalar(
            select(SystemHazardSocioDemographic).where(
                SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
                func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
            )
        )
        if system_profile is None:
            return []
        matched_caches = self.db.scalars(
            select(EurostatPopulationCache)
            .join(
                SystemHazardSocioDemographicPopulationMatch,
                SystemHazardSocioDemographicPopulationMatch.eurostat_population_cache_id
                == EurostatPopulationCache.id,
            )
            .where(
                SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                == system_profile.id,
                SystemHazardSocioDemographicPopulationMatch.match_status == 1,
                EurostatPopulationCache.id.in_(cache_ids),
                EurostatPopulationCache.country_id == session.country_id,
                EurostatPopulationCache.region_id == session.region_id,
                EurostatPopulationCache.sector_id == session.sector_id,
                EurostatPopulationCache.system_hazard_id == system_hazard.id,
                EurostatPopulationCache.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
            )
        ).all()
        matched_cache_ids = {cache.id for cache in matched_caches}
        return [
            population_profile
            for population_profile in population_profiles
            if int(population_profile.get("eurostat_population_cache_id") or 0)
            in matched_cache_ids
        ]

    def _profile_population_match_blocked(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> bool:
        system_profile = self._system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return False
        blocked = self.db.scalar(
            select(SystemHazardSocioDemographicPopulationMatch).where(
                SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                == system_profile.id,
                SystemHazardSocioDemographicPopulationMatch.match_status == -1,
            )
        )
        return blocked is not None

    def _record_profile_population_match_failure(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> None:
        system_profile = self._ensure_system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographicPopulationMatch).where(
                    SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                    == system_profile.id,
                    SystemHazardSocioDemographicPopulationMatch.eurostat_population_cache_id.is_(None),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographicPopulationMatch(
                    system_hazard_socio_demographic_id=system_profile.id,
                    eurostat_population_cache_id=None,
                    match_status=0,
                    attempt_count=0,
                )
                self.db.add(row)
            row.attempt_count = int(row.attempt_count or 0) + 1
            if row.attempt_count >= 3:
                row.match_status = -1
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to record profile population match failure")

    @staticmethod
    def _deterministic_population_profile_matches(
        profile_name: str,
        population_profiles: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        profile_key = normalize(profile_name)
        return [
            population_profile
            for population_profile in population_profiles
            if (
                (candidate_key := normalize(str(population_profile.get("name") or "").strip()))
                and profile_key
                and (profile_key in candidate_key or candidate_key in profile_key)
            )
        ]

    @staticmethod
    def _merge_population_profile_matches(
        *match_groups: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        seen: set[tuple[int, str]] = set()
        for group in match_groups:
            for profile in group:
                try:
                    cache_id = int(profile.get("eurostat_population_cache_id") or 0)
                except (TypeError, ValueError):
                    cache_id = 0
                key = (cache_id, normalize(str(profile.get("name") or "")))
                if key in seen:
                    continue
                seen.add(key)
                matches.append(profile)
        return matches

    @staticmethod
    def _population_context_percentages(
        profiles: list[dict[str, object]],
    ) -> tuple[float, float] | None:
        regional_values: list[float] = []
        national_values: list[float] = []
        for profile in profiles:
            try:
                regional_values.append(float(profile.get("population_pct")))
                national_values.append(float(profile.get("national_population_pct")))
            except (TypeError, ValueError):
                continue
        if not regional_values or not national_values:
            return None
        regional_pct = sum(regional_values) / len(regional_values)
        national_pct = sum(national_values) / len(national_values)
        return round(regional_pct, 1), round(national_pct, 1)

    def _format_hazard_profiles_markdown(
        self,
        hazard: str,
        profiles: list[dict[str, str]],
        *,
        user_profiles: list[dict[str, str]] | None = None,
    ) -> str:
        lines = [f"### Socio-demographic profiles most affected by {hazard}"]
        if not profiles and not user_profiles:
            lines.append("- No clearly supported socio-demographic profiles were returned for this hazard.")
            return "\n".join(lines)
        rows = self._hazard_profile_table_rows(profiles)
        user_rows = self._hazard_profile_table_rows(
            [self._system_style_user_profile(profile) for profile in (user_profiles or [])]
        )
        if rows:
            lines.append("")
            lines.append(self._hazard_profile_table_html(rows))
        if user_rows:
            lines.append("")
            lines.append("#### User-added socio-demographic profiles")
            lines.append(self._hazard_profile_table_html(user_rows))
        return "\n".join(lines)

    @classmethod
    def _hazard_profile_table_rows(
        cls, profiles: list[dict[str, str]]
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip()
            variable_type = str(profile.get("variable_type") or "").strip()
            rows.append(
                {
                    "name": name,
                    "explanation": cls._clean_profile_explanation(
                        str(profile.get("explanation") or "").strip()
                    ),
                    "regional": profile.get("regional_population_pct")
                    or profile.get("population_pct"),
                    "national": profile.get("national_population_pct"),
                    "is_macro": cls._profile_variable_type(variable_name, variable_type) == "macro",
                }
            )
        return rows

    @classmethod
    def _hazard_profile_table_html(cls, rows: list[dict[str, object]]) -> str:
        body_rows: list[str] = []
        for row in rows:
            macro_label = (
                '<span class="profile-type-label">MACRO</span>'
                if row.get("is_macro")
                else ""
            )
            regional = row.get("regional")
            national = row.get("national")
            description = str(row.get("explanation") or "").strip()
            body_rows.append(
                "<tr>"
                '<th scope="row">'
                f'<strong>{escape(str(row.get("name") or ""))}</strong>{macro_label}'
                f'{f"<small>{escape(description)}</small>" if description else ""}'
                "</th>"
                f'<td><span class="population-value">{cls._format_profile_population(regional)}</span>'
                f"{cls._profile_population_comparison(regional, national)}</td>"
                f'<td><span class="population-value">{cls._format_profile_population(national)}</span></td>'
                "</tr>"
            )
        return (
            '<div class="hazard-population-table hazard-population-table--selected">'
            "<table>"
            "<thead><tr>"
            '<th scope="col">Population profile</th>'
            '<th scope="col">Regional</th>'
            '<th scope="col">National</th>'
            "</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
            "</div>"
        )

    @staticmethod
    def _clean_profile_explanation(explanation: str) -> str:
        cleaned = re.sub(
            r"(?i)\s*(?:This profile represents about [0-9.]+% of the regional population, "
            r"compared with [0-9.]+% nationally\.|Across \d+ matched Eurostat profiles, the average "
            r"population share is [0-9.]+% regionally and [0-9.]+% nationally\.)",
            "",
            explanation,
        )
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _format_profile_population(value: object) -> str:
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _profile_population_comparison(regional: object, national: object) -> str:
        try:
            difference = float(regional) - float(national)
        except (TypeError, ValueError):
            return ""
        if abs(difference) < 0.05:
            return '<span class="population-trend is-equal" title="Equal to national" aria-label="equal to national">•</span>'
        if difference > 0:
            return '<span class="population-trend is-up" title="Higher than national" aria-label="higher than national">↑</span>'
        return '<span class="population-trend is-down" title="Lower than national" aria-label="lower than national">↓</span>'

    @staticmethod
    def _append_profile_lines(lines: list[str], profiles: list[dict[str, str]]) -> None:
        for profile in profiles:
            name = profile.get("name", "").strip()
            if not name:
                continue
            explanation = profile.get("explanation", "").strip()
            if explanation:
                lines.append(f"- **{name}**: {explanation}")
            else:
                lines.append(f"- **{name}**")

    @staticmethod
    def _system_style_user_profile(profile: dict[str, str]) -> dict[str, str]:
        name = str(profile.get("name") or profile.get("profile") or "").strip()
        explanation = str(profile.get("explanation") or "").strip()
        if not explanation:
            metadata = profile.get("metadata")
            if isinstance(metadata, dict):
                explanation = str(
                    metadata.get("explanation")
                    or metadata.get("reason")
                    or metadata.get("description")
                    or ""
                ).strip()
        if not explanation:
            source = str(profile.get("source") or "").strip()
            if source == "target_population":
                explanation = "Added from the target-population selections for this hazard."
            else:
                explanation = (
                    "Added by the user as an additional socio-demographic profile "
                    "and validated for this hazard."
                )
        return {
            **profile,
            "name": name,
            "profile": name,
            "explanation": explanation,
        }

    @staticmethod
    def _stored_hazard_profiles(session: ChatSession, hazard: str) -> list[dict[str, str]]:
        stored_profiles = session.hazard_profiles or {}
        values = stored_profiles.get(hazard)
        if values is None:
            hazard_key = normalize(hazard)
            for stored_hazard, stored_value in stored_profiles.items():
                if normalize(str(stored_hazard)) == hazard_key:
                    values = stored_value
                    break
        if values is None:
            return []
        if isinstance(values, str):
            raw_items: list[dict[str, str] | str] = [values]
        else:
            raw_items = list(values)

        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("profile") or "").strip()
                explanation = str(item.get("explanation") or "").strip()
                variable_name = str(item.get("variable_name") or item.get("variable") or "").strip()
                variable_type = str(item.get("variable_type") or "").strip()
                statistical_basis = str(item.get("statistical_basis") or "").strip()
                source = str(item.get("source") or "").strip()
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                target_population_option_ids = item.get("target_population_option_ids")
                target_population_labels = item.get("target_population_labels")
                population_context = item.get("population_context")
                population_lookup_labels = item.get("population_lookup_labels")
                regional_population_pct = (
                    item.get("regional_population_pct") or item.get("population_pct")
                )
                national_population_pct = item.get("national_population_pct")
                population_source = item.get("population_source")
            else:
                name = str(item).strip()
                explanation = ""
                variable_name = ""
                variable_type = ""
                statistical_basis = ""
                source = ""
                metadata = {}
                target_population_option_ids = []
                target_population_labels = []
                population_context = []
                population_lookup_labels = []
                regional_population_pct = None
                national_population_pct = None
                population_source = None
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": explanation,
                    "variable_name": variable_name,
                    "variable_type": ChatService._profile_variable_type(variable_name, variable_type),
                    "statistical_basis": statistical_basis,
                    "source": source,
                    "metadata": metadata,
                    "target_population_option_ids": (
                        list(target_population_option_ids)
                        if isinstance(target_population_option_ids, list)
                        else []
                    ),
                    "target_population_labels": (
                        list(target_population_labels)
                        if isinstance(target_population_labels, list)
                        else []
                    ),
                    "regional_population_pct": regional_population_pct,
                    "population_pct": regional_population_pct,
                    "national_population_pct": national_population_pct,
                    "population_source": str(population_source or ""),
                    "population_context": (
                        list(population_context)
                        if isinstance(population_context, list)
                        else []
                    ),
                    "population_lookup_labels": (
                        list(population_lookup_labels)
                        if isinstance(population_lookup_labels, list)
                        else []
                    ),
                }
            )
        return profiles

    @staticmethod
    def _profile_variable_type(variable_name: object, variable_type: object = "") -> str:
        if str(variable_type or "").strip().casefold() == "macro":
            return "macro"
        if str(variable_name or "").strip().casefold().startswith("macro_"):
            return "macro"
        return "individual"

    def _stored_user_hazard_profiles(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        try:
            query = (
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    func.lower(UserHazard.name) == hazard.casefold(),
                    UserSession.country_id == session.country_id,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id.is_(None)
                    if session.region_id is None
                    else UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                    UserHazardSocioDemographic.source.in_(
                        ["user_validated", "target_population"]
                    ),
                )
                .order_by(UserHazardSocioDemographic.id)
            )
            if self.user_id is not None:
                query = query.where(UserSession.user_id == self.user_id)
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load user-added socio-demographic profiles")
            return []

        target_questions = self._target_population_questions()
        grouped: dict[str, dict[str, object]] = {}
        ungrouped: list[dict[str, str]] = []
        for row in rows:
            name = str(row.profile or "").strip()
            if not name:
                continue
            metadata = self._metadata_from_json(row.metadata_json)
            variable_name = str(
                row.variable_name
                or metadata.get("variable_name")
                or metadata.get("variable")
                or ""
            ).strip()
            if not variable_name:
                variable_name = self._infer_target_population_question(name, target_questions)
            source = str(row.source or "user_validated")
            if variable_name:
                group = grouped.setdefault(
                    normalize(variable_name),
                    {
                        "question": variable_name,
                        "labels": [],
                        "source": source,
                        "statistical_basis": str(row.statistical_basis or ""),
                        "metadata": metadata,
                    },
                )
                labels = group.setdefault("labels", [])
                if isinstance(labels, list):
                    label = self._target_population_group_label(variable_name, name)
                    if label and normalize(label) not in {normalize(str(item)) for item in labels}:
                        labels.append(label)
                if not group.get("statistical_basis") and row.statistical_basis:
                    group["statistical_basis"] = str(row.statistical_basis)
                continue

            key = normalize(name)
            if key in {normalize(profile["name"]) for profile in ungrouped}:
                continue
            ungrouped.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.explanation or ""),
                    "variable_name": "",
                    "variable_type": self._profile_variable_type(""),
                    "statistical_basis": str(row.statistical_basis or ""),
                    "source": source,
                    "metadata": metadata,
                    "target_population_labels": (
                        metadata.get("target_population_labels")
                        if isinstance(metadata.get("target_population_labels"), list)
                        else ([name] if source == "target_population" else [])
                    ),
                }
            )

        profiles: list[dict[str, str]] = []
        for group in grouped.values():
            question = str(group.get("question") or "").strip()
            labels = [
                str(label).strip()
                for label in group.get("labels", [])
                if str(label).strip()
            ]
            labels_text = ", ".join(labels)
            name = f"{question}: {labels_text}" if labels_text else question
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": "Selected target-population responses for this hazard.",
                    "variable_name": question,
                    "variable_type": self._profile_variable_type(question),
                    "statistical_basis": str(group.get("statistical_basis") or ""),
                    "source": str(group.get("source") or "user_validated"),
                    "target_population_labels": labels,
                    "metadata": group.get("metadata") if isinstance(group.get("metadata"), dict) else {},
                }
            )
        profiles.extend(ungrouped)
        return profiles

    @staticmethod
    def _target_population_group_label(question: str, profile_name: str) -> str:
        question = question.strip().rstrip(".")
        profile_name = profile_name.strip()
        if normalize(profile_name) == normalize(question):
            return "Yes"
        not_prefix = f"Not {question[:1].lower()}{question[1:]}"
        if normalize(profile_name) == normalize(not_prefix):
            return "No"
        prefix = f"{question}:"
        if normalize(profile_name).startswith(normalize(prefix)):
            return profile_name.split(":", 1)[1].strip()
        if "age" in normalize_for_match(question) and normalize_for_match(profile_name).startswith("age "):
            return profile_name[4:].strip()
        return profile_name

    @staticmethod
    def _infer_target_population_question(
        profile_name: str, questions: list[dict[str, object]]
    ) -> str:
        profile_key = normalize_for_match(profile_name)
        for question in questions:
            question_text = str(question.get("question") or "").strip()
            question_key = normalize_for_match(question_text)
            if not question_key:
                continue
            if profile_key == question_key or profile_key == f"not {question_key}":
                return question_text
            if profile_key.startswith(f"{question_key} "):
                return question_text
            for option in question.get("options", []):
                option_key = normalize_for_match(str(option))
                if option_key and profile_key == option_key:
                    return question_text
        for question in questions:
            question_text = str(question.get("question") or "").strip()
            if "age" in normalize_for_match(question_text) and profile_key.startswith("age "):
                return question_text
        return ""

    @staticmethod
    def _confirmed_predictor_hazard_block(sector_prompt: str, hazard: str) -> str:
        target = normalize_for_match(hazard)
        hazard_pattern = re.compile(
            r"(?ms)^HAZARD\s+\d+\.\s+(.+?)\n(.*?)(?=^HAZARD\s+\d+\.|\Z)"
        )
        prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
        for match in hazard_pattern.finditer(prompt):
            heading = ChatService._clean_sector_hazard_name(match.group(1))
            if normalize_for_match(heading) == target:
                return strip_rule_lines(match.group(0))
        for match in hazard_pattern.finditer(prompt):
            heading = ChatService._clean_sector_hazard_name(match.group(1))
            if target in normalize_for_match(heading) or normalize_for_match(heading) in target:
                return strip_rule_lines(match.group(0))
        return ""

    @staticmethod
    def _is_statistical_basis_line(value: str) -> bool:
        normalized = normalize_markdown_text(value).strip().strip("*_` ").casefold()
        return normalized.startswith(
            (
                "statistical basis",
                "basis",
                "evidence",
                "reason",
                "why",
                "rationale",
                "note",
            )
        )

    @staticmethod
    def _strip_practical_sections(markdown_text: str) -> str:
        practical_headings = (
            "practical considerations",
            "practical policy recommendations",
            "current policy implementation",
        )
        kept_lines: list[str] = []
        skipping_heading_level: int | None = None

        for line in markdown_text.splitlines():
            stripped = line.strip()
            heading_marker = len(stripped) - len(stripped.lstrip("#"))
            is_heading = heading_marker > 0 and stripped[heading_marker:].startswith(" ")
            heading_text = stripped[heading_marker:].strip().strip(":").casefold()

            if is_heading:
                if any(heading in heading_text for heading in practical_headings):
                    skipping_heading_level = heading_marker
                    continue
                if skipping_heading_level is not None and heading_marker <= skipping_heading_level:
                    skipping_heading_level = None

            if skipping_heading_level is not None:
                continue

            lowered = stripped.casefold()
            if any(lowered.startswith(f"- {heading}") for heading in practical_headings):
                continue

            kept_lines.append(line)

        cleaned = "\n".join(kept_lines).strip()
        return cleaned or markdown_text.strip()

    def _hazard_options(self, session: ChatSession) -> list[Option]:
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(self._primary_hazard_names(session), start=1)
        ]
        if self._additional_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show hazards added by experts"))
        if self._custom_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show co-created hazards"))
        return options

    def _additional_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._additional_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    def _custom_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._custom_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    @staticmethod
    def _primary_hazard_names(session: ChatSession) -> list[str]:
        additional_keys = {normalize(hazard) for hazard in (session.additional_hazards or [])}
        custom_keys = {normalize(hazard) for hazard in (session.custom_hazards or [])}
        return [
            hazard
            for hazard in hazard_names(session)
            if normalize(hazard) not in additional_keys
            and normalize(hazard) not in custom_keys
        ]

    @staticmethod
    def _additional_hazard_options(session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.additional_hazards or [])
            if hazard and ChatService._stored_hazard_profiles(session, hazard)
        ]

    @staticmethod
    def _custom_hazard_options(session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.custom_hazards or [])
            if hazard and ChatService._stored_hazard_profiles(session, hazard)
        ]

    def _saved_custom_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        rows = self.db.scalars(
            select(UserHazard.name)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.country_id == session.country_id,
                UserHazard.sector_id == session.sector_id,
                UserHazard.region_id.is_(None)
                if session.region_id is None
                else UserHazard.region_id == session.region_id,
                UserHazard.source == "custom",
            )
            .order_by(UserHazard.name)
        ).all()

        seen: set[str] = set()
        hazards: list[str] = []
        system_names = {normalize(hazard) for hazard in (session.hazards or [])}
        for row in rows:
            key = normalize(row)
            if key in seen or key in system_names:
                continue
            seen.add(key)
            hazards.append(row)
        return hazards

    def _additional_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        rows = self.db.execute(
            select(AdditionalHazard, AdditionalHazardProfile)
            .outerjoin(
                AdditionalHazardProfile,
                AdditionalHazardProfile.additional_hazard_id == AdditionalHazard.id,
            )
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(
                AdditionalHazard.csv_row_number,
                AdditionalHazard.name,
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [
            int(profile_row.id)
            for _, profile_row in rows
            if profile_row is not None and isinstance(profile_row.id, int)
        ]
        target_population_by_profile = (
            self._additional_hazard_profile_target_population_map(profile_ids)
            if profile_ids
            else {}
        )

        existing_names = {
            normalize(hazard)
            for hazard in [
                *(session.hazards or []),
                *(session.custom_hazards or []),
            ]
        }
        seen: set[str] = set()
        hazards: list[str] = []
        profiles_by_hazard: dict[str, list[dict[str, str]]] = {}
        for hazard_row, profile_row in rows:
            hazard = str(hazard_row.name or "").strip()
            key = normalize(hazard)
            if not hazard or key in existing_names:
                continue
            if key not in seen:
                seen.add(key)
                hazards.append(hazard)
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            mapped_targets = target_population_by_profile.get(int(profile_row.id), [])
            profiles_by_hazard.setdefault(hazard, []).append(
                {
                    "name": str(profile_row.profile).strip(),
                    "profile": str(profile_row.profile).strip(),
                    "explanation": str(profile_row.evidence or "").strip(),
                    "statistical_basis": str(profile_row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        int(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        if profiles_by_hazard:
            session.hazard_profiles = {
                **(session.hazard_profiles or {}),
                **profiles_by_hazard,
            }
        return hazards

    def _is_saved_custom_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.custom_hazards or []))

    def _is_additional_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.additional_hazards or []))

    def _additional_hazard_profile_target_population_map(
        self, profile_ids: list[int]
    ) -> dict[int, list[dict[str, object]]]:
        if not profile_ids:
            return {}
        rows = self.db.execute(
            select(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id
                == AdditionalHazardProfileTargetPopulation.question_option_id,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id.in_(
                    profile_ids
                ),
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                EvaluationQuestion.sort_order,
                QuestionOption.id,
            )
        ).all()
        mapped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            profile_id = int(row.additional_hazard_profile_id)
            mapped.setdefault(profile_id, []).append(
                {
                    "option_id": int(row.id),
                    "label": f"{row.question}: {row.option}",
                }
            )
        return mapped

    def _target_population_answers_for_saved_hazard(
        self, session: ChatSession, hazard: str
    ) -> str:
        if session.country_id is None or session.sector_id is None:
            return ""

        rows = self.db.execute(
            select(
                EvaluationQuestion.question,
                UserQuestionResponse.response_text,
            )
            .join(UserHazard, UserHazard.id == UserQuestionResponse.user_hazard_id)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
            .where(
                UserSession.country_id == session.country_id,
                UserHazard.sector_id == session.sector_id,
                UserHazard.region_id.is_(None)
                if session.region_id is None
                else UserHazard.region_id == session.region_id,
                UserHazard.source == "custom",
                UserHazard.name == hazard,
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
        ).all()

        if not rows:
            return ""

        lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        for question, response in rows:
            if not response:
                continue
            key = (question, response)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- **{normalize_markdown_text(question)}**: {response}")
        return "\n".join(lines)

    def _ensure_user_session(self, session_id: str, session: ChatSession) -> UserSession | None:
        try:
            user_session = self.db.scalar(
                select(UserSession).where(UserSession.session_key == session_id)
            )
            if user_session is None:
                user_session = UserSession(session_key=session_id)
                self.db.add(user_session)
            if self.user_id is not None:
                user_session.user_id = self.user_id
            user_session.country_id = session.country_id
            user_session.region_id = session.region_id
            user_session.sector_id = session.sector_id
            user_session.title = self._session_title(session)
            session_data = asdict(session)
            session_data["stats_dialog_conversation"] = None
            user_session.session_data = json.dumps(session_data, default=str)
            self.db.commit()
            self.db.refresh(user_session)
            return user_session
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist user session")
            return None

    def _session_belongs_to_current_user(self, session_id: str | None) -> bool:
        if not session_id or self.user_id is None:
            return True
        user_session = self.db.scalar(
            select(UserSession).where(UserSession.session_key == session_id)
        )
        if user_session is None or user_session.user_id is None:
            return True
        return user_session.user_id == self.user_id

    def _hydrate_session_from_db(self, session_id: str | None) -> None:
        if not session_id:
            return
        user_session = self.db.scalar(
            select(UserSession).where(UserSession.session_key == session_id)
        )
        if not user_session or not user_session.session_data:
            return
        try:
            session_store.put(session_id, json.loads(user_session.session_data))
        except json.JSONDecodeError:
            logger.warning("Could not restore invalid session snapshot for %s", session_id)

    def _finalize_chat_response(
        self, session_id: str, session: ChatSession, response: ChatResponse
    ) -> None:
        self._ensure_user_session(session_id, session)
        if response.error:
            return
        self._record_chat_message(
            session_id,
            session,
            "bot",
            response.bot_message,
            is_error=response.error,
        )

    def _record_chat_message(
        self,
        session_id: str,
        session: ChatSession,
        role: str,
        content: str,
        is_error: bool = False,
    ) -> None:
        if not content.strip():
            return
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            self.db.add(
                UserChatMessage(
                    user_session_id=user_session.id,
                    role=role,
                    content=content,
                    is_error=is_error,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist chat message")

    @staticmethod
    def _chat_message_display_content(content: str) -> str:
        if content.strip().startswith("TARGET_POPULATION_BATCH:"):
            return "Quick Select Target Population"
        return content

    def _recent_chat_messages_for_auto_user(
        self, session_id: str, limit: int = 10
    ) -> list[dict[str, str]]:
        try:
            user_session = self.db.scalar(
                select(UserSession).where(UserSession.session_key == session_id)
            )
            if user_session is None:
                return []
            rows = self.db.scalars(
                select(UserChatMessage)
                .where(UserChatMessage.user_session_id == user_session.id)
                .order_by(desc(UserChatMessage.created_at), desc(UserChatMessage.id))
                .limit(limit)
            ).all()
        except Exception:
            logger.exception("Failed to load chat messages for auto conversation")
            return []
        return [
            {"role": row.role, "content": row.content}
            for row in reversed(rows)
            if str(row.content or "").strip()
        ]

    async def _auto_user_message_from_llm(
        self,
        session: ChatSession,
        current_response: ChatResponse,
        history: list[dict[str, str]],
    ) -> str:
        options = [option.label for option in current_response.options]
        other_options = list(current_response.other_options or [])
        field_mode = current_response.input_mode in {
            "mitigation_measure",
            "reason_evidence",
            "textarea",
            "evaluation_question",
            "mitigation_review",
        }
        prompt_options = [] if field_mode else options
        prompt_other_options = [] if field_mode else other_options
        mode_instruction = (
            "The current step expects typed field input. Do NOT choose an option or navigation action; "
            "write the field content the form expects."
            if field_mode
            else "The current step expects an option or short answer. Prefer primary options when available."
        )
        context = f"""
You simulate a cooperative test user for Dr Transition.

Your job is to produce the next USER message that will move the workflow forward.
The real assistant will process your message after you return it.

Rules:
- Return only the exact user message, with no commentary, no Markdown wrapper, and no quotes.
- {mode_instruction}
- When selecting an option, use option text exactly.
- Do not choose "Other Options" or navigation actions unless the flow is blocked.
- Keep answers realistic, concise, and policy-relevant.
- For clarification questions, answer the specific question directly.
- For mitigation measure input, include the "Mitigation measure:" label.
- For reason/evidence input, include "Reason:" and omit evidence unless a simple citation is useful.
- For evaluation questions, include "Score:" and a short "Reason:".
- Never upload files or reference local files.

Current session:
- Country: {session.country or "Not selected"}
- Region: {session.region or "Not selected"}
- Sector: {session.sector or "Not selected"}
- Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or "Not selected"}
- Step: {current_response.step}
- Input mode: {current_response.input_mode}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Recent conversation:\n"
                    + (
                        "\n".join(
                            f"{item['role']}: {normalize_markdown_text(item['content'])[:900]}"
                            for item in history
                        )
                        or "- No prior messages."
                    )
                    + "\n\nCurrent assistant message:\n"
                    f"{normalize_markdown_text(current_response.bot_message)[:1200] or '- Empty.'}\n\n"
                    "Primary options:\n"
                    + ("\n".join(f"- {option}" for option in prompt_options) or "- None")
                    + "\n\nOther navigation options:\n"
                    + ("\n".join(f"- {option}" for option in prompt_other_options) or "- None")
                    + "\n\nGenerate the next user message now."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.35,
            max_tokens=260,
        )
        if is_llm_unavailable_response(response):
            return ""
        return self._clean_auto_user_message(
            response,
            current_response.input_mode,
            options,
            other_options,
            session,
        )

    @staticmethod
    def _clean_auto_user_message(
        response: str,
        input_mode: str,
        options: list[str],
        other_options: list[str],
        session: ChatSession,
    ) -> str:
        cleaned = response.strip().strip("`").strip()
        if cleaned.casefold().startswith("user:"):
            cleaned = cleaned.split(":", 1)[1].strip()
        cleaned = ChatService._strip_wrapping_quotes(cleaned)
        allowed = [*options, *other_options]
        for option in allowed:
            if normalize(cleaned) == normalize(option):
                fallback = ChatService._auto_user_fallback_for_input_mode(input_mode, session)
                if fallback:
                    return fallback
                return option
        return cleaned[:2000]

    @staticmethod
    def _auto_user_fallback_for_input_mode(input_mode: str, session: ChatSession) -> str:
        hazard = session.selected_hazard or session.accepted_custom_hazard or "the selected hazard"
        dgs = format_all_dgs(session)
        if input_mode == "mitigation_measure":
            return (
                "Mitigation measure: Provide targeted subsidies and advisory support "
                f"so affected groups can adapt to {hazard} without bearing disproportionate costs."
            )
        if input_mode == "reason_evidence":
            return (
                "Reason: This measure reduces the negative impact by lowering upfront "
                f"costs and giving practical support to the affected groups: {dgs[:400]}."
            )
        if input_mode == "textarea":
            return (
                "The cost coverage applies to the affected target groups by paying "
                "or reimbursing upfront adaptation costs directly for them, with "
                "guidance and implementation support so they can use the measure in practice."
            )
        if input_mode == "evaluation_question":
            return (
                "Score: 7\n"
                "Reason: The mitigation is relevant and practical, though it may need stronger "
                "funding and monitoring to reach every affected group."
            )
        if input_mode == "mitigation_review":
            return "Move to next step"
        return ""

    @staticmethod
    def _session_title(session: ChatSession) -> str:
        parts = [item for item in [session.country, session.region, session.sector] if item]
        if session.selected_hazard:
            parts.append(session.selected_hazard)
        return " / ".join(parts[:4]) or "New policy session"

    def _record_activity(
        self,
        session_id: str,
        session: ChatSession,
        activity_type: str,
        details: str | None = None,
        step: str | None = None,
    ) -> None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            self.db.add(
                UserActivity(
                    user_session_id=user_session.id,
                    activity_type=activity_type,
                    step=step or self._activity_step(session),
                    details=details,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist user activity")

    @staticmethod
    def _activity_step(session: ChatSession) -> str:
        if session.pending_fuzzy_option:
            return "fuzzy_confirmation"
        if session.country is None:
            return "country"
        if session.region is None:
            return "region"
        if session.sector is None:
            return "sector"
        return session.phase

    def _stored_hazard_items_for_context(
        self, session_id: str, session: ChatSession
    ) -> list[dict[str, object]]:
        if session.sector_id is None:
            return []
        try:
            query = (
                select(SystemHazard, SystemHazardSocioDemographic)
                .outerjoin(
                    SystemHazardSocioDemographic,
                    and_(
                        SystemHazardSocioDemographic.system_hazard_id == SystemHazard.id,
                        SystemHazardSocioDemographic.sector_id == session.sector_id,
                    ),
                )
                .where(SystemHazard.sector_id == session.sector_id)
                .order_by(SystemHazard.id, SystemHazardSocioDemographic.id)
            )
            rows = self.db.execute(query).all()
        except Exception:
            logger.exception("Failed to load stored hazards and profiles")
            return []

        items_by_hazard: dict[int, dict[str, object]] = {}
        seen_profiles: dict[int, set[str]] = {}
        for hazard, profile_row in rows:
            item = items_by_hazard.setdefault(
                hazard.id,
                {"hazard": hazard.name, "profiles": []},
            )
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            seen = seen_profiles.setdefault(hazard.id, set())
            profile_name = str(profile_row.profile).strip()
            key = normalize(profile_name)
            if key in seen:
                continue
            seen.add(key)
            item_profiles = item.setdefault("profiles", [])
            if isinstance(item_profiles, list):
                item_profiles.append(
                    {
                        "name": profile_name,
                        "profile": profile_name,
                        "explanation": str(profile_row.explanation or ""),
                        "variable_name": str(profile_row.variable_name or ""),
                        "variable_type": self._profile_variable_type(profile_row.variable_name),
                        "statistical_basis": str(profile_row.statistical_basis or ""),
                        "source": str(profile_row.source or "sector_prompt"),
                    }
                )
        return [
            item
            for item in items_by_hazard.values()
            if self._hazard_item_has_profiles(item)
        ]

    @staticmethod
    def _hazard_item_has_profiles(item: dict[str, object]) -> bool:
        profiles = item.get("profiles")
        if not isinstance(profiles, list):
            return False
        return any(
            (
                isinstance(profile, dict)
                and bool(str(profile.get("name") or profile.get("profile") or "").strip())
            )
            or (isinstance(profile, str) and bool(profile.strip()))
            for profile in profiles
        )

    async def _refresh_hazards_and_profiles_from_llm(
        self,
        session_id: str,
        session: ChatSession,
        *,
        replace_sector_hazards: bool = False,
    ) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_from_llm(session)
        valid_hazard_items = [
            item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
            and normalize(str(item.get("hazard") or "")) != normalize("Analysis not available")
            and self._hazard_item_has_profiles(item)
        ]
        if replace_sector_hazards and not valid_hazard_items:
            logger.warning(
                "Keeping existing hazards because refresh returned no usable sector hazards"
            )
            return self._stored_hazard_items_for_context(session_id, session)
        if replace_sector_hazards:
            self._delete_sector_hazards_and_generated_dgs(session)
        await self._match_system_profiles_to_target_populations(valid_hazard_items)
        self._persist_hazard_items_for_context(
            session_id,
            session,
            valid_hazard_items,
            replace_generated_profiles=True,
        )
        if replace_sector_hazards:
            self._relink_user_system_hazards(session)
        return valid_hazard_items

    async def _match_system_profiles_to_target_populations(
        self, hazard_items: list[dict[str, object]]
    ) -> None:
        option_rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, QuestionOption.id)
        ).all()
        if not option_rows:
            return

        profiles_by_key: dict[str, dict[str, object]] = {}
        for hazard_index, item in enumerate(hazard_items):
            for profile_index, profile in enumerate(item.get("profiles", [])):
                if not isinstance(profile, dict):
                    continue
                key = f"{hazard_index}:{profile_index}"
                profiles_by_key[key] = profile
        if not profiles_by_key:
            return

        for key, profile in profiles_by_key.items():
            profile["target_population_option_ids"] = sorted(
                self._deterministic_target_population_option_ids(profile, option_rows)
            )

    async def backfill_system_profile_target_populations(self) -> int:
        rows = self.db.execute(
            select(SystemHazardSocioDemographic, SystemHazard.name)
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .order_by(SystemHazardSocioDemographic.id)
        ).all()
        matched_rows = 0
        batch_size = 20
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            hazard_items: list[dict[str, object]] = []
            profiles_by_id: dict[int, dict[str, object]] = {}
            for row, hazard_name in batch:
                profile = {
                    "name": str(row.profile or ""),
                    "profile": str(row.profile or ""),
                    "variable_name": str(row.variable_name or ""),
                    "variable_type": self._profile_variable_type(row.variable_name),
                    "explanation": str(row.explanation or ""),
                    "statistical_basis": str(row.statistical_basis or ""),
                }
                profiles_by_id[int(row.id)] = profile
                hazard_items.append(
                    {"hazard": str(hazard_name or ""), "profiles": [profile]}
                )
            await self._match_system_profiles_to_target_populations(hazard_items)
            for profile_id, profile in profiles_by_id.items():
                option_ids = profile.get("target_population_option_ids")
                self._store_system_target_population_matches(profile_id, option_ids)
                if isinstance(option_ids, list) and option_ids:
                    matched_rows += 1
        return matched_rows

    @staticmethod
    def _deterministic_target_population_option_ids(
        profile: dict[str, object], option_rows: list[object]
    ) -> set[int]:
        identity_text = normalize_for_match(
            " ".join(
                str(profile.get(field) or "")
                for field in ("name", "profile", "variable_name")
            )
        )

        explanation_text = normalize_for_match(str(profile.get("explanation") or ""))
        statistical_text = normalize_for_match(str(profile.get("statistical_basis") or ""))
        profile_text = " ".join(
            value for value in (identity_text, explanation_text, statistical_text) if value
        )
        padded_profile = f" {profile_text} "
        padded_explanation = f" {explanation_text} "
        matched: set[int] = set()
        for row in option_rows:
            question = normalize_for_match(str(row.question))
            option = normalize_for_match(str(row.option))
            if not option or option in {"yes", "no", "other"}:
                continue
            if f" {option} " in padded_profile:
                matched.add(int(row.id))
                continue
            aliases = {
                ("gender", "woman"): ("women", "female"),
                ("gender", "male"): (" men ", " man "),
                ("age range", "65"): ("older", "older people", "older adults", "elderly", "senior"),
                ("level of income", "low income"): ("poor households", "income poor"),
                ("tenancy status", "tenant"): ("renters", "rented housing"),
                ("tenancy status", "homeowner"): ("homeowners", "owner occupier"),
            }.get((question, option), ())
            if any(
                f" {normalize_for_match(alias)} " in padded_profile
                for alias in aliases
            ):
                matched.add(int(row.id))

        age_match = re.search(r"\bage(?: group)?\s+(\d{1,2})\s*(\+|and over)?", profile_text)
        if age_match:
            minimum_age = int(age_match.group(1))
            if minimum_age >= 65:
                allowed_age_options = {"65"}
            elif minimum_age >= 35:
                allowed_age_options = {"35 65", "65"}
            elif minimum_age >= 25:
                allowed_age_options = {"25 35", "35 65", "65"}
            else:
                allowed_age_options = {"18", "25 35", "35 65", "65"}
            for row in option_rows:
                if normalize_for_match(str(row.question)) != "age range":
                    continue
                option = normalize_for_match(str(row.option))
                if option in allowed_age_options:
                    matched.add(int(row.id))

        yes_question_markers = {
            "living in a house with low energy efficiency": (
                "low energy efficiency", "energy inefficient", "ber rating e g", "cold home"
            ),
            "need of a car to perform daily activities": (
                "car dependent", "need a car", "car reliance"
            ),
            "care responsibility as the main activity": (
                "care responsibility", "carer", "caregiver"
            ),
            "eu citizenship": ("eu citizen", "eu citizenship"),
            "disability of long term condition": (
                "disability", "disabled", "long term condition"
            ),
        }
        for row in option_rows:
            if normalize_for_match(str(row.option)) != "yes":
                continue
            markers = yes_question_markers.get(normalize_for_match(str(row.question)), ())
            if any(f" {normalize_for_match(marker)} " in padded_profile for marker in markers):
                matched.add(int(row.id))

        explanation_markers = {
            ("living in a house with low energy efficiency", "yes"): (
                "inability to keep home warm",
                "cannot keep home warm",
                "cold home",
                "home quality problem",
                "damp draughts mould",
                "pre 1945 housing",
            ),
            ("level of income", "low income"): (
                "struggling to pay bills",
                "utility bill arrears",
                "utility arrears",
                "cannot afford",
                "unable to afford",
                "often need help support",
            ),
            ("level of income", "high income"): (
                "higher income respondents",
                "high income respondents",
            ),
            ("tenancy status", "homeowner"): (
                "owns outright",
                "owner occupier",
            ),
            ("tenancy status", "tenant"): (
                "rented home",
                "rented housing",
                "private renter",
                "social renter",
            ),
            ("level of education", "further normal education"): (
                "further education or training after school",
            ),
        }
        for row in option_rows:
            key = (
                normalize_for_match(str(row.question)),
                normalize_for_match(str(row.option)),
            )
            if (
                key == ("level of income", "low income")
                and any(
                    marker in f" {identity_text} "
                    for marker in (" higher income ", " high income ")
                )
            ):
                continue
            markers = explanation_markers.get(key, ())
            if any(
                f" {normalize_for_match(marker)} " in padded_explanation
                for marker in markers
            ):
                matched.add(int(row.id))
        return matched

    def _historical_evaluation_series(
        self, session: ChatSession, limit: int = 4
    ) -> list[dict[str, object]]:
        if self.user_id is None or not session.evaluation_answers or session.sector_id is None:
            return []
        question_ids = [
            int(answer["question_id"])
            for answer in session.evaluation_answers
            if answer.get("question_id") is not None
        ]
        if not question_ids:
            return []
        query = (
            select(
                UserMitigationMeasure.id,
                UserMitigationMeasure.measure,
                EvaluationQuestion.id.label("question_id"),
                UserQuestionResponse.score,
                UserMitigationMeasure.created_at,
            )
            .join(
                UserQuestionResponse,
                UserQuestionResponse.mitigation_measure_id == UserMitigationMeasure.id,
            )
            .join(
                EvaluationQuestion,
                EvaluationQuestion.id == UserQuestionResponse.question_id,
            )
            .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.user_id == self.user_id,
                UserSession.sector_id == session.sector_id,
                UserSession.region_id.is_(None)
                if session.region_id is None
                else UserSession.region_id == session.region_id,
                EvaluationQuestion.id.in_(question_ids),
                UserQuestionResponse.score.is_not(None),
            )
            .order_by(UserMitigationMeasure.id.desc(), UserQuestionResponse.id)
        )
        if session.mitigation_record_id is not None:
            query = query.where(UserMitigationMeasure.id != session.mitigation_record_id)
        rows = self.db.execute(query).all()
        grouped: dict[int, dict[str, object]] = {}
        for row in rows:
            mitigation_id = int(row.id)
            if mitigation_id not in grouped and len(grouped) >= limit:
                continue
            group = grouped.setdefault(
                mitigation_id,
                {
                    "id": mitigation_id,
                    "measure": str(row.measure or "Prior mitigation"),
                    "scores": {},
                },
            )
            scores = group["scores"]
            if isinstance(scores, dict):
                scores[int(row.question_id)] = int(row.score)

        series: list[dict[str, object]] = []
        for group in grouped.values():
            scores = group.get("scores")
            if not isinstance(scores, dict) or not scores:
                continue
            measure = normalize_markdown_text(str(group.get("measure") or "Prior mitigation"))
            series.append(
                {
                    "name": f"#{group['id']} — {measure[:64]}",
                    "values": [scores.get(question_id) for question_id in question_ids],
                    "current": False,
                }
            )
        return series

    def _persist_hazard_items_for_context(
        self,
        session_id: str,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        *,
        replace_generated_profiles: bool = False,
    ) -> None:
        for item in hazard_items:
            hazard = str(item.get("hazard") or "").strip()
            if not hazard:
                continue
            system_hazard = self._ensure_system_hazard(session, hazard)
            if system_hazard is None:
                continue
            self._delete_user_linked_system_generated_socio_demographics(session, hazard)
            if replace_generated_profiles:
                self._delete_generated_system_socio_demographics(system_hazard.id, session)
            for profile in item.get("profiles", []):
                if isinstance(profile, dict):
                    profile_data = profile
                else:
                    profile_data = {"name": str(profile or "").strip()}
                profile_name = str(
                    profile_data.get("name") or profile_data.get("profile") or ""
                ).strip()
                if not profile_name:
                    continue
                self._store_system_socio_demographic(
                    session,
                    system_hazard.id,
                    profile_data,
                )
        self._normalize_stored_sdp_variable_names(session)

    def _delete_sector_hazards_and_generated_dgs(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            generated_user_dgs = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                    UserHazardSocioDemographic.source == "llm",
                )
            ).all()
            for row in generated_user_dgs:
                self.db.delete(row)

            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            )
            self.db.execute(
                delete(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear old sector hazards and generated DGs")
            raise

    def _relink_user_system_hazards(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_hazards = {
                normalize(hazard.name): hazard.id
                for hazard in self.db.scalars(
                    select(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
                ).all()
            }
            user_hazards = self.db.scalars(
                select(UserHazard).where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                )
            ).all()
            for hazard in user_hazards:
                hazard.system_hazard_id = system_hazards.get(normalize(hazard.name))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to relink refreshed system hazards")

    def _normalize_stored_sdp_variable_names(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_rows = self.db.scalars(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            user_rows = self.db.scalars(
                select(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            changed = False
            for row in [*system_rows, *user_rows]:
                normalized = self._valid_sdp_variable_name(session, row.variable_name)
                if normalized and normalized != row.variable_name:
                    row.variable_name = normalized
                    changed = True
                if isinstance(row, SystemHazardSocioDemographic):
                    variable_type = self._profile_variable_type(row.variable_name)
                    if row.variable_type != variable_type:
                        row.variable_type = variable_type
                        changed = True
            if changed:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to normalize stored socio-demographic variable names")

    def _delete_generated_system_socio_demographics(
        self, system_hazard_id: int, session: ChatSession
    ) -> None:
        try:
            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear generated system socio-demographic profiles")

    def _delete_user_linked_system_generated_socio_demographics(
        self, session: ChatSession, hazard: str
    ) -> None:
        try:
            rows = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.name == hazard,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazardSocioDemographic.source == "llm",
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            ).all()
            for row in rows:
                self.db.delete(row)
            if rows:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear user-linked generated system socio-demographic rows")

    def _ensure_system_hazard(self, session: ChatSession, name: str) -> SystemHazard | None:
        if session.sector_id is None:
            return None
        try:
            hazard = self.db.scalar(
                select(SystemHazard).where(
                    SystemHazard.sector_id == session.sector_id,
                    SystemHazard.name == name,
                )
            )
            if hazard is None:
                hazard = SystemHazard(sector_id=session.sector_id, name=name)
                self.db.add(hazard)
                self.db.commit()
                self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system hazard")
            return None

    def _ensure_user_hazard(
        self,
        session_id: str,
        session: ChatSession,
        name: str,
        *,
        source: str = "custom",
        reason: str | None = None,
        evidence: str | None = None,
    ) -> UserHazard | None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return None
            system_hazard = None
            if source == "system":
                system_hazard = self._ensure_system_hazard(session, name)
            hazard = self.db.scalar(
                select(UserHazard).where(
                    UserHazard.user_session_id == user_session.id,
                    UserHazard.name == name,
                )
            )
            if hazard is None:
                hazard = UserHazard(
                    user_session_id=user_session.id,
                    system_hazard_id=system_hazard.id if system_hazard else None,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    name=name,
                    source=source,
                )
                self.db.add(hazard)
            hazard.source = source
            if system_hazard is not None:
                hazard.system_hazard_id = system_hazard.id
            hazard.sector_id = session.sector_id
            hazard.region_id = session.region_id
            if reason is not None:
                hazard.reason = reason
            if evidence is not None:
                hazard.evidence = evidence
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist hazard")
            return None

    def _store_socio_demographic(
        self,
        session: ChatSession,
        hazard_id: int | None,
        profile: str,
        *,
        source: str,
        variable_name: str | None = None,
        explanation: str | None = None,
        statistical_basis: str | None = None,
        metadata: dict[str, object] | None = None,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> None:
        if hazard_id is None or not profile.strip():
            return
        try:
            clean_profile = profile.strip()
            hazard_name = session.selected_hazard or session.accepted_custom_hazard
            context_query = (
                select(UserHazardSocioDemographic)
                .join(
                    UserHazard,
                    UserHazard.id == UserHazardSocioDemographic.user_hazard_id,
                )
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    func.lower(UserHazardSocioDemographic.profile) == clean_profile.casefold(),
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            if hazard_name:
                context_query = context_query.where(func.lower(UserHazard.name) == hazard_name.casefold())
            if self.user_id is not None:
                context_query = context_query.where(UserSession.user_id == self.user_id)

            row = self.db.scalar(context_query.limit(1))
            if row is not None and row.user_hazard_id != hazard_id:
                return
            if row is None:
                row = self.db.scalar(
                    select(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.user_hazard_id == hazard_id,
                    func.lower(UserHazardSocioDemographic.profile) == clean_profile.casefold(),
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                    )
                )
            if row is None:
                row = UserHazardSocioDemographic(
                    user_hazard_id=hazard_id,
                    profile=clean_profile,
                    source=source,
                )
                self.db.add(row)
            row.country_id = session.country_id
            row.region_id = session.region_id
            row.sector_id = session.sector_id
            row.source = source
            if variable_name is not None:
                row.variable_name = self._valid_sdp_variable_name(session, variable_name) or None
            if explanation is not None:
                row.explanation = explanation.strip() or None
            if statistical_basis is not None:
                row.statistical_basis = statistical_basis.strip() or None
            if metadata is not None:
                row.metadata_json = self._metadata_to_json(metadata)
            if reason is not None:
                row.reason = reason
            if evidence is not None:
                row.evidence = evidence
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist socio-demographic profile")

    def _store_system_socio_demographic(
        self,
        session: ChatSession,
        system_hazard_id: int,
        profile: dict[str, object],
    ) -> None:
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        explanation = str(profile.get("explanation") or "").strip()
        statistical_basis = str(
            profile.get("statistical_basis") or profile.get("basis") or ""
        ).strip()
        source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard_id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.country_id = None
            row.region_id = None
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = explanation or None
            row.statistical_basis = statistical_basis or None
            row.source = source
            self.db.commit()
            self.db.refresh(row)
            if "target_population_option_ids" in profile:
                self._store_system_target_population_matches(
                    row.id,
                    profile.get("target_population_option_ids"),
                )
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile")

    def _store_system_target_population_matches(
        self, system_profile_id: int, option_ids: object
    ) -> None:
        requested_ids: set[int] = set()
        if isinstance(option_ids, list):
            for option_id in option_ids:
                try:
                    requested_ids.add(int(option_id))
                except (TypeError, ValueError):
                    continue
        valid_ids = set(
            self.db.scalars(
                select(QuestionOption.id)
                .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
                .where(
                    QuestionOption.id.in_(requested_ids),
                    EvaluationQuestion.active.is_(True),
                    EvaluationQuestion.category == "target_population",
                )
            ).all()
        ) if requested_ids else set()
        existing = self.db.scalars(
            select(SystemHazardSocioDemographicTargetPopulation).where(
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id
                == system_profile_id
            )
        ).all()
        existing_by_option = {row.question_option_id: row for row in existing}
        for option_id, row in existing_by_option.items():
            if option_id not in valid_ids:
                self.db.delete(row)
        for option_id in valid_ids:
            if option_id not in existing_by_option:
                self.db.add(
                    SystemHazardSocioDemographicTargetPopulation(
                        system_hazard_socio_demographic_id=system_profile_id,
                        question_option_id=option_id,
                    )
                )
        self.db.commit()

    def _store_matched_profile_population_references(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
        matched_population_profiles: list[dict[str, object]],
    ) -> None:
        system_profile = self._ensure_system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return
        try:
            valid_cache_ids: set[int] = set()
            for matched_profile in matched_population_profiles:
                try:
                    cache_id = int(matched_profile.get("eurostat_population_cache_id") or 0)
                except (TypeError, ValueError):
                    continue
                cache_row = self.db.get(EurostatPopulationCache, cache_id)
                if (
                    cache_row is None
                    or cache_row.country_id != session.country_id
                    or cache_row.region_id != session.region_id
                    or cache_row.sector_id != session.sector_id
                    or cache_row.system_hazard_id != system_profile.system_hazard_id
                ):
                    continue
                valid_cache_ids.add(cache_id)
            if not valid_cache_ids:
                return
            existing_rows = self.db.scalars(
                select(SystemHazardSocioDemographicPopulationMatch).where(
                    SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                    == system_profile.id,
                )
            ).all()
            rows_by_cache_id = {
                row.eurostat_population_cache_id: row
                for row in existing_rows
                if row.eurostat_population_cache_id is not None
            }
            for row in existing_rows:
                if (
                    row.eurostat_population_cache_id is not None
                    and row.eurostat_population_cache_id not in valid_cache_ids
                ):
                    row.match_status = 0
            for cache_id in valid_cache_ids:
                row = rows_by_cache_id.get(cache_id)
                if row is None:
                    row = SystemHazardSocioDemographicPopulationMatch(
                        system_hazard_socio_demographic_id=system_profile.id,
                        eurostat_population_cache_id=cache_id,
                    )
                    self.db.add(row)
                row.match_status = 1
                row.attempt_count = 0
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist matched profile population reference")

    def _system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self.db.scalar(
            select(SystemHazard).where(
                SystemHazard.sector_id == session.sector_id,
                SystemHazard.name == hazard,
            )
        )
        if system_hazard is None:
            return None
        return self.db.scalar(
            select(SystemHazardSocioDemographic).where(
                SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
                func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
            )
        )

    def _ensure_system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self._ensure_system_hazard(session, hazard)
        if system_hazard is None:
            return None
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard.id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile row")
            return None

    @staticmethod
    def _metadata_to_json(metadata: dict[str, object]) -> str:
        try:
            return json.dumps(metadata, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _metadata_from_json(value: str | None) -> dict[str, object]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _store_mitigation_measure(
        self,
        hazard_id: int | None,
        mitigation_measure: str,
        reason: str,
        target_population: list[str] | None = None,
    ) -> int | None:
        if hazard_id is None:
            return None
        try:
            row = UserMitigationMeasure(
                user_hazard_id=hazard_id,
                measure=mitigation_measure,
                reason=reason,
                target_population=(
                    json.dumps(target_population, ensure_ascii=False)
                    if target_population
                    else None
                ),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return row.id
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation measure")
            return None

    def _update_mitigation_review_details(
        self,
        session: ChatSession,
        conclusion: str,
        target_groups: dict[str, object],
    ) -> None:
        if session.mitigation_record_id is None:
            return
        try:
            row = self.db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == session.mitigation_record_id
                )
            )
            if row is None:
                return
            row.conclusion = conclusion.strip() or None
            row.target_groups_json = self._metadata_to_json(target_groups)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation conclusion and target groups")

    def _mitigation_target_affected_groups_json(
        self,
        session: ChatSession,
    ) -> dict[str, object]:
        hazard = session.selected_hazard or session.accepted_custom_hazard or ""
        system_profiles = self._stored_hazard_profiles(session, hazard) if hazard else []
        user_profiles = self._stored_user_hazard_profiles(session, hazard) if hazard else []
        target_answers = self._target_population_answer_objects(session)
        target_profiles = [
            self._group_json_item(profile, "target_group")
            for profile in self._target_population_profiles_from_answers(
                session.target_population_answers or [],
                hazard or "the selected hazard",
            )
        ]
        affected_profiles = [
            self._group_json_item(profile, "affected_group")
            for profile in [*system_profiles, *user_profiles]
        ]
        return {
            "hazard": hazard,
            "target_population_answers": target_answers,
            "target_groups": self._dedupe_group_items(target_profiles),
            "affected_groups": self._dedupe_group_items(affected_profiles),
            "all_groups": self._dedupe_group_items([*target_profiles, *affected_profiles]),
        }

    @staticmethod
    def _group_json_item(profile: dict[str, object], group_type: str) -> dict[str, object]:
        name = str(profile.get("name") or profile.get("profile") or "").strip()
        variable_name = str(
            profile.get("variable_name") or profile.get("variable") or ""
        ).strip()
        return {
            "type": group_type,
            "name": name,
            "variable_name": variable_name,
            "explanation": str(profile.get("explanation") or "").strip(),
            "statistical_basis": str(profile.get("statistical_basis") or "").strip(),
            "source": str(profile.get("source") or "").strip(),
        }

    @staticmethod
    def _dedupe_group_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            name = str(item.get("name") or "").strip()
            variable_name = str(item.get("variable_name") or "").strip()
            source = str(item.get("source") or "").strip()
            key = (normalize(name), normalize(variable_name), normalize(source))
            if not name or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _target_population_answer_objects(session: ChatSession) -> list[dict[str, object]]:
        answers: list[dict[str, object]] = []
        for answer in session.target_population_answers or []:
            question = str(answer.get("question") or "").strip()
            stored_selected = answer.get("selected")
            selected = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [
                    item.strip()
                    for item in str(answer.get("answer") or "").split(",")
                    if item.strip()
                ]
            )
            if not question and not selected:
                continue
            answers.append(
                {
                    "question_id": answer.get("question_id"),
                    "question": question,
                    "selected": selected,
                }
            )
        return answers

    def _store_question_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        question_id: int | None,
        category: str | None,
        response_text: str | None = None,
        question_option_id: int | None = None,
        score: int | None = None,
        reason: str | None = None,
        evidence: str | None = None,
        hazard_id: int | None = None,
        mitigation_measure_id: int | None = None,
    ) -> None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            self.db.add(
                UserQuestionResponse(
                    user_session_id=user_session.id,
                    user_hazard_id=hazard_id or session.selected_hazard_record_id,
                    mitigation_measure_id=mitigation_measure_id or session.mitigation_record_id,
                    question_id=question_id,
                    question_option_id=question_option_id,
                    category=category,
                    response_text=response_text,
                    score=score,
                    reason=reason,
                    evidence=evidence,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist question response")

    def _match_hazard(self, message: str, session: ChatSession) -> str | None:
        normalized = normalize(message)
        hazards = hazard_names(session)
        for index, hazard in enumerate(hazards, start=1):
            if str(index) == message.strip() or normalize(hazard) == normalized:
                return hazard
        return None

    @staticmethod
    def _fuzzy_hazard(message: str, session: ChatSession) -> str | None:
        return best_fuzzy_label(message, hazard_names(session))

    @staticmethod
    def _scope_instruction(session: ChatSession) -> str:
        return (
            "Scope guard:\n"
            f"- Selected country: {session.country or 'Not selected'}\n"
            f"- Selected region: {session.region or 'Not selected'}\n"
            f"- Selected sector: {session.sector or 'Not selected'}\n"
            "- Keep every answer, validation, hazard, profile, mitigation, and example "
            "anchored to the selected country and selected sector.\n"
            "- Do not switch to another country, region, or sector. If the user asks "
            "about another context, say it is outside the current selection and relate "
            "the answer back to the selected country and sector.\n"
            "- If retrieved knowledge or examples mention other countries or sectors, "
            "use them only as clearly labelled general background. Do not present them "
            "as evidence for the selected country or sector unless the text explicitly "
            "matches the current selection."
        )

    @staticmethod
    def _twin_transition_hazard_scope_instruction() -> str:
        return (
            "Twin-transition hazard scope guard:\n"
            "- A user-added hazard must be a negative social, economic, access, "
            "affordability, health, safety, or distributional impact connected to "
            "green-transition or digital-transition policies, regulations, incentives, "
            "infrastructure changes, technology adoption, pricing, restrictions, or "
            "market shifts.\n"
            "- Accept hazards that describe unintended consequences of twin-transition "
            "policies, even when locally specific or not already listed.\n"
            "- Reject hazards that are general problems with no clear policy mechanism "
            "from the twin transition, such as generic poverty, crime, illness, weather, "
            "unemployment, or infrastructure failure unless the user links them to a "
            "green/digital transition policy impact in the selected sector.\n"
            "- When rejecting, explain that the hazard must be rewritten as a "
            "twin-transition policy impact, with the mechanism or affected outcome."
        )

    async def _build_deep_dive_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"{session.selected_hazard or ''} {format_all_dgs(session)} {user_message}",
            limit=8,
        )
        context = f"""
Use the retrieved sector-prompt RAG excerpts below as your authoritative statistical context.
Do not invent precise live statistics. If a number would be needed but is not present,
explain what data source the user should check.

{self._scope_instruction(session)}

Sector-prompt RAG excerpts:
{sector_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Session context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n"
                    f"User question:\n{user_message}\n\n"
                    "Answer in Markdown. Be practical and structured. Do not include "
                    "a heading or bullet named 'Policy Implications'."
                ),
            }
        ]
        return context, messages

    async def _build_stats_deep_dive_messages(
        self,
        session: ChatSession,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        context, messages = await self._build_deep_dive_messages(session, user_message)
        history = list((session.stats_conversation or []) if history is None else history)
        if not history:
            return context, messages

        current_message = messages[-1]
        messages = [
            {
                "role": "user",
                "content": (
                    "Session context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n\n"
                    "Continue the statistical findings conversation below. "
                    "Use only the loaded sector statistical context. Stay within "
                    "the selected country and sector."
                ),
            },
            *history[-10:],
            current_message,
        ]
        return context, messages

    async def _get_hazards_from_llm(self, session: ChatSession) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_and_profiles_from_sector_rag(session)
        if hazard_items:
            return hazard_items

        hazards = await self._get_hazard_names_from_llm(session)
        profile_lists = await asyncio.gather(
            *(self._get_hazard_profiles_from_llm(session, hazard) for hazard in hazards)
        )
        return [
            {"hazard": hazard, "profiles": profiles}
            for hazard, profiles in zip(hazards, profile_lists, strict=False)
        ]

    async def _get_hazard_names_from_llm(self, session: ChatSession) -> list[str]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            "HAZARD confirmed predictors ranked concern",
            limit=25,
        )
        context = f"""
You are a strict extraction assistant for Dr Transition.

Hazard names are sector-level system hazards. Extract them from the selected
sector only; do not make them country-specific or region-specific.

Use only these retrieved sector-prompt RAG excerpts as the source:
{sector_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Selected context:\n"
                    f"- Sector: {session.sector}\n\n"
                    "List all the hazards perceived for the selected sector. Return ONLY "
                    "valid JSON, an array of hazard names like:\n"
                    '["hazard name", "another hazard name"]\n\n'
                    "Rules:\n"
                    "- Use only hazards named in the sector prompt.\n"
                    "- Each item must be only the hazard name.\n"
                    "- Do not include profiles, explanations, Markdown, or code fences.\n"
                    '- If the sector analysis is unavailable, return ["Analysis not available"].'
                ),
            }
        ]

        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return parse_llm_hazard_list(response)

    async def _get_hazard_profiles_from_llm(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        hazard_block = await self._sector_prompt_hazard_block_from_rag(
            session,
            hazard,
        )
        if hazard_block:
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                return []
            profiles = self._profiles_from_hazard_block(hazard_block)
            if profiles:
                return profiles

        context = f"""
You are a strict socio-demographic profile extraction assistant for Dr Transition.

{self._scope_instruction(session)}

Use only these retrieved sector-prompt RAG excerpts as the source:
{hazard_block or "- No relevant sector-prompt RAG excerpts were found."}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Selected context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n"
                    f"- Hazard: {hazard}\n\n"
                    "Identify the exact socio-demographic profiles for this hazard. "
                    "If the prompt says "
                    "affected profiles are exactly the confirmed predictors for the hazard, "
                    "include every confirmed predictor, including LOWER concern or protective "
                    "predictors. For each profile, include only a short explanation. "
                    "Return ONLY valid JSON, an array of objects like:\n"
                    '[{"variable_name": "predictor variable or ID", "profile": "affected profile", '
                    '"name": "affected profile", "explanation": "short explanation", '
                    '"statistical_basis": "brief statistical basis", "source": "sector_prompt"}]\n\n'
                    "Rules:\n"
                    "- Use only profiles supported for this specific hazard in the prompt.\n"
                    "- Profile names must be human-readable people, household, home, or "
                    "country-context groups derived from confirmed predictors; do not return "
                    "raw predictor variable names.\n"
                    "- Store only the raw predictor variable name in variable_name; do not include predictor IDs like 1A or 2B.\n"
                    "- Include a concise statistical_basis grounded in the sector prompt.\n"
                    "- Include protective/lower-concern confirmed predictors too, labelled "
                    "plainly in the explanation as lower concern or protective.\n"
                    "- Keep profile names concise and explanations under 24 words.\n"
                    "- Explanations should be plain-language, hazard-specific, and grounded "
                    "in the prompt context.\n"
                    "- Do not include statistical basis, model metrics, caveats, Markdown, or code fences.\n"
                    "- If no profiles are clearly supported for this hazard, return []."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return self._parse_hazard_profile_items(response)

    async def _get_hazards_and_profiles_from_sector_rag(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        rag_service = SectorPromptRagService(self.db)
        try:
            results = await rag_service.hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block extraction lookup failed")
            results = []
        if not results:
            try:
                results = await rag_service.search(
                    session.sector,
                    "HAZARD confirmed predictors PREDICTOR Plain-English Direction",
                    limit=40,
                )
            except Exception:
                logger.exception("Sector-prompt RAG hazard/profile extraction lookup failed")
                results = []
        sector_context = SectorPromptRagService.format_results(results, content_limit=6000)
        if not sector_context:
            return []

        context = f"""
You extract structured hazard and socio-demographic profile data for Dr Transition.

{self._scope_instruction(session)}

Use ONLY these retrieved sector-prompt RAG excerpts. Do not use general knowledge.

Sector-prompt RAG excerpts:
{sector_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Return ONLY valid JSON: an array of objects with this shape:\n"
                    "[{\"hazard\": \"hazard name\", \"profiles\": ["
                    "{\"variable_name\": \"raw predictor variable only\", "
                    "\"profile\": \"human-readable profile\", "
                    "\"name\": \"human-readable profile\", "
                    "\"explanation\": \"short plain-language explanation\", "
                    "\"statistical_basis\": \"brief source-grounded basis\", "
                    "\"source\": \"sector_prompt\"}]}]\n\n"
                    "Rules:\n"
                    "- Include every HAZARD found in the RAG excerpts.\n"
                    "- For hazards with zero confirmed predictors, use an empty profiles array.\n"
                    "- Create one profile object for each confirmed PREDICTOR entry.\n"
                    "- Store only the raw predictor variable name in variable_name; never include IDs like 1A or 2B.\n"
                    "- Keep profile names concise and human-readable.\n"
                    "- Include lower-concern/protective predictors too and label them in the explanation.\n"
                    "- Do not include Markdown, comments, or text outside the JSON."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=9000,
        )
        hazard_items = self._parse_hazard_items_response(response)
        expected_hazards = self._hazard_names_from_sector_prompt(
            "\n\n".join(str(result.get("content") or "") for result in results)
        )
        if expected_hazards:
            hazard_items = await self._complete_sector_rag_hazard_items(
                session,
                hazard_items,
                expected_hazards,
                results,
            )
        return hazard_items

    async def _complete_sector_rag_hazard_items(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        expected_hazards: list[str],
        rag_results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        items_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        rag_text = "\n\n".join(str(result.get("content") or "") for result in rag_results)
        completed: list[dict[str, object]] = []
        for hazard in expected_hazards:
            item = items_by_hazard.get(normalize(hazard))
            hazard_block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            deterministic_profiles = self._profiles_from_hazard_block(hazard_block)
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                profiles = []
            elif predictor_entries:
                profiles = deterministic_profiles
            else:
                profiles = list(item.get("profiles", [])) if item else []
            completed.append({"hazard": hazard, "profiles": profiles})
        return completed

    async def _sector_prompt_rag_hazard_count(self, sector: str | None) -> int:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(sector)
            return sum(
                1
                for result in results
                if self._profiles_from_hazard_block(str(result.get("content") or ""))
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-count lookup failed")
            return 0

    async def _sector_prompt_profiles_match_rag(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
    ) -> bool:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG profile-integrity lookup failed")
            return True
        rag_text = "\n\n".join(str(result.get("content") or "") for result in results)
        stored_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        for hazard in self._hazard_names_from_sector_prompt(rag_text):
            block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            expected = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in self._profiles_from_hazard_block(block)
            )
            if not expected:
                continue
            item = stored_by_hazard.get(normalize(hazard))
            actual = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in (item.get("profiles", []) if item else [])
                if isinstance(profile, dict)
            )
            if actual != expected:
                return False
        expected_hazards = {
            normalize(hazard)
            for hazard in self._hazard_names_from_sector_prompt(rag_text)
            if self._profiles_from_hazard_block(
                self._confirmed_predictor_hazard_block(rag_text, hazard)
            )
        }
        return set(stored_by_hazard) == expected_hazards

    def _parse_hazard_items_response(self, response: str) -> list[dict[str, object]]:
        parsed = self._json_array_from_response(response)
        if not isinstance(parsed, list):
            return []
        hazard_items: list[dict[str, object]] = []
        seen_hazards: set[str] = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            hazard = self._clean_sector_hazard_name(
                str(item.get("hazard") or item.get("name") or "")
            )
            if not hazard:
                continue
            hazard_key = normalize(hazard)
            if hazard_key in seen_hazards:
                continue
            seen_hazards.add(hazard_key)
            raw_profiles = item.get("profiles")
            profiles: list[dict[str, str]] = []
            if isinstance(raw_profiles, list):
                profiles = [
                    profile
                    for profile in (
                        self._clean_hazard_profile_item(profile)
                        for profile in raw_profiles
                    )
                    if profile
                ]
            hazard_items.append({"hazard": hazard[:180], "profiles": profiles})
        return hazard_items

    @staticmethod
    def _json_array_from_response(response: str) -> object:
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass
        start = response.find("[")
        end = response.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(response[start : end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _clean_hazard_profile_item(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        name = strip_rule_lines(str(value.get("name") or value.get("profile") or "")).strip()
        if not name:
            return {}
        variable_name = str(
            value.get("variable_name") or value.get("variable") or ""
        ).strip()
        prefixed_match = re.match(
            r"^(?:PREDICTOR\s+)?[0-9]+[A-Z]\s*:\s*(.+)$",
            variable_name,
            flags=re.IGNORECASE,
        )
        if prefixed_match:
            variable_name = prefixed_match.group(1).strip()
        variable_token = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", variable_name)
        if variable_token:
            variable_name = variable_token.group(1)
        return {
            "name": name[:120],
            "profile": name[:120],
            "variable_name": variable_name[:160],
            "explanation": strip_rule_lines(str(value.get("explanation") or "")).strip()[:260],
            "statistical_basis": str(
                value.get("statistical_basis") or value.get("basis") or ""
            ).strip()[:600],
            "source": str(value.get("source") or "sector_prompt").strip()[:40] or "sector_prompt",
        }

    async def _sector_prompt_hazard_block_from_rag(
        self,
        session: ChatSession,
        hazard: str,
    ) -> str:
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                f'HAZARD {hazard} confirmed predictors PREDICTOR',
                limit=3,
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block lookup failed")
            return ""
        combined = "\n\n".join(
            str(result.get("content") or "").strip()
            for result in results
            if str(result.get("content") or "").strip()
        )
        if not combined:
            return ""
        return self._confirmed_predictor_hazard_block(combined, hazard)

    @staticmethod
    def _hazard_names_from_sector_prompt(sector_prompt: str) -> list[str]:
        prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
        hazards: list[str] = []
        for match in re.finditer(r"(?m)^HAZARD\s+\d+\.\s+(.+?)\s*$", prompt):
            hazard = ChatService._clean_sector_hazard_name(match.group(1))
            if hazard and normalize_for_match(hazard) not in {
                normalize_for_match(existing) for existing in hazards
            }:
                hazards.append(hazard)
        return hazards

    @staticmethod
    def _clean_sector_hazard_name(value: str) -> str:
        hazard = re.sub(r"\s+", " ", str(value or "")).strip()
        hazard = re.sub(r"(?i)^HAZARD\s+\d+\.\s*", "", hazard).strip()
        hazard = strip_rule_lines(hazard).strip()
        if re.fullmatch(r"[─═\-_=]{6,}", hazard):
            return ""
        return hazard

    @staticmethod
    def _profiles_from_hazard_block(hazard_block: str) -> list[dict[str, str]]:
        profiles: list[dict[str, str]] = []
        for entry in ChatService._confirmed_predictor_entries(hazard_block):
            profile = ChatService._profile_from_predictor_entry(entry)
            if profile:
                profiles.append(profile)
        return profiles

    @staticmethod
    def _profile_from_predictor_entry(entry: str) -> dict[str, str]:
        header = re.search(
            r"(?m)^PREDICTOR\s+([0-9]+[A-Z]):\s+(.+?)\s*$",
            entry,
        )
        if not header:
            return {}
        predictor_id = header.group(1).strip()
        variable_text = header.group(2).strip()
        variable_name = variable_text.split("(", 1)[0].strip()
        level_match = re.search(r'\blevel:\s*"([^"]+)"', variable_text, flags=re.IGNORECASE)
        variable_label = ChatService._humanize_predictor_label(variable_name)
        if level_match:
            profile_name = f"{variable_label}: {level_match.group(1).strip()}"
        elif "country-level" in variable_text.casefold():
            profile_name = f"Countries with higher {variable_label}"
        elif "continuous" in variable_text.casefold():
            profile_name = f"Higher {variable_label}"
        else:
            profile_name = variable_label

        direction_match = re.search(r"(?m)^\s*Direction\s*=\s*(.+?)\s*$", entry)
        direction = direction_match.group(1).strip() if direction_match else ""
        plain_match = re.search(
            r"(?ms)^\s*Plain-English:\s*(.+?)(?=^\s*(?:Odds ratio|p-value|Direction|COUNTRY PATTERN|PREDICTORS NOT CONFIRMED)|\Z)",
            entry,
        )
        plain = re.sub(r"\s+", " ", plain_match.group(1)).strip() if plain_match else ""
        explanation = plain or f"{profile_name} is listed as a confirmed predictor for this hazard."
        if direction and "lower" in direction.casefold() and "lower" not in explanation.casefold():
            explanation = f"Lower concern/protective predictor: {explanation}"
        elif direction and "protective" in direction.casefold() and "protective" not in explanation.casefold():
            explanation = f"Protective predictor: {explanation}"

        basis_parts = [f"PREDICTOR {predictor_id}: {variable_text}"]
        if direction:
            basis_parts.append(f"Direction: {direction}")
        if plain:
            basis_parts.append(f"Plain-English: {plain}")
        return {
            "variable_name": variable_name[:160],
            "profile": profile_name[:120],
            "name": profile_name[:120],
            "explanation": explanation[:260],
            "statistical_basis": "; ".join(basis_parts)[:600],
            "source": "sector_prompt",
        }

    @staticmethod
    def _humanize_predictor_label(value: str) -> str:
        label = re.sub(r"[_\-]+", " ", value).strip()
        label = re.sub(r"\s+", " ", label)
        if label.casefold().startswith("macro "):
            label = label[6:]
        return label[:1].upper() + label[1:] if label else "Confirmed predictor"

    async def _get_hazard_block_profiles_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        hazard_block: str,
        expected_count: int | None = None,
        predictor_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        predictor_id_list = predictor_ids or []
        count_rule = (
            f"- Return exactly {expected_count} profile objects: one for each confirmed PREDICTOR entry in the block.\n"
            if expected_count
            else "- Return one profile object for each confirmed PREDICTOR entry in the block.\n"
        )
        id_rule = (
            "- Use this predictor checklist and return one object for each ID in this order: "
            + ", ".join(predictor_id_list)
            + ".\n"
            if predictor_id_list
            else ""
        )
        context = f"""
You convert confirmed predictor evidence into readable socio-demographic profiles for Dr Transition.

{self._scope_instruction(session)}

Use only the hazard-specific confirmed-predictor block below. Do not use other hazards.

Confirmed-predictor block:
{hazard_block}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Selected context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n"
                    f"- Hazard: {hazard}\n\n"
                    "Return the socio-demographic profiles for this hazard as ONLY valid JSON:\n"
                    '[{"variable_name": "predictor ID or variable", "profile": "human-readable profile", '
                    '"name": "human-readable profile", "explanation": "short explanation", '
                    '"statistical_basis": "confirmed-predictor evidence summary", "source": "sector_prompt"}]\n\n'
                    "Rules:\n"
                    + count_rule
                    + id_rule
                    + "- Do not merge predictors into fewer profiles, even when predictors are related.\n"
                    "- If the block says 0 confirmed predictors, return [].\n"
                    "- Convert variable names into human-readable profile names.\n"
                    "- Store only the raw predictor variable name in variable_name; do not include predictor IDs like 1A or 2B.\n"
                    "- Include a concise statistical_basis grounded in the predictor entry.\n"
                    "- Include LOWER concern or protective predictors too, and say lower concern/protective in the explanation.\n"
                    "- Explanation should be plain language only, under 24 words.\n"
                    "- Do not include odds ratios, p-values, statistical basis, model metrics, Markdown, or code fences."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=900,
        )
        profiles = self._parse_hazard_profile_items(response)
        if expected_count and len(profiles) != expected_count:
            retry_response = await ask_llm_chat(
                context=context,
                messages=[
                    *messages,
                    {
                        "role": "assistant",
                        "content": response,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"The response returned {len(profiles)} profiles, but this hazard has "
                            f"{expected_count} confirmed predictors. Return ONLY valid JSON with "
                            f"exactly {expected_count} objects, one object for each PREDICTOR entry. "
                            + (
                                "Use this checklist in order: "
                                + ", ".join(predictor_id_list)
                                + ". "
                                if predictor_id_list
                                else ""
                            )
                            + "Do not merge, skip, or add predictors."
                        ),
                    },
                ],
                temperature=0,
                max_tokens=900,
            )
            retry_profiles = self._parse_hazard_profile_items(retry_response)
            if len(retry_profiles) == expected_count:
                return retry_profiles
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            if len(predictor_entries) == expected_count:
                single_profiles = await asyncio.gather(
                    *(
                        self._get_single_predictor_profile_from_llm(session, hazard, entry)
                        for entry in predictor_entries
                    )
                )
                fallback_profiles = [
                    profile
                    for profile in single_profiles
                    if profile and profile.get("name", "").strip()
                ]
                if len(fallback_profiles) == expected_count:
                    return fallback_profiles
        return profiles

    async def _get_single_predictor_profile_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        predictor_entry: str,
    ) -> dict[str, str]:
        context = f"""
You convert one confirmed predictor into one readable socio-demographic profile for Dr Transition.

{self._scope_instruction(session)}

Use only this predictor entry:
{predictor_entry}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Selected context:\n"
                    f"- Country: {session.country}\n"
                    f"- Region: {session.region}\n"
                    f"- Sector: {session.sector}\n"
                    f"- Hazard: {hazard}\n\n"
                    "Return ONLY valid JSON with one object:\n"
                    '{"variable_name": "predictor ID or variable", "profile": "human-readable profile", '
                    '"name": "human-readable profile", "explanation": "short explanation", '
                    '"statistical_basis": "confirmed-predictor evidence summary", "source": "sector_prompt"}\n\n'
                    "Rules:\n"
                    "- Convert the predictor variable and level into a concise human-readable profile name.\n"
                    "- Store only the raw predictor variable name in variable_name; do not include predictor IDs like 1A or 2B.\n"
                    "- Include a concise statistical_basis grounded in the predictor entry.\n"
                    "- The explanation must be plain language only, under 24 words.\n"
                    "- If the predictor is LOWER concern or protective, say lower concern/protective in the explanation.\n"
                    "- Do not include odds ratios, p-values, statistical basis, model metrics, Markdown, or code fences."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=300,
        )
        if is_llm_unavailable_response(response):
            return {}
        try:
            parsed = json.loads(response.strip())
        except json.JSONDecodeError:
            try:
                parsed = json.loads(self._extract_json_object(response))
            except json.JSONDecodeError:
                parsed = None
        if not isinstance(parsed, dict):
            return {}
        name = str(parsed.get("name") or parsed.get("profile") or "").strip().strip("`*_ ")
        explanation = str(
            parsed.get("explanation")
            or parsed.get("reason")
            or parsed.get("description")
            or ""
        ).strip().strip("`*_ ")
        variable_name = str(
            parsed.get("variable_name")
            or parsed.get("variable")
            or parsed.get("predictor")
            or ""
        ).strip().strip("`*_ ")
        statistical_basis = str(
            parsed.get("statistical_basis")
            or parsed.get("basis")
            or parsed.get("statistical_evidence")
            or ""
        ).strip().strip("`*_ ")
        source = str(parsed.get("source") or "sector_prompt").strip().strip("`*_ ")
        if not name:
            return {}
        return {
            "name": name[:120],
            "profile": name[:120],
            "explanation": explanation[:260],
            "variable_name": variable_name[:160],
            "statistical_basis": statistical_basis[:600],
            "source": source[:40] if source else "sector_prompt",
            "metadata": parsed,
        }

    @staticmethod
    def _extract_json_object(value: str) -> str:
        start = value.find("{")
        end = value.rfind("}")
        if start != -1 and end != -1 and end > start:
            return value[start : end + 1]
        return "{}"

    @staticmethod
    def _confirmed_predictor_count(hazard_block: str) -> int | None:
        match = re.search(r"\b(\d+)\s+confirmed predictors?\b", hazard_block, re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _confirmed_predictor_ids(hazard_block: str) -> list[str]:
        return re.findall(r"^PREDICTOR\s+([0-9]+[A-Z]):", hazard_block, re.MULTILINE)

    @staticmethod
    def _confirmed_predictor_entries(hazard_block: str) -> list[str]:
        entry_pattern = re.compile(
            r"(?ms)^PREDICTOR\s+[0-9]+[A-Z]:.*?(?=^PREDICTOR\s+[0-9]+[A-Z]:|^COUNTRY PATTERN|^PREDICTORS NOT CONFIRMED|\Z)"
        )
        return [match.group(0).strip() for match in entry_pattern.finditer(hazard_block)]

    def _expected_hazard_profile_count(self, session: ChatSession, hazard: str) -> int | None:
        _ = session, hazard
        return None

    async def _validate_hazard_against_stats(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        existing_hazards = "\n".join(f"- {item}" for item in (session.hazards or []))
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"{hazard} {reason} {evidence} {existing_hazards}",
        )
        context = f"""
You are a practical validation assistant for Dr Transition.

{self._scope_instruction(session)}

{self._twin_transition_hazard_scope_instruction()}

Use these retrieved sector-prompt excerpts as the authoritative statistical source:
{sector_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate whether the proposed new regional hazard is a reasonable "
                    "negative impact or risk of twin-transition policies for the selected "
                    "country and sector, and does not contradict the sector statistics, "
                    "survey findings, or prompt context.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    "Existing hazards:\n"
                    f"{existing_hazards or '- No existing hazards were generated.'}\n\n"
                    f"Proposed hazard: {hazard}\n"
                    f"Reason: {reason}\n"
                    f"Evidence: {evidence or 'Not provided'}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "short validation explanation"}\n\n'
                    "Example valid response:\n"
                    '{"valid": true, "reason": "The reason aligns with the survey context."}\n\n'
                    "Example invalid response:\n"
                    '{"valid": false, "reason": "The reason contradicts the sector context or is too vague to evaluate."}\n\n'
                    "Rules:\n"
                    "- valid should be true only when the hazard and reason are meaningful, "
                    "country/sector-relevant, compatible with the loaded context, and clearly "
                    "connected to twin-transition policy impacts, even if the exact regional "
                    "hazard is not explicitly named in the statistics.\n"
                    "- User-added regional hazards may extend the system hazard list; "
                    "do not reject solely because the hazard is new or locally specific.\n"
                    "- If evidence content is supplied from a URL or file, use it as "
                    "additional support, but do not require optional evidence when the "
                    "reason itself is clear and plausible.\n"
                    "- valid must be false only when the reason or supplied evidence "
                    "clearly contradicts the statistics, confuses predictors with hazards, "
                    "invents unsupported numbers as facts, is unrelated to the sector, "
                    "is unrelated to the selected country, "
                    "is unrelated to twin-transition policies, "
                    "or is too vague/generic to evaluate.\n"
                    "- The reason field must be useful to the user and under 60 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        return parse_validation_response(response)

    async def _semantic_hazard_duplicate_check(
        self, session: ChatSession, hazard: str
    ) -> dict[str, object] | None:
        existing_hazards = hazard_names(session)
        if not existing_hazards:
            return {"duplicate": False, "match": "", "reason": "", "duplicates": []}

        context = f"""
You are a strict semantic duplicate checker for Dr Transition.

Your job is to decide whether a proposed hazard is already covered by an existing
hazard, even when the wording, grammar, or language differs.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Compare the proposed hazard with the existing hazard list.\n\n"
                    "Treat it as duplicate when it has the same meaning, is a close "
                    "paraphrase, uses different wording for the same policy risk, or is "
                    "written in another language but means the same thing.\n\n"
                    "Do not mark it duplicate merely because it is in the same broad "
                    "topic area; the hazard mechanism or affected outcome must be "
                    "substantially the same.\n\n"
                    "Existing hazards:\n"
                    + "\n".join(f"- {item}" for item in existing_hazards)
                    + "\n\n"
                    f"Proposed hazard: {hazard}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"duplicate": false, "match": "", "reason": "short explanation"}\n\n'
                    "If duplicate is true, match must be the closest existing hazard. "
                    "Keep reason under 40 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=300,
        )

        if is_llm_unavailable_response(response):
            return None

        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        match_record = self._mitigation_record_for_match(session, str(parsed.get("match") or ""))
        if match_record is not None:
            parsed["match_id"] = match_record.id
            parsed["match"] = match_record.measure
        return parsed

    def _mitigation_record_for_match(
        self, session: ChatSession, match: str
    ) -> UserMitigationMeasure | None:
        records = self._existing_mitigation_records_for_selected_hazard(session)
        if not records:
            return None
        normalized_match = normalize(match)
        for record in records:
            if normalize(record.measure) == normalized_match:
                return record
        best_record = None
        best_score = 0.0
        for record in records:
            score = fuzzy_score(match, record.measure)
            if score > best_score:
                best_record = record
                best_score = score
        return best_record if best_score >= 0.45 else None

    async def _review_custom_hazard_input(
        self, session: ChatSession, hazard: str
    ) -> dict[str, object] | None:
        existing_hazards = hazard_names(session)
        local_matches = self._local_similar_hazards(hazard, existing_hazards)
        if local_matches:
            return {
                "status": "Invalid",
                "valid": False,
                "reason": "This appears to have the same or similar meaning as an existing hazard.",
                "suggestions": local_matches,
            }
        context = f"""
You are a practical hazard intake reviewer for Dr Transition.

Your job is to classify user text before it can be used as a new social hazard,
and then decide whether it is already clearly covered by existing sector or
user-added hazards.

{self._scope_instruction(session)}

{self._twin_transition_hazard_scope_instruction()}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Review the proposed hazard before it is accepted.\n\n"
                    "Classify the text as one of: Valid, Ambiguous, Invalid.\n\n"
                    "Validation checks:\n"
                    "- Check for generic or ambiguous context.\n"
                    "- It should appear to be a recognizable word or phrase.\n"
                    "- It must describe a negative impact, risk, burden, exclusion, "
                    "or distributional harm caused or intensified by twin-transition "
                    "policies in the selected sector.\n"
                    "- Valid and meaningful text should pass only when it stays inside "
                    "the twin-transition policy scope.\n"
                    "- Random characters, gibberish, keyboard mashing, or unrecognizable text should fail.\n"
                    "- Text that is too short to determine intent should be Ambiguous.\n\n"
                    "Classification rules:\n"
                    "- Invalid: random characters, keyboard mashing, gibberish, or no clear meaning.\n"
                    "- Ambiguous: too short, incomplete, only a very broad topic label, "
                    "or not enough context to understand the intended twin-transition "
                    "policy hazard.\n"
                    "- Valid: a clear question, request, statement, recognizable phrase, "
                    "or meaningful hazard-like phrase that is within twin-transition "
                    "policy scope. It does not need perfect wording.\n\n"
                    "Be permissive for meaningful regional hazards. Accept concise phrases "
                    "when the risk or negative outcome is understandable, even if the "
                    "affected group or place is not fully specified yet, but reject hazards "
                    "that are general social problems, natural disasters, health issues, "
                    "or market risks with no clear twin-transition policy mechanism.\n\n"
                    "if the topic is too broad or generic, ask for a rewrite with more specific mechanism or outcome. For example, if the user writes 'transport', you might say 'Please rewrite this with the affected outcome or mechanism, such as 'increased road traffic deaths' or 'disrupted public transit access'.\n\n"
                    "Compare against existing hazards for semantic similarity. Put any "
                    "existing hazards with the same meaning, a similar meaning, a close "
                    "paraphrase, a narrower/broader version, or a substantially overlapping "
                    "risk in suggestions. If any such match exists, set status to Invalid "
                    "and valid to false. Do not accept a hazard that is similar to an "
                    "existing one.\n\n"
                    "Existing system and user regional hazards:\n"
                    + ("\n".join(f"- {item}" for item in existing_hazards) or "- None")
                    + "\n\n"
                    f"Proposed hazard: {hazard}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"status": "Valid", "valid": true, "reason": "This is a meaningful hazard-like phrase.", "suggestions": []}\n\n'
                    "Alternative examples:\n"
                    '{"status": "Ambiguous", "valid": false, "reason": "Please rewrite this with the affected outcome or mechanism.", "suggestions": []}\n'
                    '{"status": "Invalid", "valid": false, "reason": "The text appears to be gibberish or has no clear meaning.", "suggestions": []}\n\n'
                    "Output rules:\n"
                    "- status must be exactly one of: Valid, Ambiguous, Invalid.\n"
                    "- valid must be true only when status is Valid and no existing hazard has the same or similar meaning.\n"
                    "- valid must be false when the proposed hazard is outside twin-transition policy scope.\n"
                    "- suggestions must contain exact existing hazard names with the same meaning, similar meaning, close paraphrase, broader/narrower overlap, or substantially overlapping risk.\n"
                    "- If suggestions is not empty, status must be Invalid and valid must be false.\n"
                    "- If no existing hazards are relevant, suggestions must be an empty array.\n"
                    "- If status is Ambiguous, ask one reflective rewrite question in the reason field.\n"
                    "- Keep the reason field under 50 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=450,
        )
        if is_llm_unavailable_response(response):
            return None

        parsed = parse_hazard_input_review_response(response)
        if parsed.get("error"):
            return None
        existing_by_key = {normalize(item): item for item in existing_hazards}
        suggestions = [
            existing_by_key[normalize(item)]
            for item in parsed.get("suggestions", [])
            if isinstance(item, str) and normalize(item) in existing_by_key
        ]
        parsed["suggestions"] = list(dict.fromkeys(suggestions))
        if parsed["suggestions"]:
            parsed["status"] = "Invalid"
            parsed["valid"] = False
        return parsed

    async def _validate_input_quality(
        self,
        session: ChatSession,
        purpose: str,
        fields: dict[str, str],
    ) -> dict[str, str | bool] | None:
        cleaned_fields = {
            label: value.strip()
            for label, value in fields.items()
            if isinstance(value, str) and value.strip()
        }
        field_text = "\n".join(f"{label}: {value}" for label, value in cleaned_fields.items())
        context = f"""
You are a practical input-quality validator for Dr Transition.

Your job is to validate user-entered policy workflow text before it is saved or
used for statistical validation.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Review the fields below.\n\n"
                    f"Purpose: {purpose}\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or session.pending_hazard or 'Not provided'}\n\n"
                    f"Fields:\n{field_text or '- No text provided'}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "The text is meaningful and specific enough."}\n\n'
                    "Validation checks:\n"
                    "- Each required field should appear to be recognizable words or a meaningful phrase.\n"
                    "- The text must be valid and meaningful for the stated purpose.\n"
                    "- This is not a grammar, spelling, punctuation, or style check.\n"
                    "- Check for generic, ambiguous, incomplete, or unsupported context.\n"
                    "- Check evidence URL/file content when provided; extracted content that says it could not be read is not valid evidence.\n"
                    "- Random characters, keyboard mashing, gibberish, or unrecognizable text is invalid.\n"
                    "- Text that is too short to determine intent is ambiguous and must be invalid for this workflow.\n\n"
                    "Rules:\n"
                    "- Do not mark text invalid only because it has grammar, spelling, punctuation, capitalization, or style errors.\n"
                    "- If a question, request, or statement is understandable, mark it valid even when the wording is imperfect.\n"
                    "- valid must be false if any field is random, gibberish, keyboard mashing, too short, ambiguous, incomplete, or unrelated to the purpose.\n"
                    "- valid must be false if the reason is only a broad label, such as 'poverty', 'transport', or 'policy', without a clear mechanism or outcome.\n"
                    "- valid must be false if provided evidence is only a filename/URL with no readable evidence content, or extracted content says it could not be read.\n"
                    "- valid may be true for concise text when the mechanism, expected benefit, or affected outcome is understandable.\n"
                    "- The reason field must tell the user what to rewrite, stay under 60 words, and mention the specific weak field."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_validation_response(response)
        if (
            not parsed.get("valid")
            and self._is_style_only_validation_rejection(str(parsed.get("reason") or ""))
            and self._fields_are_locally_meaningful(cleaned_fields)
        ):
            return {
                "valid": True,
                "reason": "The text is understandable despite minor wording issues.",
            }
        return parsed

    async def _validate_clarification_answer_quality(
        self, session: ChatSession, answer: str
    ) -> dict[str, str | bool] | None:
        context = f"""
You are a strict input-quality validator for Dr Transition.

Your job is to validate a user's answer to one mitigation clarification question
before it is used to freeze the mitigation inputs. This is an understandability
check only. Do not validate whether the mitigation is correct or supported.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Review this clarification answer.\n\n"
                    f"Clarification answer: {answer}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "The clarification answer is understandable."}\n\n'
                    "Validation checks:\n"
                    "- The answer must be meaningful, recognizable text that can clarify "
                    "the mitigation measure, justification, or evidence.\n"
                    "- Reject random characters, keyboard mashing, gibberish, repeated "
                    "letters/symbols, or unrecognizable text.\n"
                    "- Reject jargon-heavy or acronym-only answers when the meaning is not "
                    "clear from the words provided.\n"
                    "- Reject vague fragments such as 'policy', 'technology', 'impact', "
                    "'better', or 'it helps' when no concrete clarification is given.\n"
                    "- Do not reject understandable policy terms merely because they are "
                    "technical, as long as the meaning is clear enough.\n"
                    "- Do not check factual correctness, groundedness, citations, or support.\n"
                    "- The reason field must tell the user what to rewrite and stay under 50 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=350,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_validation_response(response)
        if (
            not parsed.get("valid")
            and self._is_style_only_validation_rejection(str(parsed.get("reason") or ""))
            and self._fields_are_locally_meaningful({"Clarification answer": answer})
        ):
            return {
                "valid": True,
                "reason": "The clarification answer is understandable despite minor wording issues.",
            }
        return parsed

    async def _validate_profile_names_input(
        self, session: ChatSession, profiles: list[str]
    ) -> dict[str, str | bool] | None:
        context = f"""
You are a practical socio-demographic profile intake reviewer for Dr Transition.

Your job is to validate user-entered profile names before they can be added to
the affected socio-demographic profile list.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Review the proposed socio-demographic profile names.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'Not provided'}\n\n"
                    "Proposed profiles:\n"
                    + "\n".join(f"- {profile}" for profile in profiles)
                    + "\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "The profile names are recognizable and meaningful."}\n\n'
                    "Validation checks:\n"
                    "- Each item should be a recognizable socio-demographic group, population segment, household type, worker group, age group, income group, location-based group, or other affected profile.\n"
                    "- The text must be valid and meaningful as a profile name.\n"
                    "- Random characters, keyboard mashing, gibberish, or unrecognizable text is invalid.\n"
                    "- Text that is too short to determine intent is invalid.\n"
                    "- A profile should not be a mitigation measure, policy action, hazard, evidence URL, file name, or full sentence unrelated to affected people.\n\n"
                    "Rules:\n"
                    "- valid must be false if any proposed profile is invalid, ambiguous, too generic to identify a group, or unrelated to people/groups.\n"
                    "- valid may be true for concise phrases such as 'low-income households', 'rural residents', 'older adults', or 'small business owners'.\n"
                    "- If invalid, the reason must name the weak profile and ask the user to rewrite it as a clear affected group.\n"
                    "- Keep reason under 60 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
        )
        if is_llm_unavailable_response(response):
            return None
        return parse_validation_response(response)

    async def _semantic_dg_duplicate_check(
        self, session: ChatSession, dgs: list[str]
    ) -> dict[str, object] | None:
        existing_context = self._format_selected_hazard_profiles_for_duplicate_check(session)
        context = f"""
You are a strict semantic duplicate checker for Dr Transition.

Your job is to decide whether newly proposed socio-demographic profiles are
already covered by the selected hazard's existing profile text or user-added
profile list, even when the wording, grammar, or language differs.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Compare each proposed socio-demographic profile with the existing "
                    "profiles.\n\n"
                    "Treat a profile as duplicate when it has the same meaning, is a "
                    "close paraphrase, names the same group in another language, or is "
                    "a narrower/restated version already clearly covered. Do not mark "
                    "it duplicate when it adds a meaningfully distinct group.\n\n"
                    "Existing socio-demographic profiles for the selected hazard only:\n"
                    f"{existing_context}\n\n"
                    "Proposed profiles:\n"
                    + "\n".join(f"- {item}" for item in dgs)
                    + "\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"duplicate": false, "match": "", "reason": "", "duplicates": []}\n\n'
                    "When any proposed profile is duplicate, set duplicate to true and "
                    "include duplicates as an array of objects like "
                    '{"profile": "new profile", "match": "existing profile", '
                    '"reason": "short explanation"}. Keep each reason under 30 words.'
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=500,
        )

        if is_llm_unavailable_response(response):
            return None

        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        return parsed

    async def _validate_dgs_context_against_stats(
        self,
        session: ChatSession,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} "
                f"{self._format_pending_additional_dgs(session)} "
                f"{reason} {evidence}"
            ),
        )
        context = f"""
You are an evaluator for Dr Transition.

Your task is to evaluate whether the user's mitigation measure addresses the
current evaluation question AND whether the provided reason/evidence supports
the claims and selected score.

{self._scope_instruction(session)}

Use these retrieved sector-prompt excerpts as the authoritative statistical source:
{sector_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate whether the listed socio-demographic profiles are supported "
                    "as severely affected groups for the selected hazard.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n"
                    "Pending socio-demographic profiles to validate:\n"
                    f"{self._format_pending_additional_dgs(session)}\n\n"
                    "Already confirmed socio-demographic profiles for context:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Reason: {reason or 'Not provided'}\n"
                    f"Evidence: {evidence or 'Not provided'}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "short validation explanation"}\n\n'
                    "Rules:\n"
                    "- valid must be true only when the reason aligns with the loaded "
                    "statistical context for the selected hazard.\n"
                    "- If evidence content is supplied from a URL or file, valid must "
                    "also require the reason to be supported by that extracted evidence.\n"
                    "- valid must be false when the reason or supplied evidence contradicts "
                    "the statistics, invents unsupported numbers, is too vague, or the "
                    "evidence content does not support the reason.\n"
                    "- Validate the pending socio-demographic profiles before they are added.\n"
                    "- Use already confirmed profiles only as context; do not treat pending "
                    "profiles as accepted until the reason/evidence supports them.\n"
                    "- Reason is optional for user-added DGs. If no reason is provided, "
                    "validate whether the pending profiles are supported or plausible "
                    "from the sector statistical context and selected hazard alone.\n"
                    "- The reason field must be useful to the user and under 60 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        return parse_validation_response(response)

    async def _assess_mitigation_clarity(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence: str,
        clarification_answer: str | None = None,
    ) -> dict[str, object] | None:
        context = f"""
You are the Step 1 clarity-track assessor for Dr Transition.
Your only job is INPUT UNDERSTANDABILITY: can a later stage tell what the
user means? You do NOT judge correctness, validity, completeness,
sufficiency, feasibility, evidence quality, or groundedness. Those are
checked later or not at all here.

OPERATING PRINCIPLE — default to CLEAR.
Mark a dimension NEEDS_CLARIFICATION only if you can point to a SPECIFIC
word or phrase whose meaning you genuinely cannot recover. If you cannot
name the specific ambiguity, the dimension is CLEAR. A weak, partial, or
unsupported reason is still CLEAR as long as you can tell what it claims.

DIMENSIONS — resolve each as CLEAR or NEEDS_CLARIFICATION using these
operational pass tests:
1. specificity — CLEAR if you can name WHAT would be done (an action or
   instrument), even if amounts, timing, or implementation detail are
   missing.
2. justification_clarity — CLEAR if you can restate the user's reason as ONE
   declarative sentence linking the measure to its intended effect on the
   hazard, WITHOUT inventing content. Being able to write that sentence is
   the test. Do not require the reasoning to be correct, complete, or
   supported.
3. evidence_identifiability — CLEAR if you can tell what source or content is
   being pointed to. If no evidence is present, mark CLEAR.

CLARIFICATION HISTORY IS AUTHORITATIVE.
The clarification history in the user message overrides and extends the
original input. Evaluate the justification AS CLARIFIED by it. Never ask
again about a point the user has already addressed; if your only remaining
doubt is something they already answered, mark the dimension CLEAR. If a
'Clarification:' line makes the intended meaning recoverable, mark CLEAR even
if a later stage might reject the claim on the merits.

SCOPE ANCHORING (output framing only — NEVER a reason to mark
NEEDS_CLARIFICATION).
Phrase frozen_inputs and any examples in terms of the user's selected country
and sector. If the user references another context, relate it back to the
selection; treat other-country/sector material as labelled background. The
user does NOT have to restate things in terms of the selection for an input
to be CLEAR — missing anchoring is a framing task for you, not a gap in the
user's input.

QUESTIONING.
If ALL dimensions are CLEAR, produce frozen_inputs: a concise, unambiguous
restatement of measure_description, justification, and evidence, integrating
the clarification history only to pin down what the user meant (add no new
claims).
If ANY dimension is NEEDS_CLARIFICATION, pick only the FIRST unresolved one
in this order: specificity, justification_clarity, evidence_identifiability.
Return two or three short questions about THAT ONE dimension, each pointing at
the specific phrase you could not interpret, answerable in one reply. Do not
ask about any other dimension this round.

OUTPUT — return ONLY valid JSON with this exact shape:
{{
  \"clear\": false,
  \"dimensions\": {{
    \"specificity\": \"CLEAR\",
    \"justification_clarity\": \"NEEDS_CLARIFICATION\",
    \"evidence_identifiability\": \"CLEAR\"
  }},
  \"follow_up_questions\": [\"q1 about the one unresolved dimension\", \"q2 about the same dimension\"],
  \"frozen_inputs\": {{ \"measure_description\": \"\", \"justification\": \"\", \"evidence\": \"\" }},
  \"reason\": \"short clarity explanation\"
}}

RULES.
- \"clear\" is true only when all three dimensions are CLEAR.
- follow_up_questions: two or three questions, only the selected dimension,
  each tied to a specific ambiguous phrase you quote.
- Do not require implementation detail, evidence, feasibility, or correctness.
- Do not penalise unsupported or arguable reasoning — support is checked later.
- Scope anchoring is framing only and must never cause NEEDS_CLARIFICATION.
- Keep "reason" under 50 words.

CALIBRATION (justification_clarity).
- "It will reduce flood risk because raising the road keeps it above the
  waterline" -> CLEAR (restatable in one sentence; correctness is for later).
- "It helps with the situation and is generally good practice" ->
  NEEDS_CLARIFICATION (cannot name the mechanism or the intended effect).

""".strip()
        clarification_block = self._mitigation_clarification_history_block(
            session,
            clarification_answer,
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"- Selected country: {session.country or 'Not selected'}\n"
                    f"- Selected region: {session.region or 'Not selected'}\n"
                    f"- Selected sector: {session.sector or 'Not selected'}\n"
                    f"- Selected target populations/groups: "
                    f"{self._mitigation_target_population_text(session)}\n"

                    f"Measure description: {mitigation_measure or 'Not provided'}\n"
                    f"Justification: {reason or 'Not provided'}\n"
                    f"Evidence: {evidence or 'Not provided'}\n"

                    "Clarification history:\n"
                    f"{clarification_block}"
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=650,
        )
        if is_llm_unavailable_response(response):
            return None
        return parse_mitigation_clarity_response(response)

    async def _validate_mitigation_against_stats(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence: str = "",
    ) -> dict[str, object] | None:
        has_evidence = self._has_user_supplied_evidence(evidence)
        raw_support_context = (
            await self._mitigation_evidence_context(session, mitigation_measure, reason, evidence)
            if has_evidence
            else await self._mitigation_main_knowledge_context(session, mitigation_measure, reason)
        )
        support_label = (
            self.mitigation_support_label_user_evidence
            if has_evidence
            else self.mitigation_support_label_curated_knowledge_base
        )
        support_context = self._floor_filtered_support_context(raw_support_context)
        valid_citation_ids = set(self._support_citation_scores(support_context))
        clarification_block = self._mitigation_clarification_history_block(session)
        context = f"""
You are the groundedness validator for Dr Transition. You decide, per
dimension, whether the AUTHORITATIVE SUPPORT CORPUS backs the proposed
mitigation measure and the user's justification. You judge support from the
corpus only — not whether you personally find the measure wise.

AUTHORITATIVE CORPUS IS FIXED.
The user message names the authoritative corpus (support_label) and provides
its excerpts. That selection is final: cite ONLY those excerpts. Do not
reason about which corpus should apply, do not pull in any other corpus, and
do not use your own background knowledge or sector statistics as support.
User assertions are not corpus evidence. However, the mitigation measure and
justification are the AUTHORITATIVE USER INPUTS for hazard_fit and
justification_soundness. Assess those two dimensions directly from the complete
input. The user input cannot serve as evidence for mechanism, evidence_quality,
contraindications, or feasibility.

VERDICTS — resolve each dimension as exactly one of:
SUPPORTED: the dimension's pass test (below) is met. Hazard_fit and
  justification_soundness may be supported directly by the authoritative user
  input. Every other dimension requires at least one provided excerpt.
CONTRADICTED: a provided excerpt states something that conflicts with the
  measure or justification on this dimension. Cite the excerpt ID(s).
INSUFFICIENT_INFO: the excerpts neither establish nor contradict this
  dimension. This is the correct, neutral verdict when the corpus is simply
  silent — it is NOT a failure to try harder, and you must NOT infer support
  from general principles to avoid it.

CALIBRATION — when unsure between SUPPORTED and INSUFFICIENT_INFO, ask only:
"Does an excerpt meet this dimension's pass test?" If yes, SUPPORTED with that
citation. If no, INSUFFICIENT_INFO. Do not withhold SUPPORTED when a clear
excerpt exists, and do not manufacture SUPPORTED when none does.

DIMENSIONS:
1. hazard_fit — does the submitted measure directly address the selected
   hazard? Mark SUPPORTED when the measure description or complete
   justification explicitly links its action to reducing the selected
   harm/outcome; no corpus citation is required for that direct semantic fit.
   A supporting excerpt may also establish fit. Mark CONTRADICTED only when an
   excerpt conflicts with the claimed fit. (critical)
2. mechanism — SUPPORTED if excerpts support the causal pathway by which the
   measure reduces harm, INCLUDING when they support the component steps or
   the underlying mechanism rather than the exact named implementation.
   (Example: excerpts on metering or consumption visibility can support the
   mechanism of a platform that surfaces consumption data.) Cite the
   excerpt(s). The excerpt does not need to mention the exact target
   population, country, subsidy design, or program name. For example, an
   excerpt stating that insulation reduces energy bills supports the mechanism
   of a subsidized insulation program intended to reduce heating and cooling
   costs. Mark INSUFFICIENT_INFO only if no excerpt speaks to the pathway at
   all; do not invent a pathway. (critical)
3. justification_soundness — SUPPORTED if the user's reasoning is internally
   coherent (the measure plausibly connects to its intended effect on the
   hazard) AND no excerpt contradicts it. This dimension does NOT require an
   excerpt that affirmatively proves the reasoning — proving the pathway is
   the mechanism dimension's job — so it MAY be SUPPORTED with an empty
   citation list. Mark CONTRADICTED only if an excerpt conflicts with the
   reasoning (cite it). Mark INSUFFICIENT_INFO only if the reasoning cannot be
   followed at all. You MUST evaluate the complete text under
   "Justification to evaluate" in the user message; do not ignore it merely
   because it is not part of the support corpus. (critical)
4. evidence_quality — branch-aware:
   - If support_label is USER_EVIDENCE: are those excerpts relevant
     and adequate for this specific measure and hazard?
   - If support_label is CURATED_KNOWLEDGE_BASE: do the KB excerpts give
     relevant, adequate coverage for this measure and hazard? The absence of
     user-supplied evidence is NEVER the finding here and never counts against
     the measure — assess KB coverage, not whether the user attached evidence.
5. contraindications — CONTRADICTED if an excerpt states a conflict, risk, or
   incompatibility (cite it); SUPPORTED only if an excerpt affirmatively
   endorses the measure as conflict-free; otherwise INSUFFICIENT_INFO
   ("no conflict found in corpus"). Finding no conflict is INSUFFICIENT_INFO,
   not a low score.
6. feasibility — do the excerpts support practical applicability?

SEVERITY (for your explanations, not an aggregate verdict you compute):
hazard_fit, mechanism, justification_soundness are CRITICAL.
evidence_quality, contraindications, feasibility are CAUTION dimensions:
  INSUFFICIENT_INFO in these is a caution, not proof the measure needs
  revision.
Any CONTRADICTED verdict, in any dimension, is a hard veto.
You output per-dimension verdicts only. Do not compute or state an overall
pass/fail — the pipeline decides that from your verdicts.

SCOPE ANCHORING (framing only — NEVER a reason for CONTRADICTED):
Anchor wording and examples to the user's selected country and sector. Treat
excerpts about other countries/sectors as labelled general background; do not
cite them as if they matched the selection unless the text explicitly does.
A mismatch of country/sector framing is not a contradiction of the measure.

OUTPUT — return ONLY one valid JSON object, no Markdown, fences, headers, or
text before/after:
{{"dimensions": {{
  "hazard_fit": {{"status": "SUPPORTED", "citation_ids": ["S1"], "explanation": "..."}},
  "mechanism": {{"status": "SUPPORTED", "citation_ids": ["S1"], "explanation": "..."}},
  "justification_soundness": {{"status": "SUPPORTED", "citation_ids": [], "explanation": "..."}},
  "evidence_quality": {{"status": "INSUFFICIENT_INFO", "citation_ids": [], "explanation": "..."}},
  "contraindications": {{"status": "INSUFFICIENT_INFO", "citation_ids": [], "explanation": "..."}},
  "feasibility": {{"status": "SUPPORTED", "citation_ids": ["S1"], "explanation": "..."}}
}}, "reason": "short grounded validation explanation"}}

RULES:
SUPPORTED and CONTRADICTED must carry at least one citation_id from the
  provided excerpts — EXCEPT hazard_fit and justification_soundness, which may
  be SUPPORTED with an empty citation_ids list based on the authoritative user
  input.
  A CONTRADICTED justification_soundness must still cite the conflicting
  excerpt.
INSUFFICIENT_INFO always carries an empty citation_ids list.
Cite only IDs that appear in the provided excerpts; never invent IDs.
Keep each explanation to one or two sentences. Keep "reason" under 90 words.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    f"Authoritative support corpus for this verdict: {support_label}\n"
                    "\nValidated user inputs to evaluate:\n"
                    f"Selected Country: {session.country}\n"
                    f"Selected Sector: {session.sector}\n"
                    f"Selected Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n"
                    "Selected target populations/groups:\n"
                    f"{self._mitigation_target_population_text(session)}\n"
                    "Affected socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Mitigation measure: {mitigation_measure}\n\n"
                    f"Justification to evaluate: {reason}\n\n"
                    f"Clarification history:\n{clarification_block}\n\n"
                    f"User-supplied evidence description: {evidence or 'Not provided'}\n\n"
                    "Support excerpts eligible as citations:\n"
                    f"{support_context or '- No relevant support excerpts were found.'}\n\n"
                    "Evaluate every dimension. For justification_soundness, directly "
                    "evaluate the complete justification above for internal coherence "
                    "and check whether any support excerpt contradicts it."
                ),
            }
        ]

        async def sample_validator(sample_count: int) -> list[dict[str, object]]:
            responses = await asyncio.gather(
                *[
                    ask_llm_chat(
                        context=context,
                        messages=messages,
                        temperature=self.settings.mitigation_verdict_temperature,
                        max_tokens=700,
                    )
                    for _ in range(max(0, sample_count))
                ]
            )
            if any(is_llm_unavailable_response(response) for response in responses):
                return []
            return [
                self._sanitize_grounding_sample(parsed, valid_citation_ids)
                for response in responses
                if not (parsed := parse_grounded_validation_response(response)).get("error")
            ]

        parsed_samples = await sample_validator(max(1, self.settings.mitigation_verdict_samples))        
        if not parsed_samples:
            return None
        contradiction_dimensions = self._dimensions_with_any_status(
            parsed_samples,
            "CONTRADICTED",
        )
        contradiction_samples = (
            await sample_validator(self.settings.mitigation_contradiction_resamples)
            if contradiction_dimensions
            else []
        )
        parsed = self._majority_grounding_verdict(
            parsed_samples,
            contradiction_samples=contradiction_samples,
            contradiction_dimensions=contradiction_dimensions,
        )
        return self._score_mitigation_grounding(
            parsed,
            support_context=support_context,
            support_label=support_label,
            has_user_evidence=has_evidence,
        )

    def _sanitize_grounding_sample(
        self,
        sample: dict[str, object],
        valid_citation_ids: set[str],
    ) -> dict[str, object]:
        raw_dimensions = sample.get("dimensions")
        raw_dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
        dimensions: dict[str, dict[str, object]] = {}
        for name in self.mitigation_grounding_dimensions:
            raw_dimension = raw_dimensions.get(name)
            raw_dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
            status = str(raw_dimension.get("status") or "INSUFFICIENT_INFO").upper()
            raw_citation_ids = raw_dimension.get("citation_ids")
            raw_citation_ids = raw_citation_ids if isinstance(raw_citation_ids, list) else []
            citation_ids = sorted({
                citation_id.upper()
                for citation_id in raw_citation_ids
                if isinstance(citation_id, str)
                and citation_id.upper() in valid_citation_ids
            })
            citation_required = not (
                name in self.mitigation_input_supported_dimensions
                and status == "SUPPORTED"
            )
            if status in {"SUPPORTED", "CONTRADICTED"} and citation_required and not citation_ids:
                status = "INSUFFICIENT_INFO"
            dimensions[name] = {
                "status": status,
                "citation_ids": citation_ids,
                "explanation": str(raw_dimension.get("explanation") or "").strip(),
            }
        return {
            "dimensions": dimensions,
            "reason": str(sample.get("reason") or "").strip(),
        }

    def _dimensions_with_any_status(
        self,
        samples: list[dict[str, object]],
        status: str,
    ) -> set[str]:
        dimensions: set[str] = set()
        for sample in samples:
            sample_dimensions = sample.get("dimensions")
            if not isinstance(sample_dimensions, dict):
                continue
            for name in self.mitigation_grounding_dimensions:
                dimension = sample_dimensions.get(name)
                if isinstance(dimension, dict) and dimension.get("status") == status:
                    dimensions.add(name)
        return dimensions

    def _floor_filtered_support_context(self, support_context: str) -> str:
        floor = self.settings.mitigation_support_score_floor
        lines: list[str] = []
        keep_current_excerpt = False
        for line in support_context.splitlines():
            if re.match(r"^\s*-\s*\[S\d+\]", line, flags=re.IGNORECASE):
                score_match = re.search(r", score (-?\d+(?:\.\d+)?)[: ,]", line)
                keep_current_excerpt = bool(
                    score_match and float(score_match.group(1)) >= floor
                )
            if keep_current_excerpt:
                lines.append(line)
        return "\n".join(lines)

    def _majority_grounding_verdict(
        self,
        samples: list[dict[str, object]],
        *,
        contradiction_samples: list[dict[str, object]] | None = None,
        contradiction_dimensions: set[str] | None = None,
    ) -> dict[str, object]:
        dimensions: dict[str, dict[str, object]] = {}
        stability_values: list[float] = []
        contradiction_samples = contradiction_samples or []
        contradiction_dimensions = contradiction_dimensions or set()
        for name in self.mitigation_grounding_dimensions:
            candidates: list[dict[str, object]] = []
            dimension_samples = [
                *samples,
                *(contradiction_samples if name in contradiction_dimensions else []),
            ]
            for sample in dimension_samples:
                sample_dimensions = sample.get("dimensions")
                if not isinstance(sample_dimensions, dict):
                    continue
                dimension = sample_dimensions.get(name)
                if isinstance(dimension, dict):
                    candidates.append(dimension)
            status_counts: dict[str, int] = {}
            for candidate in candidates:
                status = str(candidate.get("status") or "INSUFFICIENT_INFO")
                status_counts[status] = status_counts.get(status, 0) + 1
            total_samples = max(1, len(candidates))
            contradiction_fraction = status_counts.get("CONTRADICTED", 0) / total_samples
            if (
                status_counts.get("CONTRADICTED", 0)
                and contradiction_fraction
                >= self.settings.mitigation_contradiction_confirmation_fraction
            ):
                winning_status = "CONTRADICTED"
            else:
                supported_count = status_counts.get("SUPPORTED", 0)
                insufficient_count = (
                    status_counts.get("INSUFFICIENT_INFO", 0)
                    + status_counts.get("CONTRADICTED", 0)
                )
                winning_status = (
                    "SUPPORTED"
                    if supported_count > insufficient_count
                    else "INSUFFICIENT_INFO"
                )
            winning = [
                candidate
                for candidate in candidates
                if candidate.get("status") == winning_status
            ]
            unconfirmed_contradiction = (
                winning_status == "INSUFFICIENT_INFO"
                and not winning
                and status_counts.get("CONTRADICTED", 0) > 0
            )
            winning_count = status_counts.get(winning_status, 0)
            if winning_status == "INSUFFICIENT_INFO":
                winning_count += status_counts.get("CONTRADICTED", 0)
            stability_values.append(winning_count / total_samples)
            citation_ids = sorted({
                citation_id
                for candidate in winning
                for citation_id in candidate.get("citation_ids", [])
                if isinstance(citation_id, str)
            })
            dimensions[name] = {
                "status": winning_status,
                "citation_ids": citation_ids,
                "explanation": next(
                    (
                        str(candidate.get("explanation") or "")
                        for candidate in winning
                        if candidate.get("explanation")
                    ),
                    (
                        "A possible contradiction was sampled but did not meet the "
                        "confirmation threshold."
                        if unconfirmed_contradiction
                        else ""
                    ),
                ),
            }
        reasons = [str(sample.get("reason") or "").strip() for sample in samples]
        return {
            "dimensions": dimensions,
            "reason": next((reason for reason in reasons if reason), ""),
            "verdict_stability": (
                sum(stability_values) / len(stability_values) if stability_values else 0.0
            ),
            "sample_count": len(samples) + len(contradiction_samples),
        }

    def _score_mitigation_grounding(
        self,
        parsed: dict[str, object],
        *,
        support_context: str,
        support_label: str,
        has_user_evidence: bool | None = None,
    ) -> dict[str, object]:
        if has_user_evidence is None:
            has_user_evidence = support_label == self.mitigation_support_label_user_evidence
        citation_scores = self._support_citation_scores(support_context)
        raw_dimensions = parsed.get("dimensions")
        raw_dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
        dimensions, supported_scores = self._scored_mitigation_dimensions(
            raw_dimensions,
            citation_scores,
            has_user_evidence,
        )

        applicable_dimensions = {
            name: dimension
            for name, dimension in dimensions.items()
            if dimension["status"] != "NOT_APPLICABLE"
        }
        critical_dimensions = {
            name: dimensions[name]
            for name in self.mitigation_critical_grounding_dimensions
            if name in dimensions
        }
        critical_statuses = [
            str(dimension["status"]) for dimension in critical_dimensions.values()
        ]
        supported_count = critical_statuses.count("SUPPORTED")
        coverage = supported_count / len(critical_dimensions) if critical_dimensions else 0.0
        has_contradiction = any(
            dimension["status"] == "CONTRADICTED"
            for dimension in applicable_dimensions.values()
        )
        all_critical_supported = bool(critical_dimensions) and all(
            dimension["status"] == "SUPPORTED"
            for dimension in critical_dimensions.values()
        )
        if has_contradiction:
            outcome = "REJECT"
        elif not all_critical_supported:
            outcome = "ABSTAIN"
        else:
            outcome = "PASS"
        retrieval_support = (
            sum(min(score, 1.0) for score in supported_scores) / len(supported_scores)
            if supported_scores
            else 0.0
        )
        verdict_stability = float(parsed.get("verdict_stability") or 0.0)
        confidence_score = round(
            100 * ((0.6 * coverage) + (0.25 * retrieval_support) + (0.15 * verdict_stability))
        )

        reason = self._mitigation_grounding_reason(
            str(parsed.get("reason") or "").strip(),
            outcome,
            applicable_dimensions,
            critical_dimensions,
        )

        return {
            "valid": outcome == "PASS",
            "outcome": outcome,
            "reason": reason or "The grounded validation rubric was incomplete.",
            "dimensions": dimensions,
            "rubric_coverage": round(coverage, 4),
            "retrieval_support": round(retrieval_support, 4),
            "verdict_stability": round(verdict_stability, 4),
            "sample_count": int(parsed.get("sample_count") or 1),
            "confidence_score": confidence_score,
            "support_context": support_context,
            "support_label": support_label,
        }

    def _scored_mitigation_dimensions(
        self,
        raw_dimensions: dict[object, object],
        citation_scores: dict[str, float],
        has_user_evidence: bool,
    ) -> tuple[dict[str, dict[str, object]], list[float]]:
        dimensions: dict[str, dict[str, object]] = {}
        supported_scores: list[float] = []
        for name in self.mitigation_grounding_dimensions:
            raw_dimension = raw_dimensions.get(name)
            raw_dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
            status = str(raw_dimension.get("status") or "INSUFFICIENT_INFO").upper()
            citation_ids = raw_dimension.get("citation_ids")
            citation_ids = citation_ids if isinstance(citation_ids, list) else []
            valid_scores = [
                citation_scores[citation_id]
                for citation_id in citation_ids
                if isinstance(citation_id, str)
                and citation_id in citation_scores
                and citation_scores[citation_id] >= self.settings.mitigation_support_score_floor
            ]
            citation_optional = (
                name in self.mitigation_input_supported_dimensions
                and status == "SUPPORTED"
            )
            if status == "SUPPORTED" and not valid_scores and not citation_optional:
                status = "INSUFFICIENT_INFO"
            if status == "SUPPORTED" and valid_scores:
                supported_scores.append(max(valid_scores))
            dimensions[name] = {
                "status": status,
                "citation_ids": citation_ids,
                "support_score": round(max(valid_scores), 4) if valid_scores else None,
                "explanation": str(raw_dimension.get("explanation") or "").strip(),
            }
        return dimensions, supported_scores

    def _mitigation_grounding_reason(
        self,
        reason: str,
        outcome: str,
        applicable_dimensions: dict[str, dict[str, object]],
        critical_dimensions: dict[str, dict[str, object]],
    ) -> str:
        if outcome == "PASS":
            unresolved_cautions = self._dimension_names_with_status(
                applicable_dimensions,
                "INSUFFICIENT_INFO",
                exclude=set(self.mitigation_critical_grounding_dimensions),
            )
            if not unresolved_cautions:
                return reason
            caution = (
                "Proceed with caution because the authoritative corpus did not resolve: "
                + ", ".join(unresolved_cautions)
                + "."
            )
            return f"{reason} {caution}".strip()

        reason_parts: list[str] = []
        contradicted = self._dimension_names_with_status(
            applicable_dimensions,
            "CONTRADICTED",
        )
        if outcome == "REJECT":
            reason_parts.append(
                "The authoritative corpus actively conflicts with the mitigation measure."
            )
            reason_parts.append("Contradicted dimensions: " + ", ".join(contradicted) + ".")
            return " ".join(reason_parts)

        reason_parts.append(
            "The authoritative corpus does not cover every critical dimension needed "
            "to validate this mitigation measure."
        )
        insufficient = self._dimension_names_with_status(
            critical_dimensions,
            "INSUFFICIENT_INFO",
        )
        if insufficient:
            reason_parts.append(
                "Insufficiently supported dimensions: " + ", ".join(insufficient) + "."
            )
        return " ".join(reason_parts)

    def _mitigation_outcome_reason(
        self,
        validation: dict[str, object],
        outcome: str,
    ) -> str:
        dimensions = validation.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, dict) else {}
        support_context = str(validation.get("support_context") or "")
        support_label = str(validation.get("support_label") or "")
        if outcome == "REJECT":
            relevant_names = self._dimension_names_with_status_keys(dimensions, "CONTRADICTED")
            heading = "### Confirmed contradictions"
        else:
            relevant_names = self._dimension_names_with_status_keys(
                dimensions,
                "INSUFFICIENT_INFO",
                include=set(self.mitigation_critical_grounding_dimensions),
            )
            heading = "### Critical coverage gaps"

        lines = [str(validation.get("reason") or "").strip(), "", heading, ""]
        for name in relevant_names:
            dimension = dimensions.get(name)
            dimension = dimension if isinstance(dimension, dict) else {}
            explanation = str(dimension.get("explanation") or "No explanation was provided.")
            citations = [
                str(citation_id)
                for citation_id in dimension.get("citation_ids", [])
                if isinstance(citation_id, str)
            ]
            label = name.replace("_", " ").title()
            citation_text = f" Citations: {', '.join(citations)}." if citations else ""
            lines.append(f"- **{label}:** {explanation}{citation_text}")
            if outcome == "REJECT":
                lines.extend(
                    f"  - `{citation_id}`: {self._support_excerpt(support_context, citation_id)}"
                    for citation_id in citations
                )

        if outcome == "ABSTAIN":
            if support_label == self.mitigation_support_label_user_evidence:
                lines.extend([
                    "",
                    "Add a published source that directly covers the critical gap, or "
                    "revise the justification so the claimed mechanism is clear.",
                ])
            else:
                lines.extend([
                    "",
                    "This measure is beyond the curated knowledge base's current scope. "
                    "Attach a published source that covers the critical gap to proceed.",
                ])
        return "\n".join(lines).strip()

    @staticmethod
    def _dimension_names_with_status_keys(
        dimensions: dict[str, object],
        status: str,
        include: set[str] | None = None,
    ) -> list[str]:
        return [
            name
            for name, dimension in dimensions.items()
            if (include is None or name in include)
            and isinstance(dimension, dict)
            and dimension.get("status") == status
        ]

    @staticmethod
    def _support_excerpt(support_context: str, citation_id: str) -> str:
        marker = f"[{citation_id}]"
        return next(
            (
                line.split(":", 1)[1].strip()
                for line in support_context.splitlines()
                if marker in line and ":" in line
            ),
            "The cited excerpt was not available.",
        )

    @staticmethod
    def _evidence_url(evidence: str | None) -> str:
        if not evidence:
            return ""
        match = re.search(r"^Evidence URL:\s*(.+)$", evidence, flags=re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else ""

    async def _admit_inline_evidence_to_quarantine(
        self,
        session: ChatSession,
        evidence: str,
        validation: dict[str, object],
    ) -> None:
        inline_evidence = self._inline_evidence_content(evidence)
        if not inline_evidence or self._temporary_evidence_document_ids(evidence):
            return
        source_uri = self._evidence_url(evidence) or None
        validated_at = datetime.now(timezone.utc).isoformat()
        title = (
            f"[validated_user_evidence; validated_at={validated_at}; "
            f"session={session.session_key}; outcome={validation.get('outcome')}] "
            f"{source_uri or 'Inline user evidence'}"
        )
        try:
            await KnowledgeBaseService(
                self.db,
                self.user_id,
                scope="quarantined",
                session_key=session.session_key,
            ).ingest_text(
                inline_evidence,
                title[:255],
                "validated_user_evidence",
                source_uri,
            )
        except Exception:
            logger.exception("Failed to admit validated inline evidence to quarantine")

    @staticmethod
    def _dimension_names_with_status(
        dimensions: dict[str, dict[str, object]],
        status: str,
        exclude: set[str] | None = None,
    ) -> list[str]:
        excluded = exclude or set()
        return [
            name.replace("_", " ")
            for name, dimension in dimensions.items()
            if name not in excluded and dimension["status"] == status
        ]

    @staticmethod
    def _support_citation_scores(support_context: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for citation_id, score in re.findall(
            r"\[(S\d+)\][^\n]*?, score (-?\d+(?:\.\d+)?)[: ,]",
            support_context,
            flags=re.IGNORECASE,
        ):
            scores[citation_id.upper()] = float(score)
        return scores

    async def _grounded_mitigation_synthesis(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        validation: dict[str, object],
    ) -> str | None:
        support_context = str(validation.get("support_context") or "")
        support_label = str(validation.get("support_label") or "the authoritative corpus")
        context = f"""
You generate a citation-required grounded synthesis for Dr Transition.

Use only the validated user fields and authoritative support excerpts below.
Every atomic claim must cite at least one support excerpt ID or explicitly name
one validated user field. Omit unsupported ideas instead of hedging or inferring.

Authoritative corpus: {support_label}

Support excerpts:
{support_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validated user fields:\n"
                    f"- measure_description: {mitigation_measure}\n"
                    f"- justification: {reason}\n"
                    f"- selected_hazard: {session.selected_hazard or 'Not provided'}\n"
                    f"- target_population: {self._mitigation_target_population_text(session)}\n"
                    f"- affected_groups: {format_all_dgs(session)}\n\n"
                    "Create a concise synthesis explaining how to think about the measure "
                    "and what to be careful about. Decompose it into atomic claims.\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"claims": [{"text": "one atomic claim", "citation_ids": ["S1"], '
                    '"user_fields": ["measure_description"]}]}\n\n'
                    "Rules:\n"
                    "- Each claim must be directly supported by its cited excerpt IDs or "
                    "be a faithful restatement of its named validated user fields.\n"
                    "- Do not introduce recommendations, benefits, risks, groups, mechanisms, "
                    "or limitations that are not explicitly supported.\n"
                    "- Keep each claim to one sentence and return no more than six claims."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=900,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_grounded_claims_response(response)
        if parsed.get("error"):
            return None

        citation_scores = self._support_citation_scores(support_context)
        allowed_user_fields = {
            "measure_description",
            "justification",
            "selected_hazard",
            "target_population",
            "affected_groups",
        }
        claims = [
            claim
            for claim in parsed.get("claims", [])
            if isinstance(claim, dict)
            and (
                any(
                    citation_id in citation_scores
                    and citation_scores[citation_id]
                    >= self.settings.mitigation_support_score_floor
                    for citation_id in claim.get("citation_ids", [])
                )
                or any(field in allowed_user_fields for field in claim.get("user_fields", []))
            )
        ]
        if not claims:
            return None

        entailed_claims = await self._entailed_mitigation_claims(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            support_context=support_context,
            claims=claims,
        )
        if not entailed_claims:
            return None

        confidence_score = int(validation.get("confidence_score") or 0)
        evidence_note = (
            "This conclusion is grounded in user-supplied evidence."
            if support_label == self.mitigation_support_label_user_evidence
            else "This conclusion is grounded in the curated main knowledge base."
        )
        claim_lines = []
        for claim in entailed_claims:
            citations = [*claim.get("citation_ids", []), *claim.get("user_fields", [])]
            citation_text = ", ".join(f"`{citation}`" for citation in citations)
            claim_lines.append(f"- {claim['text']} ({citation_text})")
        dimensions = validation.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, dict) else {}
        caution_lines = [
            (
                f"- The authoritative corpus does not address "
                f"**{name.replace('_', ' ')}** for this measure: "
                f"{str(dimension.get('explanation') or 'coverage is insufficient.').strip()}"
            )
            for name, dimension in dimensions.items()
            if name not in self.mitigation_critical_grounding_dimensions
            and isinstance(dimension, dict)
            and dimension.get("status") == "INSUFFICIENT_INFO"
        ]
        caution_block = (
            "\n\n### What to be careful about\n\n" + "\n".join(caution_lines)
            if caution_lines
            else ""
        )
        return (
            "### Grounded conclusion\n\n"
            + "\n".join(claim_lines)
            + caution_block
            + f"\n\n**Grounding confidence:** {confidence_score}/100. {evidence_note}"
        )

    async def _entailed_mitigation_claims(
        self,
        *,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        support_context: str,
        claims: list[dict[str, object]],
    ) -> list[dict[str, object]] | None:
        user_fields = {
            "measure_description": mitigation_measure,
            "justification": reason,
            "selected_hazard": session.selected_hazard or "Not provided",
            "target_population": self._mitigation_target_population_text(session),
            "affected_groups": format_all_dgs(session),
        }
        premises = [
            self._claim_entailment_premise(claim, support_context, user_fields)
            for claim in claims
        ]
        nli_verdicts = await self.grounding_models.entail(
            premises,
            [str(claim.get("text") or "") for claim in claims],
        )
        if nli_verdicts is not None:
            return [
                claim
                for claim, verdict in zip(claims, nli_verdicts, strict=True)
                if verdict.get("entailed") is True
            ]

        claim_text = "\n".join(
            f"{index}. {claim['text']} | citations={claim.get('citation_ids', [])} "
            f"| user_fields={claim.get('user_fields', [])}"
            for index, claim in enumerate(claims, start=1)
        )
        context = """
You are a strict entailment verifier for Dr Transition.
Verify each atomic claim independently. A claim is entailed only when its cited
support excerpts or named validated user fields directly support the full claim.
Do not allow indirect inference, general knowledge, or plausible extrapolation.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validated user fields:\n"
                    f"- measure_description: {mitigation_measure}\n"
                    f"- justification: {reason}\n"
                    f"- selected_hazard: {session.selected_hazard or 'Not provided'}\n"
                    f"- target_population: {self._mitigation_target_population_text(session)}\n"
                    f"- affected_groups: {format_all_dgs(session)}\n\n"
                    f"Support excerpts:\n{support_context}\n\n"
                    f"Claims to verify:\n{claim_text}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"verdicts": [{"claim_index": 1, "entailed": true, '
                    '"reason": "directly supported"}]}\n\n'
                    "Mark entailed false if any part of a claim is unsupported or if its "
                    "listed citations/user fields do not directly entail it."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=700,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_entailment_response(response)
        if parsed.get("error"):
            return None
        entailed_indexes = {
            verdict["claim_index"]
            for verdict in parsed.get("verdicts", [])
            if isinstance(verdict, dict) and verdict.get("entailed") is True
        }
        return [
            claim
            for index, claim in enumerate(claims, start=1)
            if index in entailed_indexes
        ]

    @staticmethod
    def _claim_entailment_premise(
        claim: dict[str, object],
        support_context: str,
        user_fields: dict[str, str],
    ) -> str:
        citation_ids = {
            citation_id
            for citation_id in claim.get("citation_ids", [])
            if isinstance(citation_id, str)
        }
        cited_lines = [
            line
            for line in support_context.splitlines()
            if any(f"[{citation_id}]" in line for citation_id in citation_ids)
        ]
        field_lines = [
            f"{field}: {user_fields[field]}"
            for field in claim.get("user_fields", [])
            if isinstance(field, str) and field in user_fields
        ]
        return "\n".join([*cited_lines, *field_lines])

    async def _mitigation_main_knowledge_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
        try:
            results = await KnowledgeBaseService(self.db, self.user_id).search(query, limit=8)
        except Exception:
            logger.exception("Main knowledge-base lookup failed during mitigation validation")
            results = []
        return self._format_knowledge_results(results)

    async def _mitigation_evidence_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence: str,
    ) -> str:
        query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
        temporary_results: list[dict[str, object]] = []
        if session.session_key:
            try:
                temporary_results = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope="temporary",
                    session_key=session.session_key,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Temporary evidence lookup failed during mitigation validation")

        inline_evidence = self._inline_evidence_content(evidence)
        inline_results: list[dict[str, object]] = []
        if inline_evidence:
            inline_results.append(
                {
                    "title": "User-supplied evidence",
                    "source_type": "evidence",
                    "score": 1.0,
                    "content": inline_evidence,
                }
            )
        results = [*temporary_results, *inline_results]
        if inline_results:
            results = await self.grounding_models.ground_results(query, results)
        return self._format_knowledge_results(results)

    async def _sector_prompt_rag_context(
        self,
        session: ChatSession,
        query: str,
        limit: int = 5,
    ) -> str:
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                query,
                limit=limit,
            )
        except Exception:
            logger.exception("Sector-prompt RAG lookup failed")
            results = []

        formatted = SectorPromptRagService.format_results(results)
        if formatted:
            return formatted
        return "- No relevant sector-prompt RAG excerpts were found."

    async def _mitigation_knowledge_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
        try:
            main_results = await KnowledgeBaseService(self.db, self.user_id).search(query, limit=5)
        except Exception:
            logger.exception("Main knowledge-base lookup failed during mitigation validation")
            main_results = []
        temporary_results: list[dict[str, object]] = []
        if session.session_key:
            try:
                temporary_results = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope="temporary",
                    session_key=session.session_key,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Temporary evidence lookup failed during mitigation validation")
        results = await self.grounding_models.ground_results(
            query,
            [*temporary_results, *main_results],
        )
        return self._format_knowledge_results(results)

    @staticmethod
    def _mitigation_retrieval_query(
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        # Retrieval must prioritize the proposed intervention and its mechanism.
        # A long affected-profile list dilutes cross-encoder relevance and can
        # push genuinely supporting evidence below the eligibility floor.
        return (
            f"{session.selected_hazard or ''} {mitigation_measure} {reason} "
            f"{ChatService._mitigation_target_population_text(session)} "
            f"{session.country or ''} {session.sector or ''} {session.region or ''}"
        )

    @staticmethod
    def _format_knowledge_results(results: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Knowledge source")
            page = result.get("page_number")
            page_label = f", page {page}" if page else ""
            score = result.get("score")
            score_label = f", score {score}" if score is not None else ""
            nli_label = result.get("nli_label")
            nli_score = result.get("nli_score")
            nli_score_label = (
                f", NLI {nli_label} {nli_score}"
                if nli_label is not None and nli_score is not None
                else ""
            )
            content = str(result.get("content") or "").strip()
            if content:
                lines.append(
                    f"- [S{index}] {title}{page_label}{score_label}{nli_score_label}: "
                    f"{content[:900]}"
                )
        return "\n".join(lines)

    @staticmethod
    def _has_user_supplied_evidence(evidence: str | None) -> bool:
        return bool(evidence and evidence.strip())

    @staticmethod
    def _has_readable_evidence_content(evidence: str | None) -> bool:
        if not evidence or not evidence.strip():
            return False
        if re.search(r"Temporary evidence document ID:\s*\d+", evidence, flags=re.IGNORECASE):
            return True
        content = ChatService._inline_evidence_content(evidence)
        if content:
            return not content.casefold().startswith("unable to extract evidence")
        lowered = evidence.casefold()
        if "unable to extract evidence" in lowered:
            return False
        if "evidence url:" in lowered or "evidence file:" in lowered:
            return False
        return True

    @staticmethod
    def _inline_evidence_content(evidence: str | None) -> str:
        if not evidence or not evidence.strip():
            return ""
        lines = [line.strip() for line in evidence.splitlines() if line.strip()]
        content_lines = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.casefold().startswith("evidence content:")
            and line.split(":", 1)[1].strip()
        ]
        if content_lines:
            return "\n".join(content_lines)
        lowered = evidence.casefold()
        if not any(
            marker in lowered
            for marker in (
                "evidence url:",
                "evidence file:",
                "temporary evidence document id:",
                "temporary evidence indexing failed:",
                "unable to extract evidence",
            )
        ):
            return evidence.strip()
        return ""

    def _mitigation_measure_examples(self, sector_id: int | None, limit: int = 6) -> str:
        if sector_id is None:
            return ""
        query = (
            select(MitigationMeasureExample.measure)
            .where(MitigationMeasureExample.sector_id == sector_id)
            .order_by(MitigationMeasureExample.id)
            .limit(limit)
        )
        rows = self.db.scalars(query).all()
        return "\n".join(f"- {measure}" for measure in rows if str(measure or "").strip())

    def _selected_system_hazard_id(self, session: ChatSession) -> int | None:
        if session.sector_id is None or not session.selected_hazard:
            return None
        if isinstance(session.selected_hazard_record_id, int):
            linked_hazard_id = self.db.scalar(
                select(UserHazard.system_hazard_id).where(
                    UserHazard.id == session.selected_hazard_record_id
                )
            )
            if isinstance(linked_hazard_id, int):
                return linked_hazard_id
        hazard_id = self.db.scalar(
            select(SystemHazard.id).where(
                SystemHazard.sector_id == session.sector_id,
                func.lower(SystemHazard.name) == session.selected_hazard.casefold(),
            )
        )
        if isinstance(hazard_id, int):
            return hazard_id
        return None

    def _selected_system_profile_ids(
        self, session: ChatSession, system_hazard_id: int | None
    ) -> list[int]:
        if system_hazard_id is None:
            return []

        selected_profiles = self._selected_hazard_profile_names(session)
        selected_keys = {normalize(profile) for profile in selected_profiles if normalize(profile)}
        selected_variable_keys = {
            normalize(str(profile.get("variable_name") or ""))
            for profile in self._stored_hazard_profiles(
                session,
                session.selected_hazard or session.accepted_custom_hazard or "",
            )
            if normalize(str(profile.get("variable_name") or ""))
        }
        if not selected_keys and not selected_variable_keys:
            return []

        rows = self.db.execute(
            select(
                SystemHazardSocioDemographic.id,
                SystemHazardSocioDemographic.profile,
                SystemHazardSocioDemographic.variable_name,
            ).where(SystemHazardSocioDemographic.system_hazard_id == system_hazard_id)
        ).all()

        profile_ids: list[int] = []
        seen: set[int] = set()
        for row in rows:
            row_id = int(row.id)
            row_keys = {
                normalize(str(row.profile or "")),
                normalize(str(row.variable_name or "")),
            }
            if row_keys & selected_keys or row_keys & selected_variable_keys:
                if row_id not in seen:
                    seen.add(row_id)
                    profile_ids.append(row_id)
        return profile_ids

    def _matched_mitigation_measure_examples(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)

        if not rows:
            return ""

        lines: list[str] = []
        for index, example in enumerate(rows, start=1):
            profile = str(example.profile_label or "Matched profile").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            evidence = str(example.evidence or "").strip()
            country = str(example.country_city or "").strip()
            case_study = str(example.policy_case_study or "").strip()
            reference_links = str(example.reference_links or "").strip()
            details: list[str] = [f"measure: {example.measure}"]
            if summary:
                details.append(f"implementation: {summary}")
            if evidence:
                details.append(f"evidence: {evidence}")
            if country:
                details.append(f"implemented country/city: {country}")
            if case_study or country:
                details.append(
                    f"case: {case_study}{f' ({country})' if country else ''}"
                )
            if reference_links:
                details.append(
                    "reference links: "
                    + self._format_mitigation_reference_links(reference_links)
                )
            lines.append(f"{index}. Profile '{profile}' - " + " | ".join(details))
        return "\n".join(lines)

    def _matched_mitigation_measure_example_rows(
        self, session: ChatSession, limit: int | None = None
    ) -> list[MitigationMeasureExample]:
        if session.sector_id is None:
            return []

        system_hazard_id = self._selected_system_hazard_id(session)

        filters = [
            MitigationMeasureExample.sector_id == session.sector_id,
            MitigationMeasureExample.source == "mm_csv",
        ]
        if system_hazard_id is not None:
            filters.append(MitigationMeasureExample.system_hazard_id == system_hazard_id)

        query = (
            select(MitigationMeasureExample)
            .where(*filters)
            .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = self.db.scalars(query).all()

        if not rows and system_hazard_id is not None:
            fallback_query = (
                select(MitigationMeasureExample)
                .where(
                    MitigationMeasureExample.sector_id == session.sector_id,
                    MitigationMeasureExample.source == "mm_csv",
                )
                .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
            )
            if limit is not None:
                fallback_query = fallback_query.limit(limit)
            rows = self.db.scalars(fallback_query).all()

        return list(rows)

    def _current_policy_implementations_section(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)
        heading = self._policy_section_heading(
            "Current Policy Implementations",
            self._current_policy_implementations_intro(),
        )
        if not rows:
            return (
                f"{heading}\n\n"
                "No matching current policy implementations were found for this "
                "sector, hazard, and profile set."
            )

        sections = [heading]
        grouped_examples: dict[str, dict[str, object]] = {}
        for example in rows:
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if not measure:
                continue
            measure_key = normalize_for_match(measure)
            if not measure_key:
                continue
            group = grouped_examples.setdefault(
                measure_key,
                {
                    "measure": measure,
                    "countries": [],
                    "summaries": [],
                    "evidence": [],
                    "reference_links": [],
                },
            )

            country = normalize_markdown_text(str(example.country_city or "")).strip()
            evidence = normalize_markdown_text(str(example.evidence or "")).strip()
            reference_links = str(example.reference_links or "").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            case_study = normalize_markdown_text(str(example.policy_case_study or "")).strip()
            summary_text = summary or case_study

            self._append_unique_text(group["countries"], country)
            self._append_unique_text(group["summaries"], summary_text)
            self._append_unique_text(group["evidence"], evidence)
            for link in self._mitigation_reference_link_values(reference_links):
                self._append_unique_text(group["reference_links"], link)

        for group in grouped_examples.values():
            measure = str(group["measure"])
            countries = group["countries"]
            summaries = group["summaries"]
            evidence_items = group["evidence"]
            reference_links = group["reference_links"]

            details: list[str] = []
            if countries:
                details.append(
                    "- **Implemented in:** " + "; ".join(str(item) for item in countries)
                )
            if summaries:
                details.append(
                    "- **Summary:** " + " ".join(str(item) for item in summaries)
                )
            if evidence_items:
                details.append(
                    "- **Evidence:** " + " ".join(str(item) for item in evidence_items)
                )
            if reference_links:
                details.append(
                    "- **Reference links:** "
                    + self._format_mitigation_reference_links("; ".join(str(item) for item in reference_links))
                )

            if not details:
                details.append("- No implementation details were provided for this example.")

            sections.append(f"### {measure}\n\n" + "\n".join(details))

        return "\n\n".join(sections)

    @staticmethod
    def _ensure_practical_considerations_intro(markdown: str) -> str:
        intro = (
            "This section translates the selected hazard and affected profiles into "
            "practical design considerations for mitigation. It highlights issues to "
            "check before choosing a measure, such as delivery barriers, targeting, "
            "and implementation risks."
        )
        heading = ChatService._policy_section_heading("Practical Considerations", intro)
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return heading
        cleaned = ChatService._strip_policy_section_heading(
            cleaned,
            "Practical Considerations",
        )
        cleaned = ChatService._strip_section_intro_paragraph(
            cleaned,
            (
                "practical design considerations",
                "delivery barriers",
                "implementation risks",
                "design trade-offs",
            ),
        )
        if not cleaned:
            return heading
        if cleaned.casefold().lstrip().startswith("## practical considerations"):
            cleaned = ChatService._strip_policy_section_heading(
                cleaned,
                "Practical Considerations",
            )
        return f"{heading}\n\n{cleaned}"

    @staticmethod
    def _current_policy_implementations_intro() -> str:
        return (
            "This section shows real policy implementations mitigating similar twin transition "
            "policy hazards, relevant to the selected sector and socio-demographic "
            "profiles. For each match, it summarizes where it has been implemented, "
            "the available evidence, and any reference links that support the example."
        )

    @staticmethod
    def _new_policy_proposals_intro() -> str:
        return (
            "New policy proposals created using the data collection from open labs. The open labs followed a structured co-creation process that began with identifying twin-transition challenges and mapping their systemic causes. Participants then envisioned a fair future transition, translated the required systemic changes into new or improved policy measures, and finally refined and evaluated each proposal for its impact, feasibility, and contribution to an inclusive twin transition."
        )

    @staticmethod
    def _new_policy_proposals_title() -> str:
        return "New policy proposals (Inspiration for the regional mitigation plans)"

    @staticmethod
    def _policy_section_heading(title: str, tooltip: str) -> str:
        safe_title = escape(str(title or "").strip())
        safe_tooltip = escape(str(tooltip or "").strip())
        return (
            f'<h2 class="policy-section-heading">{safe_title} '
            '<span class="policy-section-info" tabindex="0" '
            f'aria-label="{safe_tooltip}" title="{safe_tooltip}">'
            '<span aria-hidden="true">i</span>'
            f'<span class="policy-section-tooltip" aria-hidden="true">{safe_tooltip}</span>'
            "</span></h2>"
        )

    @staticmethod
    def _strip_policy_section_heading(markdown: str, title: str) -> str:
        title_key = normalize_for_match(title)
        kept: list[str] = []
        for line in str(markdown or "").splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip().strip("*_:- ")
            if normalize_for_match(heading_text) == title_key:
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _strip_section_intro_paragraph(markdown: str, markers: tuple[str, ...]) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
        first = parts[0].strip()
        first_key = first.casefold()
        if first and any(marker.casefold() in first_key for marker in markers):
            return parts[1].strip() if len(parts) > 1 else ""
        return cleaned

    @staticmethod
    def _extract_practical_consideration_items(markdown: str) -> list[str]:
        cleaned = str(markdown or "")
        cleaned = re.sub(
            r'<h[1-6][^>]*class="[^"]*\bpolicy-section-heading\b[^"]*"[^>]*>.*?</h[1-6]>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*\bpolicy-section-tooltip\b[^"]*"[^>]*>.*?</span>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = ChatService._strip_policy_section_heading(cleaned, "Practical Considerations")
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        items: list[str] = []
        current: list[str] = []

        def flush_current() -> None:
            if not current:
                return
            item = ChatService._clean_practical_consideration_item(" ".join(current))
            current.clear()
            if item and normalize_for_match(item) not in {
                normalize_for_match(existing) for existing in items
            }:
                items.append(item)

        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                flush_current()
                continue
            if re.match(r"^\s*#{1,6}\s+", line):
                flush_current()
                continue
            bullet_match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
            if bullet_match:
                flush_current()
                current.append(bullet_match.group(1).strip())
                continue
            if current:
                current.append(line)
            elif len(line) > 24:
                current.append(line)

        flush_current()
        return items

    @staticmethod
    def _clean_practical_consideration_item(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or ""))
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -•\t\r\n")
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations This section translates")
        ):
            return ""
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations i This section translates")
        ):
            return ""
        return cleaned

    async def _new_policy_suggestions_section(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> str:
        candidates = self._ranked_new_policy_suggestions(session, limit=limit)
        intro = self._new_policy_proposals_intro()
        heading = self._policy_section_heading(self._new_policy_proposals_title(), intro)
        if not candidates:
            return (
                f"{heading}\n\n"
                "No matching policy proposals were found for this country, sector, "
                "and selected hazard context."
            )

        candidate_context = self._new_policy_suggestion_context(candidates)
        current_policy_context = self._matched_mitigation_measure_examples(session, limit=5)
        context = """
You write one concise, evidence-conscious mitigation measure proposal for Dr Transition.
Use only the provided candidate policy context and current policy implementation
context. Do not invent external evidence, reference links, laws, budgets, or
country examples.

Scoring context:
- Target-group values: treat "Yes" as full target-group coverage and "Partially"
  as partial target-group coverage. Do not mention "New" or "PP" in target-group matches.
- Hazard mitigation effects rank as High mitigation > Medium mitigation > Low mitigation.
- Higher target-population overlap between selected system-hazard profiles and policy
  target groups should be described as a stronger fit.

Synthesis rule:
- Do not list separate policy candidates.
- Use the Top 3 / highest-scoring candidate policies supplied in the context. Prefer
  policies with High hazard mitigation effect and strong target-population overlap.
- Improve and combine those strongest MM policy proposals into ONE practical mitigation
  measure proposal that can inspire the user's own regional mitigation plan.
- Try to cover all relevant target groups mentioned in the supplied candidate context.
- If candidate policies overlap, merge the compatible actions and remove duplication.
- If candidate policies differ, combine only complementary elements that fit the
  selected hazard, affected profiles, and target population context.
- Explain the mitigation mechanism for each covered target group.
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Create the markdown body for the policy proposal section. "
                    "Do not include the section heading and do not include an "
                    "introductory paragraph; start directly with ONE synthesized "
                    "proposal.\n\n"
                    "Output exactly one proposal, 150-200 words total, using this structure:\n"
                    "### [short proposal title]\n"
                    "- **Proposal:** one clear, user-ready mitigation measure sentence "
                    "tailored to the selected country and region where possible.\n"
                    "- **Top policy basis:** mention that it combines the strongest/top-scored "
                    "MM policy proposals; name only the most relevant policy codes/titles.\n"
                    "- **Target-group mechanisms:** short bullets explaining how each covered "
                    "target group is mitigated.\n"
                    "- **Why this helps:** one short sentence linking the combined measure to "
                    "the selected hazard and high proposal scores.\n\n"
                    "Do not output multiple policy candidates. Do not include a score table. "
                    "Keep it concise and make the proposal sound like a single coherent "
                    "regional mitigation measure that inspires the user to create their own.\n\n"
                    f"Selected country: {session.country or 'Not specified'}\n"
                    f"Selected region: {session.region or 'Not specified'}\n"
                    f"Selected sector: {session.sector or 'Not specified'}\n"
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not specified'}\n"
                    f"Selected socio-demographic profiles:\n{format_all_dgs(session)}\n\n"
                    "Current policy implementation context:\n"
                    f"{current_policy_context or '- No matching current implementation context was found.'}\n\n"
                    f"Candidate policy context:\n{candidate_context}"
                ),
            }
        ]
        for attempt in range(2):
            attempt_messages = messages
            if attempt:
                retry_instruction = {
                    "role": "user",
                    "content": (
                        "Retry once. The previous response could not be used. "
                        "Return only the requested markdown body with the exact "
                        "proposal structure and no introductory paragraph."
                    ),
                }
                attempt_messages = [*messages, retry_instruction]
            response = await ask_llm_chat(
                context=context,
                messages=attempt_messages,
                temperature=0.2,
                max_tokens=1000,
            )
            if response and not is_llm_unavailable_response(response):
                cleaned = self._strip_new_policy_suggestions_heading(response)
                if cleaned:
                    ensured = self._ensure_new_policy_intro(cleaned)
                    if ensured:
                        return heading + "\n\n" + self._format_new_policy_proposal_body(ensured)

        return (
            f"{heading}\n\n"
            "I could not generate a reliable new policy proposal from the matched "
            "policy basis after retrying. Please try again, or continue by writing "
            "your own regional mitigation measure."
        )

    @staticmethod
    def _strip_new_policy_suggestions_heading(markdown: str) -> str:
        lines = []
        heading_keys = {
            normalize_for_match("new policy proposals"),
            normalize_for_match(ChatService._new_policy_proposals_title()),
        }
        for line in str(markdown or "").strip().splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            heading_text = heading_text.strip("*_:- ")
            if normalize_for_match(heading_text) in heading_keys:
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _ranked_new_policy_suggestions(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> list[dict[str, object]]:
        if session.country_id is None or session.sector_id is None:
            return []

        selected_system_hazard_id = self._selected_system_hazard_id(session)
        hazard_target_option_ids = self._selected_system_hazard_target_option_ids(
            session,
            selected_system_hazard_id,
        )

        query = (
            select(
                MitigationMeasurePolicy.id,
                MitigationMeasurePolicy.policy_code,
                MitigationMeasurePolicy.policy_title,
                MitigationMeasurePolicy.policy_type,
                MitigationMeasurePolicy.short_description,
                MitigationMeasurePolicySystemHazard.system_hazard_id,
                MitigationMeasurePolicySystemHazard.mitigation_effect,
                SystemHazard.name.label("hazard_name"),
            )
            .join(
                MitigationMeasurePolicySystemHazard,
                MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id
                == MitigationMeasurePolicy.id,
            )
            .join(
                SystemHazard,
                SystemHazard.id == MitigationMeasurePolicySystemHazard.system_hazard_id,
            )
            .where(
                MitigationMeasurePolicy.country_id == session.country_id,
                MitigationMeasurePolicy.sector_id == session.sector_id,
                MitigationMeasurePolicy.source == "xlsx",
            )
        )
        if selected_system_hazard_id is not None:
            query = query.where(
                MitigationMeasurePolicySystemHazard.system_hazard_id
                == selected_system_hazard_id
            )

        policy_rows = self.db.execute(query).mappings().all()
        if not policy_rows:
            return []

        policy_ids = [int(row["id"]) for row in policy_rows]
        target_rows = self.db.execute(
            select(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id.label("policy_id"),
                MitigationMeasureTargetGroup.question_option_id,
                MitigationMeasureTargetGroup.match_value,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id == MitigationMeasureTargetGroup.question_option_id,
            )
            .join(
                EvaluationQuestion,
                and_(
                    EvaluationQuestion.id == QuestionOption.question_id,
                    EvaluationQuestion.category == "target_population",
                ),
            )
            .where(MitigationMeasureTargetGroup.mitigation_measure_policy_id.in_(policy_ids))
            .order_by(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
        ).mappings().all()

        targets_by_policy: dict[int, list[dict[str, object]]] = {}
        for row in target_rows:
            policy_id = int(row["policy_id"])
            label = self._target_population_label(
                str(row["question"] or ""),
                str(row["option"] or ""),
            )
            targets_by_policy.setdefault(policy_id, []).append(
                {
                    "question_option_id": int(row["question_option_id"]),
                    "label": label,
                    "match_value": str(row["match_value"] or "").strip(),
                }
            )

        candidates: list[dict[str, object]] = []
        for row in policy_rows:
            policy_id = int(row["id"])
            target_groups = targets_by_policy.get(policy_id, [])
            score_details = self._new_policy_suggestion_score(
                mitigation_effect=str(row["mitigation_effect"] or ""),
                target_groups=target_groups,
                hazard_target_option_ids=hazard_target_option_ids,
            )
            if score_details["score"] <= 0:
                continue
            candidates.append(
                {
                    "policy_id": policy_id,
                    "policy_code": str(row["policy_code"] or ""),
                    "policy_title": normalize_markdown_text(str(row["policy_title"] or "")).strip(),
                    "policy_type": normalize_markdown_text(str(row["policy_type"] or "")).strip(),
                    "short_description": normalize_markdown_text(
                        str(row["short_description"] or "")
                    ).strip(),
                    "hazard_name": normalize_markdown_text(str(row["hazard_name"] or "")).strip(),
                    "mitigation_effect": str(row["mitigation_effect"] or "").strip(),
                    "target_groups": target_groups,
                    **score_details,
                }
            )

        candidates.sort(
            key=lambda candidate: (
                float(candidate.get("score") or 0),
                float(candidate.get("hazard_effect_score") or 0),
                float(candidate.get("target_match_score") or 0),
                str(candidate.get("policy_title") or ""),
            ),
            reverse=True,
        )
        return candidates[:limit]

    def _selected_system_hazard_target_option_ids(
        self,
        session: ChatSession,
        system_hazard_id: int | None,
    ) -> set[int]:
        if system_hazard_id is None:
            return self._selected_target_population_option_ids(session)

        profile_ids = self._selected_system_profile_ids(session, system_hazard_id)
        if not profile_ids:
            profile_ids = [
                int(row_id)
                for row_id in self.db.scalars(
                    select(SystemHazardSocioDemographic.id).where(
                        SystemHazardSocioDemographic.system_hazard_id
                        == system_hazard_id
                    )
                ).all()
            ]
        if not profile_ids:
            return self._selected_target_population_option_ids(session)

        option_ids = {
            int(option_id)
            for option_id in self.db.scalars(
                select(
                    SystemHazardSocioDemographicTargetPopulation.question_option_id
                ).where(
                    SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id.in_(
                        profile_ids
                    )
                )
            ).all()
        }
        return option_ids or self._selected_target_population_option_ids(session)

    def _selected_target_population_option_ids(self, session: ChatSession) -> set[int]:
        answer_pairs: set[tuple[str, str]] = set()
        for answer in session.target_population_answers or []:
            question = normalize_for_match(str(answer.get("question") or ""))
            selected = answer.get("selected")
            labels = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for label in labels:
                option = normalize_for_match(str(label or ""))
                if question and option:
                    answer_pairs.add((question, option))
        if not answer_pairs:
            return set()

        rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(EvaluationQuestion.category == "target_population")
        ).all()
        return {
            int(row.id)
            for row in rows
            if (
                normalize_for_match(str(row.question or "")),
                normalize_for_match(str(row.option or "")),
            )
            in answer_pairs
        }

    @staticmethod
    def _new_policy_suggestion_score(
        *,
        mitigation_effect: str,
        target_groups: list[dict[str, object]],
        hazard_target_option_ids: set[int],
    ) -> dict[str, object]:
        effect_key = normalize_for_match(mitigation_effect)
        hazard_effect_score = {
            "high mitigation": 60.0,
            "medium mitigation": 35.0,
            "low mitigation": 15.0,
        }.get(effect_key, 0.0)

        value_scores = {
            "yes": 12.0,
            "partially": 6.0,
        }
        matched_targets: list[dict[str, object]] = []
        target_match_score = 0.0
        if not hazard_target_option_ids:
            return {
                "score": round(hazard_effect_score, 2),
                "hazard_effect_score": hazard_effect_score,
                "target_match_score": 0.0,
                "matched_target_groups": matched_targets,
                "hazard_target_option_count": 0,
            }
        for group in target_groups:
            option_id = int(group.get("question_option_id") or 0)
            if option_id not in hazard_target_option_ids:
                continue
            value = str(group.get("match_value") or "").strip()
            value_score = value_scores.get(value.casefold(), 0.0)
            if value_score <= 0:
                continue
            matched_targets.append(group)
            target_match_score += value_score

        target_match_score = min(40.0, target_match_score)
        return {
            "score": round(hazard_effect_score + target_match_score, 2),
            "hazard_effect_score": hazard_effect_score,
            "target_match_score": round(target_match_score, 2),
            "matched_target_groups": matched_targets,
            "hazard_target_option_count": len(hazard_target_option_ids),
        }

    def _new_policy_suggestion_context(self, candidates: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            matched_targets = candidate.get("matched_target_groups")
            target_groups = candidate.get("target_groups")
            matched_labels = self._policy_target_group_summary(
                matched_targets if isinstance(matched_targets, list) else []
            )
            all_target_labels = self._policy_target_group_summary(
                target_groups if isinstance(target_groups, list) else []
            )
            lines.append(
                "\n".join(
                    [
                        f"{index}. Policy code: {candidate.get('policy_code')}",
                        f"   Title: {candidate.get('policy_title')}",
                        f"   Type: {candidate.get('policy_type') or 'Not specified'}",
                        f"   Description: {candidate.get('short_description') or 'Not specified'}",
                        f"   Related system hazard: {candidate.get('hazard_name')}",
                        f"   Hazard mitigation effect: {candidate.get('mitigation_effect')}",
                        f"   Matched target groups: {matched_labels or 'None'}",
                        f"   All policy target groups: {all_target_labels or 'None'}",
                        (
                            f"   Score: {candidate.get('score')}/100 "
                            f"(hazard effect {candidate.get('hazard_effect_score')}, "
                            f"target match {candidate.get('target_match_score')})"
                        ),
                    ]
                )
            )
        return "\n\n".join(lines)

    def _fallback_new_policy_suggestions_section(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> str:
        sections = [
            self._policy_section_heading(
                self._new_policy_proposals_title(),
                self._new_policy_proposals_intro(),
            )
        ]
        top_candidate = candidates[0]
        matched_targets: list[dict[str, object]] = []
        all_targets: list[dict[str, object]] = []
        source_policy_lines: list[str] = []
        action_parts: list[str] = []
        for candidate in candidates:
            candidate_targets = candidate.get("matched_target_groups")
            if isinstance(candidate_targets, list):
                matched_targets.extend(candidate_targets)
            candidate_all_targets = candidate.get("target_groups")
            if isinstance(candidate_all_targets, list):
                all_targets.extend(candidate_all_targets)
            title = str(candidate.get("policy_title") or "Untitled policy").strip()
            code = str(candidate.get("policy_code") or "Policy").strip()
            effect = str(candidate.get("mitigation_effect") or "relevant mitigation effect").strip()
            description = str(candidate.get("short_description") or "").strip()
            source_policy_lines.append(
                f"{code}: {title}"
                + (f" ({effect})" if effect else "")
            )
            if description:
                self._append_unique_text(action_parts, description)

        target_groups = matched_targets or all_targets
        target_mechanisms = self._fallback_target_group_mechanisms(target_groups)
        policy_basis = "; ".join(source_policy_lines[:3])
        hazard_name = str(top_candidate.get("hazard_name") or "the selected hazard").strip()
        effect_label = str(top_candidate.get("mitigation_effect") or "relevant").strip()
        action_summary = (
            action_parts[:3]
            if action_parts
            else [f"Adapt the strongest scored policy actions to reduce {hazard_name.lower()}."]
        )
        body = (
            "### Integrated Regional Mitigation Support Package\n\n"
            f"- **Proposal:** In {self._session_place_label_for_sentence(session)}, "
            f"combine the top-scored MM policy proposals into a regional support package "
            f"that reduces **{hazard_name}** through targeted assistance, delivery "
            "guidance, and safeguards for affected groups.\n"
            f"- **Top policy basis:** {policy_basis or 'Top-ranked MM policy proposals'}.\n"
            "- **Target-group mechanisms:**\n"
            + "\n".join(f"    - {item}" for item in target_mechanisms)
            + "\n"
            f"- **Why this helps:** The proposal is a strong inspiration because its source "
            f"policies have **{effect_label}** mitigation relevance and combine complementary "
            "actions: "
            + "; ".join(action_summary[:2])
            + "."
        )
        sections.append(self._format_new_policy_proposal_body(body))
        return "\n\n".join(sections)

    @staticmethod
    def _session_place_label_for_sentence(session: ChatSession) -> str:
        region = str(session.region or "").strip()
        country = str(session.country or "").strip()
        if region and country:
            return f"{region}, {country}"
        return region or country or "the selected region"

    def _fallback_target_group_mechanisms(
        self,
        target_groups: list[dict[str, object]],
        *,
        limit: int = 5,
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip().casefold()
            if not label or value == "pp":
                continue
            key = normalize_for_match(label)
            if key and key not in seen:
                seen.add(key)
                labels.append(label)
        if not labels:
            return [
                "Affected profiles — use the selected hazard profiles to target support and prevent exclusion."
            ]
        return [
            f"{label} — receives tailored support, reduced exposure to the hazard, and clearer access to the measure."
            for label in labels[:limit]
        ]

    @staticmethod
    def _ensure_new_policy_intro(markdown: str) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        cleaned = ChatService._strip_section_intro_paragraph(
            cleaned,
            (
                "candidate policies",
                "hazard mitigation effect",
                "target-group overlap",
                "policy database",
            ),
        )
        return cleaned

    @staticmethod
    def _format_new_policy_proposal_body(markdown: str) -> str:
        cleaned = ChatService._normalize_target_group_mechanism_indentation(markdown)
        return ChatService._append_top_policy_basis_to_proposal(cleaned)

    @staticmethod
    def _normalize_target_group_mechanism_indentation(markdown: str) -> str:
        lines: list[str] = []
        in_target_group_block = False
        for raw_line in str(markdown or "").splitlines():
            line = raw_line.rstrip()
            section_key = normalize_for_match(line)
            if "target group mechanisms" in section_key:
                in_target_group_block = True
                lines.append(line)
                continue
            if in_target_group_block and re.match(r"^\s*[-*]\s+\*\*(?:why this helps|proposal|top policy basis)\s*:", line, flags=re.IGNORECASE):
                in_target_group_block = False
            if in_target_group_block and re.match(r"^\s{0,3}[-*]\s+", line):
                lines.append("    " + line.lstrip())
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _append_top_policy_basis_to_proposal(markdown: str) -> str:
        text = str(markdown or "").strip()
        basis_match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Top policy basis:\*\*\s*(?P<basis>.+?)\s*$",
            text,
        )
        if not basis_match:
            return text

        basis = ChatService._clean_policy_basis_source(basis_match.group("basis"))
        text_without_basis = (
            text[: basis_match.start()] + text[basis_match.end() :]
        ).strip()
        if not basis:
            return re.sub(r"\n{3,}", "\n\n", text_without_basis)

        def append_source(match: re.Match[str]) -> str:
            proposal = match.group("proposal").rstrip()
            proposal = ChatService._strip_policy_source_reference(proposal)
            safe_basis = escape(basis)
            return (
                f"{match.group('prefix')}{proposal} "
                '<span class="policy-section-info proposal-source-info" '
                f'tabindex="0" aria-label="{safe_basis}" title="{safe_basis}">'
                '<span aria-hidden="true">i</span>'
                f'<span class="policy-section-tooltip" aria-hidden="true">{safe_basis}</span>'
                "</span>"
            )

        updated, count = re.subn(
            r"(?im)^(?P<prefix>\s*[-*]\s*\*\*Proposal:\*\*\s*)(?P<proposal>.+?)\s*$",
            append_source,
            text_without_basis,
            count=1,
        )
        return re.sub(r"\n{3,}", "\n\n", updated if count else text_without_basis).strip()

    @staticmethod
    def _clean_policy_basis_source(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or "")).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;")
        return cleaned

    @staticmethod
    def _strip_policy_source_reference(value: str) -> str:
        return re.sub(
            r"\s*\[(?:Source|Sources):\s*[^\]]+\]\s*$",
            "",
            str(value or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    @staticmethod
    def _extract_suggested_policy_proposal(markdown: str) -> str:
        text = str(markdown or "")
        title = ""
        title_match = re.search(r"(?im)^\s*###\s+(.+?)\s*$", text)
        if title_match:
            title = normalize_markdown_text(title_match.group(1))
            title = re.sub(r"\*\*(.*?)\*\*", r"\1", title)
            title = re.sub(r"\*(.*?)\*", r"\1", title)
            title = re.sub(r"\s+", " ", title).strip()
        match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Proposal:\*\*\s*(.+?)\s*$",
            text,
        )
        if not match:
            match = re.search(r"(?im)^\s*[-*]\s*Proposal:\s*(.+?)\s*$", text)
        if not match:
            return ""
        proposal = normalize_markdown_text(match.group(1))
        proposal = re.sub(r"\*\*(.*?)\*\*", r"\1", proposal)
        proposal = re.sub(r"\*(.*?)\*", r"\1", proposal)
        proposal = ChatService._strip_policy_source_reference(proposal)
        proposal = re.sub(r"\s+", " ", proposal).strip()
        if title and proposal and not normalize_for_match(proposal).startswith(
            normalize_for_match(title)
        ):
            return f"{title}: {proposal}"
        return proposal

    @staticmethod
    def _policy_target_group_summary(target_groups: list[dict[str, object]]) -> str:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip()
            if value.casefold() == "pp":
                continue
            if not label:
                continue
            rendered = f"{label} ({value})" if value else label
            key = normalize_for_match(rendered)
            if key and key not in seen:
                seen.add(key)
                labels.append(rendered)
        return "; ".join(labels)

    @staticmethod
    def _target_population_label(question: str, option: str) -> str:
        question = str(question or "").strip()
        option = str(option or "").strip()
        if question and option:
            return f"{question}: {option}"
        return question or option

    def _current_policy_mitigation_measure(self, session: ChatSession) -> str:
        for example in self._matched_mitigation_measure_example_rows(session, limit=None):
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if measure:
                return measure
        return ""

    @staticmethod
    def _append_unique_text(items: object, value: str) -> None:
        if not isinstance(items, list):
            return
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return
        if normalize_for_match(cleaned) not in {normalize_for_match(str(item)) for item in items}:
            items.append(cleaned)

    @staticmethod
    def _mitigation_reference_link_values(reference_links: str) -> list[str]:
        links = re.findall(r"https?://[^\s;,]+", reference_links)
        if links:
            return links
        cleaned = re.sub(r"\s+", " ", reference_links or "").strip()
        return [cleaned] if cleaned else []

    @staticmethod
    def _simplify_mitigation_implementation_summary(summary: str) -> str:
        cleaned = normalize_markdown_text(str(summary or "")).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(
            r'(?i)^for\s+the\s+profile\s+["“][^"”]+["”]\s*,?\s*',
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)^for\s+the\s+profile\s+[^,]+,\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""
        return cleaned[:1].upper() + cleaned[1:]

    @staticmethod
    def _format_mitigation_reference_links(reference_links: str) -> str:
        links = re.findall(r"https?://[^\s;,]+", reference_links)
        if not links:
            return reference_links.strip()
        return "; ".join(f"[Reference {index}]({link})" for index, link in enumerate(links, start=1))

    def _local_mitigation_measure_error(self, mitigation_measure: str) -> str | None:
        if self._is_invalid_user_text(mitigation_measure):
            return (
                "The mitigation measure appears to contain gibberish, keyboard mashing, "
                "or text that is not meaningful. Please rewrite it as a clear policy action."
            )
        if len(compact_for_match(mitigation_measure)) < 8:
            return "The mitigation measure is too short. Please write a clearer policy action."
        return None

    def _local_mitigation_field_error(self, mitigation_measure: str, reason: str) -> str | None:
        if self._is_invalid_user_text(mitigation_measure):
            return (
                "The mitigation measure appears to contain gibberish, keyboard mashing, "
                "or text that is not meaningful. Please rewrite it as a clear policy action."
            )
        if self._is_invalid_user_text(reason):
            return (
                "The reason appears to contain gibberish, keyboard mashing, or text that "
                "is not meaningful. Please explain why this measure would reduce the "
                "selected hazard for the affected groups."
            )
        if len(compact_for_match(mitigation_measure)) < 8:
            return "The mitigation measure is too short. Please write a clearer policy action."
        if len(compact_for_match(reason)) < 8:
            return "The reason is too short. Please explain the mechanism in a little more detail."
        return None

    def _existing_mitigation_records_for_selected_hazard(
        self, session: ChatSession
    ) -> list[UserMitigationMeasure]:
        hazard_name = session.selected_hazard or session.accepted_custom_hazard
        if not hazard_name:
            return []
        try:
            query = (
                select(UserMitigationMeasure)
                .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserHazard.name == hazard_name,
                    UserHazard.sector_id == session.sector_id,
                    UserSession.country_id == session.country_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                )
                .order_by(UserMitigationMeasure.id)
            )
            if self.user_id is not None:
                query = query.where(UserSession.user_id == self.user_id)
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load mitigation measures for duplicate check")
            return []
        records: list[UserMitigationMeasure] = []
        seen: set[str] = set()
        for row in rows:
            measure = str(row.measure or "").strip()
            key = normalize(measure)
            if not measure or key in seen:
                continue
            seen.add(key)
            records.append(row)
        return records

    def _existing_mitigation_measures_for_selected_hazard(
        self, session: ChatSession
    ) -> list[str]:
        return [
            record.measure
            for record in self._existing_mitigation_records_for_selected_hazard(session)
            if str(record.measure or "").strip()
        ]

    def _local_mitigation_duplicate_check(
        self, session: ChatSession, mitigation_measure: str
    ) -> dict[str, object] | None:
        for existing in self._existing_mitigation_records_for_selected_hazard(session):
            if self._mitigations_are_similar(mitigation_measure, existing.measure):
                return {
                    "duplicate": True,
                    "match": existing.measure,
                    "match_id": existing.id,
                    "reason": "The proposed measure is the same as, or very similar to, an existing mitigation measure for this hazard.",
                    "duplicates": [],
                }
        return None

    @staticmethod
    def _mitigations_are_similar(left: str, right: str) -> bool:
        left_key = normalize_for_match(left)
        right_key = normalize_for_match(right)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        left_compact = compact_for_match(left)
        right_compact = compact_for_match(right)
        if left_compact in right_compact or right_compact in left_compact:
            return True
        left_words = ChatService._hazard_similarity_words(left_key)
        right_words = ChatService._hazard_similarity_words(right_key)
        overlap = len(left_words & right_words)
        smaller_overlap = overlap / max(1, min(len(left_words), len(right_words)))
        larger_overlap = overlap / max(1, max(len(left_words), len(right_words)))
        return smaller_overlap >= 0.8 or (smaller_overlap >= 0.65 and larger_overlap >= 0.45)

    async def _semantic_mitigation_duplicate_check(
        self, session: ChatSession, mitigation_measure: str
    ) -> dict[str, object] | None:
        existing_measures = self._existing_mitigation_measures_for_selected_hazard(session)
        if not existing_measures:
            return {"duplicate": False, "match": "", "reason": "", "duplicates": []}

        context = f"""
You are a strict semantic duplicate checker for Dr Transition.

Your job is to decide whether a proposed mitigation measure is already covered
by an existing mitigation measure for the SAME selected hazard, even when the
wording, grammar, or language differs.

{self._scope_instruction(session)}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Compare the proposed mitigation measure with existing mitigation "
                    "measures for the selected hazard only.\n\n"
                    "Treat it as duplicate when it has the same meaning, same policy "
                    "action, a close paraphrase, or a narrower/restated version that "
                    "is already clearly covered. Do not mark it duplicate merely "
                    "because it is in the same broad policy area.\n\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n\n"
                    "Existing mitigation measures:\n"
                    + "\n".join(f"- {item}" for item in existing_measures)
                    + "\n\n"
                    f"Proposed mitigation measure: {mitigation_measure}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"duplicate": false, "match": "", "reason": "short explanation"}\n\n'
                    "If duplicate is true, match must be the closest existing measure. "
                    "Keep reason under 40 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=350,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        return parsed

    @staticmethod
    def _format_mitigation_duplicate_reason(duplicate_check: dict[str, object]) -> str:
        match = str(duplicate_check.get("match") or "").strip()
        reason = str(duplicate_check.get("reason") or "").strip()
        if match and reason:
            return f"This mitigation measure appears to duplicate **{match}**: {reason}"
        if match:
            return f"This mitigation measure appears to duplicate **{match}**."
        return reason or "This mitigation measure appears to duplicate an existing measure for this hazard."

    def _duplicate_mitigation_match_id(
        self, session: ChatSession, duplicate_check: dict[str, object]
    ) -> int | None:
        try:
            match_id = int(duplicate_check.get("match_id"))
        except (TypeError, ValueError):
            match_id = None
        if match_id is not None:
            return match_id
        record = self._mitigation_record_for_match(session, str(duplicate_check.get("match") or ""))
        return record.id if record else None

    def _suggested_mitigation_record(self, session: ChatSession) -> UserMitigationMeasure | None:
        if session.suggested_mitigation_measure_id is None:
            return None
        try:
            return self.db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == session.suggested_mitigation_measure_id,
                )
            )
        except Exception:
            logger.exception("Failed to load suggested mitigation measure")
            return None

    def _suggested_mitigation_reason(self, session: ChatSession) -> str:
        record = self._suggested_mitigation_record(session)
        if record is None:
            return "No saved reason was found for this mitigation measure."
        return record.reason or "No saved reason was found for this mitigation measure."

    def _suggested_mitigation_evaluation_report(self, session: ChatSession) -> str:
        if session.suggested_mitigation_measure_id is None:
            return "- No evaluation report was found for this mitigation measure."
        try:
            rows = self.db.execute(
                select(
                    UserQuestionResponse.category,
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                    UserQuestionResponse.score,
                    UserQuestionResponse.reason,
                    UserQuestionResponse.evidence,
                )
                .outerjoin(
                    EvaluationQuestion,
                    EvaluationQuestion.id == UserQuestionResponse.question_id,
                )
                .where(
                    UserQuestionResponse.mitigation_measure_id
                    == session.suggested_mitigation_measure_id
                )
                .order_by(UserQuestionResponse.id)
            ).all()
        except Exception:
            logger.exception("Failed to load suggested mitigation evaluation report")
            return "- No evaluation report was found for this mitigation measure."

        if not rows:
            return "- No evaluation report was found for this mitigation measure."

        lines: list[str] = []
        for category, question, response_text, score, reason, evidence in rows:
            category_label = str(category or "Evaluation")
            question_label = normalize_markdown_text(str(question or category_label))
            lines.append(f"- **{category_label}: {question_label}**")
            if score is not None:
                lines.append(f"  - Score: **{score} / 10**")
            elif response_text:
                lines.append(f"  - Response: {response_text}")
            if reason:
                lines.append(f"  - Reason: {reason}")
            if evidence:
                lines.append(f"  - Evidence: {evidence}")
        return "\n".join(lines)

    async def _validate_evaluation_answer_against_stats(
        self,
        session: ChatSession,
        question: dict[str, str | int],
        score: int,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} {session.mitigation_measure or ''} "
                f"{question['question']} {reason} {evidence}"
            ),
        )
        knowledge_context = await self._mitigation_knowledge_context(
            session,
            session.mitigation_measure or "",
            f"{question['question']} {reason} {evidence}",
        )
        context = f"""
You are a strict validation assistant for Dr Transition.

{self._scope_instruction(session)}

Use these retrieved sector-prompt excerpts as the authoritative statistical source:
{sector_context}

Use these retrieved knowledge-base excerpts when relevant:
{knowledge_context or "- No relevant knowledge-base excerpts were found."}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate the user's evaluation answer. Check the reason if provided. "
                    "Check the evidence if provided. If the evidence came from a URL or "
                    "uploaded file, use the extracted evidence text below rather than only "
                    "the URL or filename. Use both the statistical context and the "
                    "retrieved knowledge-base excerpts.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n"
                    "Selected target populations/groups:\n"
                    f"{self._mitigation_target_population_text(session)}\n"
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Mitigation measure: {session.mitigation_measure or 'Not provided'}\n"
                    f"Mitigation reason: {session.mitigation_reason or 'Not provided'}\n\n"
                    f"Question category: {question['category']}\n"
                    f"Question: {question['question']}\n"
                    f"Score: {score}/10\n"
                    f"Reason provided by user: {reason or 'Not provided'}\n"
                    f"Evidence extracted/provided: {evidence or 'Not provided'}\n\n"
                    "Evaluation dimensions:\n"
                    "1. Relevance:\n"
                    "- Does the mitigation measure address the evaluation question?\n"
                    "- Does the user's reason explain the selected score in relation to "
                    "the question?\n\n"
                    "2. Evidence Quality:\n"
                    "- Does the evidence clearly support the claims made?\n"
                    "- Is it specific, credible, and relevant to the selected country, "
                    "sector, hazard, mitigation measure, and question?\n\n"
                    "Scoring rules, combining BOTH dimensions:\n"
                    "- 1-3: Not relevant AND/OR no meaningful evidence.\n"
                    "- 4-6: Partially relevant OR weak/generic evidence.\n"
                    "- 7-8: Relevant with reasonably supportive evidence.\n"
                    "- 9-10: Strong alignment AND strong, clear, convincing evidence.\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "short validation explanation"}\n\n'
                    "Rules:\n"
                    "- valid must be true only when the mitigation measure, user reason, "
                    "any supplied evidence, and selected score are mutually consistent.\n"
                    "- If a reason is provided, validate that it is meaningful, relevant "
                    "to the question, and consistent with the selected score.\n"
                    "- If evidence is provided, validate that the extracted/provided "
                    "evidence supports the user's claims. A bare URL or filename without "
                    "readable extracted content is not meaningful evidence.\n"
                    "- valid must be false if the reason or evidence is unrelated to the "
                    "question, contradicts the sector context or retrieved knowledge, "
                    "invents unsupported facts, is unsupported by the extracted evidence, "
                    "or does not justify the chosen score under the scoring rules.\n"
                    "- valid may be true when no reason/evidence is provided only if the "
                    "score can stand as a simple answer for this optional field.\n"
                    "- The reason field must tell the user what to revise and stay under 80 words."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        return parse_validation_response(response)

    def _match_country(self, message: str) -> Country | None:
        countries = self.db.scalars(
            select(Country).options(selectinload(Country.regions)).order_by(Country.name)
        ).all()
        return self._match_by_id_or_name(list(countries), message)

    def _match_region(self, message: str, country_id: int | None) -> Region | None:
        if country_id is None:
            return None
        regions = self.db.scalars(
            select(Region).where(Region.country_id == country_id).order_by(Region.name)
        ).all()
        return self._match_by_id_or_name(list(regions), message)

    def _match_sector(self, message: str, country_id: int | None) -> Sector | None:
        return self._match_by_id_or_name(self._sectors_for_country(country_id), message)

    def _sectors_for_country(self, country_id: int | None) -> list[Sector]:
        if country_id is None:
            return []
        country = self.db.scalar(
            select(Country)
            .where(Country.id == country_id)
            .options(selectinload(Country.sectors))
        )
        if country is None:
            return []
        return sorted(country.sectors, key=lambda sector: sector.name)

    @staticmethod
    def _match_by_id_or_name(rows: list[Country] | list[Region] | list[Sector], message: str):
        normalized = normalize(message)
        for row in rows:
            if str(row.id) == message.strip() or normalize(row.name) == normalized:
                return row
        return None

    @staticmethod
    def _fuzzy_row_by_name(rows: list[Country] | list[Region] | list[Sector], message: str):
        fuzzy_name = best_fuzzy_label(message, [row.name for row in rows])
        if fuzzy_name is None:
            return None
        for row in rows:
            if row.name == fuzzy_name:
                return row
        return None

    def _could_be_fuzzy_selection(self, session: ChatSession, message: str) -> bool:
        labels: list[str] = []
        if session.country is None:
            labels = [
                country.name
                for country in self.db.scalars(select(Country).order_by(Country.name)).all()
            ]
        elif session.region is None:
            labels = [
                region.name
                for region in self.db.scalars(
                    select(Region)
                    .where(Region.country_id == session.country_id)
                    .order_by(Region.name)
                ).all()
            ]
        elif session.sector is None:
            labels = [sector.name for sector in self._sectors_for_country(session.country_id)]
        elif session.phase == "hazards":
            labels = [option.label for option in POST_SECTOR_OPTIONS]
        elif session.phase == "stats_deep_dive":
            labels = [option.label for option in STATS_DEEP_DIVE_OPTIONS]
        elif session.phase == "hazard_profile_selection":
            labels = hazard_names(session)
        elif session.phase == "socio_demographic_review":
            labels = [option.label for option in SOCIO_DEMOGRAPHIC_OPTIONS]
        elif session.phase == "reason_confirmation":
            labels = [option.label for option in REASON_CONFIRMATION_OPTIONS]
        elif session.phase == "mitigation_review":
            labels = [option.label for option in MITIGATION_REVIEW_OPTIONS]

        labels.extend(self._other_nav_options(session, self._current_step(session)))
        return bool(labels and best_fuzzy_label(message, labels) is not None)

    def _fields_are_locally_meaningful(self, fields: dict[str, str]) -> bool:
        if not fields:
            return False
        for value in fields.values():
            if self._is_invalid_user_text(value) or len(compact_for_match(value)) < 4:
                return False
        return True

    @staticmethod
    def _is_style_only_validation_rejection(reason: str) -> bool:
        lowered = reason.casefold()
        style_terms = (
            "grammar",
            "grammatical",
            "spelling",
            "punctuation",
            "capitalization",
            "capitalisation",
            "typo",
            "wording",
            "style",
        )
        meaning_terms = (
            "gibberish",
            "keyboard",
            "random",
            "unrecognizable",
            "unrecognisable",
            "meaningless",
            "too short",
            "ambiguous",
            "incomplete",
            "unrelated",
            "unsupported",
            "vague",
        )
        return any(term in lowered for term in style_terms) and not any(
            term in lowered for term in meaning_terms
        )

    @staticmethod
    def _is_invalid_user_text(message: str) -> bool:
        value = message.strip()
        if not value:
            return False
        if value.startswith(("TARGET_POPULATION_BATCH:", "http://", "https://")):
            return False
        if value.isdigit():
            return False

        normalized = normalize_for_match(value)
        compact = compact_for_match(value)
        if len(compact) < 2:
            return True
        if not re.search(r"[a-z0-9]", compact):
            return True

        total_chars = len(value)
        alnum_chars = sum(1 for char in value if char.isalnum())
        if total_chars >= 5 and alnum_chars / total_chars < 0.45:
            return True
        if re.search(r"(.)\1{5,}", compact):
            return True

        tokens = normalized.split()
        if not tokens:
            return True
        keyboard_rows = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")
        for token in tokens:
            if len(token) >= 4 and any(token in row or token[::-1] in row for row in keyboard_rows):
                return True

        long_tokens = [token for token in tokens if len(token) >= 4]
        if long_tokens and all(not re.search(r"[aeiou]", token) for token in long_tokens):
            return True

        unique_chars = set(compact)
        if len(compact) >= 8 and len(unique_chars) <= 2:
            return True

        return False
