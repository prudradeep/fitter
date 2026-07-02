import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Country, Region, Sector
from app.schemas import ChatResponse, Option
from app.services.chat_options import best_fuzzy_label, normalize, normalize_for_match, option_list
from app.services.chat_session import ChatSession
from app.services.conversational_selection import resolve_selection
from app.services.message_renderer import render_message
from app.services.question_intent import detect_message_intent

logger = logging.getLogger(__name__)

SELECTION_CONFIRMATION_OPTIONS = [
    Option(id=1, label="Yes"),
    Option(id=2, label="No"),
]


class ChatSelectionStepsMixin:
    async def _select_country(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        pending_response = await self._handle_pending_selection_workflow(
            session_id,
            session,
            message,
        )
        if pending_response is not None:
            return pending_response

        conversational_response = await self._maybe_apply_conversational_selection(
            session_id,
            session,
            message,
            current_phase="country",
        )
        if conversational_response is not None:
            return conversational_response

        country = self._match_country(message)
        if country is None:
            fuzzy_country = self._fuzzy_row_by_name(
                self.db.scalars(select(Country).order_by(Country.name)).all(),
                message,
            )
            if fuzzy_country is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_country.name)
            question_response = await self._handle_anytime_grounded_question(
                session_id,
                session,
                message,
            )
            if question_response is not None:
                return question_response
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
        pending_response = await self._handle_pending_selection_workflow(
            session_id,
            session,
            message,
        )
        if pending_response is not None:
            return pending_response

        conversational_response = await self._maybe_apply_conversational_selection(
            session_id,
            session,
            message,
            current_phase="region",
        )
        if conversational_response is not None:
            return conversational_response

        region = self._match_region(message, session.country_id)
        if region is None:
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            fuzzy_region = self._fuzzy_row_by_name(list(regions), message)
            if fuzzy_region is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_region.name)
            question_response = await self._handle_anytime_grounded_question(
                session_id,
                session,
                message,
            )
            if question_response is not None:
                return question_response
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
        deferred_sector = ""
        if isinstance(session.pending_selection, dict):
            deferred_sector = str(session.pending_selection.get("sector") or "").strip()
        if deferred_sector and session.sector is None:
            session.pending_selection = None
            return await self._select_sector(session_id, session, deferred_sector)
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
        pending_response = await self._handle_pending_selection_workflow(
            session_id,
            session,
            message,
        )
        if pending_response is not None:
            return pending_response

        conversational_response = await self._maybe_apply_conversational_selection(
            session_id,
            session,
            message,
            current_phase="sector",
        )
        if conversational_response is not None:
            return conversational_response

        sector = self._match_sector(message, session.country_id)
        if sector is None:
            fuzzy_sector = self._fuzzy_row_by_name(
                self._sectors_for_country(session.country_id),
                message,
            )
            if fuzzy_sector is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_sector.name)
            question_response = await self._handle_anytime_grounded_question(
                session_id,
                session,
                message,
            )
            if question_response is not None:
                return question_response
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

    async def _maybe_apply_conversational_selection(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> ChatResponse | None:
        if not message.strip() or self._is_exact_current_selection(session, message):
            return None

        deterministic_selection = self._deterministic_selection_from_text(session, message)
        if deterministic_selection is not None:
            if self._selection_is_outside_current_phase(
                session,
                deterministic_selection,
                current_phase,
            ):
                return self._store_outside_current_selection(
                    session_id,
                    session,
                    deterministic_selection,
                )
            if not self._selection_dependencies_are_valid(
                session,
                deterministic_selection,
                current_phase,
            ):
                return self._invalid_selection_response(
                    session_id,
                    session,
                    deterministic_selection,
                )
            return await self._apply_pending_selection(session_id, session, deterministic_selection)

        intent = await detect_message_intent(
            message,
            context={
                "current_phase": current_phase,
                "country": session.country,
                "region": session.region,
                "sector": session.sector,
                "available_countries": self._available_country_names(),
                "available_regions": self._available_region_names(session),
                "available_sectors": self._available_sector_names(session),
            },
        )
        logger.info("Selection message intent: %s", intent)
        intent_name = str(intent.get("intent") or "unclear")
        intent_confidence = str(intent.get("confidence") or "low")
        if intent_name == "question" and intent_confidence in {"high", "medium"}:
            return await self._handle_anytime_grounded_question(session_id, session, message)
        if intent_name in {"change_country", "change_region", "change_sector"}:
            return self._selection_action_confirmation_step(session_id, session, intent_name)
        if intent_name == "restart_selection":
            return self._selection_action_confirmation_step(
                session_id,
                session,
                "restart_selection",
            )
        if intent_name in {"confirmation_yes", "confirmation_no", "unclear"}:
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=True,
            )

        question_response = await self._handle_anytime_grounded_question(
            session_id,
            session,
            message,
        )
        if question_response is not None:
            return question_response

        result = await resolve_selection(
            user_text=message,
            available_countries=self._available_country_names(),
            available_regions=self._available_region_names(session),
            available_sectors=self._available_sector_names(session),
            current_phase=current_phase,
        )
        if not result.get("matched"):
            return None

        selection = {
            "country": result.get("country") if isinstance(result.get("country"), str) else None,
            "region": result.get("region") if isinstance(result.get("region"), str) else None,
            "sector": result.get("sector") if isinstance(result.get("sector"), str) else None,
        }
        if not self._selection_dependencies_are_valid(session, selection, current_phase):
            return self._invalid_selection_response(session_id, session, selection)
        if not any(selection.values()):
            return None

        session.pending_selection_confirmation = selection
        logger.info("Selection requires clarification: %s", selection)
        return ChatResponse(
            session_id=session_id,
            step="selection_clarification",
            bot_message=self._selection_confirmation_message(selection),
            options=SELECTION_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_pending_selection_workflow(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse | None:
        if not session.pending_selection_confirmation and not session.pending_selection_action:
            return None
        action = normalize(message)
        if action in {"yes", "yeah", "correct", "right", "ok", "okay", "confirm", "proceed"}:
            if session.pending_selection_confirmation:
                selection = dict(session.pending_selection_confirmation)
                session.pending_selection_confirmation = None
                logger.info("Selection clarification accepted: %s", selection)
                return await self._apply_pending_selection(session_id, session, selection)
            pending_action = session.pending_selection_action
            session.pending_selection_action = None
            logger.info("Selection action confirmed: %s", pending_action)
            return await self._apply_selection_action(session_id, session, pending_action)
        if action in {"no", "wrong", "cancel", "incorrect", "not this", "change"}:
            session.pending_selection_confirmation = None
            session.pending_selection_action = None
            return self._repeat_current_options(
                session_id,
                session,
                "No problem. Please choose from the available options.",
                error=False,
            )
        return ChatResponse(
            session_id=session_id,
            step="selection_confirmation",
            bot_message="Please answer **Yes** or **No**.",
            options=SELECTION_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=True,
        )

    async def _apply_pending_selection(
        self,
        session_id: str,
        session: ChatSession,
        selection: dict[str, object],
    ) -> ChatResponse:
        response: ChatResponse | None = None
        country = str(selection.get("country") or "").strip()
        region = str(selection.get("region") or "").strip()
        sector = str(selection.get("sector") or "").strip()

        if country and session.country is None:
            response = await self._select_country(session_id, session, country)
        if region and session.country is not None and session.region is None:
            response = await self._select_region(session_id, session, region)
        if sector and session.country is not None and session.region is not None and session.sector is None:
            response = await self._select_sector(session_id, session, sector)
        elif sector and session.country is not None and session.region is None:
            session.pending_selection = {"country": None, "region": None, "sector": sector}

        if response is not None:
            return response
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _deterministic_selection_from_text(
        self,
        session: ChatSession,
        message: str,
    ) -> dict[str, str | None] | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        remaining = f" {normalized} "
        selection: dict[str, str | None] = {"country": None, "region": None, "sector": None}
        option_groups = [
            ("country", self._available_country_names()),
            ("region", self._available_region_names(session)),
            ("sector", self._available_sector_names(session)),
        ]
        for key, labels in option_groups:
            matches = [
                label
                for label in labels
                if f" {normalize_for_match(label)} " in remaining
            ]
            if len(matches) > 1:
                return None
            if matches:
                label = matches[0]
                selection[key] = label
                remaining = remaining.replace(f" {normalize_for_match(label)} ", " ")
        if not any(selection.values()):
            return None
        if not self._is_selection_filler_text(remaining):
            return None
        return selection

    @staticmethod
    def _is_selection_filler_text(value: str) -> bool:
        tokens = normalize_for_match(value).split()
        if not tokens:
            return True
        filler = {
            "i",
            "id",
            "d",
            "let",
            "lets",
            "want",
            "would",
            "like",
            "to",
            "start",
            "begin",
            "with",
            "use",
            "select",
            "choose",
            "please",
            "the",
            "a",
            "an",
            "and",
            "in",
            "for",
            "sector",
            "country",
            "region",
            "state",
        }
        return all(token in filler for token in tokens)

    def _selection_is_outside_current_phase(
        self,
        session: ChatSession,
        selection: dict[str, str | None],
        current_phase: str,
    ) -> bool:
        if current_phase == "country":
            return not selection.get("country")
        if current_phase == "region":
            return not selection.get("region") and bool(selection.get("sector"))
        if current_phase == "sector":
            return False
        return False

    def _store_outside_current_selection(
        self,
        session_id: str,
        session: ChatSession,
        selection: dict[str, str | None],
    ) -> ChatResponse:
        sector = selection.get("sector")
        if sector:
            session.pending_selection = {"country": None, "region": None, "sector": sector}
            return self._repeat_current_options(
                session_id,
                session,
                f"I'll remember **{sector}** for the sector. Please choose the required option first.",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _invalid_selection_response(
        self,
        session_id: str,
        session: ChatSession,
        selection: dict[str, str | None],
    ) -> ChatResponse:
        country_name = selection.get("country") or session.country
        sector = selection.get("sector")
        if country_name and sector:
            country = self._country_by_name(country_name)
            if country is not None:
                sectors = self._sectors_for_country(country.id)
                sector_names = [row.name for row in sectors]
                if normalize(sector) not in {normalize(name) for name in sector_names}:
                    if session.country is None and selection.get("country"):
                        session.country_id = country.id
                        session.country = country.name
                        self._ensure_user_session(session_id, session)
                        self._record_activity(
                            session_id,
                            session,
                            "country_selected",
                            country.name,
                            step="country",
                        )
                    regions = self.db.scalars(
                        select(Region)
                        .where(Region.country_id == country.id)
                        .order_by(Region.name)
                    ).all()
                    return ChatResponse(
                        session_id=session_id,
                        step="sector" if session.region else "region",
                        bot_message=(
                            f"**{sector}** is not available for **{country_name}**.\n\n"
                            "The available sectors are:\n"
                            + "\n".join(f"- {name}" for name in sector_names)
                        ),
                        options=option_list(sectors if session.region else list(regions)),
                        session=session.summary(),
                        error=True,
                    )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    def _selection_action_confirmation_step(
        self,
        session_id: str,
        session: ChatSession,
        action: str,
    ) -> ChatResponse:
        session.pending_selection_action = action
        labels = {
            "change_country": "change country",
            "change_region": "change region",
            "change_sector": "change sector",
            "restart_selection": "restart the selection process",
        }
        return ChatResponse(
            session_id=session_id,
            step="selection_confirmation",
            bot_message=f"Do you want to **{labels.get(action, action)}**?",
            options=SELECTION_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _apply_selection_action(
        self,
        session_id: str,
        session: ChatSession,
        action: str | None,
    ) -> ChatResponse:
        if action in {"change_country", "restart_selection"}:
            self.reset_state_from(session, "country")
            return self._country_step(session_id, session, self.welcome_message, False)
        if action == "change_region":
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self.reset_state_from(session, "region")
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
        if action == "change_sector":
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self.reset_state_from(session, "sector")
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
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    @classmethod
    def reset_state_from(cls, session: ChatSession, level: str) -> None:
        session.pending_selection = None
        session.pending_selection_confirmation = None
        session.pending_selection_action = None
        if level == "country":
            session.country_id = None
            session.country = None
            session.region_id = None
            session.region = None
            cls._clear_sector_context(session)
        elif level == "region":
            session.region_id = None
            session.region = None
            cls._clear_sector_context(session)
        elif level == "sector":
            cls._clear_sector_context(session)

    def _is_exact_current_selection(self, session: ChatSession, message: str) -> bool:
        if session.country is None:
            return self._match_country(message) is not None
        if session.region is None:
            return self._match_region(message, session.country_id) is not None
        if session.sector is None:
            return self._match_sector(message, session.country_id) is not None
        return False

    def _available_country_names(self) -> list[str]:
        return [
            country.name
            for country in self.db.scalars(select(Country).order_by(Country.name)).all()
        ]

    def _available_region_names(self, session: ChatSession) -> list[str]:
        query = select(Region).order_by(Region.name)
        if session.country_id is not None:
            query = query.where(Region.country_id == session.country_id)
        return [region.name for region in self.db.scalars(query).all()]

    def _available_sector_names(self, session: ChatSession) -> list[str]:
        if session.country_id is not None:
            return [sector.name for sector in self._sectors_for_country(session.country_id)]
        return [
            sector.name
            for sector in self.db.scalars(select(Sector).order_by(Sector.name)).all()
        ]

    def _selection_dependencies_are_valid(
        self,
        session: ChatSession,
        selection: dict[str, str | None],
        current_phase: str,
    ) -> bool:
        country_name = selection.get("country") or session.country
        country = self._country_by_name(country_name) if country_name else None

        if selection.get("region") and country is None:
            return False
        if selection.get("region") and country is not None:
            region = self._region_by_name(selection["region"], country.id)
            if region is None:
                return False

        if selection.get("sector"):
            if country is None:
                return current_phase == "sector" and session.country_id is not None
            sector_names = [sector.name for sector in self._sectors_for_country(country.id)]
            if normalize(selection["sector"]) not in {normalize(name) for name in sector_names}:
                return False

        if current_phase == "country" and selection.get("region") and not selection.get("country"):
            return False
        return True

    def _country_by_name(self, name: str | None) -> Country | None:
        if not name:
            return None
        return self.db.scalar(select(Country).where(Country.name == name))

    def _region_by_name(self, name: str | None, country_id: int) -> Region | None:
        if not name:
            return None
        return self.db.scalar(
            select(Region).where(Region.country_id == country_id, Region.name == name)
        )

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

    @staticmethod
    def _selection_confirmation_message(selection: dict[str, str | None]) -> str:
        labels = [
            value
            for value in [
                selection.get("country"),
                selection.get("region"),
                selection.get("sector"),
            ]
            if value
        ]
        return f"Did you mean **{' -> '.join(labels)}**?"

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
