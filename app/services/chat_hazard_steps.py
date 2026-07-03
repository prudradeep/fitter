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
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import default_custom_hazard_state
from app.services.message_renderer import render_message


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
        exact_label = exact_option_label(message, POST_SECTOR_OPTIONS)
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
        self._record_activity(
            session_id,
            session,
            "hazards_refreshed",
            session.sector or "",
        )

    def _stats_deep_dive_dialog_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "stats_deep_dive"
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
