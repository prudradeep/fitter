from app.schemas import ChatResponse
from app.services.chat_formatters import format_hazards
from app.services.chat_options import (
    HAZARD_ENTRY_OPTIONS,
    POST_SECTOR_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    exact_option_label,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import default_custom_hazard_state
from app.services.message_renderer import render_message


def is_hazard_action_label(label: str) -> bool:
    return normalize_for_match(label) in {
        "show hazards added by experts",
        "show co created hazards",
        "show listed hazards",
    }


class ChatHazardStepsMixin:
    def _hazards_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        self._hydrate_custom_hazard_profiles(session)
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
        open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
        if open_selection_handler is not None:
            open_selection_response = await open_selection_handler(
                session_id,
                session,
                message,
                current_phase="sector",
            )
            if open_selection_response is not None:
                return open_selection_response
        else:
            navigation_handler = getattr(self, "_open_selection_navigation_response", None)
            if navigation_handler is not None:
                navigation_response = await navigation_handler(
                    session_id,
                    session,
                    message,
                    "sector",
                )
                if navigation_response is not None:
                    return navigation_response

            selection = self._post_sector_selection_from_open_text(session, message)
            if selection is not None:
                apply_selection = getattr(self, "_apply_pending_selection", None)
                if apply_selection is not None:
                    return await apply_selection(session_id, session, selection)

        exact_label = exact_option_label(message, POST_SECTOR_OPTIONS)
        if exact_label is None:
            exact_label = self._post_sector_label_from_open_text(message)
        if exact_label is None:
            fuzzy_label = match_option_label(message, POST_SECTOR_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            return self._custom_hazard_input_step(session_id, session)

        if action == normalize("Refresh hazards and DGs"):
            await self._refresh_session_hazards(session_id, session)
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

    def _custom_hazard_input_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        self._clear_selected_hazard_context(session)
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

    async def _refresh_session_hazards(
        self, session_id: str, session: ChatSession
    ) -> None:
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
        cache_store = getattr(self, "_store_hazard_listing_cache", None)
        if cache_store is not None:
            cache_store(session)
        self._record_activity(
            session_id,
            session,
            "hazards_refreshed",
            session.sector or "",
        )

    def _stats_deep_dive_dialog_step(
        self,
        session_id: str,
        session: ChatSession,
        initial_question: str | None = None,
    ) -> ChatResponse:
        session.phase = "stats_deep_dive"
        return ChatResponse(
            session_id=session_id,
            step="stats_deep_dive_dialog",
            bot_message="",
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            input_values={"stats_question": str(initial_question or "").strip()},
            error=False,
        )

    async def _handle_stats_deep_dive(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, STATS_DEEP_DIVE_OPTIONS)
        if exact_label is None:
            exact_label = self._post_sector_label_from_open_text(message)
        if exact_label is None:
            fuzzy_label = match_option_label(message, STATS_DEEP_DIVE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            return self._custom_hazard_input_step(session_id, session)

        if action == normalize("Refresh hazards and DGs"):
            await self._refresh_session_hazards(session_id, session)
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

    def _post_sector_label_from_open_text(self, message: str) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        if normalized == "other options":
            return None
        if normalized in {
            "next",
            "next step",
            "continue",
            "continue flow",
            "continue the flow",
            "go ahead",
            "proceed",
            "move forward",
            "start mitigation",
            "start mitigation planning",
            "create mitigation",
            "create a mitigation",
            "create mitigation measure",
            "create a mitigation measure",
            "start creating mitigation",
            "make mitigation measure",
            "new mitigation measure",
        }:
            return "Start Mitigation Planning"
        if "mitigation" in normalized and any(
            token in normalized
            for token in (
                "start",
                "create",
                "make",
                "build",
                "develop",
                "plan",
                "prepare",
            )
        ):
            return "Start Mitigation Planning"
        if normalized in {
            "add hazard",
            "add a hazard",
            "add new hazard",
            "add a new hazard",
            "create hazard",
            "create a hazard",
            "create a new hazard",
            "new hazard",
            "start a new hazard",
        }:
            return "Add a new Hazard"
        if "hazard" in normalized and any(
            token in normalized
            for token in ("add", "create", "new")
        ):
            return "Add a new Hazard"
        if normalized in {
            "refresh",
            "refresh hazards",
            "refresh dgs",
            "refresh hazards and dgs",
            "reload hazards",
            "regenerate hazards",
            "update hazards",
        }:
            return "Refresh hazards and DGs"
        if "hazard" in normalized and any(
            token in normalized
            for token in ("refresh", "reload", "regenerate", "update")
        ):
            return "Refresh hazards and DGs"

        ordinal_parser = getattr(self, "_ordinal_index_from_text", None)
        if ordinal_parser is None:
            return None
        ordinal = ordinal_parser(message)
        if ordinal is None:
            return None
        labels = [option.label for option in POST_SECTOR_OPTIONS]
        index = ordinal if ordinal >= 0 else len(labels) + ordinal
        if index < 0 or index >= len(labels):
            return None
        return labels[index]

    def _post_sector_selection_from_open_text(
        self,
        session: ChatSession,
        message: str,
    ) -> dict[str, str | None] | None:
        selector = getattr(self, "_deterministic_selection_from_text", None)
        if selector is None:
            return None
        selection = selector(session, message)
        if selection is None:
            return None
        return selection

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
        open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
        if open_selection_handler is not None:
            open_selection_response = await open_selection_handler(
                session_id,
                session,
                message,
                current_phase="sector",
            )
            if open_selection_response is not None:
                return open_selection_response
        else:
            navigation_handler = getattr(self, "_open_selection_navigation_response", None)
            if navigation_handler is not None:
                navigation_response = await navigation_handler(
                    session_id,
                    session,
                    message,
                    "sector",
                )
                if navigation_response is not None:
                    return navigation_response

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
            self._hydrate_custom_hazard_profiles(session)
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

        hazard = self._open_hazard_selection_from_text(session, message)
        if hazard is None:
            hazard = self._match_hazard(message, session)
        if hazard is None:
            fuzzy_hazard = self._fuzzy_hazard(message, session)
            if fuzzy_hazard is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_hazard)

            question_handler = getattr(self, "_handle_anytime_grounded_question", None)
            if question_handler is not None:
                question_response = await question_handler(session_id, session, message)
                if question_response is not None:
                    return question_response

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
        self._record_activity(session_id, session, "hazard_selected", hazard)
        session.phase = "socio_demographic_review"

        if is_saved_custom_hazard:
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
            session.saved_target_population_answers = self._target_population_answers_for_saved_hazard(
                session,
                hazard,
            )
            self._hydrate_custom_hazard_profiles(session)
            return await self._hazard_profiles_response(session_id, session, hazard)

        return await self._hazard_profiles_response(session_id, session, hazard)

    def _open_hazard_selection_from_text(
        self,
        session: ChatSession,
        message: str,
    ) -> str | None:
        normalized_message = normalize_for_match(message)
        if not normalized_message:
            return None
        hazard_labels = [
            option.label
            for option in self._hazard_options(session)
            if not is_hazard_action_label(option.label)
        ]
        if not hazard_labels:
            return None

        ordinal_parser = getattr(self, "_ordinal_index_from_text", None)
        if ordinal_parser is not None:
            ordinal = ordinal_parser(message)
            if ordinal is not None:
                index = ordinal if ordinal >= 0 else len(hazard_labels) + ordinal
                if 0 <= index < len(hazard_labels):
                    return hazard_labels[index]

        normalized_hazards = [
            (hazard, normalize_for_match(hazard))
            for hazard in hazard_labels
            if normalize_for_match(hazard)
        ]
        exact_matches = [
            hazard
            for hazard, normalized_hazard in normalized_hazards
            if normalized_hazard == normalized_message
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        contained_matches = [
            hazard
            for hazard, normalized_hazard in normalized_hazards
            if self._normalized_phrase_contains(normalized_message, normalized_hazard)
        ]
        if len(contained_matches) == 1:
            return contained_matches[0]
        return None

    @staticmethod
    def _normalized_phrase_contains(text: str, phrase: str) -> bool:
        if not text or not phrase:
            return False
        text_tokens = text.split()
        phrase_tokens = phrase.split()
        if not text_tokens or not phrase_tokens or len(phrase_tokens) > len(text_tokens):
            return False
        window_size = len(phrase_tokens)
        return any(
            text_tokens[index : index + window_size] == phrase_tokens
            for index in range(0, len(text_tokens) - window_size + 1)
        )

    async def _handle_socio_demographic_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SOCIO_DEMOGRAPHIC_OPTIONS)
        if exact_label is None:
            option_matcher = getattr(self, "_open_option_label_from_text", None)
            if option_matcher is not None:
                exact_label = option_matcher(message, SOCIO_DEMOGRAPHIC_OPTIONS)
        if exact_label is None:
            exact_label = self._socio_demographic_label_from_open_text(message)
        if exact_label is None:
            open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
            if open_selection_handler is not None:
                open_selection_response = await open_selection_handler(
                    session_id,
                    session,
                    message,
                    current_phase="sector",
                )
                if open_selection_response is not None:
                    return open_selection_response
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

    def _socio_demographic_label_from_open_text(self, message: str) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        if normalized in {
            "create mitigation",
            "create a mitigation",
            "create mitigation measure",
            "create a mitigation measure",
            "start mitigation",
            "start mitigation measure",
            "make mitigation measure",
            "new mitigation measure",
        }:
            return "Create Mitigation Measure"
        if "mitigation" in normalized and any(
            token in normalized
            for token in ("start", "create", "make", "build", "develop", "prepare")
        ):
            return "Create Mitigation Measure"
        if normalized in {"add dgs", "add more dgs", "add demographic groups"}:
            return "Add more DGs"
        if "dg" in normalized and "add" in normalized:
            return "Add more DGs"
        return None
