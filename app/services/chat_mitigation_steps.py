from app.schemas import ChatResponse, Option
from app.services.chat_formatters import format_all_dgs
from app.services.chat_options import (
    MITIGATION_DUPLICATE_OPTIONS,
    REASON_CONFIRMATION_OPTIONS,
    exact_option_label,
    fuzzy_score,
    match_option_label,
    normalize,
)
from app.services.chat_parsers import parse_mitigation_reason, parse_reason_evidence
from app.services.chat_session import ChatSession
from app.services.message_renderer import markdown_to_html, render_message


class ChatMitigationStepsMixin:
    async def _create_mitigation_measure_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        target_population_confirmed: bool = False,
    ) -> ChatResponse:
        previous_phase = str(session.phase or "")
        if (
            not target_population_confirmed
            and previous_phase
            not in {
                "mitigation_target_population_review",
                "mitigation_clarity",
                "mitigation_reason",
                "mitigation_review",
            }
        ):
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
        exact_label = self._exact_or_safe_fuzzy_option(
            message,
            REASON_CONFIRMATION_OPTIONS,
        )
        if exact_label is not None and normalize(exact_label) != normalize(message):
            return self._fuzzy_confirmation_step(session_id, session, exact_label)
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

    @staticmethod
    def _mitigation_clarity_options() -> list[Option]:
        return [Option(id=1, label="Write mitigation measure again")]

    @staticmethod
    def _mitigation_duplicate_confirmation_options() -> list[Option]:
        return [
            Option(id=1, label="Yes, show existing mitigation report"),
            Option(id=2, label="No, continue with my proposal"),
        ]

    @staticmethod
    def _mitigation_existing_report_options() -> list[Option]:
        return [
            Option(id=1, label="Yes, continue with my proposed mitigation"),
            Option(id=2, label="No, write another mitigation measure"),
        ]

    @staticmethod
    def _exact_or_safe_fuzzy_option(message: str, options: list[Option]) -> str | None:
        exact_label = exact_option_label(message, options)
        if exact_label is not None:
            return exact_label

        normalized_message = normalize(message)
        if len(normalized_message) < 3:
            return None

        fuzzy_label = match_option_label(message, options)
        if fuzzy_label is None:
            return None

        # Avoid accidental confirmation from very short or weakly related text.
        if fuzzy_score(message, fuzzy_label) < 88:
            return None
        return fuzzy_label

    async def _capture_mitigation_measure(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        mitigation_measure, _ = parse_mitigation_reason(message)
        mitigation_measure = mitigation_measure or message.strip()
        self._clear_mitigation_clarity_state(session)
        self._clear_mitigation_validation_state(session)
        session.suggested_mitigation_measure_id = None
        session.suggested_mitigation_measure_name = None
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

        if self._is_invalid_user_text(mitigation_measure):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=(
                        "The mitigation measure appears to contain gibberish, "
                        "keyboard mashing, or unrecognizable text."
                    ),
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
        if duplicate_check is not None and duplicate_check.get("duplicate"):
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
            options=self._mitigation_duplicate_confirmation_options(),
            session=session.summary(),
            error=False,
        )

    def _handle_mitigation_duplicate_suggestion(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._mitigation_duplicate_confirmation_options()
        exact_label = self._exact_or_safe_fuzzy_option(message, options)
        action = normalize(exact_label or message)

        if action in {
            normalize("Yes"),
            normalize("Yes, show existing mitigation report"),
        }:
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
                options=self._mitigation_existing_report_options(),
                session=session.summary(),
                error=False,
            )

        if action in {
            normalize("No"),
            normalize("No, continue with my proposal"),
        }:
            return self._continue_pending_mitigation_reason_step(session_id, session)

        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _handle_mitigation_duplicate_report(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._mitigation_duplicate_confirmation_options()
        exact_label = self._exact_or_safe_fuzzy_option(message, options)
        action = normalize(exact_label or message)

        if action in {
            normalize("Yes"),
            normalize("Yes, show existing mitigation report"),
        }:
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

        local_reason_error = self._local_mitigation_reason_error(reason)
        if local_reason_error:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=local_reason_error,
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
                        "Evidence is optional. If you provide evidence, it must be "
                        "readable supporting content. Please provide a DOI/URL with "
                        "extractable text or a supported file: PDF, DOCX, MD, or TXT."
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

    def _plain_reason_from_unlabelled_message(self, message: str) -> str | None:
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
        return self._strip_wrapping_quotes(stripped)
