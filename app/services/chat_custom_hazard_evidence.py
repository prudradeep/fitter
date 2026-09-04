import html
import logging
import re

from app.schemas import ChatResponse
from app.services.chat_hazard_duplicates import hazard_duplicate_payloads
from app.services.chat_options import (
    HAZARD_DUPLICATE_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    HAZARD_EVIDENCE_DECISION_OPTIONS,
    HAZARD_EVIDENCE_INPUT_OPTIONS,
    exact_option_label,
    match_option_label,
    normalize,
)
from app.services.chat_parsers import (
    normalize_evidence_message,
    open_evidence_decision_action,
    parse_reason_evidence,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.custom_hazard_validation import default_custom_hazard_state
from app.services.enums import ChatPhase, CustomHazardStatus
from app.services.knowledge_base import TEMPORARY_KB_SCOPE, KnowledgeBaseService
from app.services.message_renderer import markdown_to_html, render_message

logger = logging.getLogger("app.services.chat_hazard_creation")


class ChatCustomHazardEvidenceMixin:
    async def _start_custom_hazard_grounding_check(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
        state = self._custom_hazard_state(session)
        state["resolved_hazard_text"] = hazard
        state["raw_text"] = hazard
        return await self._run_custom_hazard_dimension_check(session_id, session)

    def _hazard_reason_evidence_step(
        self, session_id: str, session: ChatSession, hazard: str
    ) -> ChatResponse:
        return self._hazard_reason_step(session_id, session, hazard)

    def _hazard_reason_step(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        *,
        error: bool = False,
        message: str | None = None,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        transition_custom_hazard(session, ChatPhase.ADD_HAZARD_REASON)
        session.pending_hazard_reason = None
        session.pending_hazard_evidence = None
        session.suggested_duplicate_hazard = None
        session.suggested_duplicate_hazard_record_id = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = None
        session.pending_hazard_title_clarification_question = None
        bot_message = message or markdown_to_html(
            "## Clarification Needed\n\n"
            f"Proposed hazard:\n\n- **{hazard}**\n\n"
            "Please clarify why this should be treated as a hazard in this context. "
            "Include the reason or justification in your answer."
        )
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["resolved_hazard_text"] = hazard
            state["raw_text"] = str(state.get("raw_text") or hazard).strip()
            state["status"] = CustomHazardStatus.DRAFT.value
            state["message"] = "Reason or justification is required before grounding validation."
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_clarification",
                bot_message=bot_message,
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="textarea",
                error=error,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazard_clarification",
            bot_message=bot_message,
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=error,
        )

    def _capture_hazard_reason(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.pending_hazard_reason = None
            session.pending_hazard_evidence = None
            transition_custom_hazard(session, ChatPhase.HAZARDS)
            return self._hazards_step(session_id, session)

        hazard = session.pending_hazard or ""
        if not hazard:
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            session.custom_hazard = default_custom_hazard_state()
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        parsed_reason, _ = parse_reason_evidence(message)
        reason = (parsed_reason or message or "").strip()
        reason_error = self._text_quality_rejection_reason(reason, "short description")
        if reason_error:
            return self._hazard_reason_step(
                session_id,
                session,
                hazard,
                error=True,
                message=markdown_to_html(
                    reason_error
                ),
            )

        session.pending_hazard_reason = reason
        session.pending_hazard_evidence = ""
        return self._hazard_evidence_decision_step(session_id, session)

    def _hazard_evidence_decision_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error: bool = False,
        message: str | None = None,
    ) -> ChatResponse:
        transition_custom_hazard(session, ChatPhase.ADD_HAZARD_EVIDENCE_DECISION)
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["evidence_decision_asked"] = True
        bot_message = message or markdown_to_html(
            "Evidence is optional, but it can make this hazard easier to validate "
            "and more useful to other users. Do you have evidence for this hazard, "
            "such as a report, article, dataset, policy document, or URL?\n\n"
            "Choose **Yes** to add evidence, or **No** to continue without it."
        )
        if isinstance(session.custom_hazard, dict):
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_evidence_decision",
                bot_message=bot_message,
                options=HAZARD_EVIDENCE_DECISION_OPTIONS,
                error=error,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazard_evidence_decision",
            bot_message=bot_message,
            options=HAZARD_EVIDENCE_DECISION_OPTIONS,
            session=session.summary(),
            error=error,
        )

    async def _handle_hazard_evidence_decision(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_EVIDENCE_DECISION_OPTIONS)
        open_action = open_evidence_decision_action(message)
        if open_action == "evidence":
            return await self._validate_staged_custom_hazard(
                session_id,
                session,
                normalize_evidence_message(message),
            )
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_EVIDENCE_DECISION_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or open_action or message)
        if action == normalize("Yes"):
            return self._hazard_evidence_input_step(session_id, session)
        if action == normalize("No"):
            return await self._validate_staged_custom_hazard(session_id, session, "")
        return self._hazard_evidence_decision_step(
            session_id,
            session,
            error=True,
            message=self.invalid_message,
        )

    def _hazard_evidence_input_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error: bool = False,
        message: str | None = None,
    ) -> ChatResponse:
        transition_custom_hazard(session, ChatPhase.ADD_HAZARD_EVIDENCE_INPUT)
        bot_message = message or markdown_to_html(
            "Great. Paste a URL here or attach a supported file: PDF, DOCX, MD, "
            "or TXT. If you do not have it ready, choose **Skip** and continue."
        )
        if isinstance(session.custom_hazard, dict):
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_evidence",
                bot_message=bot_message,
                options=HAZARD_EVIDENCE_INPUT_OPTIONS,
                input_mode="evidence_only",
                error=error,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazard_evidence",
            bot_message=bot_message,
            options=HAZARD_EVIDENCE_INPUT_OPTIONS,
            session=session.summary(),
            input_mode="evidence_only",
            error=error,
        )

    async def _capture_hazard_evidence(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_EVIDENCE_INPUT_OPTIONS)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            return self._hazard_evidence_decision_step(session_id, session)
        if normalize(exact_label or message) == normalize("Skip"):
            return await self._validate_staged_custom_hazard(session_id, session, "")

        evidence = normalize_evidence_message(message)
        if not evidence:
            return self._hazard_evidence_input_step(
                session_id,
                session,
                error=True,
                message="Please add an evidence URL or file, or choose Skip.",
            )
        evidence_url = self._evidence_url(evidence)
        if evidence_url and session.session_key:
            try:
                ingestion = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope=TEMPORARY_KB_SCOPE,
                    session_key=session.session_key,
                ).ingest_url(
                    evidence_url,
                    allow_lexical_only=True,
                )
            except Exception as exc:
                logger.exception("Failed to extract custom-hazard evidence URL")
                return self._hazard_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=f"The evidence URL could not be read: {exc}",
                )
            if ingestion.get("error"):
                return self._hazard_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=(
                        "The evidence URL was received, but no readable content could be "
                        f"extracted: {ingestion.get('detail') or 'unknown extraction error'}"
                    ),
                )
            document_id = str(ingestion.get("document_id") or "").strip()
            if document_id:
                evidence = f"{evidence}\nTemporary evidence document ID: {document_id}"
        return await self._validate_staged_custom_hazard(session_id, session, evidence)

    async def _validate_staged_custom_hazard(
        self, session_id: str, session: ChatSession, evidence: str
    ) -> ChatResponse:
        reason = str(session.pending_hazard_reason or "").strip()
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["reason"] = reason
            state["evidence"] = evidence
            session.pending_hazard_evidence = evidence
            if not evidence:
                return await self._route_custom_hazard_next_action(
                    session_id,
                    session,
                )
            return await self._run_custom_hazard_dimension_check(session_id, session)
        if not reason:
            return self._hazard_reason_step(
                session_id,
                session,
                session.pending_hazard or "New hazard",
                error=True,
                message=markdown_to_html(
                    "Please answer the clarification question and include the reason "
                    "or justification before continuing."
                ),
            )
        session.pending_hazard_evidence = evidence
        lines = [f"Reason: {reason}"]
        if evidence:
            lines.append(f"Evidence: {evidence}")
        return await self._validate_custom_hazard(session_id, session, "\n".join(lines))

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
        transition_custom_hazard(
            session,
            ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION
            if isinstance(session.custom_hazard, dict)
            else ChatPhase.HAZARD_DUPLICATE_SUGGESTION,
        )
        suggested_summary = self._custom_hazard_summary_for_duplicate(
            session,
            suggested_hazard,
        )
        message = render_message(
            "hazard_duplicate.md",
            hazard=hazard,
            suggested_hazard=suggested_hazard or "the suggested existing hazard",
            suggested_summary=suggested_summary,
            reason=reason or "The proposed hazard appears similar to an existing hazard.",
        )
        message = self._ensure_duplicate_hazard_summary_visible(
            message,
            suggested_summary,
        )
        if session.phase == "custom_hazard_duplicate_confirmation":
            state = self._custom_hazard_state(session)
            state["duplicate_candidates"] = hazard_duplicate_payloads(
                hazard,
                [suggested_hazard],
            )
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

    @staticmethod
    def _ensure_duplicate_hazard_summary_visible(message: str, summary: str) -> str:
        """Support DB-backed duplicate templates created before summaries existed."""
        summary = str(summary or "").strip()
        if not summary or re.search(
            r"<strong>\s*Summary\s*:</strong>", message, flags=re.IGNORECASE
        ):
            return message
        summary_html = (
            '<p><strong>Summary:</strong> '
            f"{html.escape(summary)}</p>"
        )
        updated = re.sub(
            r"(<p>\s*Suggested existing hazard:\s*</p>\s*<ul>.*?</ul>)",
            rf"\1{summary_html}",
            message,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return updated if updated != message else f"{message}{summary_html}"

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
                transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
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
            return await self._start_custom_hazard_grounding_check(session_id, session, hazard)

        if action in {normalize("Explore suggested hazard"), normalize("Use existing hazard")}:
            suggested_hazard = session.suggested_duplicate_hazard or ""
            hazard = self._match_hazard(suggested_hazard, session) or self._fuzzy_hazard(
                suggested_hazard,
                session,
            )
            if hazard is None:
                transition_custom_hazard(session, ChatPhase.HAZARDS)
                return self._hazards_step(session_id, session)
            self._clear_selected_hazard_context(session)
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.selected_hazard = hazard
            transition_custom_hazard(session, ChatPhase.SOCIO_DEMOGRAPHIC_REVIEW)
            self._record_activity(session_id, session, "hazard_selected", hazard)
            return await self._hazard_profiles_response(session_id, session, hazard)

        if action in {normalize("Write hazard again"), normalize("Edit custom hazard")}:
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.pending_hazard_title_clarification_question = None
            session.pending_hazard_title_clarification_answers = []
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            session.custom_hazard = default_custom_hazard_state()
            session.custom_hazard_input_history = []
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
        session.pending_hazard = None
        session.pending_hazard_reason = None
        session.pending_hazard_evidence = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = clarification
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = reason
        session.accepted_custom_hazard_evidence = evidence or "Not provided"
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None

        await self._ensure_custom_hazard_generated_title(session, hazard)

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
