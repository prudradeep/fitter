from app.schemas import ChatResponse, Option
from app.llm import ask_llm_chat
from app.services.chat_formatters import format_all_dgs
from app.services.chat_json import parse_json_object
from app.services.chat_options import (
    REASON_CONFIRMATION_OPTIONS,
    exact_option_label,
    fuzzy_score,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_mitigation_reason,
    parse_reason_evidence,
)
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
        if exact_label is None:
            exact_label = self._open_option_label_from_text(
                message,
                REASON_CONFIRMATION_OPTIONS,
            )
        if exact_label is not None and normalize(exact_label) != normalize(message):
            if self._ordinal_index_from_open_text(message) is None:
                return self._fuzzy_confirmation_step(session_id, session, exact_label)
        action = normalize(exact_label or message)
        open_action = self._reason_confirmation_action_from_open_text(message)
        if exact_label is None and open_action is None:
            # Navigation and a complete typed mitigation are deterministic
            # inputs. Handle them before asking the LLM to infer a Yes/No or
            # adoption action, otherwise those inputs can be misclassified.
            selection_response = await self._open_selection_response_from_any_step(
                session_id,
                session,
                message,
                current_phase="sector",
            )
            if selection_response is not None:
                return selection_response
            if self._looks_like_typed_mitigation_measure(message):
                return await self._capture_mitigation_measure(session_id, session, message)
            open_action = await self._reason_confirmation_action_from_llm(session, message)
        if exact_label is None and open_action is not None:
            action = open_action

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
            return await self._adopt_suggested_mitigation_response(session_id, session)

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

    async def _adopt_suggested_mitigation_response(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        mitigation_measure = self._suggested_mitigation_measure_for_context(session)
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
        reason = self._suggested_mitigation_reason_for_adoption(
            session,
            mitigation_measure,
        )
        return self._mitigation_initial_clarification_step(
            session_id,
            session,
            mitigation_measure,
            reason,
        )

    def _suggested_mitigation_measure_for_context(self, session: ChatSession) -> str:
        return (
            str(session.suggested_new_policy_proposal or "").strip()
            or self._current_policy_mitigation_measure(session)
        )

    def _suggested_mitigation_reason_for_adoption(
        self,
        session: ChatSession,
        mitigation_measure: str,
    ) -> str:
        suggested_reason = str(session.suggested_new_policy_reason or "").strip()
        mechanisms = str(
            session.suggested_new_policy_target_group_mechanisms or ""
        ).strip()
        if suggested_reason:
            if mechanisms:
                return f"{suggested_reason} Target-group mechanisms: {mechanisms}"
            return suggested_reason

        parts: list[str] = []
        hazard = session.selected_hazard or session.accepted_custom_hazard
        target_population = ", ".join(session.mitigation_target_population or [])
        if hazard:
            parts.append(f"It is intended to reduce the impact of {hazard}.")
        if target_population:
            parts.append(f"It targets the selected mitigation population: {target_population}.")
        elif session.socio_demographic_profiles:
            parts.append(
                "It is targeted to the affected socio-demographic profiles: "
                + ", ".join(session.socio_demographic_profiles)
                + "."
            )
        if session.practical_considerations:
            parts.append(
                "Available implementation considerations include: "
                + "; ".join(str(item) for item in session.practical_considerations[:3])
                + "."
            )
        if mechanisms:
            parts.append(f"Target-group mechanisms: {mechanisms}")
        if not parts and mitigation_measure:
            parts.append(
                "This adopted proposal should be assessed against the selected "
                "hazard, affected profiles, and local implementation context."
            )
        return " ".join(parts).strip()

    async def _open_selection_response_from_any_step(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        *,
        current_phase: str,
    ) -> ChatResponse | None:
        pending_handler = getattr(self, "_handle_pending_selection_workflow", None)
        if pending_handler is not None:
            pending_response = await pending_handler(session_id, session, message)
            if pending_response is not None:
                return pending_response

        deterministic_selector = getattr(self, "_deterministic_selection_from_text", None)
        apply_selection = getattr(self, "_apply_pending_selection", None)
        dependencies_valid = getattr(self, "_selection_dependencies_are_valid", None)
        invalid_response = getattr(self, "_invalid_selection_response", None)
        if deterministic_selector is not None and apply_selection is not None:
            selection = deterministic_selector(session, message)
            if selection is not None and any(selection.values()):
                if dependencies_valid is not None and not dependencies_valid(
                    session,
                    selection,
                    current_phase,
                ):
                    if invalid_response is not None:
                        return invalid_response(session_id, session, selection)
                    return None
                return await apply_selection(session_id, session, selection)

        invalid_change_response = self._invalid_open_selection_change_response(
            session_id,
            session,
            message,
        )
        if invalid_change_response is not None:
            return invalid_change_response

        navigation_handler = getattr(self, "_open_selection_navigation_response", None)
        if navigation_handler is not None:
            return await navigation_handler(session_id, session, message, current_phase)
        return None

    def _invalid_open_selection_change_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse | None:
        normalized = normalize_for_match(message)
        if not normalized.startswith("change ") or " to " not in normalized:
            return None
        target = normalized.rsplit(" to ", 1)[1].strip()
        checks = [
            ("change country", getattr(self, "_available_country_names", None)),
            ("change region", getattr(self, "_available_region_names", None)),
            ("change sector", getattr(self, "_available_sector_names", None)),
        ]
        for prefix, provider in checks:
            if not normalized.startswith(prefix) or provider is None:
                continue
            labels = provider(session) if prefix != "change country" else provider()
            if target not in {normalize_for_match(label) for label in labels}:
                return ChatResponse(
                    session_id=session_id,
                    step=session.phase or "selection",
                    bot_message=self.invalid_message,
                    options=[],
                    session=session.summary(),
                    error=True,
                )
        return None

    def _open_option_label_from_text(
        self,
        message: str,
        options: list[Option],
    ) -> str | None:
        ordinal = self._ordinal_index_from_open_text(message)
        if ordinal is None:
            return None
        labels = [option.label for option in options]
        index = ordinal if ordinal >= 0 else len(labels) + ordinal
        if index < 0 or index >= len(labels):
            return None
        return labels[index]

    def _ordinal_index_from_open_text(self, message: str) -> int | None:
        ordinal_parser = getattr(self, "_ordinal_index_from_text", None)
        if ordinal_parser is not None:
            return ordinal_parser(message)

        tokens = normalize_for_match(message).split()
        if not tokens:
            return None
        allowed = {"the", "one", "option", "please", "select", "choose", "go", "with"}
        number_words = {
            "first": 1,
            "second": 2,
            "third": 3,
            "fourth": 4,
            "fifth": 5,
            "sixth": 6,
            "seventh": 7,
            "eighth": 8,
            "ninth": 9,
            "tenth": 10,
        }
        ordinal_positions: list[tuple[int, int]] = []
        for index, token in enumerate(tokens):
            value = number_words.get(token)
            if value is None:
                value = self._ordinal_number_from_token(token)
            if value is not None:
                ordinal_positions.append((index, value))
        if tokens == ["last"] or all(token in {*allowed, "last"} for token in tokens):
            return -1
        if len(ordinal_positions) != 1:
            return None
        ordinal_position, ordinal_value = ordinal_positions[0]
        if any(token not in {*allowed, "last", tokens[ordinal_position]} for token in tokens):
            return None
        if "last" in tokens:
            return -ordinal_value
        return ordinal_value - 1

    @staticmethod
    def _ordinal_number_from_token(token: str) -> int | None:
        for suffix in ("st", "nd", "rd", "th"):
            if token.endswith(suffix) and token[: -len(suffix)].isdigit():
                return int(token[: -len(suffix)])
        return int(token) if token.isdigit() else None

    @staticmethod
    def _reason_confirmation_action_from_open_text(message: str) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        yes_phrases = {
            "yes",
            "yes please",
            "continue",
            "go ahead",
            "proceed",
            "start",
            "start writing",
            "i will write one",
            "i want to write one",
            "let me create one",
            "write my own",
            "write my own mitigation",
            "write my own mitigation measure",
            "create manually",
            "create my own",
            "create my own mitigation",
            "create my own mitigation measure",
            "add mitigation",
            "add a mitigation",
            "add mitigation measure",
            "add a mitigation measure",
            "add new mitigation",
            "add a new mitigation",
            "add new mitigation measure",
            "add a new mitigation measure",
            "new mitigation",
            "new mitigation measure",
            "start a new mitigation",
            "start a new mitigation measure",
            "manual",
        }
        no_phrases = {
            "no",
            "no thanks",
            "not now",
            "skip",
            "cancel",
            "stop",
            "do not continue",
        }
        adopt_phrases = {
            "adopt",
            "adopt it",
            "adopt proposal",
            "adopt the proposal",
            "adopt mitigation proposal",
            "adopt mitigation proposal suggested above",
            "use it",
            "use this",
            "use this proposal",
            "use suggested",
            "use suggested proposal",
            "use the suggested proposal",
            "use proposed mitigation",
            "use the proposed mitigation",
            "show proposed mitigation",
            "show the proposed mitigation",
            "show the proposed mitigation measure",
            "show suggested mitigation",
            "show the suggested mitigation",
            "show the suggested mitigation measure",
            "continue with current mitigation measure",
        }
        if normalized in yes_phrases:
            return normalize("Yes")
        if normalized in no_phrases:
            return normalize("No")
        if normalized in adopt_phrases:
            return normalize("Adopt mitigation proposal suggested above")
        if "mitigation" in normalized and any(
            token in normalized for token in ("adopt", "use", "show", "suggested", "proposed")
        ):
            return normalize("Adopt mitigation proposal suggested above")
        if "proposal" in normalized and any(token in normalized for token in ("adopt", "use", "show")):
            return normalize("Adopt mitigation proposal suggested above")
        if (
            "mitigation" in normalized
            and any(
                phrase in normalized
                for phrase in (
                    "dont make sense",
                    "do not make sense",
                    "doesnt make sense",
                    "does not make sense",
                    "not make sense",
                    "none fit",
                    "none of these fit",
                    "none of them fit",
                    "not fit",
                    "does not fit",
                    "dont fit",
                    "do not fit",
                    "missing",
                    "not listed",
                    "not shown",
                )
            )
        ):
            return normalize("Yes")
        if any(
            phrase in normalized
            for phrase in (
                "want to add one",
                "add one",
                "add my own",
                "create my own",
                "write my own",
                "custom mitigation",
                "own mitigation",
                "another mitigation",
                "different mitigation",
                "new measure",
                "missing measure",
            )
        ):
            return normalize("Yes")
        if "mitigation" in normalized and any(
            token in normalized for token in ("add", "create", "new", "write", "manual")
        ):
            return normalize("Yes")
        return None

    async def _reason_confirmation_should_handle_before_quality(
        self,
        session: ChatSession,
        message: str,
    ) -> bool:
        if self._exact_or_safe_fuzzy_option(message, REASON_CONFIRMATION_OPTIONS) is not None:
            return True
        if self._open_option_label_from_text(message, REASON_CONFIRMATION_OPTIONS) is not None:
            return True
        if self._reason_confirmation_action_from_open_text(message) is not None:
            return True
        if self._looks_like_typed_mitigation_measure(message):
            return True
        return await self._reason_confirmation_action_from_llm(session, message) is not None

    async def _reason_confirmation_action_from_llm(
        self,
        session: ChatSession,
        message: str,
    ) -> str | None:
        value = str(message or "").strip()
        if not value:
            return None

        prompt = (
            "Classify a user message shown after the app suggests or asks about "
            "creating a mitigation measure for a selected hazard.\n\n"
            "Return one valid JSON object only:\n"
            '{"action":"write_new_mitigation|adopt_suggested_mitigation|'
            'decline|none","confidence":"high|medium|low","reason":"Brief reason."}\n\n'
            "Use write_new_mitigation when the user wants to add, create, write, "
            "provide, define, or use their own mitigation measure, or says the "
            "suggested/proposed/current mitigation does not fit, does not make "
            "sense, is missing something, or is not the measure they want. The "
            "wording may be informal or indirect.\n"
            "Use adopt_suggested_mitigation only when they want to use/adopt/show "
            "the suggested proposal. Use decline only when they do not want to "
            "continue. Use none for questions, navigation, or unrelated text."
        )
        response = await ask_llm_chat(
            context=prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Country: {session.country or ''}\n"
                        f"Region: {session.region or ''}\n"
                        f"Sector: {session.sector or ''}\n"
                        f"Hazard: {session.selected_hazard or session.accepted_custom_hazard or ''}\n"
                        f"Suggested mitigation: {self._suggested_mitigation_measure_for_context(session)}\n"
                        f"Message: {value}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=120,
            response_format="json",
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return None
        action = str(parsed.get("action") or "").strip().casefold()
        confidence = str(parsed.get("confidence") or "").strip().casefold()
        if confidence not in {"high", "medium"}:
            return None
        return {
            "write_new_mitigation": normalize("Yes"),
            "adopt_suggested_mitigation": normalize(
                "Adopt mitigation proposal suggested above"
            ),
            "decline": normalize("No"),
        }.get(action)

    @staticmethod
    def _looks_like_typed_mitigation_measure(message: str) -> bool:
        normalized = normalize_for_match(message)
        if len(normalized) < 20:
            return False
        if "?" in str(message or ""):
            return False
        navigation_prefixes = (
            "change ",
            "choose ",
            "select ",
            "switch ",
            "go back",
            "back ",
            "previous",
            "start over",
            "reset",
        )
        if normalized.startswith(navigation_prefixes):
            return False
        if any(
            normalized.startswith(prefix)
            for prefix in ("what ", "why ", "how ", "when ", "where ", "which ", "who ")
        ):
            return False
        return True

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
        mitigation_measure, initial_reason = parse_mitigation_reason(message)
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

        meaning_check = await self._validate_text_meaning(mitigation_measure)
        if meaning_check.classification in {"GIBBERISH", "UNCERTAIN"}:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter a clear, meaningful response.",
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
            if "too short" in local_quality_reason.casefold():
                session.pending_mitigation_measure = mitigation_measure
                return await self._start_mitigation_clarification_step(
                    session_id,
                    session,
                    mitigation_measure,
                    initial_reason or "",
                )
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

        input_review = await self._validate_mitigation_measure_only(
            session,
            mitigation_measure,
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
        review_status = str(input_review.get("status") or "").upper()
        if review_status == "NEEDS_CLARIFICATION":
            session.pending_mitigation_measure = mitigation_measure
            return await self._start_mitigation_clarification_step(
                session_id,
                session,
                mitigation_measure,
                initial_reason or "",
            )
        if review_status != "VALID":
            reason = self._mitigation_measure_validation_message(input_review)
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=reason,
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
        return await self._start_mitigation_clarification_step(
            session_id,
            session,
            mitigation_measure,
            initial_reason or "",
        )

    @staticmethod
    def _mitigation_measure_validation_message(review: dict[str, object]) -> str:
        status = str(review.get("status") or "").upper()
        summary = str(review.get("summary") or "").strip()
        clarification = str(review.get("clarification_question") or "").strip()
        suggestion = str(review.get("suggested_improvement") or "").strip()
        parts = []
        if status == "NEEDS_CLARIFICATION":
            parts.append(summary or "The mitigation measure needs clarification.")
            if clarification:
                parts.append(clarification)
        else:
            parts.append(summary or "The mitigation measure is not valid for the selected context.")
        if suggestion:
            parts.append(f"Suggested improvement: {suggestion}")
        return " ".join(part for part in parts if part).strip()

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
        if exact_label is None:
            exact_label = self._open_option_label_from_text(message, options)
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
                    system_inquiry_report=(
                        self._suggested_mitigation_system_inquiry_report(session)
                        if hasattr(self, "_suggested_mitigation_system_inquiry_report")
                        else "- No system inquiry reflections were found for this mitigation measure."
                    ),
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
        options = self._mitigation_existing_report_options()
        exact_label = self._exact_or_safe_fuzzy_option(message, options)
        if exact_label is None:
            exact_label = self._open_option_label_from_text(message, options)
        action = normalize(exact_label or message)

        if action in {
            normalize("Yes"),
            normalize("Yes, continue with my proposed mitigation"),
        }:
            return self._continue_pending_mitigation_reason_step(session_id, session)

        if action in {
            normalize("No"),
            normalize("No, write another mitigation measure"),
        }:
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
        self._clear_mitigation_clarity_state(session)
        session.suggested_mitigation_measure_id = None
        session.suggested_mitigation_measure_name = None
        return self._mitigation_initial_clarification_step(
            session_id,
            session,
            session.pending_mitigation_measure
            or session.mitigation_measure
            or "Your proposed mitigation measure",
            session.pending_mitigation_reason or session.mitigation_reason or "",
        )

    async def _start_mitigation_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        initial_reason: str = "",
    ) -> ChatResponse:
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = initial_reason.strip()
        session.pending_mitigation_evidence = ""
        session.mitigation_evidence_declined = False
        session.mitigation_frozen_inputs = None
        clarity_runner = getattr(self, "_run_mitigation_clarity_track", None)
        if clarity_runner is not None:
            response = await clarity_runner(
                session_id,
                session,
                mitigation_measure,
                session.pending_mitigation_reason or "",
                "",
            )
            if response is not None:
                return response
        return self._mitigation_initial_clarification_step(
            session_id,
            session,
            mitigation_measure,
            session.pending_mitigation_reason or "",
        )

    def _mitigation_initial_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        initial_reason: str = "",
    ) -> ChatResponse:
        session.phase = "mitigation_clarity"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = initial_reason.strip()
        session.pending_mitigation_evidence = ""
        session.mitigation_evidence_declined = False
        session.pending_mitigation_clarity_dimension = "justification_clarity"
        session.mitigation_clarity_turns += 1
        question = (
            "How will this mitigation measure reduce the negative impact of the "
            "selected hazard for the affected profiles, and why is it appropriate "
            "for this context?"
        )
        context_lines = []
        if session.country:
            context_lines.append(f"- **Country:** {session.country}")
        if session.region:
            context_lines.append(f"- **Region:** {session.region}")
        if session.sector:
            context_lines.append(f"- **Sector:** {session.sector}")
        context_block = (
            "\n\nSelected context:\n\n" + "\n".join(context_lines)
            if context_lines
            else ""
        )
        reason_block = (
            f"\n\nReason:\n\n{session.pending_mitigation_reason}\n"
            if session.pending_mitigation_reason
            else ""
        )
        append_message = getattr(self, "_append_mitigation_clarification_message", None)
        if append_message is not None:
            append_message(session, "assistant", question)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=markdown_to_html(
                "### Clarification needed\n\n"
                f"Proposed mitigation measure:\n\n- **{mitigation_measure}**\n\n"
                f"{reason_block}"
                f"{context_block}\n\n"
                "Please answer this clarification question before evidence is collected:\n\n"
                f"1. {question}"
            ),
            options=self._mitigation_clarity_options(),
            session=session.summary(),
            input_mode="textarea",
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

        local_reason_error = self._local_mitigation_reason_error(
            reason,
            mitigation_measure,
        )
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
