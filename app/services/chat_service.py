import logging
import re
from datetime import datetime, timezone

from app.llm import ask_llm_chat
from app.config import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Country,
    EvaluationQuestion,
    QuestionOption,
    Region,
    UserSession,
)
from app.schemas import ChatResponse, Option
from app.services.chat_formatters import (
    format_all_dgs,
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_json import parse_json_array
from app.services.chat_context_retrieval import ChatContextRetrievalMixin
from app.services.chat_hazard_creation import ChatHazardCreationMixin
from app.services.chat_hazard_steps import ChatHazardStepsMixin
from app.services.chat_custom_hazard_population_steps import (
    ChatCustomHazardPopulationStepsMixin,
)
from app.services.chat_grounded_question_steps import ChatGroundedQuestionStepsMixin
from app.services.chat_auto_user import ChatAutoUserMixin
from app.services.chat_mitigation_creation import ChatMitigationCreationMixin
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_navigation_steps import ChatNavigationStepsMixin
from app.services.chat_options import (
    DG_REASON_EVIDENCE_OPTIONS,
    MITIGATION_REVIEW_OPTIONS,
    POST_SECTOR_OPTIONS,
    REASON_CONFIRMATION_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    best_fuzzy_label,
    compact_for_match,
    exact_option_label,
    match_option_label,
    normalize,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_mitigation_clarity_response,
)
from app.services.chat_persistence import ChatPersistenceMixin
from app.services.chat_session import ChatSession, session_store
from app.services.validation_service import ChatValidationServiceMixin
from app.services.chat_profile_rendering import ChatProfileRenderingMixin
from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.knowledge_base import KnowledgeBaseService
from app.services.eurostat_service import EurostatService
from app.services.grounding_models import GroundingModelService
from app.services.hazard_ranking_service import HazardRankingService
from app.services.message_renderer import markdown_to_html, render_message
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.voice_summary import generate_voice_summary

logger = logging.getLogger(__name__)


class ChatService(
    ChatAutoUserMixin,
    ChatContextRetrievalMixin,
    ChatCustomHazardPopulationStepsMixin,
    ChatValidationServiceMixin,
    ChatHazardCreationMixin,
    ChatGroundedQuestionStepsMixin,
    ChatHazardStepsMixin,
    ChatMitigationCreationMixin,
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

    def __init__(
        self,
        db: Session,
        user_id: str | None = None,
        *,
        is_admin: bool = False,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.is_admin = bool(is_admin)
        self.settings = get_settings()
        self.grounding_models = GroundingModelService()
        self.eurostat = EurostatService(db)
        self.hazard_ranking = HazardRankingService(db)

    async def handle_message(
        self,
        message: str,
        session_id: str | None,
        validation_mode: str = "strict",
        crowd_sourcing_enabled: bool = False,
    ) -> ChatResponse:
        clean_message = message.strip()
        validation_mode = self._validation_mode(validation_mode)
        crowd_sourcing_enabled = bool(crowd_sourcing_enabled)

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
            session.validation_mode = validation_mode
            session.crowd_sourcing_enabled = crowd_sourcing_enabled
            response = self._country_step(
                current_session_id,
                session,
                await self._intro_message_from_llm(current_session_id),
            )
            self._attach_other_options(response, session)
            await self._attach_voice_summary(response)
            self._finalize_chat_response(current_session_id, session, response)
            return response

        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        session.validation_mode = validation_mode
        session.crowd_sourcing_enabled = crowd_sourcing_enabled
        self._ensure_user_session(current_session_id, session)

        response = await self._chat_response(current_session_id, session, clean_message)
        self._attach_persisted_session_counts(current_session_id, session, response)
        self._attach_other_options(response, session)
        if clean_message:
            if not response.error:
                self._record_activity(current_session_id, session, "message_received", clean_message)
            self._record_chat_message(
                current_session_id,
                session,
                "user",
                self._chat_message_display_content(clean_message),
            )
        await self._attach_voice_summary(response)
        self._finalize_chat_response(current_session_id, session, response)
        return response


    async def handle_stats_deep_dive_dialog(
        self,
        message: str,
        session_id: str | None,
        validation_mode: str = "strict",
        crowd_sourcing_enabled: bool = False,
    ) -> ChatResponse:
        clean_message = message.strip()
        validation_mode = self._validation_mode(validation_mode)
        crowd_sourcing_enabled = bool(crowd_sourcing_enabled)
        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        session.validation_mode = validation_mode
        session.crowd_sourcing_enabled = crowd_sourcing_enabled
        self._ensure_user_session(current_session_id, session)

        if session.sector is None:
            response = ChatResponse(
                session_id=current_session_id,
                step=session.phase,
                bot_message=self.invalid_message,
                options=[],
                session=session.summary(),
                error=True,
            )
            await self._attach_voice_summary(response)
            return response

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
        self._attach_persisted_session_counts(current_session_id, session, response)
        response.other_options = []
        await self._attach_voice_summary(response)
        return response

    @staticmethod
    async def _attach_voice_summary(response: ChatResponse) -> None:
        if response.voice_summary or not response.bot_message.strip():
            return
        response.voice_summary = await generate_voice_summary(response.bot_message)

    async def generate_auto_user_message(
        self,
        session_id: str | None,
        validation_mode: str = "strict",
        crowd_sourcing_enabled: bool = False,
    ) -> dict[str, object]:
        validation_mode = self._validation_mode(validation_mode)
        crowd_sourcing_enabled = bool(crowd_sourcing_enabled)
        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        session.session_key = current_session_id
        session.validation_mode = validation_mode
        session.crowd_sourcing_enabled = crowd_sourcing_enabled
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

        open_selection_response = await self._open_selection_response_from_any_step(
            current_session_id,
            session,
            clean_message,
            current_phase="sector",
        )
        if open_selection_response is not None:
            return open_selection_response

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

        question_response = await self._handle_anytime_grounded_question(
            current_session_id,
            session,
            clean_message,
        )
        if question_response is not None:
            return question_response

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
        payload = parse_json_array(raw_json)
        if not isinstance(payload, list):
            return self._additional_dg_question_step(
                session_id,
                session,
                error_reason="Please submit valid socio-demographic selections.",
            )

        questions_by_id = {
            str(question["id"]): question
            for question in (session.target_population_questions or [])
            if "id" in question
        }
        recorded_any = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id") or "").strip()
            if not question_id:
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
        payload = parse_json_array(raw_json)
        if not isinstance(payload, list):
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please submit valid affected population group selections.",
            )

        questions_by_id = {
            str(question["id"]): question
            for question in (session.target_population_questions or [])
            if "id" in question
        }
        recorded_any = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id") or "").strip()
            if not question_id:
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
        parsed = parse_mitigation_clarity_response(response)
        if session.validation_mode == "easy" and not parsed.get("error"):
            dimensions = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
            unresolved_count = sum(
                1
                for status in dimensions.values()
                if str(status or "").upper() != "CLEAR"
            )
            if dimensions and unresolved_count <= 1:
                parsed["clear"] = True
        return parsed

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
