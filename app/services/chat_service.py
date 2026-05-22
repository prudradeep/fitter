import json
import logging
from dataclasses import asdict

from app.llm import ask_llm_chat
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Country,
    EvaluationQuestion,
    QuestionOption,
    Region,
    Sector,
    SystemHazard,
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
    build_validation_context,
    extract_sector_stats,
    format_additional_dgs,
    format_all_dgs,
    format_evaluation_answers,
    format_hazards,
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_options import (
    EVALUATION_CATEGORIES,
    MITIGATION_REVIEW_OPTIONS,
    POST_SECTOR_OPTIONS,
    REASON_CONFIRMATION_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    best_fuzzy_label,
    exact_option_label,
    match_option_label,
    normalize,
    option_list,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_additional_dgs,
    parse_duplicate_check_response,
    parse_evaluation_answer,
    parse_llm_hazard_list,
    parse_mitigation_reason,
    parse_reason_evidence,
    parse_validation_response,
)
from app.services.chat_session import ChatSession, session_store
from app.services.message_renderer import markdown_to_html, render_message
from app.services.prompt_loader import load_sector_prompt

logger = logging.getLogger(__name__)


class ChatService:
    welcome_message = render_message("welcome.md")
    invalid_message = render_message("invalid_selection.md")
    fuzzy_rejected_message = render_message("fuzzy_rejected.md")

    def __init__(self, db: Session, user_id: int | None = None) -> None:
        self.db = db
        self.user_id = user_id

    async def handle_message(self, message: str, session_id: str | None) -> ChatResponse:
        clean_message = message.strip()

        if clean_message == "/reset":
            current_session_id, session = session_store.reset(session_id)
            response = self._country_step(current_session_id, session, self.welcome_message)
            self._finalize_chat_response(current_session_id, session, response)
            return response

        if not self._session_belongs_to_current_user(session_id):
            session_id = None
        self._hydrate_session_from_db(session_id)
        current_session_id, session = session_store.get_or_create(session_id)
        self._ensure_user_session(current_session_id, session)
        if clean_message:
            self._record_activity(current_session_id, session, "message_received", clean_message)
            self._record_chat_message(current_session_id, session, "user", clean_message)

        response = await self._chat_response(current_session_id, session, clean_message)
        self._finalize_chat_response(current_session_id, session, response)
        return response

    async def _chat_response(
        self, current_session_id: str, session: ChatSession, clean_message: str
    ) -> ChatResponse:
        if session.pending_fuzzy_option:
            fuzzy_response = await self._handle_pending_fuzzy_option(
                current_session_id, session, clean_message
            )
            if fuzzy_response is not None:
                return fuzzy_response

        if not clean_message and not any([session.country, session.region, session.sector]):
            return self._country_step(current_session_id, session, self.welcome_message)

        if session.country is None:
            return self._select_country(current_session_id, session, clean_message)

        if session.region is None:
            return self._select_region(current_session_id, session, clean_message)

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
            return self._handle_target_population_answer(
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
            return self._handle_reason_confirmation(current_session_id, session, clean_message)

        if session.phase == "add_dgs":
            return await self._capture_additional_dgs(
                current_session_id, session, clean_message
            )

        if session.phase == "dg_reason_evidence":
            return await self._validate_dgs_against_stats(
                current_session_id, session, clean_message
            )

        if session.phase == "mitigation_reason":
            return await self._validate_mitigation_reason(
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
            return await self.handle_message(selected_option or "", session_id)

        if action == normalize("No"):
            session.pending_fuzzy_option = None
            return self._repeat_current_options(
                session_id,
                session,
                self.fuzzy_rejected_message,
                error=False,
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

        if session.phase == "add_dgs":
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=message,
                options=[],
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=[],
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard_evidence":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "target_population_question":
            question = self._current_target_population_question(session)
            options = [
                Option(id=index, label=label)
                for index, label in enumerate(question.get("options", []), start=1)
            ] if question else []
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
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "mitigation_reason":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="mitigation_reason",
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

        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=message,
            options=[],
            session=session.summary(),
            error=error,
        )

    def _select_country(
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
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=render_message("national_scope.md", country=country.name),
                options=option_list(sectors),
                session=session.summary(),
                error=False,
            )

        return ChatResponse(
            session_id=session_id,
            step="region",
            bot_message=render_message("country_selected.md", country=country.name),
            options=option_list(list(regions)),
            session=session.summary(),
            error=False,
        )

    def _select_region(self, session_id: str, session: ChatSession, message: str) -> ChatResponse:
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
        return ChatResponse(
            session_id=session_id,
            step="sector",
            bot_message=render_message("region_selected.md", region=region.name),
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
        session.hazards = await self._get_hazards_from_llm(session.sector)
        session.custom_hazards = self._saved_custom_hazards_for_context(session)
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "sector_selected", sector.name, step="sector")
        for hazard in session.hazards:
            self._ensure_system_hazard(session, hazard)
        return self._hazards_step(session_id, session)

    def _hazards_step(self, session_id: str, session: ChatSession) -> ChatResponse:
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

        if action == normalize("Move to next step"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=[],
                session=session.summary(),
                error=False,
            )

        if action == normalize("Dive deeper into statistical findings"):
            session.phase = "stats_deep_dive"
            session.stats_conversation = []
            return await self._stats_deep_dive(
                session_id,
                session,
                "Dive deeper into the statistical findings for the listed hazards. "
                "Summarise the most important results and affected groups.",
            )

        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=self.invalid_message,
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=True,
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

        if action == normalize("Move to next step"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            session.phase = "add_hazard"
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=[],
                session=session.summary(),
                error=False,
            )

        if not message:
            return ChatResponse(
                session_id=session_id,
                step="stats_deep_dive",
                bot_message=self._sector_briefing(session),
                options=STATS_DEEP_DIVE_OPTIONS,
                session=session.summary(),
                error=False,
            )

        return await self._stats_deep_dive(session_id, session, message)

    def _hazard_profile_step(self, session_id: str, session: ChatSession) -> ChatResponse:
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

        session.selected_hazard = hazard
        session.saved_target_population_answers = None
        is_saved_custom_hazard = self._is_saved_custom_hazard(session, hazard)
        hazard_record = self._ensure_user_hazard(
            session_id,
            session,
            hazard,
            source="custom" if is_saved_custom_hazard else "system",
        )
        session.selected_hazard_record_id = hazard_record.id if hazard_record else None
        self._record_activity(session_id, session, "hazard_selected", hazard)
        session.phase = "socio_demographic_review"

        if is_saved_custom_hazard:
            session.saved_target_population_answers = self._target_population_answers_for_saved_hazard(
                session,
                hazard,
            )
            return await self._socio_demographic_response(
                session_id,
                session,
                (
                    f"For the selected custom hazard '{hazard}', identify the "
                    "socio-demographic profiles most affected using the saved target "
                    "population answers below. Connect the answer to the country, region, "
                    "and sector context. Only describe affected groups and the basis for "
                    "selecting them. Do not include Practical Considerations, Practical "
                    "Policy Recommendations, mitigation measures, or policy recommendations yet.\n\n"
                    "Saved target population answers:\n"
                    f"{session.saved_target_population_answers or '- No saved target population answers were found.'}"
                ),
            )

        return await self._socio_demographic_response(
            session_id,
            session,
            (
                f"For the selected hazard '{hazard}', identify the socio-demographic "
                "profiles that are most affected. Focus on statistically supported "
                "groups from the loaded sector prompt. Only describe affected groups "
                "and the statistical basis. Do not include Practical Considerations, "
                "Practical Policy Recommendations, mitigation measures, or policy "
                "recommendations yet."
            ),
        )

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
            session.phase = "add_dgs"
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=render_message(
                    "add_dgs.md", hazard=session.selected_hazard or "the selected hazard"
                ),
                options=[],
                session=session.summary(),
                error=False,
            )

        if action == normalize("Move to next step"):
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

        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=self.invalid_message,
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _handle_reason_confirmation(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, REASON_CONFIRMATION_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, REASON_CONFIRMATION_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Yes"):
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_measure_reason.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_reason",
                error=False,
            )

        if action == normalize("No"):
            session.phase = "dg_reason_evidence"
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_reason_evidence.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    dgs=format_all_dgs(session),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
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

    async def _validate_mitigation_reason(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        mitigation_measure, reason = parse_mitigation_reason(message)
        if not mitigation_measure or not reason:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=self._mitigation_reason_prompt(
                    session,
                    error_reason="Both `Mitigation measure` and `Reason` are required.",
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_reason",
                error=True,
            )

        validation = await self._validate_mitigation_against_stats(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="mitigation_reason",
                error=True,
            )

        if not validation["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=self._mitigation_reason_prompt(session, validation["reason"]),
                options=[],
                session=session.summary(),
                input_mode="mitigation_reason",
                error=True,
            )

        session.mitigation_measure = mitigation_measure
        session.mitigation_reason = reason
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
            mitigation_measure,
            reason,
        )
        self._record_activity(session_id, session, "mitigation_measure_validated", mitigation_measure)
        return await self._mitigation_review_step(session_id, session)

    async def _mitigation_review_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "mitigation_review"
        answer = await self._mitigation_review_response(
            session,
            (
                "Provide a concise conclusion about the validated mitigation measure. "
                "Include related statistical context, affected groups, expected strengths, "
                "and limitations. Do not ask evaluation questions yet."
            ),
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
                    review=answer,
                )
            ),
            options=MITIGATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=False,
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
            session.phase = "mitigation"
            return ChatResponse(
                session_id=session_id,
                step="mitigation",
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
            validation = await self._validate_evaluation_answer_against_stats(
                session=session,
                question=question,
                score=score,
                reason=reason or "",
                evidence=evidence or "",
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
                "question": question["question"],
                "score": score,
                "reason": reason,
                "evidence": evidence,
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
            evidence=evidence,
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
        dgs = parse_additional_dgs(message)
        if not dgs:
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=render_message(
                    "add_dgs.md", hazard=session.selected_hazard or "the selected hazard"
                ),
                options=[],
                session=session.summary(),
                error=True,
            )

        existing_dg = self._match_existing_dg(session, dgs)
        if existing_dg is not None:
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=render_message(
                    "dg_duplicate.md",
                    duplicates=f"- **{existing_dg}** is already added.",
                ),
                options=[],
                session=session.summary(),
                error=True,
            )

        duplicate_check = await self._semantic_dg_duplicate_check(session, dgs)
        if duplicate_check is None:
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=(
                    "I could not check whether these socio-demographic profiles are "
                    "already covered because the local LLM is unavailable. Please try again."
                ),
                options=[],
                session=session.summary(),
                error=True,
            )

        if duplicate_check["duplicate"]:
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=render_message(
                    "dg_duplicate.md",
                    duplicates=self._format_duplicate_dgs(duplicate_check),
                ),
                options=[],
                session=session.summary(),
                error=True,
            )

        if session.additional_dgs is None:
            session.additional_dgs = []
        self._extend_unique_profiles(session.additional_dgs, dgs)
        for dg in dgs:
            self._store_socio_demographic(
                session.selected_hazard_record_id,
                dg,
                source="user",
            )
        self._record_activity(session_id, session, "socio_demographics_added", ", ".join(dgs))
        session.phase = "socio_demographic_review"

        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=render_message("dgs_added.md", dgs=format_additional_dgs(session)),
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _validate_dgs_against_stats(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        reason, evidence = parse_reason_evidence(message)
        if not reason:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_validation_failed.md",
                    sector=session.sector,
                    reason="`Reason:` is required. Evidence URL and evidence file are optional.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        validation = await self._validate_dgs_context_against_stats(
            session=session,
            reason=reason,
            evidence=evidence or "",
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message("dg_validation_unavailable.md"),
                options=[],
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
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        session.dg_reason = reason
        session.dg_evidence = evidence
        if session.additional_dgs:
            self._store_socio_demographic(
                session.selected_hazard_record_id,
                "\n".join(session.additional_dgs),
                source="user_validated",
                reason=reason,
                evidence=evidence or None,
            )
        self._record_activity(session_id, session, "socio_demographics_validated", reason)
        session.phase = "mitigation_reason"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_reason",
            bot_message=render_message(
                "mitigation_measure_reason.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=format_all_dgs(session),
            ),
            options=[],
            session=session.summary(),
            input_mode="mitigation_reason",
            error=False,
        )

    async def _capture_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        hazard = message.strip()
        if not hazard:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=[],
                session=session.summary(),
                error=True,
            )

        existing_hazard = self._match_hazard(hazard, session)
        if existing_hazard is not None:
            session.phase = "hazards"
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_duplicate.md", hazard=existing_hazard),
                options=POST_SECTOR_OPTIONS,
                session=session.summary(),
                error=True,
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
                options=[],
                session=session.summary(),
                error=True,
            )

        if duplicate_check["duplicate"]:
            session.phase = "hazards"
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_duplicate.md",
                    hazard=duplicate_check["match"] or hazard,
                ),
                options=POST_SECTOR_OPTIONS,
                session=session.summary(),
                error=True,
            )

        session.pending_hazard = hazard
        session.phase = "add_hazard_evidence"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message("hazard_reason_evidence.md", hazard=hazard),
            options=[],
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    async def _validate_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
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
                options=[],
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
                options=[],
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
                options=[],
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

        return self._custom_hazard_added_step(session_id, session)

    async def _stats_deep_dive(
        self, session_id: str, session: ChatSession, user_message: str
    ) -> ChatResponse:
        context, messages = self._build_stats_deep_dive_messages(session, user_message)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=900,
        )

        if session.stats_conversation is None:
            session.stats_conversation = []
        session.stats_conversation.extend(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer},
            ]
        )

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
        context, messages = self._build_deep_dive_messages(session, user_message)
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
        context, messages = self._build_deep_dive_messages(session, user_message)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=900,
        )
        answer = self._strip_practical_sections(answer)
        session.socio_demographic_findings = answer
        self._store_socio_demographic(
            session.selected_hazard_record_id,
            answer,
            source="llm",
        )
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
        context, messages = self._build_deep_dive_messages(
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
        context, messages = self._build_deep_dive_messages(
            session,
            (
                f"For the selected hazard '{session.selected_hazard}', provide practical "
                "considerations and practical policy recommendations based on the loaded "
                "sector statistical context and the affected socio-demographic profiles "
                "identified so far.\n\n"
                "Socio-demographic profiles:\n"
                f"{format_all_dgs(session)}\n\n"
                "Use only the loaded statistical context. Answer in Markdown with these "
                "two short sections only: Practical Considerations and Practical Policy "
                "Recommendations. Keep bullets concise and do not create a mitigation "
                "measure yet."
            ),
        )
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=750,
        )

    async def _mitigation_review_response(self, session: ChatSession, user_message: str) -> str:
        context, messages = self._build_mitigation_review_messages(session, user_message)
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=850,
        )

    def _sector_briefing(self, session: ChatSession) -> str:
        sector_prompt = load_sector_prompt(session.sector)
        return render_message(
            "deep_dive_intro.md",
            country=session.country,
            region=session.region,
            sector=session.sector,
            sector_stats=extract_sector_stats(sector_prompt, session.sector),
        )

    def _mitigation_reason_prompt(
        self, session: ChatSession, error_reason: str | bool | None = None
    ) -> str:
        prompt = render_message(
            "mitigation_measure_reason.md",
            hazard=session.selected_hazard or "the selected hazard",
            dgs=format_all_dgs(session),
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

    def _build_mitigation_review_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_prompt = load_sector_prompt(session.sector)
        context = f"""
Use the sector system prompt below as your authoritative statistical context.
Do not invent precise live statistics. If a number would be needed but is not present,
explain what data source the user should check.

Sector system prompt:
{sector_prompt}
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
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    "Validated mitigation measure:\n"
                    f"{session.mitigation_measure or 'Not provided'}\n\n"
                    "Validated mitigation reason:\n"
                    f"{session.mitigation_reason or 'Not provided'}\n\n"
                    "Discuss the mitigation measure using the sector statistics, affected "
                    "groups, and the validated mitigation reason. Do not ask evaluation "
                    "questions. Do not include a heading or bullet named 'Policy Implications'."
                ),
            },
            *history,
            {
                "role": "user",
                "content": (
                    f"User message:\n{user_message}\n\n"
                    "Answer in Markdown with a short conclusion and concise related "
                    "information. Stay grounded in the statistical context and the "
                    "validated mitigation measure/reason."
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
        session.phase = "mitigation"
        return ChatResponse(
            session_id=session_id,
            step="mitigation",
            bot_message=render_message(
                "evaluation_complete.md",
                hazard=session.selected_hazard or "the selected hazard",
                mitigation_measure=session.mitigation_measure or "Not provided",
                reason=session.mitigation_reason or "Not provided",
                answers=format_evaluation_answers(session),
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

    def _target_population_question_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        question = self._current_target_population_question(session)
        if question is None:
            return self._custom_hazard_added_step(session_id, session)

        options = [
            Option(id=index, label=label)
            for index, label in enumerate(question["options"], start=1)
        ]
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
            error=bool(error_reason),
        )

    def _handle_target_population_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        question = self._current_target_population_question(session)
        if question is None:
            return self._custom_hazard_added_step(session_id, session)

        options = [
            Option(id=index, label=label)
            for index, label in enumerate(question["options"], start=1)
        ]
        selected = exact_option_label(message, options)
        if selected is None:
            fuzzy_label = match_option_label(message, options)
            if fuzzy_label is not None:
                selected = fuzzy_label

        if selected is None:
            return self._target_population_question_step(
                session_id,
                session,
                error_reason="Please choose one of the listed target population options.",
            )

        if session.target_population_answers is None:
            session.target_population_answers = []
        session.target_population_answers.append(
            {
                "question_id": int(question["id"]),
                "question": str(question["question"]),
                "answer": selected,
            }
        )
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
            category="target_population",
            response_text=selected,
            question_option_id=question_option_id,
            hazard_id=session.accepted_custom_hazard_record_id or session.selected_hazard_record_id,
        )
        self._record_activity(
            session_id,
            session,
            "target_population_question_answered",
            f"{question['question']} -> {selected}",
        )
        session.target_population_index += 1

        if session.target_population_index >= len(session.target_population_questions or []):
            return self._custom_hazard_added_step(session_id, session)

        return self._target_population_question_step(session_id, session)

    def _custom_hazard_added_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        session.phase = "hazards"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_added.md",
                hazard=session.accepted_custom_hazard or "New hazard",
                reason=session.accepted_custom_hazard_reason or "Not provided",
                evidence=session.accepted_custom_hazard_evidence or "Not provided",
                target_population_answers=self._format_target_population_answers(session),
                hazards=format_hazards(session),
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _current_target_population_question(self, session: ChatSession) -> dict[str, object] | None:
        questions = session.target_population_questions or []
        if session.target_population_index < 0 or session.target_population_index >= len(questions):
            return None
        return questions[session.target_population_index]

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
                "question": normalize_markdown_text(row.question),
            }
            for row in sorted_rows
        ]

    @staticmethod
    def _extend_unique_profiles(existing: list[str], new_profiles: list[str]) -> None:
        seen = {normalize(profile) for profile in existing}
        for profile in new_profiles:
            key = normalize(profile)
            if key in seen:
                continue
            existing.append(profile)
            seen.add(key)

    @staticmethod
    def _match_existing_dg(session: ChatSession, new_profiles: list[str]) -> str | None:
        existing = session.additional_dgs or []
        seen = {normalize(profile): profile for profile in existing}
        for profile in new_profiles:
            match = seen.get(normalize(profile))
            if match is not None:
                return match
        return None

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
    def _strip_practical_sections(markdown_text: str) -> str:
        practical_headings = (
            "practical considerations",
            "practical policy recommendations",
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
        return [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(hazard_names(session), start=1)
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

    def _is_saved_custom_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.custom_hazards or []))

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
            user_session.session_data = json.dumps(asdict(session), default=str)
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
        hazard_id: int | None,
        profile: str,
        *,
        source: str,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> None:
        if hazard_id is None or not profile.strip():
            return
        try:
            self.db.add(
                UserHazardSocioDemographic(
                    user_hazard_id=hazard_id,
                    profile=profile.strip(),
                    source=source,
                    reason=reason,
                    evidence=evidence,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist socio-demographic profile")

    def _store_mitigation_measure(
        self, hazard_id: int | None, mitigation_measure: str, reason: str
    ) -> int | None:
        if hazard_id is None:
            return None
        try:
            row = UserMitigationMeasure(
                user_hazard_id=hazard_id,
                measure=mitigation_measure,
                reason=reason,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return row.id
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation measure")
            return None

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

    def _build_deep_dive_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_prompt = load_sector_prompt(session.sector)
        context = f"""
Use the sector system prompt below as your authoritative statistical context.
Do not invent precise live statistics. If a number would be needed but is not present,
explain what data source the user should check.

Sector system prompt:
{sector_prompt}
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

    def _build_stats_deep_dive_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        context, messages = self._build_deep_dive_messages(session, user_message)
        history = list(session.stats_conversation or [])
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
                    "Use only the loaded sector statistical context."
                ),
            },
            *history[-10:],
            current_message,
        ]
        return context, messages

    async def _get_hazards_from_llm(self, sector: str | None) -> list[str]:
        sector_prompt = load_sector_prompt(sector)
        context = f"""
You are a strict extraction assistant for Dr Transition.

Use this sector system prompt as the only source:
{sector_prompt}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "List all the hazards perceived. Return ONLY valid JSON, "
                    "an array of hazard names like ['hazard 1', 'hazard 2', ..]. "
                    "Each item must be only the hazard name. Do not include predictors, "
                    "demographic variables, countries, model metrics, explanations, caveats, "
                    "Markdown, or code fences. If the sector analysis is unavailable, "
                    "return ['Analysis not available']."
                ),
            }
        ]

        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=350,
        )
        return parse_llm_hazard_list(response)

    async def _validate_hazard_against_stats(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_prompt = load_sector_prompt(session.sector)
        existing_hazards = "\n".join(f"- {item}" for item in (session.hazards or []))
        compact_context = build_validation_context(sector_prompt, session)
        context = f"""
