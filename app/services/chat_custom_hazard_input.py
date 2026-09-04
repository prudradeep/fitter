import re

from app.schemas import ChatResponse
from app.services.chat_options import (
    HAZARD_ENTRY_OPTIONS,
    compact_for_match,
    exact_option_label,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.custom_hazard_text_rules import deterministic_custom_hazard_input_review
from app.services.custom_hazard_validation import default_custom_hazard_state
from app.services.enums import ChatPhase, CustomHazardStatus
from app.services.message_renderer import markdown_to_html, render_message

MAX_HAZARD_TITLE_CLARIFICATION_ROUNDS = 3


class ChatCustomHazardInputMixin:
    def _reset_hazard_title_clarification_state(self, session: ChatSession) -> None:
        session.pending_hazard_title_clarification_question = None
        session.pending_hazard_title_clarification_answers = []
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["title_clarification_round"] = 0
            state["title_clarification_questions"] = []
            state["title_clarification_answers"] = []

    def _initialize_custom_hazard_title_state(
        self,
        session: ChatSession,
        hazard: str,
    ) -> dict[str, object]:
        session.custom_hazard = default_custom_hazard_state()
        session.generated_custom_hazard_title = None
        state = self._custom_hazard_state(session)
        normalized_hazard = normalize_for_match(hazard)
        state.update(
            {
                "raw_text": hazard,
                "normalized_text": normalized_hazard,
                "resolved_hazard_text": hazard,
                "selected_country": session.country or "",
                "selected_region": session.region or "",
                "selected_sector": session.sector or "",
                "title_validation_status": None,
                "title_validation_code": None,
                "title_validation_reason": None,
                "title_validation_confidence": None,
                "title_clarification_round": 0,
                "title_clarification_questions": [],
                "title_clarification_answers": [],
                "transition_link": None,
                "detected_sector": None,
                "affected_groups": [],
                "negative_consequence": None,
                "duplicate_candidates": [],
                "validation_round": 0,
                "dimension_scores": {},
                "status": "pending_title_validation",
            }
        )
        session.pending_hazard_title_clarification_question = None
        session.pending_hazard_title_clarification_answers = []
        return state

    def _store_custom_hazard_title_review(
        self,
        session: ChatSession,
        review: dict[str, object],
        *,
        fallback_hazard: str,
    ) -> str:
        state = self._custom_hazard_state(session)
        status = self._custom_hazard_title_status(review)
        resolved_hazard = self._custom_hazard_resolved_title(review, fallback_hazard)
        state["resolved_hazard_text"] = resolved_hazard
        state["title_validation_status"] = status
        state["title_validation_code"] = str(review.get("validation_code") or "").strip()
        state["title_validation_reason"] = str(review.get("reason") or "").strip()
        state["title_validation_confidence"] = review.get("confidence")
        state["transition_link"] = (
            review.get("transition_link")
            if isinstance(review.get("transition_link"), dict)
            else state.get("transition_link")
        )
        sector_validation = review.get("sector_validation")
        if isinstance(sector_validation, dict):
            state["detected_sector"] = str(sector_validation.get("detected_sector") or "").strip() or None
        elif review.get("detected_sector"):
            state["detected_sector"] = str(review.get("detected_sector") or "").strip()
        affected_group = review.get("affected_group")
        if isinstance(affected_group, dict) and isinstance(affected_group.get("groups"), list):
            state["affected_groups"] = [
                {"group": str(group).strip(), "reason": "Identified during hazard-title validation.", "source": "title_validation"}
                for group in affected_group.get("groups") or []
                if str(group).strip()
            ]
        elif isinstance(review.get("affected_groups"), list):
            state["affected_groups"] = [
                {"group": str(group).strip(), "reason": "Identified during hazard-title validation.", "source": "title_validation"}
                for group in review.get("affected_groups") or []
                if str(group).strip()
            ]
        negative_consequence = str(review.get("negative_consequence") or "").strip()
        if negative_consequence:
            state["negative_consequence"] = negative_consequence
        state["status"] = {
            "valid": "title_validated",
            "invalid": "title_rejected",
            "needs_clarification": "awaiting_title_clarification",
        }.get(status, state.get("status") or "pending_title_validation")
        return resolved_hazard

    @staticmethod
    def _custom_hazard_title_status(review: dict[str, object]) -> str:
        status = str(review.get("status") or "").strip().casefold()
        if status in {"valid", "invalid", "needs_clarification"}:
            return status
        if bool(review.get("valid")):
            return "valid"
        if status in {"ambiguous", "clarification"} or review.get("clarification_question"):
            return "needs_clarification"
        return "invalid"

    @staticmethod
    def _custom_hazard_resolved_title(review: dict[str, object], fallback_hazard: str) -> str:
        normalized_hazard = str(review.get("normalized_hazard") or "").strip()
        if normalized_hazard and normalized_hazard.lower() not in {
            "cleaned version of the submitted hazard",
            "not provided",
        }:
            return normalized_hazard
        return fallback_hazard

    def _custom_hazard_title_rewrite_suggestion(self, review: dict[str, object]) -> str:
        return str(
            review.get("suggested_rewrite")
            or review.get("clarification_question")
            or ""
        ).strip()

    def _custom_hazard_title_rejection_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        review: dict[str, object],
        dimension: str | None = None,
    ) -> ChatResponse:
        reason = str(review.get("reason") or "Please rewrite this as a concrete transition-related hazard.").strip()
        session.pending_hazard = hazard
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        self._store_custom_hazard_title_review(session, review, fallback_hazard=hazard)
        self._mark_custom_hazard_dimension(
            session,
            dimension or self._custom_hazard_rejection_dimension(reason),
            status="REJECTED",
            score=0,
            reason=reason,
        )
        self._refresh_custom_hazard_duplicate_candidates(session, hazard)
        return self._custom_hazard_clarification_step(
            session_id,
            session,
            rejected=True,
        )

    def _custom_hazard_input_quality_rejection_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        reason: str,
    ) -> ChatResponse:
        session.pending_hazard = None
        if not isinstance(session.custom_hazard, dict):
            session.custom_hazard = default_custom_hazard_state()
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_input",
            bot_message=markdown_to_html(
                f"## Rejected\n\n**Reason:** {reason}\n\n"
                "Please enter a clear, meaningful hazard name."
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=True,
        )

    def _custom_hazard_title_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        review: dict[str, object],
    ) -> ChatResponse:
        question = str(
            review.get("clarification_question")
            or review.get("reason")
            or "What transition measure causes the harm, and who or what is affected?"
        ).strip()
        state = self._custom_hazard_state(session)
        existing_question = session.pending_hazard_title_clarification_question
        repeat_question = bool(existing_question and normalize(existing_question) == normalize(question))
        if not repeat_question:
            round_number = int(state.get("title_clarification_round") or 0) + 1
            state["title_clarification_round"] = round_number
        questions = list(state.get("title_clarification_questions") or [])
        if not repeat_question:
            questions.append(question)
        state["title_clarification_questions"] = questions
        state["status"] = "awaiting_title_clarification"
        session.pending_hazard = self._custom_hazard_resolved_title(review, hazard)
        session.pending_hazard_title_clarification_question = question
        if session.pending_hazard_title_clarification_answers is None:
            session.pending_hazard_title_clarification_answers = []
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION)
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_title_clarification",
            bot_message=render_message(
                "hazard_clarification.md",
                hazard=session.pending_hazard or hazard,
                question=question,
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=False,
        )

    def _custom_hazard_title_clarification_answer_error(
        self,
        session: ChatSession,
        answer: str,
    ) -> str | None:
        normalized = normalize_for_match(answer)
        compact = compact_for_match(answer)
        if not normalized:
            return "Please answer the clarification question before continuing."
        if self._is_invalid_user_text(answer) or len(compact) < 8:
            return (
                "That does not provide enough information to clarify the hazard. "
                "Please name the affected group and the concrete harm or risk."
            )
        if match_option_label(answer, HAZARD_ENTRY_OPTIONS, threshold=0.72) is not None:
            return (
                "That looks like a navigation option, not a clarification. Please answer "
                "with the affected group, the concrete harm, and the transition measure causing it."
            )
        non_answer_phrases = {
            "yes",
            "yeah",
            "yep",
            "no",
            "nope",
            "maybe",
            "ok",
            "okay",
            "fine",
            "good",
            "bad",
            "continue",
            "next",
            "skip",
            "help",
            "i dont know",
            "i don t know",
            "i do not know",
            "dont know",
            "don t know",
            "do not know",
            "idk",
            "not sure",
            "unsure",
            "no idea",
            "dont have an answer",
            "do not have an answer",
            "unknown",
            "na",
            "n a",
            "none",
            "nothing",
            "something",
            "anything",
            "people",
            "users",
            "everyone",
        }
        non_answer_compacts = {compact_for_match(phrase) for phrase in non_answer_phrases}
        if normalized in non_answer_phrases or compact in non_answer_compacts:
            return (
                "That does not clarify the hazard. Please identify who is affected, "
                "what harm they face, and how the transition measure causes or worsens it."
            )
        non_clarifying_patterns = (
            r"^(?:what|how|why|where|when|who)\b",
            r"\bwhat\s+to\s+do\b",
            r"\bwhat\s+now\b",
            r"\bwhat\s+next\b",
            r"\bwhat\s+should\s+i\s+(?:do|write|say|answer)\b",
            r"\bhow\s+should\s+i\s+(?:do|write|say|answer)\b",
            r"\bcan\s+you\s+(?:help|tell|explain|suggest)\b",
            r"\bplease\s+(?:help|tell|explain|suggest)\b",
            r"\bi\s+need\s+help\b",
            r"\bnot\s+sure\b",
            r"\bunsure\b",
            r"\bno\s+idea\b",
        )
        if answer.strip().endswith("?") or any(
            re.search(pattern, normalized) for pattern in non_clarifying_patterns
        ):
            return (
                "That is a question or request, not a clarification. Please answer "
                "with the affected group, the concrete harm, and the transition measure causing it."
            )
        return None

    def _custom_hazard_title_clarification_reask(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        reason: str,
    ) -> ChatResponse:
        question = (
            session.pending_hazard_title_clarification_question
            or "What transition measure causes the harm, and who or what is affected?"
        )
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_title_clarification",
            bot_message=(
                f"{reason}\n\n"
                + render_message(
                    "hazard_clarification.md",
                    hazard=hazard,
                    question=question,
                )
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=True,
        )


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
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = (
            str(state.get("reason") or "").strip()
            or self._custom_hazard_dimension_reason(state)
        )
        session.accepted_custom_hazard_evidence = (
            str(state.get("evidence") or "").strip() or "Not provided"
        )
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None
        # The affected-group review has been confirmed. Generate and review
        # the summary before any shared hazard record is persisted.
        return await self._custom_hazard_summary_review_step(session_id, session)

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
            session.pending_hazard_title_clarification_question = None
            session.pending_hazard_title_clarification_answers = []
            transition_custom_hazard(session, ChatPhase.HAZARDS)
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

        normalized_hazard = normalize_for_match(hazard)
        input_history = session.custom_hazard_input_history or []
        if normalized_hazard in {
            normalize_for_match(previous)
            for previous in input_history
            if str(previous or '').strip()
        }:
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step='custom_hazard_input',
                bot_message=markdown_to_html(
                    'You have already entered this same hazard. Please provide a different '
                    'hazard or choose an existing hazard from the list.'
                ),
                options=HAZARD_ENTRY_OPTIONS,
                input_mode='textarea',
                error=True,
            )
        session.custom_hazard_input_history = [*input_history, hazard]

        meaning_check = await self._validate_text_meaning(hazard)
        quality_reason = self._text_quality_rejection_reason(
            hazard,
            "hazard name",
            result=meaning_check,
        )
        if quality_reason:
            return self._custom_hazard_input_quality_rejection_response(
                session_id,
                session,
                reason=quality_reason,
            )
        if meaning_check.classification in {"GIBBERISH", "UNCERTAIN"}:
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review={
                    "status": "invalid",
                    "valid": False,
                    "validation_code": "gibberish_input",
                    "reason": quality_reason or "Please enter a clear, meaningful hazard name.",
                    "confidence": 1.0 if meaning_check.classification == "GIBBERISH" else 0.5,
                },
            )

        self._initialize_custom_hazard_title_state(session, hazard)

        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            hazard,
        )
        if plain_rejection_reason:
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review={
                    "status": "invalid",
                    "valid": False,
                    "validation_code": "not_twin_transition_related",
                    "reason": plain_rejection_reason,
                    "confidence": 0.95,
                },
            )

        deterministic_review = deterministic_custom_hazard_input_review(
            selected_sector=session.sector,
            hazard=hazard,
        )
        if deterministic_review is not None and self._custom_hazard_title_status(deterministic_review) == "needs_clarification":
            self._store_custom_hazard_title_review(
                session,
                deterministic_review,
                fallback_hazard=hazard,
            )
            return self._custom_hazard_title_clarification_step(
                session_id,
                session,
                hazard=hazard,
                review=deterministic_review,
            )

        if deterministic_review is not None and not bool(deterministic_review.get("valid")):
            if str(deterministic_review.get("validation_code") or "") == "personal_preference":
                reason = str(deterministic_review.get("reason") or "This reads as a personal preference or opinion, not a policy hazard.")
                self._mark_custom_hazard_dimension(
                    session,
                    self._custom_hazard_rejection_dimension(reason),
                    status="REJECTED",
                    score=0,
                    reason=reason,
                )
                return self._custom_hazard_input_quality_rejection_response(
                    session_id,
                    session,
                    reason=reason,
                )
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review=deterministic_review,
                dimension=self._custom_hazard_rejection_dimension(
                    str(deterministic_review.get("reason") or "")
                ),
            )

        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            hazard,
        )
        if sector_mismatch_reason:
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review={
                    "status": "invalid",
                    "valid": False,
                    "validation_code": "sector_mismatch",
                    "reason": sector_mismatch_reason,
                    "confidence": 0.95,
                    "suggested_rewrite": self._custom_hazard_sector_rewrite_suggestion(
                        session,
                        hazard,
                    ),
                },
                dimension="selected_sector_fit",
            )

        if deterministic_review is not None and bool(deterministic_review.get("valid")):
            resolved_hazard = self._store_custom_hazard_title_review(
                session,
                deterministic_review,
                fallback_hazard=hazard,
            )
            return await self._continue_valid_custom_hazard(
                session_id,
                session,
                resolved_hazard,
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
        title_status = self._custom_hazard_title_status(hazard_review)
        if title_status == "invalid":
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review=hazard_review,
            )
        if title_status == "needs_clarification":
            self._store_custom_hazard_title_review(
                session,
                hazard_review,
                fallback_hazard=hazard,
            )
            return self._custom_hazard_title_clarification_step(
                session_id,
                session,
                hazard=hazard,
                review=hazard_review,
            )

        resolved_hazard = self._store_custom_hazard_title_review(
            session,
            hazard_review,
            fallback_hazard=hazard,
        )
        return await self._continue_valid_custom_hazard(session_id, session, resolved_hazard)

    async def _handle_custom_hazard_title_clarification(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.pending_hazard_title_clarification_question = None
            session.pending_hazard_title_clarification_answers = []
            session.custom_hazard = None
            transition_custom_hazard(session, ChatPhase.HAZARDS)
            return self._hazards_step(session_id, session)

        answer = message.strip()
        state = self._custom_hazard_state(session)
        hazard = str(
            session.pending_hazard
            or state.get("resolved_hazard_text")
            or state.get("raw_text")
            or ""
        ).strip()
        if not hazard:
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            session.custom_hazard = default_custom_hazard_state()
            session.custom_hazard_input_history = []
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )
        if not answer:
            return self._custom_hazard_title_clarification_reask(
                session_id,
                session,
                hazard=hazard,
                reason="Please answer the clarification question before continuing.",
            )
        answer_error = self._custom_hazard_title_clarification_answer_error(session, answer)
        if answer_error:
            return self._custom_hazard_title_clarification_reask(
                session_id,
                session,
                hazard=hazard,
                reason=answer_error,
            )

        answers = list(session.pending_hazard_title_clarification_answers or [])
        answers.append(answer)
        session.pending_hazard_title_clarification_answers = answers
        state["title_clarification_answers"] = answers
        clarification_context = self._custom_hazard_title_clarification_context(session, answer)
        review = await self._review_custom_hazard_input(
            session,
            hazard,
            clarification_context=clarification_context,
        )
        if review is None:
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_title_clarification",
                bot_message=(
                    "I could not review this clarification because the local validation "
                    "model is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="textarea",
                error=True,
            )
        title_status = self._custom_hazard_title_status(review)
        if title_status == "valid":
            resolved_hazard = self._store_custom_hazard_title_review(
                session,
                review,
                fallback_hazard=hazard,
            )
            session.pending_hazard_title_clarification_question = None
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            return await self._continue_valid_custom_hazard(
                session_id,
                session,
                resolved_hazard,
            )
        if title_status == "invalid":
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review=review,
            )

        self._store_custom_hazard_title_review(session, review, fallback_hazard=hazard)
        if int(state.get("title_clarification_round") or 0) >= MAX_HAZARD_TITLE_CLARIFICATION_ROUNDS:
            return self._custom_hazard_title_rejection_response(
                session_id,
                session,
                hazard=hazard,
                review={
                    **review,
                    "status": "invalid",
                    "valid": False,
                    "validation_code": "unclear_hazard",
                    "reason": str(review.get("reason") or "").strip()
                    or (
                        "The submitted title and clarification do not identify the "
                        "transition measure, negative consequence, or selected-sector mechanism clearly enough."
                    ),
                },
            )
        return self._custom_hazard_title_clarification_step(
            session_id,
            session,
            hazard=hazard,
            review=review,
        )

    def _custom_hazard_title_clarification_context(
        self,
        session: ChatSession,
        latest_answer: str,
    ) -> dict[str, object]:
        state = self._custom_hazard_state(session)
        questions = list(state.get("title_clarification_questions") or [])
        answers = list(session.pending_hazard_title_clarification_answers or [])
        history = [
            {"question": question, "answer": answer}
            for question, answer in zip(questions, answers)
        ]
        if questions and len(history) < len(questions):
            history.append({"question": questions[-1], "answer": latest_answer})
        return {
            "original_hazard": str(state.get("raw_text") or "").strip(),
            "normalized_hazard": session.pending_hazard or str(state.get("resolved_hazard_text") or "").strip(),
            "clarification_history": history,
            "country": session.country,
            "region": session.region,
            "sector": session.sector,
        }

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
            transition_custom_hazard(session, ChatPhase.HAZARDS)
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
            # Carry the user's clarification into the next evidence/grounding
            # pass when no standalone reason has been stored yet.
            if not str(state.get("reason") or "").strip():
                state["reason"] = answer
                session.pending_hazard_reason = answer
            state["message"] = "Scores were recalculated after clarification."
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
            return await self._run_custom_hazard_dimension_check(session_id, session)

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
                if not self._custom_hazard_context_clarification_is_satisfied(
                    session,
                    str(context_review["question"]),
                ):
                    return self._hazard_clarification_step(
                        session_id,
                        session,
                        hazard,
                        str(context_review["question"]),
                    )
                context_review = {"status": "accept", "valid": True}
            if not context_review["valid"]:
                session.pending_hazard_reason = None
                session.pending_hazard_evidence = None
                session.pending_hazard_clarification_question = None
                session.pending_hazard_clarification_answer = None
                transition_custom_hazard(session, ChatPhase.ADD_HAZARD_EVIDENCE)
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message(
                        "hazard_validation_failed.md",
                        sector=session.sector,
                        reason=str(context_review["reason"]),
                        rewrite_suggestion="",
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
                transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
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
        transition_custom_hazard(session, ChatPhase.ADD_HAZARD_CLARIFICATION)
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
                    rewrite_suggestion=self._custom_hazard_sector_rewrite_suggestion(
                        session,
                        hazard,
                    ),
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="textarea",
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
            self._duplicate_hazard_names_for_check(session),
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

        return await self._start_custom_hazard_grounding_check(session_id, session, hazard)
