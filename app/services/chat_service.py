import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape

from app.llm import ask_llm_chat
from app.config import get_settings
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    Country,
    CustomHazard,
    CustomHazardProfile,
    EvaluationQuestion,
    MitigationMeasureExample,
    MitigationMeasurePolicy,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
    KnowledgeChunk,
    KnowledgeDocument,
    QuestionOption,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
    SystemHazardSocioDemographicTargetPopulation,
    EurostatPopulationCache,
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
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_hazard_steps import ChatHazardStepsMixin
from app.services.chat_custom_hazard_population_steps import (
    ChatCustomHazardPopulationStepsMixin,
)
from app.services.chat_auto_user import ChatAutoUserMixin
from app.services.chat_json import (
    extract_json_array as extract_json_array_text,
    extract_json_object as extract_json_object_text,
)
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_navigation_steps import ChatNavigationStepsMixin
from app.services.chat_options import (
    ADD_DGS_OPTIONS,
    DG_REASON_EVIDENCE_OPTIONS,
    EVALUATION_CATEGORIES,
    FUZZY_CONFIRMATION_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    HAZARD_DUPLICATE_OPTIONS,
    HAZARD_POPULATION_REVIEW_OPTIONS,
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
    parse_llm_hazard_list,
    parse_mitigation_reason,
    parse_mitigation_clarity_response,
    parse_reason_evidence,
    parse_validation_response,
)
from app.services.chat_persistence import ChatPersistenceMixin
from app.services.chat_session import ChatSession, session_store
from app.services.custom_hazard_validation import (
    build_custom_hazard_grounding_status,
    custom_hazard_validation_details,
    default_custom_hazard_state,
    frontend_custom_hazard_payload,
    normalize_custom_group,
    validate_custom_hazard_dimensions,
)
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.chat_population_edits import (
    clean_affected_group_label,
    clean_population_edit_items,
    fallback_population_edits,
    parse_custom_affected_group_edit_message,
    split_affected_group_labels,
)
from app.services.chat_profile_rendering import ChatProfileRenderingMixin
from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.knowledge_base import KnowledgeBaseService
from app.services.eurostat_service import EurostatService
from app.services.grounding_models import GroundingModelService
from app.services.hazard_ranking_service import HazardRankingService, slugify_hazard
from app.services.message_renderer import markdown_to_html, render_message
from app.services.profile_metadata import compact_profile_metadata
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger(__name__)