You are a strict validation assistant for Dr Transition.

Use this compact sector statistical context as the authoritative source:
{compact_context}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate whether the proposed new hazard is supported by the sector "
                    "statistics, survey findings, or prompt context.\n\n"
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
                    '{"valid": false, "reason": "The reason contradicts or is not supported by the statistical context."}\n\n'
                    "Rules:\n"
                    "- valid must be true only when the reason is consistent with the "
                    "loaded statistical context.\n"
                    "- If evidence content is supplied from a URL or file, valid must "
                    "also require the reason to be supported by that extracted evidence.\n"
                    "- valid must be false when the reason or supplied evidence contradicts "
                    "the statistics, confuses predictors with hazards, invents unsupported "
                    "numbers, is too vague, or the evidence content does not support the "
                    "reason.\n"
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

        context = """
You are a strict semantic duplicate checker for Dr Transition.

Your job is to decide whether a proposed hazard is already covered by an existing
hazard, even when the wording, grammar, or language differs.
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
        return parsed

    async def _semantic_dg_duplicate_check(
        self, session: ChatSession, dgs: list[str]
    ) -> dict[str, object] | None:
        existing_context = format_all_dgs(session)
        context = """
You are a strict semantic duplicate checker for Dr Transition.

Your job is to decide whether newly proposed socio-demographic profiles are
already covered by the existing profile text or user-added profile list, even
when the wording, grammar, or language differs.
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
                    "Existing socio-demographic profiles and context:\n"
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
        sector_prompt = load_sector_prompt(session.sector)
        context = f"""
You are a strict validation assistant for Dr Transition.

Use this sector system prompt as the authoritative statistical source:
{sector_prompt}
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
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Reason: {reason}\n"
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
                    "- Validate against all socio-demographic profiles identified so far, "
                    "including both the assistant-identified DGs and user-added DGs.\n"
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

    async def _validate_mitigation_against_stats(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> dict[str, str | bool] | None:
        sector_prompt = load_sector_prompt(session.sector)
        context = f"""