class ChatService(
    ChatAutoUserMixin,
    ChatCustomHazardPopulationStepsMixin,
    ChatHazardStepsMixin,
    ChatMitigationStepsMixin,
    ChatNavigationStepsMixin,
    ChatPersistenceMixin,
    ChatProfileRenderingMixin,
    ChatSelectionStepsMixin,
):
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

        if session.phase in {"add_hazard", "custom_hazard_input"}:
            return await self._capture_custom_hazard(
                current_session_id, session, clean_message
            )

        if session.phase in {"add_hazard_clarification", "custom_hazard_clarification"}:
            return await self._handle_custom_hazard_clarification(
                current_session_id, session, clean_message
            )

        if session.phase == "custom_hazard_dimension_check":
            return await self._run_custom_hazard_dimension_check(
                current_session_id, session
            )

        if session.phase in {"add_hazard_evidence", "custom_hazard_validation"}:
            return await self._validate_custom_hazard(current_session_id, session, clean_message)

        if session.phase in {"hazard_duplicate_suggestion", "custom_hazard_duplicate_confirmation"}:
            return await self._handle_hazard_duplicate_suggestion(
                current_session_id, session, clean_message
            )

        if session.phase == "target_population_question":
            return await self._handle_target_population_answer(
                current_session_id, session, clean_message
            )

        if session.phase in {"custom_hazard_population_review", "custom_hazard_group_review", "custom_hazard_profile_reason"}:
            return await self._handle_custom_hazard_population_review(
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

    def _primary_other_nav_options(self, session: ChatSession, step: str) -> list[Option]:
        return [
            Option(id=index, label=label)
            for index, label in enumerate(self._other_nav_options(session, step), start=1)
        ]

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
        context = load_nested_prompt_file("llm/other_actions_navigation.txt")
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
            selected_hazard=session.selected_hazard or session.accepted_custom_hazard,
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
        if not session.mitigation_target_population:
            return self._mitigation_target_population_clarification_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
                error_reason=(
                    "I could not identify a specific target population. Please name a "
                    "concrete group, such as low-income households, rural residents, "
                    "tenants, older adults, or another affected group."
                ),
            )
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
        selected_hazard: str | None = None,
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

        if selected_hazard:
            questions = [
                question
                for question in questions
                if not cls._asks_for_already_selected_hazard(question)
            ]

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

    @staticmethod
    def _asks_for_already_selected_hazard(question: str) -> bool:
        normalized = normalize_for_match(question)
        asks_for_hazard = any(
            phrase in normalized
            for phrase in (
                "what specific hazard",
                "which hazard",
                "what hazard",
                "what specific risk",
                "which risk",
                "what risk",
                "what problem",
                "which problem",
            )
        )
        mitigation_target = any(
            phrase in normalized
            for phrase in (
                "aiming to mitigate",
                "intended to mitigate",
                "trying to mitigate",
                "seeking to mitigate",
                "measure address",
                "measure mitigate",
            )
        )
        return asks_for_hazard and mitigation_target

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
        if ChatService._has_evidence_url_reference(clean_evidence):
            lines = [
                line
                for line in clean_evidence.splitlines()
                if not (
                    line.strip().casefold().startswith("evidence content:")
                    and "unable to extract evidence" in line.casefold()
                )
            ]
            return "\n".join(lines).strip()
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
        hazard_reference = self._selected_hazard_reference(session_id, session)
        session.mitigation_record_id = self._store_mitigation_measure(
            user_session_id=hazard_reference["user_session_id"],
            user_hazard_id=hazard_reference["user_hazard_id"],
            custom_hazard_id=hazard_reference["custom_hazard_id"],
            system_hazard_id=hazard_reference["system_hazard_id"],
            additional_hazard_id=hazard_reference["additional_hazard_id"],
            mitigation_measure=session.mitigation_measure or "",
            reason=session.mitigation_reason or "",
            target_population=session.mitigation_target_population,
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
        context = render_prompt_template("llm/mitigation_target_population_extraction.txt")
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
        rows_by_id = {int(row.id): row for row in rows}
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
                    if (
                        option_id in allowed_ids
                        and self._target_population_option_is_supported_by_text(
                            answer,
                            rows_by_id[option_id],
                        )
                    ):
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
            "people",
            "persons",
            "person",
            "citizens",
            "citizen",
            "communities",
            "community",
            "households",
            "household",
            "residents",
            "resident",
            "users",
            "user",
            "consumers",
            "consumer",
            "population",
            "generalpopulation",
            "public",
            "family",
            "families",
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
        if not ChatService._has_specific_target_population_qualifier(cleaned):
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))

    @staticmethod
    def _target_population_phrase_map() -> dict[tuple[str, str], tuple[str, ...]]:
        return {
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

    @staticmethod
    def _has_specific_target_population_qualifier(group: str) -> bool:
        normalized = normalize_for_match(group)
        compact = compact_for_match(group)
        generic_terms = {
            "people",
            "persons",
            "person",
            "citizens",
            "citizen",
            "communities",
            "community",
            "households",
            "household",
            "residents",
            "resident",
            "users",
            "user",
            "consumers",
            "consumer",
            "population",
            "public",
            "family",
            "families",
        }
        has_generic_term = any(
            f" {normalize_for_match(term)} " in f" {normalized} "
            for term in generic_terms
        )
        if not has_generic_term:
            return True

        specific_qualifiers = (
            "low income",
            "middle income",
            "medium income",
            "high income",
            "income poor",
            "energy poor",
            "fuel poor",
            "financially vulnerable",
            "vulnerable",
            "poor",
            "rural",
            "urban",
            "suburban",
            "remote",
            "elderly",
            "older",
            "senior",
            "young",
            "youth",
            "children",
            "disabled",
            "disability",
            "long term condition",
            "tenant",
            "renter",
            "renting",
            "homeowner",
            "owner occupier",
            "unemployed",
            "retired",
            "worker",
            "employed",
            "carer",
            "caregiver",
            "migrant",
            "non eu",
            "women",
            "woman",
            "female",
            "men",
            "male",
            "small business",
            "sme",
            "utility arrears",
            "car dependent",
            "low energy efficiency",
            "poorly insulated",
            "student",
        )
        return any(
            f" {normalize_for_match(qualifier)} " in f" {normalized} "
            or compact_for_match(qualifier) in compact
            for qualifier in specific_qualifiers
        )

    @classmethod
    def _target_population_option_is_supported_by_text(cls, answer: str, row: object) -> bool:
        text = f" {normalize_for_match(answer)} "
        question = normalize_for_match(str(row.question))
        option = normalize_for_match(str(row.option))
        if not question or not option:
            return False

        phrases = cls._target_population_phrase_map().get((question, option), ())
        if any(f" {normalize_for_match(phrase)} " in text for phrase in phrases):
            return True

        if option in {"yes", "no", "other"}:
            return False

        if len(option) < 3:
            return False

        broad_options = {
            "citizens",
            "community",
            "communities",
            "households",
            "people",
            "residents",
            "users",
            "public",
        }
        if option in broad_options:
            return False

        option_words = option.split()
        if len(option_words) > 1:
            return f" {option} " in text

        exact_single_word_options = {
            "woman",
            "male",
            "unemployed",
            "retired",
            "tenant",
            "homeowner",
        }
        return option in exact_single_word_options and f" {option} " in text

    @classmethod
    def _fallback_target_population_option_ids(cls, answer: str, rows: list[object]) -> set[int]:
        matched: set[int] = set()
        for row in rows:
            if cls._target_population_option_is_supported_by_text(answer, row):
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
        if message.strip() in {
            "Quick Select Target Population",
            "Quick Select Affected Population Group",
        }:
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
            discarded = list(session.pending_additional_dgs or [])
            session.pending_additional_dgs = None
            session.additional_dg_answers = None
            session.dg_reason = None
            session.dg_evidence = None
            session.phase = "socio_demographic_review"
            self._record_activity(
                session_id,
                session,
                "socio_demographics_skipped",
                ", ".join(discarded) or "No pending socio-demographic profiles.",
            )
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=markdown_to_html(
                    "The pending affected population groups were skipped and were not saved.\n\n"
                    "You can continue with the selected hazard options."
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=False,
            )

        reason, evidence = parse_reason_evidence(message)
        if not reason and not evidence:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_validation_failed.md",
                    sector=session.sector,
                    reason=(
                        "Please provide a reason or evidence to validate these affected "
                        "population groups, or choose Skip to discard them without saving."
                    ),
                ),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
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
            hazard_reference = self._selected_hazard_reference(session_id, session)
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
                    dg,
                    user_hazard_id=hazard_reference["user_hazard_id"],
                    custom_hazard_id=hazard_reference["custom_hazard_id"],
                    system_hazard_id=hazard_reference["system_hazard_id"],
                    additional_hazard_id=hazard_reference["additional_hazard_id"],
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

    def _custom_hazard_state(self, session: ChatSession) -> dict[str, object]:
        if not isinstance(session.custom_hazard, dict):
            session.custom_hazard = default_custom_hazard_state()
        return session.custom_hazard

    def _custom_hazard_response(
        self,
        *,
        session_id: str,
        session: ChatSession,
        step: str,
        bot_message: str,
        options: list[Option],
        input_mode: str = "text",
        error: bool = False,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        return ChatResponse(
            session_id=session_id,
            step=step,
            bot_message=bot_message,
            options=options,
            session=session.summary(),
            input_mode=input_mode,
            error=error,
            validation_details=custom_hazard_validation_details(state),
            custom_hazard=frontend_custom_hazard_payload(state),
            custom_hazard_grounding_status=build_custom_hazard_grounding_status(state),
        )

    async def _run_custom_hazard_dimension_check(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("raw_text") or session.pending_hazard or "").strip()
        if not hazard:
            session.phase = "custom_hazard_input"
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_input",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )
        result = await validate_custom_hazard_dimensions(
            self._custom_hazard_grounding_text(state, hazard),
            session.sector or "",
            session.country or "",
            session.region or "",
            self._same_sector_hazard_names_for_duplicate_check(session),
            state,
        )
        self._store_custom_hazard_validation_result(session, hazard, result)
        return await self._route_custom_hazard_next_action(session_id, session)

    def _same_sector_hazard_names_for_duplicate_check(self, session: ChatSession) -> list[str]:
        names: list[str] = [
            *(session.hazards or []),
            *(session.custom_hazards or []),
            *(session.additional_hazards or []),
            *hazard_names(session),
        ]
        if session.sector_id is not None:
            try:
                names.extend(
                    self.db.scalars(
                        select(SystemHazard.name).where(
                            SystemHazard.sector_id == session.sector_id
                        )
                    ).all()
                )
                names.extend(
                    self.db.scalars(
                        select(AdditionalHazard.name).where(
                            AdditionalHazard.sector_id == session.sector_id
                        )
                    ).all()
                )
                names.extend(
                    self.db.scalars(
                        select(CustomHazard.name).where(
                            CustomHazard.sector_id == session.sector_id
                        )
                    ).all()
                )
                names.extend(
                    self.db.scalars(
                        select(UserHazard.name).where(
                            UserHazard.sector_id == session.sector_id
                        )
                    ).all()
                )
            except Exception:
                logger.exception("Failed to load same-sector hazards for duplicate check")

        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            label = str(name or "").strip()
            key = normalize_for_match(label)
            if label and key and key not in seen:
                seen.add(key)
                deduped.append(label)
        return deduped

    def _custom_hazard_grounding_text(
        self, state: dict[str, object], hazard: str
    ) -> str:
        parts = [hazard]
        reason = str(state.get("reason") or "").strip()
        evidence = str(state.get("evidence") or "").strip()
        if reason:
            parts.append(f"Reason: {reason}")
        if evidence:
            parts.append(f"Evidence: {evidence}")
        return "\n".join(parts)

    def _store_custom_hazard_validation_result(
        self, session: ChatSession, hazard: str, result: dict[str, object]
    ) -> None:
        state = self._custom_hazard_state(session)
        state["raw_text"] = hazard
        state["normalized_text"] = normalize_for_match(hazard)
        state["selected_country"] = session.country or ""
        state["selected_region"] = session.region or ""
        state["selected_sector"] = session.sector or ""
        state["validation_round"] = int(state.get("validation_round") or 0) + 1
        state["overall_score"] = int(result.get("overall_score") or 0)
        scores = list(state.get("scores") or [])
        scores.append(state["overall_score"])
        state["scores"] = scores[-4:]
        state["dimension_scores"] = result.get("dimension_scores") or {}
        state["affected_groups"] = result.get("affected_groups") or []
        state["duplicate_candidates"] = result.get("duplicate_candidates") or []
        state["next_action"] = CustomHazardAction.coerce(
            result.get("next_action"),
            CustomHazardAction.ASK_CLARIFICATION,
        ).value
        state["status"] = CustomHazardStatus.coerce(
            result.get("status"),
            CustomHazardStatus.NEEDS_CLARIFICATION,
        ).value

    async def _route_custom_hazard_next_action(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        action = CustomHazardAction.coerce(
            state.get("next_action"),
            CustomHazardAction.ASK_CLARIFICATION,
        )
        hazard = str(state.get("raw_text") or session.pending_hazard or "this hazard").strip()
        if action == CustomHazardAction.ASK_DUPLICATE_CONFIRMATION:
            candidate = (state.get("duplicate_candidates") or [{}])[0]
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                str(candidate.get("existing_hazard") or "the suggested existing hazard"),
                str(candidate.get("reason") or "The proposed hazard appears similar to an existing hazard."),
            )
        if action == CustomHazardAction.REVIEW_GROUPS:
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_reason = self._custom_hazard_dimension_reason(state)
            session.accepted_custom_hazard_evidence = "Not provided"
            return self._custom_hazard_population_review_step(session_id, session)
        if action == CustomHazardAction.VALIDATE:
            return await self._finalize_custom_hazard_from_grounding(session_id, session)
        if action == CustomHazardAction.REJECT:
            session.phase = ChatPhase.CUSTOM_HAZARD_VALIDATION.value
            state["status"] = CustomHazardStatus.REJECTED.value
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_validation",
                bot_message=(
                    "This custom hazard is insufficiently supported after the available "
                    "clarification rounds. Please edit the custom hazard or use an existing hazard."
                ),
                options=HAZARD_DUPLICATE_OPTIONS,
                input_mode="text",
                error=True,
            )
        return self._custom_hazard_clarification_step(session_id, session)

    def _custom_hazard_clarification_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        questions: list[str] = []
        dimensions = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
        for value in dimensions.values():
            if not isinstance(value, dict) or not value.get("needs_clarification"):
                continue
            question = str(value.get("clarification_question") or "").strip()
            if question:
                questions.append(question)
            if len(questions) >= 2:
                break
        if not questions:
            questions = ["Can you clarify how this hazard fits the selected sector, place, and twin-transition policy context?"]
        session.phase = ChatPhase.CUSTOM_HAZARD_CLARIFICATION.value
        state["pending_clarification_questions"] = questions
        state["message"] = "Clarification is needed before validation."
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(
                "I need a little more detail before validating this custom hazard:\n\n"
                + "\n".join(f"- {question}" for question in questions)
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=False,
        )

    def _custom_hazard_dimension_reason(self, state: dict[str, object]) -> str:
        dimensions = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
        reasons = [
            str(value.get("reason") or "").strip()
            for value in dimensions.values()
            if isinstance(value, dict) and str(value.get("reason") or "").strip()
        ]
        return " ".join(reasons)[:1000] or "Validated through custom hazard grounding checks."

    @staticmethod
    def _clean_affected_group_label(value: str) -> str:
        return clean_affected_group_label(value)

    @classmethod
    def _split_affected_group_labels(cls, value: str) -> list[str]:
        return split_affected_group_labels(value)

    @classmethod
    def _parse_custom_affected_group_edit_message(cls, message: str) -> dict[str, list[str]]:
        return parse_custom_affected_group_edit_message(message)

    def _custom_affected_group_matches(self, state: dict[str, object], target: str) -> bool:
        target_label = self._clean_affected_group_label(target)
        return any(
            isinstance(group, dict)
            and self._profiles_are_similar(
                self._clean_affected_group_label(str(group.get("group") or "")),
                target_label,
            )
            for group in state.get("affected_groups") or []
        )

    def _custom_affected_group_label_error(self, group: str) -> str | None:
        label = self._clean_affected_group_label(group)
        normalized = normalize_for_match(label)
        compact = compact_for_match(label)
        if not label:
            return "Please name the affected population group to add."
        if self._is_invalid_user_text(label) or len(compact) < 4:
            return "Please add a meaningful affected population group."

        population_terms = {
            "adults",
            "businesses",
            "citizens",
            "communities",
            "community",
            "consumers",
            "drivers",
            "employees",
            "families",
            "farmers",
            "firms",
            "households",
            "landlords",
            "miners",
            "owners",
            "patients",
            "people",
            "population",
            "renters",
            "residents",
            "students",
            "suppliers",
            "tenants",
            "users",
            "workers",
        }
        non_population_terms = {
            "bill",
            "bills",
            "closure",
            "cost",
            "costs",
            "decarbonization",
            "electricity",
            "emission",
            "emissions",
            "energy",
            "fee",
            "fees",
            "grid",
            "hazard",
            "inflation",
            "infrastructure",
            "policy",
            "price",
            "prices",
            "plant",
            "plants",
            "poverty",
            "regulation",
            "shock",
            "tariff",
            "tariffs",
            "transition",
            "unemployment",
        }
        words = set(normalized.split())
        if words & population_terms:
            return None
        if words & non_population_terms:
            return (
                f"'{label}' does not look like an affected population group. "
                "Please add a group of people, households, workers, communities, firms, or residents affected by the hazard."
            )
        return (
            f"'{label}' does not look like an affected population group. "
            "Please add a group of people, households, workers, communities, firms, or residents affected by the hazard."
        )

    def _is_user_added_custom_affected_group(
        self,
        state: dict[str, object],
        group: dict[str, object],
    ) -> bool:
        if str(group.get("source") or "").strip() == "user_added":
            return True
        group_label = self._clean_affected_group_label(str(group.get("group") or ""))
        return any(
            isinstance(added_group, dict)
            and self._profiles_are_similar(
                self._clean_affected_group_label(str(added_group.get("group") or "")),
                group_label,
            )
            for added_group in state.get("added_affected_groups") or []
        )

    def _remove_custom_affected_group(self, state: dict[str, object], target: str) -> str | None:
        target_label = self._clean_affected_group_label(target)
        removed: list[dict[str, object]] = []
        kept: list[dict[str, object]] = []
        blocked_labels: list[str] = []
        for group in state.get("affected_groups") or []:
            if isinstance(group, dict) and self._profiles_are_similar(
                self._clean_affected_group_label(str(group.get("group") or "")),
                target_label,
            ):
                if self._is_user_added_custom_affected_group(state, group):
                    removed.append(group)
                else:
                    kept.append(group)
                    blocked_labels.append(
                        self._clean_affected_group_label(str(group.get("group") or "this affected group"))
                    )
            else:
                kept.append(group)
        state["affected_groups"] = kept
        state["removed_affected_groups"] = [
            *list(state.get("removed_affected_groups") or []),
            *removed,
        ]
        if blocked_labels:
            label = blocked_labels[0]
            return (
                f"'{label}' can't be removed because it was found by the system. "
                "You can add another affected group, or edit the group reason if the impact needs correction."
            )
        return None

    async def _validate_custom_affected_group_reason(
        self,
        session: ChatSession,
        group: str,
        reason: str,
    ) -> dict[str, str | bool] | None:
        local_error = self._custom_affected_group_reason_local_error(
            session,
            group,
            reason,
        )
        if local_error:
            return {"valid": False, "reason": local_error}

        review = await self._validate_input_quality(
            session=session,
            purpose=(
                f"an impact reason explaining how the custom hazard affects "
                f"the affected population group '{group}'"
            ),
            fields={
                "affected group": group,
                "impact reason": reason,
            },
        )
        if review is None:
            return {"valid": True, "reason": "The impact reason is locally meaningful."}
        return review

    def _custom_affected_group_reason_local_error(
        self,
        session: ChatSession,
        group: str,
        reason: str,
    ) -> str | None:
        group_label = self._clean_affected_group_label(group)
        reason_text = re.sub(r"\s+", " ", reason).strip()
        if not group_label:
            return "Please name the affected group before explaining the impact."
        if self._is_invalid_user_text(reason_text) or len(compact_for_match(reason_text)) < 8:
            return "Please provide a meaningful impact reason, not just a short label or unclear text."
        weak_reasons = {
            "yes",
            "ok",
            "okay",
            "affected",
            "impact",
            "bad",
            "problem",
            "issue",
            "poverty",
            "cost",
            "costs",
            "jobs",
        }
        if normalize_for_match(reason_text) in weak_reasons:
            return "Please explain the mechanism, such as the cost, job, access, exposure, or exclusion impact for this group."

        hazard_text = " ".join(
            str(value or "")
            for value in [
                session.accepted_custom_hazard,
                session.pending_hazard,
                (session.custom_hazard or {}).get("raw_text")
                if isinstance(session.custom_hazard, dict)
                else "",
                (session.custom_hazard or {}).get("reason")
                if isinstance(session.custom_hazard, dict)
                else "",
            ]
        )
        reason_words = self._profile_similarity_words(normalize_for_match(reason_text))
        group_words = self._profile_similarity_words(normalize_for_match(group_label))
        hazard_words = self._profile_similarity_words(normalize_for_match(hazard_text))
        mechanism_words = {
            "cost",
            "costs",
            "income",
            "job",
            "jobs",
            "employment",
            "unemployment",
            "access",
            "exposure",
            "health",
            "housing",
            "energy",
            "poverty",
            "arrears",
            "exclusion",
            "training",
            "retraining",
            "mobility",
            "tax",
            "prices",
            "bills",
            "wages",
            "livelihood",
        }
        if not (reason_words & group_words) and not (reason_words & hazard_words):
            return "Please connect the reason to this affected group or to the custom hazard."
        if not (reason_words & mechanism_words):
            return "Please explain the concrete impact mechanism, such as costs, jobs, access, exposure, or exclusion."
        return None

    def _mark_custom_hazard_dimension(
        self,
        session: ChatSession,
        dimension: str,
        *,
        status: str,
        score: int,
        reason: str,
        clarification_question: str = "",
    ) -> None:
        state = self._custom_hazard_state(session)
        dimensions = dict(state.get("dimension_scores") or {})
        dimensions[dimension] = {
            "score": max(0, min(10, score)),
            "status": status,
            "reason": reason,
            "needs_clarification": status.upper() in {"NEEDS CLARIFICATION", "INSUFFICIENT INFO"},
            "clarification_question": clarification_question,
        }
        state["dimension_scores"] = dimensions
        state["overall_score"] = 0
        state["status"] = (
            CustomHazardStatus.REJECTED.value
            if status.upper() == "REJECTED"
            else CustomHazardStatus.NEEDS_CLARIFICATION.value
        )
        state["message"] = reason

    def _refresh_custom_hazard_duplicate_candidates(
        self,
        session: ChatSession,
        hazard: str,
    ) -> None:
        state = self._custom_hazard_state(session)
        matches = self._local_similar_hazards(
            hazard,
            self._same_sector_hazard_names_for_duplicate_check(session),
        )
        state["duplicate_candidates"] = [
            {
                "existing_hazard": match,
                "similarity_score": round(fuzzy_score(hazard, match) * 100),
                "reason": (
                    "The proposed hazard is the same as, or very similar to, "
                    "an existing same-sector hazard."
                ),
            }
            for match in matches[:3]
        ]

    def _custom_hazard_validation_failed_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        reason: str,
        dimension: str | None = None,
        evidence: str = "",
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        state["raw_text"] = hazard
        state["normalized_text"] = normalize_for_match(hazard)
        if evidence:
            state["evidence"] = evidence
        self._mark_custom_hazard_dimension(
            session,
            dimension or self._custom_hazard_rejection_dimension(reason),
            status="REJECTED",
            score=0,
            reason=reason,
        )
        self._refresh_custom_hazard_duplicate_candidates(session, hazard)
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_validation",
            bot_message=render_message(
                "hazard_validation_failed.md",
                sector=session.sector,
                reason=reason,
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="reason_evidence",
            error=True,
        )

    @staticmethod
    def _custom_hazard_rejection_dimension(reason: str) -> str:
        lowered = reason.casefold()
        policy_terms = (
            "green and digital transition",
            "green transition",
            "digital transition",
            "transition policies",
            "transition policy",
            "twin-transition",
            "twin transition",
            "does not discuss a hazard",
            "merely states a fact",
        )
        if any(term in lowered for term in policy_terms):
            return "twin_transition_policy_fit"
        if "selected sector" in lowered or any(
            term in lowered for term in ("wrong sector", "sector mismatch", "unrelated to the selected sector")
        ):
            return "selected_sector_fit"
        if any(term in lowered for term in ("country", "region", "regional", "local")):
            return "country_region_fit"
        if any(term in lowered for term in ("affected", "population", "group", "people")):
            return "affected_groups_fit"
        if any(term in lowered for term in ("sector", "housing", "transport", "energy")):
            return "selected_sector_fit"
        return "twin_transition_policy_fit"

    async def _finalize_custom_hazard_from_grounding(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("raw_text") or session.pending_hazard or "New hazard").strip()
        groups = state.get("confirmed_affected_groups") or state.get("affected_groups") or []
        profiles = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            name = str(group.get("group") or group.get("name") or "").strip()
            if not name:
                continue
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "variable_name": "custom_hazard_grounding",
                    "explanation": str(group.get("reason") or "Affected group identified during custom hazard grounding.").strip(),
                    "statistical_basis": str(group.get("source_text") or "Custom hazard grounding review.").strip(),
                    "source": str(group.get("source") or "custom_hazard_grounding").strip(),
                }
            )
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        if profiles:
            profiles = self._attach_target_population_matches_to_profiles(profiles)
            session.hazard_profiles[hazard] = profiles
            session.socio_demographic_profiles = [str(profile["name"]) for profile in profiles]
        state["status"] = CustomHazardStatus.READY.value
        if session.custom_hazards is None:
            session.custom_hazards = []
        if hazard and not any(normalize(item) == normalize(hazard) for item in session.custom_hazards):
            session.custom_hazards.append(hazard)
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = (
            str(state.get("reason") or "").strip()
            or self._custom_hazard_dimension_reason(state)
        )
        session.accepted_custom_hazard_evidence = (
            str(state.get("evidence") or "").strip() or "Not provided"
        )
        shared_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=None
            if session.accepted_custom_hazard_evidence == "Not provided"
            else session.accepted_custom_hazard_evidence,
        )
        session.accepted_custom_hazard_id = shared_hazard.id if shared_hazard else None
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None
        self._record_activity(session_id, session, "custom_hazard_added", hazard)
        if session.accepted_custom_hazard_evidence != "Not provided":
            self._promote_temporary_evidence(
                session,
                target_scope="quarantined",
                provenance="validated_user_evidence",
            )
        return await self._custom_hazard_added_step(session_id, session)

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
            session.custom_hazard = None
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

        session.custom_hazard = default_custom_hazard_state()
        state = self._custom_hazard_state(session)
        state.update(
            {
                "raw_text": hazard,
                "normalized_text": normalize_for_match(hazard),
                "selected_country": session.country or "",
                "selected_region": session.region or "",
                "selected_sector": session.sector or "",
                "status": CustomHazardStatus.DRAFT.value,
            }
        )

        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            hazard,
        )
        if plain_rejection_reason:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                self._custom_hazard_rejection_dimension(plain_rejection_reason),
                status="REJECTED",
                score=0,
                reason=plain_rejection_reason,
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=plain_rejection_reason,
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        hazard_review = await self._review_custom_hazard_input(session, hazard)
        if hazard_review is None:
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=(
                    "I could not review this hazard for clarity and policy fit because "
                    "the local LLM is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )
        if not hazard_review["valid"]:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                self._custom_hazard_rejection_dimension(str(hazard_review["reason"])),
                status="REJECTED",
                score=0,
                reason=str(hazard_review["reason"]),
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=hazard_review["reason"],
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            hazard,
        )
        if sector_mismatch_reason:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                "selected_sector_fit",
                status="REJECTED",
                score=0,
                reason=sector_mismatch_reason,
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=sector_mismatch_reason,
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        return self._hazard_reason_evidence_step(session_id, session, hazard)

    async def _handle_custom_hazard_clarification(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.pending_hazard_reason = None
            session.pending_hazard_evidence = None
            session.pending_hazard_clarification_question = None
            session.pending_hazard_clarification_answer = None
            session.custom_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        answer = message.strip()
        if session.phase == "custom_hazard_clarification":
            state = self._custom_hazard_state(session)
            if not answer:
                return self._custom_hazard_clarification_step(session_id, session)
            clarifications = list(state.get("clarifications") or [])
            clarifications.append(
                {
                    "questions": list(state.get("pending_clarification_questions") or []),
                    "answer": answer,
                }
            )
            state["clarifications"] = clarifications
            state["message"] = "Scores were recalculated after clarification."
            session.phase = "custom_hazard_dimension_check"
            return await self._run_custom_hazard_dimension_check(session_id, session)

        hazard = session.pending_hazard or ""
        if not hazard:
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )
        if not answer:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                session.pending_hazard_clarification_question
                or "Could you clarify how this hazard relates to the selected transition policy context?",
            )

        pending_reason = session.pending_hazard_reason
        if pending_reason:
            reason = pending_reason
            evidence = session.pending_hazard_evidence or ""
            context_review = await self._review_custom_hazard_context(
                session,
                hazard,
                reason,
                evidence,
                clarification=answer,
            )
            if context_review is None:
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message("hazard_validation_unavailable.md"),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    input_mode="textarea",
                    error=True,
                )
            if context_review["status"] == "clarification":
                return self._hazard_clarification_step(
                    session_id,
                    session,
                    hazard,
                    str(context_review["question"]),
                )
            if not context_review["valid"]:
                session.pending_hazard_reason = None
                session.pending_hazard_evidence = None
                session.pending_hazard_clarification_question = None
                session.pending_hazard_clarification_answer = None
                session.phase = "add_hazard_evidence"
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message(
                        "hazard_validation_failed.md",
                        sector=session.sector,
                        reason=str(context_review["reason"]),
                    ),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )
            session.pending_hazard_clarification_answer = answer
            if isinstance(session.custom_hazard, dict):
                state = self._custom_hazard_state(session)
                clarifications = list(state.get("clarifications") or [])
                clarifications.append(
                    {
                        "questions": [session.pending_hazard_clarification_question or ""],
                        "answer": answer,
                    }
                )
                state["clarifications"] = clarifications
                state["reason"] = reason
                state["evidence"] = evidence
                state["message"] = "Reason, evidence, and clarification were accepted for grounding validation."
                session.accepted_custom_hazard_reason = reason
                session.accepted_custom_hazard_evidence = evidence or "Not provided"
                session.phase = "custom_hazard_dimension_check"
                return await self._run_custom_hazard_dimension_check(session_id, session)
            return await self._finalize_valid_custom_hazard(
                session_id,
                session,
                hazard,
                reason,
                evidence,
                clarification=answer,
            )

        clarified_hazard = self._clarified_hazard_text(session, hazard, answer)
        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            clarified_hazard,
        )
        if plain_rejection_reason:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                plain_rejection_reason,
            )

        hazard_review = await self._review_custom_hazard_input(session, clarified_hazard)
        if hazard_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=(
                    "I could not review this clarification because the local LLM is "
                    "unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )
        if not hazard_review["valid"]:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                str(hazard_review["reason"]),
            )

        session.pending_hazard_clarification_answer = answer
        return await self._continue_valid_custom_hazard(
            session_id,
            session,
            hazard,
            clarification=answer,
        )

    def _hazard_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        question: str,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.pending_hazard_clarification_question = question
        session.phase = "add_hazard_clarification"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_clarification.md",
                hazard=hazard,
                question=question,
            ),
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    async def _continue_valid_custom_hazard(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        *,
        clarification: str | None = None,
    ) -> ChatResponse:
        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            hazard,
        )
        if sector_mismatch_reason:
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=sector_mismatch_reason,
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        existing_hazard = self._match_hazard(hazard, session)
        if existing_hazard is not None:
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                existing_hazard,
                "This appears to already be covered by an existing hazard.",
            )

        local_matches = self._local_similar_hazards(
            hazard,
            self._same_sector_hazard_names_for_duplicate_check(session),
        )
        if local_matches:
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                local_matches[0],
                "This appears to have the same or similar meaning as an existing hazard.",
            )

        duplicate_check = await self._semantic_hazard_duplicate_check(session, hazard)
        if duplicate_check is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=(
                    "I could not check whether this hazard is already covered because "
                    "the local LLM is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )
        if duplicate_check.get("duplicate"):
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                str(duplicate_check.get("match") or ""),
                str(duplicate_check.get("reason") or ""),
            )

        return self._hazard_reason_evidence_step(session_id, session, hazard)

    def _hazard_reason_evidence_step(
        self, session_id: str, session: ChatSession, hazard: str
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.phase = "add_hazard_evidence"
        session.suggested_duplicate_hazard = None
        session.suggested_duplicate_hazard_record_id = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = None
        message = render_message(
            "hazard_reason_evidence.md",
            hazard=hazard,
            matching_hazards="",
            has_matching_hazards=False,
        )
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["raw_text"] = hazard
            state["status"] = CustomHazardStatus.DRAFT.value
            state["message"] = "Reason and optional evidence are required before grounding validation."
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_validation",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="reason_evidence",
                error=False,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=message,
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    def _hazard_duplicate_suggestion_step(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        suggested_hazard: str,
        reason: str,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.suggested_duplicate_hazard = suggested_hazard.strip() or None
        session.phase = (
            "custom_hazard_duplicate_confirmation"
            if isinstance(session.custom_hazard, dict)
            else "hazard_duplicate_suggestion"
        )
        message = render_message(
            "hazard_duplicate.md",
            hazard=hazard,
            suggested_hazard=suggested_hazard or "the suggested existing hazard",
            reason=reason or "The proposed hazard appears similar to an existing hazard.",
        )
        if session.phase == "custom_hazard_duplicate_confirmation":
            state = self._custom_hazard_state(session)
            state["message"] = (
                f"This hazard appears similar to an existing hazard: "
                f"'{suggested_hazard or 'the suggested existing hazard'}'."
            )
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_duplicate_confirmation",
                bot_message=message,
                options=HAZARD_DUPLICATE_OPTIONS,
                error=False,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=message,
            options=HAZARD_DUPLICATE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_hazard_duplicate_suggestion(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_DUPLICATE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_DUPLICATE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action in {normalize("Continue with this hazard"), normalize("Continue with custom hazard")}:
            hazard = session.pending_hazard or ""
            if not hazard:
                session.phase = "custom_hazard_input"
                session.custom_hazard = default_custom_hazard_state()
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message("add_hazard.md", sector=session.sector),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    error=True,
                )
            if session.phase == "custom_hazard_duplicate_confirmation":
                state = self._custom_hazard_state(session)
                state["duplicate_override_confirmed"] = True
                if state.get("affected_groups"):
                    state["next_action"] = CustomHazardAction.REVIEW_GROUPS.value
                    state["status"] = CustomHazardStatus.NEEDS_GROUP_REVIEW.value
                else:
                    state["next_action"] = CustomHazardAction.ASK_CLARIFICATION.value
                    state["status"] = CustomHazardStatus.NEEDS_CLARIFICATION.value
                return await self._route_custom_hazard_next_action(session_id, session)
            return self._hazard_reason_evidence_step(session_id, session, hazard)

        if action in {normalize("Explore suggested hazard"), normalize("Use existing hazard")}:
            suggested_hazard = session.suggested_duplicate_hazard or ""
            hazard = self._match_hazard(suggested_hazard, session) or self._fuzzy_hazard(
                suggested_hazard,
                session,
            )
            if hazard is None:
                session.phase = "hazards"
                return self._hazards_step(session_id, session)
            self._clear_selected_hazard_context(session)
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.selected_hazard = hazard
            session.phase = "socio_demographic_review"
            self._record_activity(session_id, session, "hazard_selected", hazard)
            return await self._hazard_profiles_response(session_id, session, hazard)

        if action in {normalize("Write hazard again"), normalize("Edit custom hazard")}:
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=self.invalid_message,
            options=HAZARD_DUPLICATE_OPTIONS,
            session=session.summary(),
            error=True,
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
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=str(input_review["reason"]),
                    evidence=evidence or "",
                )
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

        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            session.pending_hazard or "",
            reason,
            evidence or "",
        )
        if plain_rejection_reason:
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=plain_rejection_reason,
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=plain_rejection_reason,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            session.pending_hazard or "",
            reason,
            evidence,
        )
        if sector_mismatch_reason:
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=sector_mismatch_reason,
                    dimension="selected_sector_fit",
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=sector_mismatch_reason,
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
            self._discard_temporary_evidence(session, evidence)
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=str(validation["reason"]),
                    evidence=evidence or "",
                )
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

        hazard = session.pending_hazard or "New hazard"
        context_review = await self._review_custom_hazard_context(
            session,
            hazard,
            reason,
            evidence or "",
        )
        if context_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
        if context_review["status"] == "clarification":
            session.pending_hazard_reason = reason
            session.pending_hazard_evidence = evidence or ""
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                str(context_review["question"]),
            )
        if not context_review["valid"]:
            self._discard_temporary_evidence(session, evidence)
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=hazard,
                    reason=str(context_review["reason"]),
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=str(context_review["reason"]),
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["raw_text"] = hazard
            state["normalized_text"] = normalize_for_match(hazard)
            state["reason"] = reason
            state["evidence"] = evidence or ""
            state["message"] = "Reason and optional evidence were accepted for grounding validation."
            session.accepted_custom_hazard_reason = reason
            session.accepted_custom_hazard_evidence = evidence or "Not provided"
            session.phase = "custom_hazard_dimension_check"
            return await self._run_custom_hazard_dimension_check(session_id, session)

        return await self._finalize_valid_custom_hazard(
            session_id,
            session,
            hazard,
            reason,
            evidence or "",
        )

    async def _finalize_valid_custom_hazard(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> ChatResponse:
        if session.custom_hazards is None:
            session.custom_hazards = []
        if hazard and not any(normalize(item) == normalize(hazard) for item in session.custom_hazards):
            session.custom_hazards.append(hazard)

        session.pending_hazard = None
        session.pending_hazard_reason = None
        session.pending_hazard_evidence = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = clarification
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = reason
        session.accepted_custom_hazard_evidence = evidence or "Not provided"
        shared_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=reason,
            evidence=evidence or None,
        )
        session.accepted_custom_hazard_id = shared_hazard.id if shared_hazard else None
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None
        self._record_activity(session_id, session, "custom_hazard_added", hazard)
        if evidence:
            self._promote_temporary_evidence(
                session,
                target_scope="quarantined",
                provenance="validated_user_evidence",
            )

        profiles = await self._extract_custom_hazard_affected_population_profiles(
            session,
            hazard,
            reason,
            evidence,
            clarification=clarification,
        )
        if not profiles:
            profiles = self._additional_hazard_profiles_for_custom_hazard(session, hazard)
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = profiles
        if not profiles:
            session.socio_demographic_profiles = []
            target_population_step = self._start_target_population_questions(session_id, session)
            if target_population_step is not None:
                return target_population_step
        session.socio_demographic_profiles = [
            str(profile.get("name") or profile.get("profile") or "").strip()
            for profile in profiles
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        return self._custom_hazard_population_review_step(session_id, session)

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
            hazard_reference = self._selected_hazard_reference(session_id, session)
            for profile in profiles_to_store:
                self._store_socio_demographic(
                    session,
                    str(profile.get("name") or ""),
                    user_hazard_id=hazard_reference["user_hazard_id"],
                    custom_hazard_id=hazard_reference["custom_hazard_id"],
                    system_hazard_id=hazard_reference["system_hazard_id"],
                    additional_hazard_id=hazard_reference["additional_hazard_id"],
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
        is_custom_hazard = self._is_saved_custom_hazard(session, hazard) or (
            normalize(hazard) == normalize(session.accepted_custom_hazard or "")
        )
        if not profiles and is_custom_hazard:
            profiles = self._additional_hazard_profiles_for_custom_hazard(session, hazard)
            if profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = profiles
        if not profiles:
            profiles = await self._get_hazard_profiles_from_llm(session, hazard)
            if profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = profiles
        if is_custom_hazard and user_profiles:
            profiles = self._merge_hazard_profile_lists(profiles, user_profiles)
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[hazard] = profiles
        display_profiles = (
            await self._additional_profiles_with_population_context(session, hazard, profiles)
            if is_additional_hazard or is_custom_hazard
            else await self._profiles_with_population_context(session, hazard, profiles)
        )
        if is_custom_hazard and profiles and not display_profiles:
            display_profiles = profiles
        display_user_profiles = await self._profiles_with_population_context(
            session,
            hazard,
            user_profiles,
        )
        if is_custom_hazard:
            display_user_profiles = []
        answer = self._format_hazard_profiles_markdown(
            hazard,
            display_profiles,
            user_profiles=display_user_profiles,
        )
        session.socio_demographic_findings = self._format_hazard_profiles_markdown(
            hazard,
            display_profiles,
        )
        if is_custom_hazard:
            assistant_names, user_names = self._custom_hazard_profile_name_sections(
                profiles
            )
            session.socio_demographic_profiles = assistant_names
            session.additional_dgs = user_names or None
        else:
            session.socio_demographic_profiles = [
                profile["name"] for profile in profiles if profile.get("name")
            ]
            session.additional_dgs = [
                profile["name"] for profile in user_profiles if profile.get("name")
            ] or None
        if not is_custom_hazard and not is_additional_hazard:
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
            render_prompt_template(
                "llm/practical_policy_recommendations_user.txt",
                selected_hazard=session.selected_hazard,
                socio_demographic_profiles=format_all_dgs(session),
                target_population=self._mitigation_target_population_text(session),
                matched_examples=matched_examples
                or "- No matching examples were found for this sector, hazard, and profile set.",
            ),
        )
        practical_considerations_response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=1200,
        )
        practical_considerations, practical_consideration_items = (
            self._practical_considerations_json_to_markdown(
                practical_considerations_response
            )
        )
        practical_considerations = self._ensure_practical_considerations_intro(
            practical_considerations
        )
        session.practical_considerations = (
            practical_consideration_items
            or self._extract_practical_consideration_items(practical_considerations)
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
        context = load_nested_prompt_file("llm/intro_message.txt")
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
        context = load_nested_prompt_file("llm/selection_message.txt")
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
        context = render_prompt_template(
            "llm/mitigation_review_assistant.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
            knowledge_context=knowledge_context
            or "- No relevant knowledge-base excerpts were found.",
        )

        history = list(session.stats_conversation or [])[-8:]
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_review_assistant_user_context.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    selected_hazard=session.selected_hazard or "Not selected",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    mitigation_reason=session.mitigation_reason or "Not provided",
                    examples=examples or "- No sector-specific examples are available.",
                ),
            },
            *history,
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_review_assistant_user_followup.txt",
                    user_message=user_message,
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
        if message.strip() in {
            "Quick Select Target Population",
            "Quick Select Affected Population Group",
        }:
            return self._target_population_question_step(session_id, session)
        if message.strip().startswith("TARGET_POPULATION_BATCH:"):
            return await self._handle_target_population_batch(session_id, session, message)

        question = self._current_target_population_question(session)
        if question is None:
            await self._synthesize_target_population_profile(session)
            return self._custom_hazard_population_review_step(session_id, session)

        options = self._target_population_options(question)
        selected_labels = self._target_population_selected_labels(message, options)

        if not selected_labels:
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please choose one or more listed affected population group options.",
            )

        if any(normalize(label) == normalize("Skip all") for label in selected_labels):
            session.target_population_index += 1
            session.target_population_index = len(session.target_population_questions or [])
            await self._synthesize_target_population_profile(session)
            return self._custom_hazard_population_review_step(session_id, session)

        if any(normalize(label) == normalize("Skip") for label in selected_labels):
            session.target_population_index += 1
            if session.target_population_index >= len(session.target_population_questions or []):
                await self._synthesize_target_population_profile(session)
                return self._custom_hazard_population_review_step(session_id, session)
            return self._target_population_question_step(session_id, session)

        self._record_target_population_answer(session_id, session, question, selected_labels)
        session.target_population_index += 1

        if session.target_population_index >= len(session.target_population_questions or []):
            await self._synthesize_target_population_profile(session)
            return self._custom_hazard_population_review_step(session_id, session)

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
                error_reason="Please submit valid affected population group selections.",
            )

        if not isinstance(payload, list):
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please submit valid affected population group selections.",
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
                error_reason="Please select at least one affected population group option.",
            )

        session.target_population_index = len(session.target_population_questions or [])
        await self._synthesize_target_population_profile(session)
        return self._custom_hazard_population_review_step(session_id, session)

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
            Option(id=len(options) + 3, label="Quick Select Affected Population Group"),
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
        context = load_nested_prompt_file("llm/target_population_summary.txt")
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
            existing_profiles = self._stored_hazard_profiles(session, hazard)
            stored_profiles = self._stored_user_hazard_profiles(session, hazard)
            if stored_profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = self._merge_custom_hazard_profile_sources(
                    existing_profiles,
                    stored_profiles,
                )
                continue
            profiles = self._target_population_profiles_for_saved_hazard(session, hazard)
            if not profiles:
                if existing_profiles and session.hazard_profiles is not None:
                    session.hazard_profiles[hazard] = existing_profiles
                continue
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[hazard] = self._merge_hazard_profile_lists(
                existing_profiles,
                profiles,
            )

    @staticmethod
    def _custom_hazard_profile_name_sections(
        profiles: list[dict[str, object]],
    ) -> tuple[list[str], list[str]]:
        user_sources = {
            "target_population",
            "target_population_additional",
            "user_review",
            "user_validated",
        }
        assistant_names: list[str] = []
        user_names: list[str] = []
        assistant_keys: set[str] = set()
        user_keys: set[str] = set()

        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            key = normalize(name)
            if not key:
                continue
            source = str(profile.get("source") or "").strip()
            if source in user_sources:
                if key not in user_keys and key not in assistant_keys:
                    user_keys.add(key)
                    user_names.append(name)
                continue
            if key not in assistant_keys:
                assistant_keys.add(key)
                assistant_names.append(name)
            if key in user_keys:
                user_keys.remove(key)
                user_names = [value for value in user_names if normalize(value) != key]

        return assistant_names, user_names

    @staticmethod
    def _merge_hazard_profile_lists(
        *profile_groups: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for profiles in profile_groups:
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                name = str(profile.get("name") or profile.get("profile") or "").strip()
                if not name:
                    continue
                source = str(profile.get("source") or "").strip()
                key = normalize(f"{name}|{source}")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(profile)
        return merged

    @classmethod
    def _merge_custom_hazard_profile_sources(
        cls,
        *profile_groups: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[str] = set()
        by_name: dict[str, int] = {}
        for profiles in profile_groups:
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                name = str(profile.get("name") or profile.get("profile") or "").strip()
                if not name:
                    continue
                name_key = normalize(name)
                source = str(profile.get("source") or "").strip()
                label_set = cls._mapped_label_key_set(profile)
                if name_key in by_name:
                    cls._merge_profile_payload(merged[by_name[name_key]], profile)
                    continue
                is_answer_profile = source == "target_population"
                if is_answer_profile and label_set:
                    parent_index = cls._covered_profile_parent_index(merged, label_set)
                    if parent_index is not None:
                        covered_names = merged[parent_index].setdefault("covered_profile_names", [])
                        cls._append_unique_value(covered_names, name)
                        continue
                key = normalize(f"{name}|{source}|{'|'.join(sorted(label_set))}")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(profile))
                by_name[name_key] = len(merged) - 1
        return merged

    @classmethod
    def _merge_profile_payload(
        cls,
        existing: dict[str, object],
        incoming: dict[str, object],
    ) -> None:
        for key in (
            "target_population_option_ids",
            "target_population_labels",
            "population_lookup_labels",
            "population_context",
            "covered_profile_names",
        ):
            values = existing.setdefault(key, [])
            if not isinstance(values, list):
                values = []
                existing[key] = values
            incoming_values = incoming.get(key)
            if not isinstance(incoming_values, list):
                incoming_metadata = incoming.get("metadata")
                if isinstance(incoming_metadata, dict):
                    incoming_values = incoming_metadata.get(key)
            if isinstance(incoming_values, list):
                for value in incoming_values:
                    cls._append_unique_value(values, str(value))

        for key in (
            "explanation",
            "variable_name",
            "variable_type",
            "statistical_basis",
            "source",
            "regional_population_pct",
            "population_pct",
            "national_population_pct",
        ):
            if existing.get(key) in (None, "", []):
                value = incoming.get(key)
                if value not in (None, "", []):
                    existing[key] = value

        incoming_metadata = incoming.get("metadata")
        if isinstance(incoming_metadata, dict):
            metadata = existing.setdefault("metadata", {})
            if isinstance(metadata, dict):
                for key, value in incoming_metadata.items():
                    if key not in metadata or metadata.get(key) in (None, "", []):
                        metadata[key] = value

    @classmethod
    def _covered_profile_parent_index(
        cls,
        profiles: list[dict[str, object]],
        child_labels: set[str],
    ) -> int | None:
        if not child_labels:
            return None
        candidates: list[tuple[int, int]] = []
        for index, profile in enumerate(profiles):
            parent_labels = cls._mapped_label_key_set(profile)
            if parent_labels and child_labels <= parent_labels:
                candidates.append((len(parent_labels), index))
        if not candidates:
            return None
        _, index = max(candidates)
        return index

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
        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is not None:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserSession, UserSession.id == UserQuestionResponse.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserQuestionResponse.custom_hazard_id == custom_hazard_id,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        else:
            rows = []
        if not rows:
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
        grouped: dict[str, dict[str, object]] = {}
        ordered_keys: list[str] = []
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
            question_key = normalize_for_match(question)
            group = grouped.get(question_key)
            if group is None:
                display_question = ChatService._display_target_population_question(question)
                group = {
                    "question": question,
                    "name": display_question[:120],
                    "profile": display_question[:120],
                    "variable_name": question[:160],
                    "variable_type": ChatService._profile_variable_type(question),
                    "options": [],
                    "target_population_labels": [],
                    "population_lookup_labels": [],
                    "source": "target_population",
                }
                grouped[question_key] = group
                ordered_keys.append(question_key)
            for label in labels:
                cleaned_label = label.strip()
                if not cleaned_label:
                    continue
                ChatService._append_unique_value(group["options"], cleaned_label)
                mapped_label = f"{question.rstrip('.')}: {cleaned_label}"
                ChatService._append_unique_value(group["target_population_labels"], mapped_label)
                ChatService._append_unique_value(group["population_lookup_labels"], mapped_label)

        profiles: list[dict[str, str]] = []
        for key in ordered_keys:
            group = grouped[key]
            options = [
                str(option).strip()
                for option in group.get("options", [])
                if str(option).strip()
            ]
            if not options:
                continue
            explanation = (
                "Synthesized from user-selected target-population responses for "
                f"{hazard}: " + "; ".join(options)
            )
            profiles.append(
                {
                    "name": str(group.get("name") or "")[:120],
                    "profile": str(group.get("profile") or "")[:120],
                    "variable_name": str(group.get("variable_name") or "")[:160],
                    "variable_type": str(group.get("variable_type") or "individual"),
                    "explanation": explanation[:260],
                    "statistical_basis": "User-selected socio-demographic question response.",
                    "source": "target_population",
                    "target_population_labels": list(group.get("target_population_labels") or []),
                    "population_lookup_labels": list(group.get("population_lookup_labels") or []),
                    "metadata": {
                        "target_population_labels": list(group.get("target_population_labels") or []),
                        "population_lookup_labels": list(group.get("population_lookup_labels") or []),
                    },
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
        if not session.session_key or not self._has_user_supplied_evidence(evidence):
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
                metadata_value = item.get("metadata")
                metadata = metadata_value if isinstance(metadata_value, dict) else {}
                target_population_option_ids = item.get("target_population_option_ids")
                if not isinstance(target_population_option_ids, list) or not target_population_option_ids:
                    target_population_option_ids = metadata.get("target_population_option_ids")
                target_population_labels = item.get("target_population_labels")
                if not isinstance(target_population_labels, list) or not target_population_labels:
                    target_population_labels = metadata.get("target_population_labels")
                if isinstance(target_population_option_ids, list) and target_population_option_ids:
                    profile_item["target_population_option_ids"] = list(target_population_option_ids)
                if isinstance(target_population_labels, list) and target_population_labels:
                    profile_item["target_population_labels"] = list(target_population_labels)
                compacted_metadata = compact_profile_metadata(item)
                if compacted_metadata:
                    profile_item["metadata"] = compacted_metadata
            profiles.append(profile_item)
        return profiles[:12]

    @staticmethod
    def _extract_json_array(value: str) -> str:
        return extract_json_array_text(value)

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
        raw_labels = ChatService._list_from_profile_or_metadata(
            profile,
            "target_population_labels",
        )
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
        context = load_nested_prompt_file("llm/population_profile_matcher.txt")
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
                if not isinstance(target_population_option_ids, list) or not target_population_option_ids:
                    target_population_option_ids = metadata.get("target_population_option_ids")
                target_population_labels = item.get("target_population_labels")
                if not isinstance(target_population_labels, list) or not target_population_labels:
                    target_population_labels = metadata.get("target_population_labels")
                population_context = item.get("population_context")
                population_lookup_labels = item.get("population_lookup_labels")
                if not isinstance(population_lookup_labels, list) or not population_lookup_labels:
                    population_lookup_labels = metadata.get("population_lookup_labels")
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

    def _stored_custom_hazard_profiles(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, object]]:
        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is None:
            return []
        try:
            rows = self.db.scalars(
                select(CustomHazardProfile)
                .where(CustomHazardProfile.custom_hazard_id == custom_hazard_id)
                .order_by(CustomHazardProfile.id)
            ).all()
        except Exception:
            logger.exception("Failed to load shared custom hazard profiles")
            return []

        profiles: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in rows:
            name = str(row.profile or "").strip()
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            metadata = self._metadata_from_json(row.metadata_json)
            target_population_option_ids = (
                metadata.get("target_population_option_ids")
                if isinstance(metadata.get("target_population_option_ids"), list)
                else []
            )
            target_population_labels = (
                metadata.get("target_population_labels")
                if isinstance(metadata.get("target_population_labels"), list)
                else []
            )
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.explanation or metadata.get("explanation") or ""),
                    "variable_name": str(
                        row.variable_name
                        or metadata.get("variable_name")
                        or metadata.get("variable")
                        or ""
                    ),
                    "variable_type": self._profile_variable_type(
                        row.variable_name or metadata.get("variable_name") or metadata.get("variable") or "",
                        metadata.get("variable_type") or "",
                    ),
                    "statistical_basis": str(
                        row.statistical_basis
                        or metadata.get("statistical_basis")
                        or metadata.get("basis")
                        or ""
                    ),
                    "source": str(row.source or metadata.get("source") or "custom_hazard_extraction"),
                    "metadata": metadata,
                    "target_population_option_ids": target_population_option_ids,
                    "target_population_labels": target_population_labels,
                    "population_context": metadata.get("population_context")
                    if isinstance(metadata.get("population_context"), list)
                    else [],
                    "population_lookup_labels": metadata.get("population_lookup_labels")
                    if isinstance(metadata.get("population_lookup_labels"), list)
                    else [],
                }
            )
        return profiles

    def _stored_user_hazard_profiles(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        is_custom_hazard = self._is_saved_custom_hazard(session, hazard) or normalize(hazard) == normalize(
            session.accepted_custom_hazard or ""
        )
        shared_profiles = self._stored_custom_hazard_profiles(session, hazard)
        try:
            allowed_sources = [
                "user_validated",
                "target_population",
                "custom_hazard_extraction",
                "user_review",
                "d4_2_pdf",
                "llm",
            ]
            base_filters = [
                UserSession.country_id == session.country_id,
                UserHazardSocioDemographic.country_id == session.country_id,
                UserHazardSocioDemographic.region_id.is_(None)
                if session.region_id is None
                else UserHazardSocioDemographic.region_id == session.region_id,
                UserHazardSocioDemographic.sector_id == session.sector_id,
                UserHazardSocioDemographic.source.in_(allowed_sources),
            ]
            if is_custom_hazard:
                custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
                if custom_hazard_id is not None:
                    query = (
                        select(UserHazardSocioDemographic)
                        .join(UserSession, UserSession.id == UserHazardSocioDemographic.user_session_id)
                        .where(
                            UserHazardSocioDemographic.custom_hazard_id == custom_hazard_id,
                            *base_filters,
                        )
                        .order_by(UserHazardSocioDemographic.id)
                    )
                else:
                    query = (
                        select(UserHazardSocioDemographic)
                        .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                        .join(UserSession, UserSession.id == UserHazard.user_session_id)
                        .where(
                            func.lower(UserHazard.name) == hazard.casefold(),
                            UserHazard.sector_id == session.sector_id,
                            UserHazard.region_id.is_(None)
                            if session.region_id is None
                            else UserHazard.region_id == session.region_id,
                            *base_filters,
                        )
                        .order_by(UserHazardSocioDemographic.id)
                    )
            else:
                system_hazard_id = None
                additional_hazard_id = None
                if self._is_additional_hazard(session, hazard):
                    additional_hazard_id = self._selected_additional_hazard_id(session, hazard)
                else:
                    system_hazard_id = self.db.scalar(
                        select(SystemHazard.id).where(
                            SystemHazard.sector_id == session.sector_id,
                            func.lower(SystemHazard.name) == hazard.casefold(),
                        )
                    )
                if system_hazard_id is None and additional_hazard_id is None:
                    return []
                query = (
                    select(UserHazardSocioDemographic)
                    .join(UserSession, UserSession.id == UserHazardSocioDemographic.user_session_id)
                    .where(
                        UserHazardSocioDemographic.system_hazard_id == system_hazard_id,
                        UserHazardSocioDemographic.additional_hazard_id == additional_hazard_id,
                        *base_filters,
                    )
                    .order_by(UserHazardSocioDemographic.id)
                )
            if self.user_id is not None:
                query = query.where(UserSession.user_id == self.user_id)
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load user-added socio-demographic profiles")
            return shared_profiles

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
            if variable_name and source == "target_population":
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
            target_population_option_ids = (
                metadata.get("target_population_option_ids")
                if isinstance(metadata.get("target_population_option_ids"), list)
                else []
            )
            target_population_labels = (
                metadata.get("target_population_labels")
                if isinstance(metadata.get("target_population_labels"), list)
                else ([name] if source == "target_population" else [])
            )
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
                    "target_population_option_ids": target_population_option_ids,
                    "target_population_labels": target_population_labels,
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
        answer_profiles = (
            self._target_population_profiles_for_saved_hazard(session, hazard)
            if is_custom_hazard
            else []
        )
        return self._merge_custom_hazard_profile_sources(
            shared_profiles,
            profiles,
            answer_profiles,
        )

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
            "general considerations to mitigate the negative effects",
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
        shared_rows = self.db.scalars(
            select(CustomHazard.name)
            .where(
                CustomHazard.country_id == session.country_id,
                CustomHazard.sector_id == session.sector_id,
                CustomHazard.region_scope_key == (session.region_id or 0),
            )
            .order_by(CustomHazard.name)
        ).all()
        legacy_rows = self.db.scalars(
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
        for row in [*shared_rows, *legacy_rows]:
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

    def _additional_hazard_profiles_for_custom_hazard(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        hazard_rows = self.db.execute(
            select(AdditionalHazard.id, AdditionalHazard.name)
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(AdditionalHazard.csv_row_number, AdditionalHazard.name)
        ).all()
        matched_names = {
            normalize(name)
            for name in self._local_similar_hazards(
                hazard,
                [str(row.name or "").strip() for row in hazard_rows],
            )
        }
        matched_hazard_ids = [
            int(row.id)
            for row in hazard_rows
            if normalize(str(row.name or "").strip()) in matched_names
        ]
        if not matched_hazard_ids:
            return []

        profile_rows = self.db.scalars(
            select(AdditionalHazardProfile)
            .where(AdditionalHazardProfile.additional_hazard_id.in_(matched_hazard_ids))
            .order_by(
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [int(row.id) for row in profile_rows if isinstance(row.id, int)]
        target_population_by_profile = self._additional_hazard_profile_target_population_map(
            profile_ids
        )
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in profile_rows:
            name = str(row.profile or "").strip()
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            mapped_targets = target_population_by_profile.get(int(row.id), [])
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.evidence or "").strip(),
                    "statistical_basis": str(row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        int(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        return profiles

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

        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is not None:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserSession, UserSession.id == UserQuestionResponse.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserQuestionResponse.custom_hazard_id == custom_hazard_id,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        else:
            rows = []
        if not rows:
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
                ("level of income", "low income"): (
                    "poor households",
                    "income poor",
                    "utility arrears",
                    "utility bill arrears",
                    "energy arrears",
                    "households with utility arrears",
                ),
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

    @staticmethod
    def _custom_hazard_name_key(name: str) -> str:
        return normalize_for_match(name)[:255]

    def _custom_hazard_id_for_context(self, session: ChatSession, hazard: str) -> int | None:
        if session.country_id is None or session.sector_id is None or not hazard.strip():
            return None
        if normalize(hazard) == normalize(session.accepted_custom_hazard or ""):
            if session.accepted_custom_hazard_id is not None:
                return session.accepted_custom_hazard_id
        try:
            hazard_id = self.db.scalar(
                select(CustomHazard.id).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or 0),
                    CustomHazard.name_key == self._custom_hazard_name_key(hazard),
                )
            )
            return int(hazard_id) if isinstance(hazard_id, int) else None
        except Exception:
            logger.exception("Failed to load shared custom hazard id")
            return None

    def _ensure_custom_hazard(
        self,
        session: ChatSession,
        name: str,
        *,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> CustomHazard | None:
        if session.country_id is None or session.sector_id is None or not name.strip():
            return None
        name_key = self._custom_hazard_name_key(name)
        if not name_key:
            return None
        try:
            hazard = self.db.scalar(
                select(CustomHazard).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or 0),
                    CustomHazard.name_key == name_key,
                )
            )
            if hazard is None:
                hazard = CustomHazard(
                    country_id=session.country_id,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    region_scope_key=session.region_id or 0,
                    name=name.strip(),
                    name_key=name_key,
                    source="user",
                    created_by_user_id=self.user_id,
                )
                self.db.add(hazard)
            hazard.name = name.strip()
            hazard.region_id = session.region_id
            hazard.region_scope_key = session.region_id or 0
            if reason is not None:
                hazard.reason = reason.strip() or None
            if evidence is not None:
                hazard.evidence = evidence.strip() or None
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard")
            return None

    def _store_custom_hazard_profile(
        self,
        custom_hazard_id: int | None,
        profile: dict[str, object],
    ) -> None:
        if custom_hazard_id is None:
            return
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        profile_key = normalize_for_match(profile_name)[:255]
        if not profile_name or not profile_key:
            return
        try:
            row = self.db.scalar(
                select(CustomHazardProfile).where(
                    CustomHazardProfile.custom_hazard_id == custom_hazard_id,
                    CustomHazardProfile.profile_key == profile_key,
                )
            )
            if row is None:
                row = CustomHazardProfile(
                    custom_hazard_id=custom_hazard_id,
                    profile=profile_name,
                    profile_key=profile_key,
                )
                self.db.add(row)
            row.profile = profile_name
            row.variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip() or None
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "custom_hazard_extraction").strip()[:40] or "custom_hazard_extraction"
            row.metadata_json = self._metadata_to_json(dict(profile))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard profile")

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
            custom_hazard = None
            if source == "custom":
                custom_hazard = self._ensure_custom_hazard(
                    session,
                    name,
                    reason=reason,
                    evidence=evidence,
                )
            hazard = self.db.scalar(
                select(UserHazard).where(
                    UserHazard.user_session_id == user_session.id,
                    UserHazard.name == name,
                )
            )
            if hazard is None:
                hazard = UserHazard(
                    user_session_id=user_session.id,
                    custom_hazard_id=custom_hazard.id if custom_hazard else None,
                    system_hazard_id=system_hazard.id if system_hazard else None,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    name=name,
                    source=source,
                )
                self.db.add(hazard)
            hazard.source = source
            if custom_hazard is not None:
                hazard.custom_hazard_id = custom_hazard.id
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

    def _selected_hazard_reference(
        self,
        session_id: str,
        session: ChatSession,
        hazard_name: str | None = None,
    ) -> dict[str, int | None]:
        hazard = (hazard_name or session.selected_hazard or session.accepted_custom_hazard or "").strip()
        reference = {
            "user_session_id": None,
            "user_hazard_id": None,
            "custom_hazard_id": None,
            "system_hazard_id": None,
            "additional_hazard_id": None,
        }
        user_session = self._ensure_user_session(session_id, session)
        if user_session is not None:
            reference["user_session_id"] = user_session.id
        if not hazard:
            return reference
        source = self._selected_user_hazard_source(session, hazard)
        if source == "system":
            system_hazard = self._ensure_system_hazard(session, hazard)
            reference["system_hazard_id"] = system_hazard.id if system_hazard else None
            return reference
        if source == "additional":
            reference["additional_hazard_id"] = self._selected_additional_hazard_id(session, hazard)
            return reference

        custom_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=session.accepted_custom_hazard_evidence,
        )
        if custom_hazard is not None:
            session.accepted_custom_hazard_id = custom_hazard.id
            session.accepted_custom_hazard = hazard
            reference["custom_hazard_id"] = custom_hazard.id
            return reference

        if session.selected_hazard_record_id is not None:
            existing = self.db.get(UserHazard, session.selected_hazard_record_id)
            if existing is not None:
                reference["user_hazard_id"] = existing.id
                return reference
        record = self._ensure_user_hazard(
            session_id,
            session,
            hazard,
            source=source,
        )
        if record is None:
            return reference
        session.selected_hazard_record_id = record.id
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_record_id = record.id
        reference["user_hazard_id"] = record.id
        return reference

    def _selected_user_hazard_source(self, session: ChatSession, hazard: str) -> str:
        if self._is_saved_custom_hazard(session, hazard) or normalize(hazard) == normalize(
            session.accepted_custom_hazard or ""
        ):
            return "custom"
        if self._is_additional_hazard(session, hazard):
            return "additional"
        return "system"

    def _selected_additional_hazard_id(self, session: ChatSession, hazard: str) -> int | None:
        if session.country_id is None or session.sector_id is None:
            return None
        hazard_id = self.db.scalar(
            select(AdditionalHazard.id).where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
                func.lower(AdditionalHazard.name) == hazard.casefold(),
            )
        )
        return int(hazard_id) if isinstance(hazard_id, int) else None

    def _store_socio_demographic(
        self,
        session: ChatSession,
        profile: str,
        *,
        user_hazard_id: int | None = None,
        custom_hazard_id: int | None = None,
        system_hazard_id: int | None = None,
        additional_hazard_id: int | None = None,
        source: str,
        variable_name: str | None = None,
        explanation: str | None = None,
        statistical_basis: str | None = None,
        metadata: dict[str, object] | None = None,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ) or not profile.strip():
            return
        try:
            user_session = self._ensure_user_session(session.session_key, session)
            if user_session is None:
                return
            clean_profile = profile.strip()
            hazard_name = session.selected_hazard or session.accepted_custom_hazard
            context_query = (
                select(UserHazardSocioDemographic)
                .where(
                    func.lower(UserHazardSocioDemographic.profile) == clean_profile.casefold(),
                    UserHazardSocioDemographic.user_session_id == user_session.id,
                    UserHazardSocioDemographic.user_hazard_id == user_hazard_id,
                    UserHazardSocioDemographic.custom_hazard_id == custom_hazard_id,
                    UserHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    UserHazardSocioDemographic.additional_hazard_id == additional_hazard_id,
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            row = self.db.scalar(context_query.limit(1))
            if row is None:
                row = UserHazardSocioDemographic(
                    user_session_id=user_session.id,
                    user_hazard_id=user_hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    profile=clean_profile,
                    source=source,
                )
                self.db.add(row)
            row.user_session_id = user_session.id
            row.user_hazard_id = user_hazard_id
            row.custom_hazard_id = custom_hazard_id
            row.system_hazard_id = system_hazard_id
            row.additional_hazard_id = additional_hazard_id
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
        *,
        user_session_id: int | None,
        user_hazard_id: int | None,
        custom_hazard_id: int | None,
        system_hazard_id: int | None,
        additional_hazard_id: int | None,
        mitigation_measure: str,
        reason: str,
        target_population: list[str] | None = None,
    ) -> int | None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ):
            return None
        try:
            row = UserMitigationMeasure(
                user_session_id=user_session_id,
                user_hazard_id=user_hazard_id,
                custom_hazard_id=custom_hazard_id,
                system_hazard_id=system_hazard_id,
                additional_hazard_id=additional_hazard_id,
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
        custom_hazard_id: int | None = None,
        system_hazard_id: int | None = None,
        additional_hazard_id: int | None = None,
        mitigation_measure_id: int | None = None,
    ) -> None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            if (
                hazard_id is None
                and custom_hazard_id is None
                and system_hazard_id is None
                and additional_hazard_id is None
            ):
                hazard_reference = self._selected_hazard_reference(session_id, session)
                hazard_id = hazard_reference["user_hazard_id"]
                custom_hazard_id = hazard_reference["custom_hazard_id"]
                system_hazard_id = hazard_reference["system_hazard_id"]
                additional_hazard_id = hazard_reference["additional_hazard_id"]
            self.db.add(
                UserQuestionResponse(
                    user_session_id=user_session.id,
                    user_hazard_id=hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
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
        context = render_prompt_template(
            "llm/deep_dive_context.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/deep_dive_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    user_message=user_message,
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
                "content": render_prompt_template(
                    "llm/stats_deep_dive_history_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
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
        context = render_prompt_template(
            "llm/hazard_names_extraction.txt",
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_names_extraction_user.txt",
                    sector=session.sector,
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

        context = render_prompt_template(
            "llm/hazard_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block or "- No relevant sector-prompt RAG excerpts were found.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
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

        context = render_prompt_template(
            "llm/hazards_and_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazards_and_profiles_extraction_user.txt"
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
        context = render_prompt_template(
            "llm/hazard_block_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_block_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                    count_rule=count_rule,
                    id_rule=id_rule,
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
                        "content": render_prompt_template(
                            "llm/hazard_block_profiles_retry_user.txt",
                            profile_count=len(profiles),
                            expected_count=expected_count,
                            checklist=(
                                "Use this checklist in order: "
                                + ", ".join(predictor_id_list)
                                + ". "
                                if predictor_id_list
                                else ""
                            ),
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
        context = render_prompt_template(
            "llm/single_predictor_profile_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            predictor_entry=predictor_entry,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/single_predictor_profile_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
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
        return extract_json_object_text(value)

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
        user_evidence_context = await self._temporary_evidence_context(session)
        context = render_prompt_template(
            "llm/custom_hazard_stats_validation.txt",
            scope_instruction=self._scope_instruction(session),
            twin_transition_hazard_scope_instruction=(
                self._twin_transition_hazard_scope_instruction()
            ),
            sector_context=sector_context,
            user_evidence_context=user_evidence_context
            or "- No readable user evidence excerpts were indexed for this session.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/custom_hazard_stats_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    existing_hazards=existing_hazards
                    or "- No existing hazards were generated.",
                    hazard=hazard,
                    reason=reason,
                    evidence=evidence or "Not provided",
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
        existing_hazards = self._same_sector_hazard_names_for_duplicate_check(session)
        if not existing_hazards:
            return {"duplicate": False, "match": "", "reason": "", "duplicates": []}

        context = render_prompt_template(
            "llm/hazard_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_duplicate_check_user.txt",
                    existing_hazards="\n".join(f"- {item}" for item in existing_hazards),
                    hazard=hazard,
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

    @classmethod
    def _custom_hazard_sector_mismatch_reason(
        cls,
        session: ChatSession,
        hazard: str,
        reason: str = "",
        evidence: str = "",
    ) -> str | None:
        selected_family = cls._sector_family(session.sector)
        if selected_family not in {"energy", "housing", "transport"}:
            return None

        text = " ".join(
            part.strip()
            for part in (hazard, reason, evidence)
            if isinstance(part, str) and part.strip()
        )
        if not text.strip():
            return None

        scores = cls._sector_signal_scores(text)
        selected_score = scores.get(selected_family, 0)
        other_scores = {
            sector: score
            for sector, score in scores.items()
            if sector != selected_family and score > 0
        }
        if not other_scores:
            return None

        strongest_other, strongest_score = max(
            other_scores.items(),
            key=lambda item: item[1],
        )
        if selected_score == 0:
            if strongest_score < 1:
                return None
        elif strongest_score < selected_score + 2:
            return None

        return (
            f"This appears to be mainly a {cls._sector_display_name(strongest_other)} "
            f"hazard, but the selected sector is "
            f"{session.sector or cls._sector_display_name(selected_family)}. "
            "Please rewrite it so the hazard clearly belongs to the selected sector, "
            "or choose the matching sector before adding it."
        )

    @classmethod
    def _plain_custom_hazard_rejection_reason(
        cls,
        session: ChatSession,
        hazard: str,
        reason: str = "",
        evidence: str = "",
    ) -> str | None:
        text = " ".join(
            part.strip()
            for part in (hazard, reason, evidence)
            if isinstance(part, str) and part.strip()
        )
        normalized = normalize_for_match(text)
        if not normalized:
            return None

        has_transition_mechanism = any(
            phrase in normalized
            for phrase in (
                "green transition",
                "digital transition",
                "twin transition",
                "transition policy",
                "climate policy",
                "decarbonisation",
                "decarbonization",
                "net zero",
                "renewable",
                "electrification",
                "retrofit",
                "renovation policy",
                "heating replacement",
                "heat pump",
                "gas boiler ban",
                "gas phase out",
                "fossil fuel phase out",
                "energy efficiency",
                "carbon price",
                "carbon tax",
                "smart meter",
                "digitalisation",
                "digitalization",
            )
        )
        if has_transition_mechanism:
            return None

        household_safety_signals = (
            "carbon monoxide",
            "co poisoning",
            "gas leak",
            "fire hazard",
            "burn injury",
        )
        domestic_source_signals = (
            "domestic heating",
            "home heating",
            "household heating",
            "cooking",
            "stove",
            "boiler",
            "heater",
            "oven",
            "gas appliance",
        )
        if any(signal in normalized for signal in household_safety_signals) and any(
            signal in normalized for signal in domestic_source_signals
        ):
            sector_text = f" in the {session.sector} sector" if session.sector else ""
            return (
                "Carbon monoxide poisoning from domestic heating or cooking is a "
                f"general household safety risk{sector_text}. To add it as a hazard, "
                "please rewrite it to show the green or digital transition policy "
                "that creates or increases the risk, such as a heating-replacement, "
                "retrofit, electrification, or energy-efficiency policy."
            )

        return None

    @staticmethod
    def _sector_family(sector: str | None) -> str:
        normalized = normalize_for_match(sector or "")
        if "transport" in normalized or "mobility" in normalized:
            return "transport"
        if "housing" in normalized or "home" in normalized or "building" in normalized:
            return "housing"
        if "energy" in normalized or "electric" in normalized or "power" in normalized:
            return "energy"
        return normalized

    @staticmethod
    def _sector_display_name(sector_family: str) -> str:
        return {
            "energy": "Energy",
            "housing": "Housing",
            "transport": "Transport",
        }.get(sector_family, sector_family.title())

    @staticmethod
    def _sector_signal_scores(text: str) -> dict[str, int]:
        normalized = f" {normalize_for_match(text)} "
        phrase_groups: dict[str, tuple[str, ...]] = {
            "transport": (
                "transport",
                "mobility",
                "public transport",
                "public transit",
                "transit",
                "bus",
                "buses",
                "rail",
                "train",
                "trains",
                "tram",
                "metro",
                "vehicle",
                "vehicles",
                "electric vehicle",
                "ev",
                "charging station",
                "charging stations",
                "road",
                "roads",
                "traffic",
                "car",
                "cars",
                "cycling",
                "bicycle",
                "bike",
                "pedestrian",
                "freight",
                "aviation",
            ),
            "energy": (
                "energy",
                "electricity",
                "electric",
                "power",
                "grid",
                "renewable",
                "renewables",
                "solar",
                "wind",
                "utility bill",
                "utility bills",
                "utility arrears",
                "energy bill",
                "energy bills",
                "tariff",
                "tariffs",
                "fuel poverty",
                "energy poverty",
                "heat pump",
                "heat pumps",
                "clean heating",
            ),
            "housing": (
                "housing",
                "home",
                "homes",
                "house",
                "houses",
                "building",
                "buildings",
                "dwelling",
                "dwellings",
                "apartment",
                "apartments",
                "residential",
                "retrofit",
                "retrofits",
                "renovation",
                "renovations",
                "insulation",
                "tenant",
                "tenants",
                "renter",
                "renters",
                "landlord",
                "landlords",
                "rent",
                "rents",
                "housing cost",
                "housing costs",
                "energy inefficient homes",
                "poorly insulated",
            ),
        }
        return {
            sector: sum(
                1
                for phrase in phrases
                if f" {normalize_for_match(phrase)} " in normalized
            )
            for sector, phrases in phrase_groups.items()
        }

    async def _review_custom_hazard_input(
        self, session: ChatSession, hazard: str
    ) -> dict[str, object] | None:
        context = render_prompt_template(
            "llm/custom_hazard_input_classifier.txt",
            sector=session.sector or "Not selected",
            country=session.country or "Not selected",
            region=session.region or "Not selected",
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Selected sector: {session.sector or 'Not selected'}\n"
                    f"User input: {hazard}\n\n"
                    "Return exactly ACCEPT, or REJECT followed by one concise reason."
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

        return self._parse_custom_hazard_classifier_response(response)

    async def _review_custom_hazard_context(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> dict[str, object] | None:
        context = render_prompt_template(
            "llm/custom_hazard_context_review.txt",
            scope_instruction=self._scope_instruction(session),
            country=session.country or "Not selected",
            region=session.region or "Not selected",
            sector=session.sector or "Not selected",
        )
        user_content = (
            f"Hazard: {hazard}\n"
            f"Reason: {reason}\n"
            f"Evidence: {evidence or 'Not provided'}\n"
            f"Clarification: {clarification or 'Not provided'}"
        )
        response = await ask_llm_chat(
            context=context,
            messages=[{"role": "user", "content": user_content}],
            temperature=0,
            max_tokens=280,
        )
        if is_llm_unavailable_response(response):
            return None
        try:
            parsed = json.loads(self._extract_json_object(response))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        status = normalize(str(parsed.get("status") or ""))
        valid = bool(parsed.get("valid"))
        reason_text = re.sub(
            r"\s+",
            " ",
            normalize_markdown_text(str(parsed.get("reason") or "")),
        ).strip("`*_ ")
        question = re.sub(
            r"\s+",
            " ",
            normalize_markdown_text(str(parsed.get("question") or "")),
        ).strip("`*_ ")
        if status == normalize("clarification"):
            return {
                "status": "clarification",
                "valid": False,
                "reason": reason_text or "Clarification is needed.",
                "question": question
                or "Could you clarify which people or households are affected by this hazard?",
            }
        if status == normalize("reject") or not valid:
            return {
                "status": "reject",
                "valid": False,
                "reason": reason_text
                or "The hazard and reason are not clear enough to save for this context.",
                "question": "",
            }
        return {
            "status": "accept",
            "valid": True,
            "reason": reason_text or "The custom hazard is clear enough.",
            "question": "",
        }

    async def _extract_custom_hazard_affected_population_profiles(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> list[dict[str, str]]:
        option_rows = self._target_population_option_rows()
        option_catalogue = "\n".join(
            f"- {int(row.id)} | {row.question}: {row.option}" for row in option_rows
        )
        context = render_prompt_template(
            "llm/custom_hazard_population_extraction.txt",
            scope_instruction=self._scope_instruction(session),
        )
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Country: {session.country or 'Not selected'}\n"
                        f"Region: {session.region or 'Not selected'}\n"
                        f"Sector: {session.sector or 'Not selected'}\n"
                        f"Hazard: {hazard}\n"
                        f"Reason: {reason}\n"
                        f"Evidence: {evidence or 'Not provided'}\n"
                        f"Clarification: {clarification or 'Not provided'}\n\n"
                        "Saved target population options:\n"
                        f"{option_catalogue or '- No saved target population options found.'}"
                    ),
                }
            ],
            temperature=0,
            max_tokens=700,
        )
        profiles = self._parse_hazard_profile_items(response)
        cleaned: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            cleaned.append(
                {
                    **profile,
                    "name": name,
                    "profile": name,
                    "source": "custom_hazard_extraction",
                    "statistical_basis": str(
                        profile.get("statistical_basis")
                        or "Extracted from the validated custom hazard and reason."
                    )[:600],
                }
            )
        matched_profiles = self._attach_target_population_matches_to_profiles(
            cleaned,
            option_rows,
            trust_option_ids=True,
        )
        if matched_profiles:
            return matched_profiles
        return self._profiles_from_target_population_option_ids(
            self._selected_target_population_option_ids(session),
            option_rows,
            reason=reason,
        )

    @staticmethod
    def _profiles_from_target_population_option_ids(
        option_ids: set[int],
        option_rows: list[object],
        *,
        reason: str,
    ) -> list[dict[str, str]]:
        if not option_ids:
            return []
        rows_by_id = {int(row.id): row for row in option_rows}
        profiles: list[dict[str, str]] = []
        for option_id in sorted(option_ids):
            row = rows_by_id.get(option_id)
            if row is None:
                continue
            question = str(row.question or "").strip()
            option = str(row.option or "").strip()
            name = f"{question}: {option}" if question and option else option or question
            if not name:
                continue
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "variable_name": question,
                    "explanation": "Matched to a saved affected population group.",
                    "statistical_basis": reason[:600],
                    "source": "custom_hazard_extraction",
                    "target_population_option_ids": [option_id],
                    "target_population_labels": [name],
                }
            )
        return profiles

    def _target_population_option_rows(self) -> list[object]:
        return list(
            self.db.execute(
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
        )

    def _attach_target_population_matches_to_profiles(
        self,
        profiles: list[dict[str, str]],
        option_rows: list[object] | None = None,
        *,
        trust_option_ids: bool = False,
    ) -> list[dict[str, str]]:
        rows = option_rows if option_rows is not None else self._target_population_option_rows()
        if not rows:
            return profiles
        rows_by_id = {int(row.id): row for row in rows}
        allowed_ids = set(rows_by_id)
        for profile in profiles:
            support_text = " ".join(
                str(profile.get(field) or "")
                for field in (
                    "name",
                    "profile",
                    "variable_name",
                    "explanation",
                    "statistical_basis",
                )
            )
            matched_ids = self._coerce_target_population_option_ids(
                profile.get("target_population_option_ids"),
                allowed_ids,
                rows_by_id,
                support_text,
                trust_ids=trust_option_ids,
            )
            metadata = profile.get("metadata")
            if isinstance(metadata, dict):
                matched_ids.update(
                    self._coerce_target_population_option_ids(
                        metadata.get("target_population_option_ids"),
                        allowed_ids,
                        rows_by_id,
                        support_text,
                        trust_ids=trust_option_ids,
                    )
                )
            matched_ids.update(
                self._deterministic_target_population_option_ids(profile, rows)
            )
            ordered_ids = [
                int(row.id)
                for row in rows
                if int(row.id) in matched_ids
            ]
            if not ordered_ids:
                profile["target_population_option_ids"] = []
                profile["target_population_labels"] = []
                continue
            profile["target_population_option_ids"] = ordered_ids
            profile["target_population_labels"] = [
                f"{rows_by_id[option_id].question}: {rows_by_id[option_id].option}"
                for option_id in ordered_ids
                if option_id in rows_by_id
            ]
            metadata = profile.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["target_population_option_ids"] = ordered_ids
            metadata["target_population_labels"] = list(profile["target_population_labels"])
            profile["metadata"] = metadata
        return profiles

    @classmethod
    def _coerce_target_population_option_ids(
        cls,
        value: object,
        allowed_ids: set[int],
        rows_by_id: dict[int, object],
        support_text: str,
        *,
        trust_ids: bool = False,
    ) -> set[int]:
        if not isinstance(value, list):
            return set()
        ids: set[int] = set()
        for raw_id in value:
            try:
                option_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if option_id not in allowed_ids or option_id not in rows_by_id:
                continue
            if trust_ids or cls._target_population_option_is_supported_by_text(
                support_text,
                rows_by_id[option_id],
            ):
                ids.add(option_id)
        return ids

    @staticmethod
    def _parse_custom_hazard_classifier_response(response: str) -> dict[str, object] | None:
        cleaned = str(response or "").strip()
        if not cleaned:
            return None
        first_line, _, rest = cleaned.partition("\n")
        label_match = re.match(r"^\s*(ACCEPT|REJECT|CLARIFICATION)\b\s*:?\s*(.*)$", first_line, re.IGNORECASE)
        label = label_match.group(1).casefold() if label_match else first_line.strip().strip(":").casefold()
        inline_detail = label_match.group(2).strip() if label_match else ""
        if label == "accept":
            return {
                "status": "Valid",
                "valid": True,
                "reason": "The input is within the European green or digital transition hazard scope.",
                "suggestions": [],
            }
        if label == "reject":
            reason = inline_detail
            if not reason:
                reason = rest.strip()
            reason = re.sub(r"\s+", " ", normalize_markdown_text(reason)).strip("`*_ ")
            return {
                "status": "Invalid",
                "valid": False,
                "reason": reason
                or (
                    "Please enter a hazard, risk, vulnerability, unintended consequence, "
                    "or negative impact related to Europe's Green or Digital Transition "
                    "policies in the selected sector."
                ),
                "suggestions": [],
            }
        if label.startswith("clarification"):
            question = inline_detail
            if not question:
                question = rest.strip()
            if not question:
                question = (
                    "Could you clarify how this relates to hazards of Europe's "
                    "Green or Digital Transition policies?"
                )
            return {
                "status": "Ambiguous",
                "valid": False,
                "reason": question,
                "suggestions": [],
            }
        return None

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
        context = render_prompt_template(
            "llm/input_quality_validation.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/input_quality_validation_user.txt",
                    purpose=purpose,
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=(
                        session.selected_hazard
                        or session.pending_hazard
                        or "Not provided"
                    ),
                    field_text=field_text or "- No text provided",
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
        context = render_prompt_template(
            "llm/clarification_answer_quality.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/clarification_answer_quality_user.txt",
                    answer=answer,
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
        context = render_prompt_template(
            "llm/profile_names_validation.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/profile_names_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "Not provided",
                    profiles="\n".join(f"- {profile}" for profile in profiles),
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
        context = render_prompt_template(
            "llm/socio_demographic_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/socio_demographic_duplicate_check_user.txt",
                    existing_context=existing_context,
                    proposed_profiles="\n".join(f"- {item}" for item in dgs),
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
        context = render_prompt_template(
            "llm/dgs_stats_validation.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/dgs_stats_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    pending_profiles=self._format_pending_additional_dgs(session),
                    confirmed_profiles=format_all_dgs(session),
                    reason=reason or "Not provided",
                    evidence=evidence or "Not provided",
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
        context = render_prompt_template("llm/mitigation_clarity_assessment.txt")
        clarification_block = self._mitigation_clarification_history_block(
            session,
            clarification_answer,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_clarity_assessment_user.txt",
                    country=session.country or "Not selected",
                    region=session.region or "Not selected",
                    sector=session.sector or "Not selected",
                    selected_hazard=session.selected_hazard
                    or session.accepted_custom_hazard
                    or "Not selected",
                    target_population=self._mitigation_target_population_text(session),
                    mitigation_measure=mitigation_measure or "Not provided",
                    reason=reason or "Not provided",
                    evidence=evidence or "Not provided",
                    clarification_history=clarification_block,
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
        context = render_prompt_template("llm/mitigation_groundedness_validation.txt")
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_groundedness_validation_user.txt",
                    support_label=support_label,
                    country=session.country,
                    sector=session.sector,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    clarification_history=clarification_block,
                    evidence=evidence or "Not provided",
                    support_context=support_context
                    or "- No relevant support excerpts were found.",
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
        context = render_prompt_template(
            "llm/grounded_mitigation_synthesis.txt",
            support_label=support_label,
            support_context=support_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/grounded_mitigation_synthesis_user.txt",
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    selected_hazard=session.selected_hazard or "Not provided",
                    target_population=self._mitigation_target_population_text(session),
                    affected_groups=format_all_dgs(session),
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
        context = load_nested_prompt_file("llm/claim_entailment_verifier.txt")
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/claim_entailment_verifier_user.txt",
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    selected_hazard=session.selected_hazard or "Not provided",
                    target_population=self._mitigation_target_population_text(session),
                    affected_groups=format_all_dgs(session),
                    support_context=support_context,
                    claim_text=claim_text,
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
        temporary_context = await self._temporary_evidence_context(session)
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
        if inline_results:
            query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
            inline_results = await self.grounding_models.ground_results(query, inline_results)
        inline_context = self._format_full_knowledge_results(inline_results)
        return "\n".join(part for part in (temporary_context, inline_context) if part).strip()

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

    async def _temporary_evidence_context(self, session: ChatSession) -> str:
        if not session.session_key:
            return ""
        try:
            rows = self.db.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeDocument.user_id == self.user_id,
                    KnowledgeDocument.scope == "temporary",
                    KnowledgeDocument.session_key == session.session_key,
                )
                .order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            ).all()
        except Exception:
            logger.exception("Temporary evidence lookup failed during validation")
            return ""
        results = [
            {
                "document_id": document.id,
                "title": document.title,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "page_number": chunk.page_number,
                "score": None,
                "content": chunk.content,
            }
            for chunk, document in rows
        ]
        return self._format_full_knowledge_results(results)

    @staticmethod
    def _format_full_knowledge_results(results: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Knowledge source")
            page = result.get("page_number")
            page_label = f", page {page}" if page else ""
            source_uri = str(result.get("source_uri") or "").strip()
            source_label = f", source {source_uri}" if source_uri else ""
            content = str(result.get("content") or "").strip()
            if content:
                lines.append(f"- [S{index}] {title}{page_label}{source_label}: {content}")
        return "\n".join(lines)

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
        if ChatService._has_evidence_url_reference(evidence):
            return True
        content = ChatService._inline_evidence_content(evidence)
        if content:
            return not content.casefold().startswith("unable to extract evidence")
        lowered = evidence.casefold()
        if "unable to extract evidence" in lowered:
            return False
        if "evidence url:" in lowered:
            return True
        if re.search(
            r"^Evidence file:\s*.+\.(pdf|docx|md|txt)\b",
            evidence,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return True
        if "evidence file:" in lowered:
            return False
        return True

    @staticmethod
    def _has_evidence_url_reference(evidence: str | None) -> bool:
        if not evidence:
            return False
        return bool(
            re.search(
                r"^Evidence URL:\s*https?://\S+",
                evidence,
                flags=re.IGNORECASE | re.MULTILINE,
            )
        )

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
            and not line.split(":", 1)[1].strip().casefold().startswith(
                "unable to extract evidence"
            )
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

        for group in list(grouped_examples.values())[:1]:
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

            sections.append(
                f"### {self._normalize_current_policy_measure_title(measure)}\n\n"
                + "\n".join(details)
            )

        return "\n\n".join(sections)

    @staticmethod
    def _ensure_practical_considerations_intro(markdown: str) -> str:
        intro = (
            "This section translates the selected hazard and affected profiles into "
            "practical design considerations for mitigation. It highlights issues to "
            "check before choosing a measure, such as delivery barriers, targeting, "
            "and implementation risks."
        )
        heading = ChatService._policy_section_heading(
            "General considerations to mitigate the negative effects",
            intro,
        )
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return heading
        cleaned = ChatService._strip_policy_section_heading(
            cleaned,
            "Practical Considerations",
        )
        cleaned = ChatService._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
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
        if cleaned.casefold().lstrip().startswith(
            "## general considerations to mitigate the negative effects"
        ):
            cleaned = ChatService._strip_policy_section_heading(
                cleaned,
                "General considerations to mitigate the negative effects",
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
    def _normalize_current_policy_measure_title(title: str) -> str:
        cleaned = normalize_markdown_text(str(title or "")).strip()
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
        if not cleaned:
            return "Current implementation example"
        return cleaned[:1].upper() + cleaned[1:]

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
    def _practical_considerations_json_to_markdown(response: str) -> tuple[str, list[str]]:
        raw = str(response or "").strip()
        if not raw:
            return "", []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            extracted = ChatService._extract_json_object(raw)
            if extracted == "{}":
                return raw, []
            try:
                payload = json.loads(extracted)
            except json.JSONDecodeError:
                return raw, []
        if not isinstance(payload, dict):
            return raw, []

        title = ChatService._clean_practical_json_text(
            payload.get("title"),
            default="# Practical Considerations",
        )
        sections: list[str] = [title]
        panel_items: list[str] = []
        seen_panel_items: set[str] = set()
        themes = payload.get("themes")
        if not isinstance(themes, list):
            themes = []

        for theme in themes:
            if not isinstance(theme, dict):
                continue
            heading = ChatService._clean_practical_json_text(theme.get("heading"))
            heading_title = ChatService._markdown_heading_title(heading)
            if not heading_title:
                continue
            heading = f"## {heading_title}"
            panel_key = normalize_for_match(heading_title)
            if panel_key and panel_key not in seen_panel_items:
                seen_panel_items.add(panel_key)
                panel_items.append(heading_title)

            block: list[str] = [heading]
            summary = ChatService._clean_practical_json_text(theme.get("summary"))
            if summary:
                block.extend(["", summary])

            concerns = theme.get("concerns")
            if isinstance(concerns, list):
                cleaned_concerns = [
                    ChatService._clean_practical_json_bullet(concern)
                    for concern in concerns
                ]
                cleaned_concerns = [concern for concern in cleaned_concerns if concern]
                if cleaned_concerns:
                    block.extend(["", *cleaned_concerns])

            sections.append("\n".join(block).strip())

        return "\n\n".join(section for section in sections if section.strip()), panel_items

    @staticmethod
    def _clean_practical_json_text(value: object, default: str = "") -> str:
        cleaned = str(value or "").strip()
        cleaned = re.sub(r"^```(?:json|markdown|md)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or default

    @staticmethod
    def _clean_practical_json_bullet(value: object) -> str:
        cleaned = ChatService._clean_practical_json_text(value)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned).strip()
        return f"- {cleaned}" if cleaned else ""

    @staticmethod
    def _markdown_heading_title(value: object) -> str:
        cleaned = ChatService._clean_practical_json_text(value)
        cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        return cleaned.strip(" -#:\t\r\n")

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
        cleaned = ChatService._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
        )
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

        skipping_nested_bullet = False
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            leading_whitespace = len(raw_line) - len(raw_line.lstrip(" \t"))
            if not line:
                flush_current()
                skipping_nested_bullet = False
                continue
            if re.match(r"^\s*#{1,6}\s+", line):
                flush_current()
                skipping_nested_bullet = False
                continue
            bullet_match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
            if bullet_match:
                if leading_whitespace >= 2:
                    skipping_nested_bullet = True
                    continue
                flush_current()
                skipping_nested_bullet = False
                current.append(bullet_match.group(1).strip())
                continue
            if skipping_nested_bullet:
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
        colon_index = cleaned.find(":")
        if 4 <= colon_index <= 120:
            cleaned = cleaned[:colon_index]
        else:
            sentence_match = re.match(r"^(.{18,120}?[.!?])\s+", cleaned)
            if sentence_match:
                cleaned = sentence_match.group(1).rstrip(".!?")
        return cleaned.strip(" -•\t\r\n.:")

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
        context = load_nested_prompt_file("llm/new_policy_suggestion.txt")
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
        reason_error = self._local_mitigation_reason_error(reason)
        if reason_error:
            return reason_error
        return None

    def _local_mitigation_reason_error(self, reason: str) -> str | None:
        if self._is_invalid_user_text(reason):
            return (
                "The reason appears to contain gibberish, keyboard mashing, or text that "
                "is not meaningful. Please explain why this measure would reduce the "
                "selected hazard for the affected groups."
            )

        normalized = normalize_for_match(reason)
        compact = compact_for_match(reason)
        if len(compact) < 8:
            return "The reason is too short. Please explain the mechanism in a little more detail."

        non_answer_patterns = (
            r"\b(?:i\s+)?don\s*t\s+know\b",
            r"\b(?:i\s+)?do\s+not\s+know\b",
            r"\bno\s+idea\b",
            r"\bnot\s+sure\b",
            r"\bunsure\b",
            r"\bcan(?:not|t)\s+say\b",
            r"\bdon\s*t\s+have\s+(?:a\s+)?reason\b",
            r"\bno\s+reason\b",
            r"\bnot\s+applicable\b",
            r"\bn/?a\b",
        )
        if any(re.search(pattern, normalized) for pattern in non_answer_patterns):
            return (
                "The reason is ambiguous. Please explain how the mitigation measure "
                "would reduce the selected hazard for the affected groups."
            )

        mechanism_terms = {
            "reduce",
            "reduces",
            "reducing",
            "lower",
            "lowers",
            "lowering",
            "prevent",
            "prevents",
            "preventing",
            "avoid",
            "avoids",
            "avoiding",
            "support",
            "supports",
            "supporting",
            "help",
            "helps",
            "helping",
            "improve",
            "improves",
            "improving",
            "increase",
            "increases",
            "increasing",
            "provide",
            "provides",
            "providing",
            "protect",
            "protects",
            "protecting",
            "enable",
            "enables",
            "enabling",
            "ensure",
            "ensures",
            "ensuring",
            "address",
            "addresses",
            "addressing",
            "mitigate",
            "mitigates",
            "mitigating",
            "target",
            "targets",
            "targeting",
            "because",
            "by",
            "through",
            "so",
        }
        tokens = set(normalized.split())
        if len(tokens) < 4 or not tokens & mechanism_terms:
            return (
                "The reason is too vague or unrelated to the mitigation context. "
                "Please describe the mechanism, for example how the measure lowers "
                "exposure, cost, exclusion, or vulnerability for the affected groups."
            )

        return None

    def _existing_mitigation_records_for_selected_hazard(
        self, session: ChatSession
    ) -> list[UserMitigationMeasure]:
        hazard_name = session.selected_hazard or session.accepted_custom_hazard
        if not hazard_name:
            return []
        try:
            if self._is_saved_custom_hazard(session, hazard_name):
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
            else:
                system_hazard_id = None
                additional_hazard_id = None
                if self._is_additional_hazard(session, hazard_name):
                    additional_hazard_id = self._selected_additional_hazard_id(
                        session, hazard_name
                    )
                else:
                    system_hazard_id = self.db.scalar(
                        select(SystemHazard.id).where(
                            SystemHazard.sector_id == session.sector_id,
                            func.lower(SystemHazard.name) == hazard_name.casefold(),
                        )
                    )
                if system_hazard_id is None and additional_hazard_id is None:
                    return []
                query = (
                    select(UserMitigationMeasure)
                    .join(UserSession, UserSession.id == UserMitigationMeasure.user_session_id)
                    .where(
                        UserSession.country_id == session.country_id,
                        UserSession.region_id.is_(None)
                        if session.region_id is None
                        else UserSession.region_id == session.region_id,
                        UserSession.sector_id == session.sector_id,
                        UserMitigationMeasure.system_hazard_id == system_hazard_id,
                        UserMitigationMeasure.additional_hazard_id == additional_hazard_id,
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

        context = render_prompt_template(
            "llm/mitigation_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_duplicate_check_user.txt",
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    existing_measures="\n".join(f"- {item}" for item in existing_measures),
                    mitigation_measure=mitigation_measure,
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
        context = render_prompt_template(
            "llm/evaluation_answer_validation.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
            knowledge_context=knowledge_context
            or "- No relevant knowledge-base excerpts were found.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/evaluation_answer_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    mitigation_reason=session.mitigation_reason or "Not provided",
                    question_category=question["category"],
                    question=question["question"],
                    score=score,
                    reason=reason or "Not provided",
                    evidence=evidence or "Not provided",
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