You are a strict validation assistant for Dr Transition.

Use this sector system prompt as the authoritative statistical source:
{sector_prompt}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate whether the proposed mitigation measure and reason are "
                    "appropriate for reducing the negative impact of the selected hazard "
                    "on the relevant socio-demographic groups, using only the loaded "
                    "statistical context.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n"
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Mitigation measure: {mitigation_measure}\n"
                    f"Reason: {reason}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "short validation explanation"}\n\n'
                    "Rules:\n"
                    "- valid must be true only when the mitigation measure and reason "
                    "logically address a statistically supported negative impact, "
                    "affected group, or hazard mechanism from the prompt.\n"
                    "- valid must be false when the mitigation is unrelated to the "
                    "selected hazard, ignores the affected groups, contradicts the "
                    "statistics, invents unsupported facts, or the reason is too vague.\n"
                    "- The reason field must tell the user what to change and stay under "
                    "70 words."
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

    async def _validate_evaluation_answer_against_stats(
        self,
        session: ChatSession,
        question: dict[str, str | int],
        score: int,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_prompt = load_sector_prompt(session.sector)
        context = f"""
You are a strict validation assistant for Dr Transition.

Use this sector system prompt as the authoritative statistical source:
{sector_prompt}
""".strip()
        messages = [
            {
                "role": "user",
                "content": (
                    "Validate whether the user's evaluation reason and evidence are "
                    "consistent with the selected score and supported by the statistical "
                    "context. The user is scoring a mitigation measure from 1 to 10.\n\n"
                    f"Sector: {session.sector}\n"
                    f"Country: {session.country}\n"
                    f"Region: {session.region}\n"
                    f"Selected hazard: {session.selected_hazard or 'No selected hazard'}\n"
                    "Socio-demographic profiles:\n"
                    f"{format_all_dgs(session)}\n\n"
                    f"Mitigation measure: {session.mitigation_measure or 'Not provided'}\n"
                    f"Mitigation reason: {session.mitigation_reason or 'Not provided'}\n\n"
                    f"Question category: {question['category']}\n"
                    f"Question: {question['question']}\n"
                    f"Score: {score}/10\n"
                    f"Reason: {reason}\n"
                    f"Evidence: {evidence or 'Not provided'}\n\n"
                    "Return ONLY valid JSON with this exact shape:\n"
                    '{"valid": true, "reason": "short validation explanation"}\n\n'
                    "Rules:\n"
                    "- valid must be true only when the reason and any supplied evidence align with "
                    "the score and the loaded statistical context.\n"
                    "- If evidence content is supplied from a URL or file, valid must "
                    "also require the reason or score explanation to be supported by "
                    "that extracted evidence.\n"
                    "- valid must be false if the reason or supplied evidence invents "
                    "unsupported statistics, contradicts the statistical context, is unrelated to the question, "
                    "is unsupported by the extracted evidence content, "
                    "or if the explanation does not justify the chosen score.\n"
                    "- A low score should be justified by limitations, weak evidence, "
                    "low feasibility, or limited transformative impact. A high score "
                    "should be justified by strong evidence, feasibility, or expected "
                    "impact relevant to the question category.\n"
                    "- The reason field must tell the user what to revise and stay under "
                    "70 words."
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
