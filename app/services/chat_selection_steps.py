import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    Country,
    EurostatPopulationCache,
    HazardListingCache,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicTargetPopulation,
)
from app.schemas import ChatResponse, Option
from app.services.chat_options import (
    best_fuzzy_label,
    fuzzy_score,
    normalize,
    normalize_for_match,
    option_list,
)
from app.services.chat_session import ChatSession
from app.services.conversational_selection import resolve_selection
from app.services.message_renderer import render_message
from app.services.question_intent import detect_message_intent

logger = logging.getLogger(__name__)

SELECTION_CONFIRMATION_OPTIONS = [
    Option(id=1, label="Yes"),
    Option(id=2, label="No"),
]
HAZARD_LISTING_CACHE_VERSION = "v1"


class ChatSelectionStepsMixin:
    async def _select_country(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if not message.strip():
            return self._country_step(
                session_id,
                session,
                "Please select a country from the available options.",
                False,
            )

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
            session.phase = "sector"
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

        session.phase = "region"
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
        session.phase = "sector"
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "region_selected", region.name, step="region")
        sectors = self._sectors_for_country(session.country_id)
        deferred_sector = ""
        if isinstance(session.pending_selection, dict):
            deferred_sector = str(session.pending_selection.get("sector") or "").strip()
        if deferred_sector and session.sector is None:
            session.pending_selection = None
            sector_names = {normalize(row.name) for row in sectors}
            if normalize(deferred_sector) not in sector_names:
                return ChatResponse(
                    session_id=session_id,
                    step="sector",
                    bot_message=(
                        f"You mentioned **{deferred_sector}** earlier, but it is not "
                        f"available for **{session.country or 'the selected country'}**.\n\n"
                        "Please choose one of the available sectors."
                    ),
                    options=option_list(sectors),
                    session=session.summary(),
                    error=True,
                )
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
        if self._load_hazard_listing_cache(session):
            session.custom_hazards = self._saved_custom_hazards_for_context(session)
            self._hydrate_custom_hazard_profiles(session)
            await self._enrich_additional_and_custom_hazard_profiles_with_population_context(
                session
            )
            self._filter_session_hazards_without_profiles(session)
            self._ensure_user_session(session_id, session)
            self._record_activity(session_id, session, "sector_selected", sector.name, step="sector")
            return self._hazards_step(session_id, session)

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
        self._store_hazard_listing_cache(session)
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
        if self._is_selection_information_request(message):
            return self._repeat_current_options(
                session_id,
                session,
                self._information_request_message(session, current_phase),
                error=False,
            )
        if self._is_negated_selection(message):
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        negated_region_response = await self._negated_region_country_only_response(
            session_id,
            session,
            message,
            current_phase,
        )
        if negated_region_response is not None:
            return negated_region_response
        if self._looks_like_unsafe_selection_instruction(message):
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        if self._looks_like_selection_topic_without_command(message):
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        if not normalize_for_match(message):
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        if self._has_ambiguous_same_level_options(session, message, current_phase):
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        if current_phase == "sector" and normalize_for_match(message) in {"second one", "the second one"}:
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )
        invalid_tuple_response = self._invalid_conflicting_tuple_response(
            session_id,
            session,
            message,
            current_phase,
        )
        if invalid_tuple_response is not None:
            return invalid_tuple_response

        deterministic_selection = self._ordinal_selection_from_text(
            session,
            message,
            current_phase,
        )
        if deterministic_selection is None:
            sector_alias_response = self._sector_alias_confirmation_response(
                session_id,
                session,
                message,
                current_phase,
            )
            if sector_alias_response is not None:
                return sector_alias_response
        if deterministic_selection is None:
            deterministic_selection = self._implicit_region_selection_from_text(
                session,
                message,
                current_phase,
            )
        if deterministic_selection is None:
            deterministic_selection = self._deterministic_selection_from_text(session, message)
        if deterministic_selection is None:
            deterministic_selection = self._fuzzy_selection_from_text(
                session,
                message,
                current_phase,
            )
        if deterministic_selection is None:
            deterministic_selection = self._out_of_scope_selection_from_text(
                session,
                message,
                current_phase,
            )
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

        if current_phase == "country" and self._mentions_unsupported_country(message):
            return self._country_step(session_id, session, self.invalid_message, True)

        navigation_response = await self._open_selection_navigation_response(
            session_id,
            session,
            message,
            current_phase,
        )
        if navigation_response is not None:
            return navigation_response

        if current_phase in {"region", "sector"} and self._looks_like_unknown_option_label(message):
            return self._repeat_current_options(session_id, session, self.invalid_message, True)

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
        if intent_name in {"change_country", "change_region", "change_sector"}:
            return await self._apply_selection_action(session_id, session, intent_name)
        if intent_name == "restart_selection":
            return await self._apply_selection_action(session_id, session, "restart_selection")
        if intent_name in {"confirmation_yes", "confirmation_no", "multi_selection", "unclear"}:
            return self._repeat_current_options(
                session_id,
                session,
                "Please choose one of the available options, or type your selection another way.",
                error=False,
            )

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
        if (
            intent_name == "question"
            and intent_confidence in {"high", "medium"}
            and not self._looks_like_selection_request(message)
        ):
            return await self._handle_anytime_grounded_question(session_id, session, message)
        if not self._selection_dependencies_are_valid(session, selection, current_phase):
            return self._invalid_selection_response(session_id, session, selection)
        if not any(selection.values()):
            return None

        logger.info("Applying resolved conversational selection: %s", selection)
        return await self._apply_pending_selection(session_id, session, selection)

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

        if self._selection_matches_current_context(session, country, region, sector):
            return self._repeat_current_options(
                session_id,
                session,
                self._already_selected_message(session, country, region, sector),
                False,
            )

        if country and session.country is not None and normalize(country) != normalize(session.country):
            self.reset_state_from(session, "country")
            response = await self._select_country(session_id, session, country)
        elif country and session.country is None:
            response = await self._select_country(session_id, session, country)

        if (
            region
            and session.country is not None
            and session.region is not None
            and normalize(region) != normalize(session.region)
        ):
            self.reset_state_from(session, "region")
            response = await self._select_region(session_id, session, region)
        elif region and session.country is not None and session.region is None:
            response = await self._select_region(session_id, session, region)

        if (
            sector
            and session.country is not None
            and session.region is not None
            and session.sector is not None
            and normalize(sector) != normalize(session.sector)
        ):
            self.reset_state_from(session, "sector")
            response = await self._select_sector(session_id, session, sector)
        elif sector and session.country is not None and session.region is not None and session.sector is None:
            response = await self._select_sector(session_id, session, sector)
        elif sector and session.country is not None and session.region is None:
            session.pending_selection = {"country": None, "region": None, "sector": sector}

        if response is not None:
            return response
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    @staticmethod
    def _is_selection_information_request(message: str) -> bool:
        normalized = normalize_for_match(message)
        if not normalized:
            return False
        if " if " in normalized and any(phrase in normalized for phrase in {" select it", " use it", " let s use it"}):
            return False
        question_starts = (
            "what countries",
            "which country",
            "what regions",
            "which region",
            "what sectors",
            "which sector",
            "what have i selected",
            "is ",
            "tell me about ",
            "why can t i select ",
            "why cant i select ",
            "can i select the whole country",
            "what are the risks",
            "what can you tell me about ",
        )
        return normalized.endswith("?") or any(normalized.startswith(prefix) for prefix in question_starts)

    @staticmethod
    def _information_request_message(session: ChatSession, current_phase: str) -> str:
        if session.sector:
            return ChatSelectionStepsMixin._already_selected_message(session, "", "", session.sector)
        if current_phase == "country" or not session.country:
            return "Please select a country from the available options."
        if current_phase == "region" or not session.region:
            return f"{session.country} selected. Please choose a region."
        return f"{session.region} selected. Please choose a sector."

    @staticmethod
    def _is_negated_selection(message: str) -> bool:
        normalized = normalize_for_match(message)
        return normalized.startswith(("not ", "no not ", "anything except ", "anywhere except "))

    @staticmethod
    def _looks_like_selection_topic_without_command(message: str) -> bool:
        normalized = normalize_for_match(message)
        if not normalized:
            return False
        if " in the list" in f" {normalized} ":
            return True
        if " isn t available " in f" {normalized} " or " isnt available " in f" {normalized} ":
            return True
        if " i don t know " in f" {normalized} " or normalized.startswith("i don t know"):
            return True
        if normalized.startswith("i think it was"):
            return True
        if "charging" in normalized.split():
            return True
        topic_terms = {
            "prices",
            "price",
            "costs",
            "risks",
            "risk",
            "problems",
            "issue",
            "issues",
            "solar",
            "panels",
            "renters",
            "charging",
        }
        selection_verbs = {
            "select",
            "choose",
            "use",
            "set",
            "start",
            "go",
            "focus",
            "analyse",
            "analyze",
            "explore",
            "look",
        }
        tokens = set(normalized.split())
        return bool(tokens & topic_terms) and not bool(tokens & selection_verbs)

    @staticmethod
    def _looks_like_unsafe_selection_instruction(message: str) -> bool:
        normalized = normalize_for_match(message)
        unsafe_terms = {
            "system ignore",
            "internal state",
            "skip validation",
            "valid true",
            "admin approved",
            "script selectcountry",
            "sector valid true",
        }
        return any(term in normalized for term in unsafe_terms)

    async def _open_selection_navigation_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> ChatResponse | None:
        action = self._selection_action_from_open_text(session, message, current_phase)
        if action is None:
            return None
        return await self._apply_selection_action(session_id, session, action)

    def _selection_action_from_open_text(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None

        if normalized in {
            "restart",
            "restart from the beginning",
            "restart from beginning",
            "start again",
            "start over",
            "start from the beginning",
            "start from beginning",
            "reset",
            "reset everything",
            "restart selection",
        }:
            return "restart_selection"
        if normalized in {
            "change country",
            "choose another country",
            "select another country",
            "back to country",
            "go back to country",
            "start over with a different country",
        }:
            return "change_country"
        if normalized in {
            "change region",
            "change the region",
            "choose another region",
            "select another region",
            "back to region",
            "go back to region",
        }:
            return "change_region"
        if " change the region " in f" {normalized} " or " change region " in f" {normalized} ":
            return "change_region"
        if normalized in {
            "change sector",
            "choose another sector",
            "choose a different sector",
            "select another sector",
            "back to sector",
            "go back to sector",
            "back to sectors",
            "go back to sectors",
        }:
            return "change_sector"
        if normalized in {"go back", "back", "previous", "previous step", "change previous step"}:
            if session.sector is not None:
                return "change_sector"
            if current_phase == "sector" or session.region is not None:
                return "change_region"
            if current_phase == "region" or session.country is not None:
                return "change_country"
        return None

    def _ordinal_selection_from_text(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> dict[str, str | None] | None:
        ordinal = self._ordinal_index_from_text(message)
        if ordinal is None:
            return None

        key = ""
        labels: list[str] = []
        if current_phase == "country":
            key = "country"
            labels = self._available_country_names()
        elif current_phase == "region":
            key = "region"
            labels = self._available_region_names(session)
        elif current_phase == "sector":
            key = "sector"
            labels = self._available_sector_names(session)
        if not key or not labels:
            return None

        index = ordinal if ordinal >= 0 else len(labels) + ordinal
        if index < 0 or index >= len(labels):
            return None
        return {
            "country": labels[index] if key == "country" else None,
            "region": labels[index] if key == "region" else None,
            "sector": labels[index] if key == "sector" else None,
        }

    @classmethod
    def _ordinal_index_from_text(cls, message: str) -> int | None:
        tokens = normalize_for_match(message).split()
        if not tokens:
            return None
        allowed = {"the", "one", "option", "hazard", "please", "select", "choose", "go", "with"}
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
            value: int | None = None
            if token in number_words:
                value = number_words[token]
            else:
                match = cls._ordinal_number_match(token)
                if match is not None:
                    value = match
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
    def _ordinal_number_match(token: str) -> int | None:
        suffixes = ("st", "nd", "rd", "th")
        for suffix in suffixes:
            if token.endswith(suffix) and token[: -len(suffix)].isdigit():
                return int(token[: -len(suffix)])
        return int(token) if token.isdigit() else None

    def _deterministic_selection_from_text(
        self,
        session: ChatSession,
        message: str,
    ) -> dict[str, str | None] | None:
        normalized = normalize_for_match(self._selection_correction_tail(message))
        if not normalized:
            return None
        remaining = f" {normalized} "
        selection: dict[str, str | None] = {"country": None, "region": None, "sector": None}

        country_match = self._single_label_match(
            remaining,
            self._available_country_names(),
        )
        if country_match == "":
            return None
        if country_match:
            selection["country"] = country_match
            remaining = self._remove_selection_term(remaining, country_match)

        region_labels = self._available_region_names(session)
        sector_labels = self._available_sector_names(session)
        if country_match and (
            session.country is None or normalize(country_match) != normalize(session.country)
        ):
            try:
                country = self._country_by_name(country_match)
            except AttributeError:
                country = None
            if country is not None:
                region_labels = [region.name for region in self._regions_for_country_id(country.id)]
                sector_labels = [sector.name for sector in self._sectors_for_country(country.id)]

        for key, labels in [("region", region_labels), ("sector", sector_labels)]:
            matches: list[tuple[str, str]] = []
            for label in labels:
                for term in self._selection_label_terms(label):
                    if f" {term} " in remaining:
                        matches.append((label, term))
                        break
            if len(matches) > 1:
                return None
            if matches:
                label, term = matches[0]
                selection[key] = label
                remaining = remaining.replace(f" {term} ", " ")
        if selection["region"]:
            region_terms = {
                term
                for region in self._available_region_names(ChatSession())
                if normalize(region) != normalize(selection["region"])
                for term in self._selection_label_terms(region)
            }
            for term in sorted(region_terms, key=len, reverse=True):
                remaining = remaining.replace(f" {term} ", " ")
        if not any(selection.values()):
            return None
        if not self._is_selection_filler_text(remaining):
            return None
        return selection

    @staticmethod
    def _selection_correction_tail(message: str) -> str:
        normalized = normalize_for_match(message)
        if normalized.startswith("change ") and " to " in normalized:
            return normalized.rsplit(" to ", 1)[1].strip()
        if "change" in normalized.split() and " sector to " in f" {normalized} ":
            return normalized.rsplit(" sector to ", 1)[1].strip()
        if "change" in normalized.split() and " analyse " in f" {normalized} ":
            return normalized.rsplit(" analyse ", 1)[1].strip()
        if "change" in normalized.split() and " analyze " in f" {normalized} ":
            return normalized.rsplit(" analyze ", 1)[1].strip()
        if " make that " in f" {normalized} ":
            return normalized.rsplit(" make that ", 1)[1].strip()
        if " otherwise " in f" {normalized} ":
            return normalized.rsplit(" otherwise ", 1)[1].strip()
        separators = (
            " actually ",
            " no i meant ",
            " no ",
            " i meant ",
            " but i want ",
            " sorry ",
            " use ",
        )
        padded = f" {normalized} "
        best_index = -1
        best_separator = ""
        for separator in separators:
            index = padded.rfind(separator)
            if index > best_index:
                best_index = index
                best_separator = separator
        if best_index >= 0:
            return padded[best_index + len(best_separator) :].strip()
        return message

    def _implicit_region_selection_from_text(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> dict[str, str | None] | None:
        if current_phase != "country" or session.country is not None:
            return None
        original_normalized = f" {normalize_for_match(message)} "
        normalized = f" {normalize_for_match(self._selection_correction_tail(message))} "
        region_matches: list[tuple[str, str]] = []
        for country_name in self._available_country_names():
            try:
                country = self._country_by_name(country_name)
            except AttributeError:
                return None
            if country is None:
                continue
            for region in self._regions_for_country_id(country.id):
                for term in self._selection_label_terms(region.name):
                    if f" {term} " in normalized:
                        region_matches.append((country.name, region.name))
                        break
        if len(region_matches) != 1:
            return None
        country_name, region_name = region_matches[0]
        sector = self._single_label_match(normalized, self._available_sector_names(session))
        if not sector:
            sector = self._single_label_match(original_normalized, self._available_sector_names(session))
        if sector == "":
            return None
        return {"country": country_name, "region": region_name, "sector": sector}

    def _invalid_conflicting_tuple_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> ChatResponse | None:
        if current_phase != "country":
            return None
        normalized = f" {normalize_for_match(message)} "
        countries = [
            country
            for country in self._available_country_names()
            if any(f" {term} " in normalized for term in self._selection_label_terms(country))
        ]
        if (
            len(set(countries)) > 1
            and " actually " not in normalized
            and " sorry " not in normalized
            and " make that " not in normalized
            and " otherwise " not in normalized
        ):
            return self._repeat_current_options(session_id, session, self.invalid_message, True)
        if not countries:
            return None
        try:
            selected_country = self._country_by_name(countries[0])
        except AttributeError:
            return None
        if selected_country is None:
            return None
        current_region_terms = {
            term
            for region in self._regions_for_country_id(selected_country.id)
            for term in self._selection_label_terms(region.name)
        }
        all_region_terms = {
            term
            for region in self._available_region_names(ChatSession())
            for term in self._selection_label_terms(region)
        }
        has_current_region = any(f" {term} " in normalized for term in current_region_terms)
        has_other_region = any(
            f" {term} " in normalized
            for term in all_region_terms
            if term not in current_region_terms
        )
        has_sector = any(
            f" {term} " in normalized
            for sector in self._sectors_for_country(selected_country.id)
            for term in self._selection_label_terms(sector.name)
        )
        if has_other_region and not has_current_region:
            return self._repeat_current_options(session_id, session, self.invalid_message, True)
        unsupported_sector_terms = {"agriculture", "healthcare", "finance"}
        if has_current_region and any(f" {term} " in normalized for term in unsupported_sector_terms):
            return self._repeat_current_options(session_id, session, self.invalid_message, True)
        return None

    async def _negated_region_country_only_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> ChatResponse | None:
        if current_phase != "country":
            return None
        normalized = f" {normalize_for_match(message)} "
        if " not " not in normalized:
            return None
        country = self._single_label_match(normalized, self._available_country_names())
        if not country:
            return None
        sector = self._single_label_match(normalized, self._available_sector_names(session))
        if sector == "":
            sector = None
        return await self._apply_pending_selection(
            session_id,
            session,
            {"country": country, "region": None, "sector": sector},
        )

    @classmethod
    def _single_label_match(cls, remaining: str, labels: list[str]) -> str | None:
        matches: list[str] = []
        for label in labels:
            for term in cls._selection_label_terms(label):
                if f" {term} " in remaining:
                    matches.append(label)
                    break
        if len(matches) > 1:
            return ""
        return matches[0] if matches else None

    def _fuzzy_selection_from_text(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> dict[str, str | None] | None:
        normalized = normalize_for_match(message)
        if not normalized or len(normalized.split()) > 3:
            return None
        if normalized in {"yes", "no", "back", "restart", "reset"}:
            return None

        if current_phase == "country":
            country = self._high_confidence_label_match(normalized, self._available_country_names())
            return {"country": country, "region": None, "sector": None} if country else None
        if current_phase == "region":
            region = self._high_confidence_label_match(
                normalized,
                self._available_region_names(session),
            )
            return {"country": None, "region": region, "sector": None} if region else None
        if current_phase == "sector":
            sector = self._high_confidence_label_match(
                normalized,
                self._available_sector_names(session),
            )
            return {"country": None, "region": None, "sector": sector} if sector else None
        return None

    @classmethod
    def _high_confidence_label_match(cls, normalized_message: str, labels: list[str]) -> str | None:
        alias_match = cls._selection_alias_match(normalized_message, labels)
        if alias_match:
            return alias_match

        scored = sorted(
            (
                (fuzzy_score(normalized_message, label), label)
                for label in labels
                if normalize_for_match(label)
            ),
            reverse=True,
        )
        if not scored:
            return None
        best_score, best_label = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= 0.58 and best_score - second_score >= 0.12:
            return best_label
        return None

    def _out_of_scope_selection_from_text(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> dict[str, str | None] | None:
        normalized = normalize_for_match(message)
        if not normalized or len(normalized.split()) > 3:
            return None
        if current_phase == "region":
            current_regions = {normalize_for_match(region) for region in self._available_region_names(session)}
            for region in self._available_region_names(ChatSession()):
                if normalize_for_match(region) == normalized and normalized not in current_regions:
                    return {"country": None, "region": region, "sector": None}
        return None

    def _has_ambiguous_same_level_options(
        self,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> bool:
        normalized = f" {normalize_for_match(message)} "
        if " or " not in normalized and not normalized.strip().startswith("compare "):
            return False
        labels: list[str]
        if current_phase == "country":
            labels = self._available_country_names()
        elif current_phase == "region":
            labels = self._available_region_names(session)
        elif current_phase == "sector":
            labels = self._available_sector_names(session)
        else:
            labels = (
                self._available_country_names()
                + self._available_region_names(session)
                + self._available_sector_names(session)
            )
        matches = 0
        for label in labels:
            for term in self._selection_label_terms(label):
                if f" {term} " in normalized:
                    matches += 1
                    break
        return matches > 1

    @staticmethod
    def _looks_like_unknown_option_label(message: str) -> bool:
        stripped = message.strip()
        words = stripped.split()
        if not words or len(words) > 3:
            return False
        if not all(word.replace("-", "").isalpha() for word in words):
            return False
        return all(word[:1].isupper() for word in words)

    @staticmethod
    def _mentions_unsupported_country(message: str) -> bool:
        normalized = f" {normalize_for_match(message)} "
        unsupported_country_terms = {
            "france",
            "francia",
            "french",
        }
        return any(f" {term} " in normalized for term in unsupported_country_terms)

    @classmethod
    def _selection_alias_match(cls, normalized_message: str, labels: list[str]) -> str | None:
        aliases = cls._selection_aliases()
        for label in labels:
            normalized_label = normalize_for_match(label)
            if normalized_message in aliases.get(normalized_label, []):
                return label
        return None

    @staticmethod
    def _selection_aliases() -> dict[str, list[str]]:
        return {
            "germany": ["de", "deu", "ger"],
            "spain": ["es", "esp"],
            "portugal": ["pt", "prt"],
            "ireland": ["ie", "irl"],
            "italy": ["it", "ita"],
            "hungary": ["hu", "hun"],
        }

    def _sector_alias_confirmation_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        current_phase: str,
    ) -> ChatResponse | None:
        if current_phase != "sector":
            return None
        normalized = normalize_for_match(message)
        confirmation_alias_phrases = {
            "power and electricity",
            "buildings and homes",
            "mobility and public transit",
        }
        if normalized not in confirmation_alias_phrases:
            return None
        sector = self._sector_label_from_alias_text(session, message)
        if not sector:
            return None
        selection = {"country": None, "region": None, "sector": sector}
        session.pending_selection_confirmation = selection
        session.pending_selection_action = None
        return ChatResponse(
            session_id=session_id,
            step="selection_confirmation",
            bot_message=self._selection_confirmation_message(selection),
            options=SELECTION_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _sector_label_from_alias_text(
        self,
        session: ChatSession,
        message: str,
    ) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        matches: list[str] = []
        for label in self._available_sector_names(session):
            if normalized == normalize_for_match(label):
                return None
            for alias in self._selection_label_aliases(label):
                alias_normalized = normalize_for_match(alias)
                remaining = f" {normalized} ".replace(f" {alias_normalized} ", " ")
                if remaining != f" {normalized} " and self._is_selection_filler_text(remaining):
                    matches.append(label)
                    break
        return matches[0] if len(set(matches)) == 1 else None

    @classmethod
    def _remove_selection_term(cls, remaining: str, label: str) -> str:
        for term in cls._selection_label_terms(label):
            if f" {term} " in remaining:
                return remaining.replace(f" {term} ", " ")
        return remaining

    @staticmethod
    def _selection_label_terms(label: str) -> list[str]:
        normalized = normalize_for_match(label)
        terms = [normalized]
        terms.extend(ChatSelectionStepsMixin._selection_label_aliases(label))
        if normalized.endswith("ia"):
            terms.append(f"{normalized[:-1]}n")
        unique_terms = list(dict.fromkeys(term for term in terms if term))
        return sorted(unique_terms, key=lambda term: (len(term.split()), len(term)), reverse=True)

    @staticmethod
    def _selection_label_aliases(label: str) -> list[str]:
        normalized = normalize_for_match(label)
        aliases = {
            "baden württemberg": ["baden wurttemberg", "baden wurtemberg", "bw"],
            "bavaria": ["bavarian"],
            "germany": ["german", "deutschland"],
            "spain": ["spanish"],
            "portugal": ["portuguese"],
            "ireland": ["irish"],
            "italy": ["italian"],
            "hungary": ["hungarian"],
            "transport": ["mobility", "transit", "public transit", "mobility and public transit", "evs"],
            "housing": ["homes", "buildings", "buildings and homes"],
            "energy": [
                "power",
                "electricity",
                "power and electricity",
                "renewable energy",
                "energy related transition risks",
                "energy transition",
                "renewable energy risks",
            ],
        }
        return list(aliases.get(normalized, []))

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
            "s",
            "lets",
            "want",
            "understand",
            "will",
            "would",
            "like",
            "know",
            "can",
            "we",
            "look",
            "ll",
            "go",
            "analyze",
            "analyse",
            "explore",
            "to",
            "at",
            "as",
            "start",
            "begin",
            "set",
            "it",
            "with",
            "use",
            "is",
            "works",
            "right",
            "sounds",
            "me",
            "select",
            "choose",
            "please",
            "instead",
            "specifically",
            "maybe",
            "think",
            "probably",
            "interesting",
            "earlier",
            "now",
            "said",
            "meant",
            "sorry",
            "forgot",
            "we",
            "re",
            "available",
            "if",
            "yes",
            "that",
            "choice",
            "risks",
            "problems",
            "drop",
            "table",
            "countries",
            "my",
            "new",
            "analysis",
            "only",
            "necessary",
            "session",
            "the",
            "a",
            "an",
            "and",
            "first",
            "in",
            "about",
            "for",
            "sector",
            "country",
            "region",
            "state",
            "change",
            "actually",
            "again",
            "switch",
            "assessment",
            "focus",
            "on",
            "this",
            "transition",
            "context",
            "bitte",
        }
        return all(token in filler for token in tokens)

    @staticmethod
    def _looks_like_selection_request(message: str) -> bool:
        normalized = normalize_for_match(message)
        selection_phrases = (
            "can we look at ",
            "could we look at ",
            "can you look at ",
            "could you look at ",
            "can we use ",
            "could we use ",
            "can we select ",
            "could we select ",
            "can we choose ",
            "could we choose ",
            "can we analyze ",
            "could we analyze ",
            "can we analyse ",
            "could we analyse ",
        )
        return any(normalized.startswith(prefix.strip()) for prefix in selection_phrases)

    @staticmethod
    def _selection_matches_current_context(
        session: ChatSession,
        country: str,
        region: str,
        sector: str,
    ) -> bool:
        selected_values = [value for value in [country, region, sector] if value]
        if not selected_values:
            return False
        comparisons = [
            (country, session.country),
            (region, session.region),
            (sector, session.sector),
        ]
        return all(
            not incoming or (current is not None and normalize(incoming) == normalize(current))
            for incoming, current in comparisons
        )

    def _load_hazard_listing_cache(self, session: ChatSession) -> bool:
        if session.country_id is None or session.sector_id is None:
            return False
        try:
            row = self.db.scalar(
                select(HazardListingCache).where(
                    HazardListingCache.country_id == session.country_id,
                    HazardListingCache.region_scope_key == self._hazard_cache_region_scope_key(session),
                    HazardListingCache.sector_id == session.sector_id,
                    HazardListingCache.cache_version == HAZARD_LISTING_CACHE_VERSION,
                )
            )
            if row is None:
                return False
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if row.expires_at is not None and row.expires_at <= now:
                return False
            if row.source_fingerprint != self._hazard_listing_source_fingerprint(session):
                return False
            payload = json.loads(row.payload_json)
        except Exception:
            logger.exception("Hazard-listing cache lookup failed")
            return False
        if not isinstance(payload, dict):
            return False
        return self._apply_hazard_listing_cache_payload(session, payload)

    def _store_hazard_listing_cache(self, session: ChatSession) -> None:
        if session.country_id is None or session.sector_id is None:
            return
        payload = self._hazard_listing_cache_payload(session)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            row = self.db.scalar(
                select(HazardListingCache).where(
                    HazardListingCache.country_id == session.country_id,
                    HazardListingCache.region_scope_key == self._hazard_cache_region_scope_key(session),
                    HazardListingCache.sector_id == session.sector_id,
                    HazardListingCache.cache_version == HAZARD_LISTING_CACHE_VERSION,
                )
            )
            if row is None:
                row = HazardListingCache(
                    country_id=session.country_id,
                    region_id=session.region_id,
                    region_scope_key=self._hazard_cache_region_scope_key(session),
                    sector_id=session.sector_id,
                    cache_version=HAZARD_LISTING_CACHE_VERSION,
                    source_fingerprint=self._hazard_listing_source_fingerprint(session),
                    payload_json=payload_json,
                    expires_at=self._hazard_listing_cache_expiry(session),
                )
                self.db.add(row)
            else:
                row.region_id = session.region_id
                row.source_fingerprint = self._hazard_listing_source_fingerprint(session)
                row.payload_json = payload_json
                row.expires_at = self._hazard_listing_cache_expiry(session)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Hazard-listing cache store failed")

    @staticmethod
    def _hazard_cache_region_scope_key(session: ChatSession) -> str:
        return str(session.region_id or "").strip()

    @staticmethod
    def _hazard_listing_cache_payload(session: ChatSession) -> dict[str, object]:
        system_hazards = [
            str(hazard).strip()
            for hazard in (session.hazards or [])
            if str(hazard or "").strip()
        ]
        additional_hazards = [
            str(hazard).strip()
            for hazard in (session.additional_hazards or [])
            if str(hazard or "").strip()
        ]
        cache_hazard_names = {hazard.casefold() for hazard in [*system_hazards, *additional_hazards]}
        hazard_profiles = {
            str(hazard): profiles
            for hazard, profiles in (session.hazard_profiles or {}).items()
            if str(hazard or "").strip().casefold() in cache_hazard_names
        }
        hazard_rankings = {
            str(hazard): ranking
            for hazard, ranking in (session.hazard_rankings or {}).items()
            if str(hazard or "").strip().casefold()
            in {item.casefold() for item in system_hazards}
        }
        return {
            "system_hazards": system_hazards,
            "additional_hazards": additional_hazards,
            "hazard_profiles": hazard_profiles,
            "hazard_rankings": hazard_rankings,
        }

    @staticmethod
    def _apply_hazard_listing_cache_payload(
        session: ChatSession,
        payload: dict[str, object],
    ) -> bool:
        system_hazards = payload.get("system_hazards")
        additional_hazards = payload.get("additional_hazards")
        hazard_profiles = payload.get("hazard_profiles")
        hazard_rankings = payload.get("hazard_rankings")
        if not isinstance(system_hazards, list) or not isinstance(additional_hazards, list):
            return False
        if not isinstance(hazard_profiles, dict) or not isinstance(hazard_rankings, dict):
            return False
        session.hazards = [
            str(hazard).strip()
            for hazard in system_hazards
            if str(hazard or "").strip()
        ]
        session.additional_hazards = [
            str(hazard).strip()
            for hazard in additional_hazards
            if str(hazard or "").strip()
        ]
        session.hazard_profiles = {
            str(hazard): profiles
            for hazard, profiles in hazard_profiles.items()
            if isinstance(profiles, (list, str))
        }
        session.hazard_rankings = {
            str(hazard): dict(ranking)
            for hazard, ranking in hazard_rankings.items()
            if isinstance(ranking, dict)
        }
        return bool(session.hazards or session.additional_hazards)

    def _hazard_listing_source_fingerprint(self, session: ChatSession) -> str:
        parts: list[object] = [
            HAZARD_LISTING_CACHE_VERSION,
            session.country_id,
            session.region_id or "",
            session.sector_id,
            self._hazard_source_stats(
                SystemHazard,
                SystemHazard.sector_id == session.sector_id,
            ),
            self._hazard_source_stats(
                SystemHazardSocioDemographic,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
            ),
            self._hazard_source_stats(
                SystemHazardSocioDemographicTargetPopulation,
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id.in_(
                    select(SystemHazardSocioDemographic.id).where(
                        SystemHazardSocioDemographic.sector_id == session.sector_id
                    )
                ),
            ),
            self._hazard_source_stats(
                AdditionalHazard,
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            ),
            self._hazard_source_stats(
                AdditionalHazardProfile,
                AdditionalHazardProfile.additional_hazard_id.in_(
                    select(AdditionalHazard.id).where(
                        AdditionalHazard.country_id == session.country_id,
                        AdditionalHazard.sector_id == session.sector_id,
                    )
                ),
            ),
            self._hazard_source_stats(
                AdditionalHazardProfileTargetPopulation,
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id.in_(
                    select(AdditionalHazardProfile.id)
                    .join(
                        AdditionalHazard,
                        AdditionalHazard.id == AdditionalHazardProfile.additional_hazard_id,
                    )
                    .where(
                        AdditionalHazard.country_id == session.country_id,
                        AdditionalHazard.sector_id == session.sector_id,
                    )
                ),
            ),
            self._hazard_source_stats(
                EurostatPopulationCache,
                EurostatPopulationCache.country_id == session.country_id,
                EurostatPopulationCache.region_id == session.region_id,
                EurostatPopulationCache.sector_id == session.sector_id,
            ),
        ]
        payload = json.dumps(parts, default=str, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _hazard_source_stats(self, model, *conditions) -> tuple[int, str, str]:
        timestamp = getattr(model, "updated_at", None) or getattr(model, "created_at", None)
        max_timestamp = func.max(timestamp) if timestamp is not None else None
        row = self.db.execute(
            select(
                func.count(model.id),
                func.coalesce(func.max(model.id), 0),
                max_timestamp,
            ).where(*conditions)
        ).one()
        return (
            int(row[0] or 0),
            str(row[1] or ""),
            str(row[2] or ""),
        )

    def _hazard_listing_cache_expiry(self, session: ChatSession) -> datetime | None:
        if session.country_id is None or session.sector_id is None:
            return None
        return self.db.scalar(
            select(func.min(EurostatPopulationCache.expires_at)).where(
                EurostatPopulationCache.country_id == session.country_id,
                EurostatPopulationCache.region_id == session.region_id,
                EurostatPopulationCache.sector_id == session.sector_id,
            )
        )

    @staticmethod
    def _already_selected_message(
        session: ChatSession,
        country: str,
        region: str,
        sector: str,
    ) -> str:
        selected_context = ChatSelectionStepsMixin._selected_context_label(session)
        if selected_context and session.sector:
            return (
                f"{selected_context} are already selected. "
                f"{ChatSelectionStepsMixin._current_phase_instruction(session)}"
            )
        if selected_context and session.region:
            return f"{selected_context} are already selected. Please choose a sector."
        if sector and session.sector and normalize(sector) == normalize(session.sector):
            return f"{session.sector} is already selected. Selection flow completed."
        if region and session.region and normalize(region) == normalize(session.region):
            return f"{session.region} is already selected. Please choose a sector."
        if country and session.country and normalize(country) == normalize(session.country):
            return f"{session.country} is already selected. Please choose a region."
        return "This option is already selected. Please continue with the next step."

    @staticmethod
    def _selected_context_label(session: ChatSession) -> str:
        values = [session.country, session.region, session.sector]
        labels = [str(value).strip() for value in values if str(value or "").strip()]
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return f"{labels[0]}, {labels[1]}, and {labels[2]}"

    @staticmethod
    def _current_phase_instruction(session: ChatSession) -> str:
        phase = session.phase or "complete"
        if phase in {"hazards", "stats_deep_dive", "hazard_profile_selection"}:
            return "You are already reviewing hazards; choose a hazard action or type a question."
        if phase in {
            "add_hazard",
            "add_hazard_reason",
            "add_hazard_evidence_decision",
            "add_hazard_evidence_input",
            "add_hazard_evidence",
            "custom_hazard_input",
            "custom_hazard_review",
            "custom_hazard_clarification",
            "custom_hazard_duplicate_confirmation",
            "custom_hazard_group_review",
            "custom_hazard_reason",
            "custom_hazard_evidence",
        }:
            return "You are already adding a hazard; continue with the current hazard step."
        if phase in {
            "mitigation",
            "mitigation_intro",
            "mitigation_measure",
            "mitigation_target_population",
            "mitigation_target_population_review",
            "mitigation_review",
            "reason_confirmation",
        }:
            return "You are already in the mitigation step; continue with the current prompt."
        if phase in {"evaluation_question", "evaluation_complete"}:
            return "You are already in the evaluation step; continue with the current prompt."
        if phase in {"socio_demographic_review", "dg_reason_evidence", "add_dgs"}:
            return "You are already reviewing affected profiles; continue with the current prompt."
        if phase == "other_actions":
            return "Please choose one of the available actions."
        return "Please continue with the current step."

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
        applied_context_response = self._apply_valid_context_before_invalid_tail(
            session_id,
            session,
            selection,
        )
        if applied_context_response is not None:
            return applied_context_response

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

    def _apply_valid_context_before_invalid_tail(
        self,
        session_id: str,
        session: ChatSession,
        selection: dict[str, str | None],
    ) -> ChatResponse | None:
        country_name = selection.get("country")
        region_name = selection.get("region")
        sector_name = selection.get("sector")
        if not country_name or not region_name or not sector_name or session.region is not None:
            return None

        country = self._country_by_name(country_name)
        if country is None:
            return None
        region = self._region_by_name(region_name, country.id)
        if region is None:
            return None

        session.country_id = country.id
        session.country = country.name
        session.region_id = region.id
        session.region = region.name
        session.phase = "sector"
        self._ensure_user_session(session_id, session)
        self._record_activity(session_id, session, "country_selected", country.name, step="country")
        self._record_activity(session_id, session, "region_selected", region.name, step="region")
        sectors = self._sectors_for_country(country.id)
        return ChatResponse(
            session_id=session_id,
            step="sector",
            bot_message=(
                f"**{sector_name}** is not available for **{country.name}**.\n\n"
                "Please choose one of the available sectors."
            ),
            options=option_list(sectors),
            session=session.summary(),
            error=True,
        )

    def _selection_action_confirmation_step(
        self,
        session_id: str,
        session: ChatSession,
        action: str,
    ) -> ChatResponse:
        session.pending_selection_action = action
        session.pending_selection_confirmation = None
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
            session.phase = "country"
            return self._country_step(session_id, session, self.welcome_message, False)
        if action == "change_region":
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self.reset_state_from(session, "region")
            session.phase = "region"
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
                session.phase = "country"
                return self._country_step(session_id, session, self.invalid_message, True)
            if session.region_id is None and session.region != "National scope":
                self.reset_state_from(session, "region")
                session.phase = "region"
                regions = self.db.scalars(
                    select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
                ).all()
                return ChatResponse(
                    session_id=session_id,
                    step="region",
                    bot_message=(
                        "Please choose a region first. Then I can show the sectors "
                        "available for that country."
                    ),
                    options=option_list(list(regions)),
                    session=session.summary(),
                    error=False,
                )
            self.reset_state_from(session, "sector")
            session.phase = "sector"
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

    def _regions_for_country_id(self, country_id: str | None) -> list[Region]:
        if country_id is None:
            return []
        if hasattr(self, "_regions_for_country"):
            return list(self._regions_for_country(country_id))
        return list(
            self.db.scalars(
                select(Region).where(Region.country_id == country_id).order_by(Region.name)
            ).all()
        )

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
        matched = self._match_country(name)
        if matched is not None:
            return matched
        target = normalize_for_match(name)
        countries = self.db.scalars(select(Country).order_by(Country.name)).all()
        return next((country for country in countries if normalize_for_match(country.name) == target), None)

    def _region_by_name(self, name: str | None, country_id: str) -> Region | None:
        if not name:
            return None
        matched = self._match_region(name, country_id)
        if matched is not None:
            return matched
        target = normalize_for_match(name)
        regions = self.db.scalars(
            select(Region).where(Region.country_id == country_id).order_by(Region.name)
        ).all()
        return next((region for region in regions if normalize_for_match(region.name) == target), None)

    def _match_country(self, message: str) -> Country | None:
        countries = self.db.scalars(
            select(Country).options(selectinload(Country.regions)).order_by(Country.name)
        ).all()
        return self._match_by_id_or_name(list(countries), message)

    def _match_region(self, message: str, country_id: str | None) -> Region | None:
        if country_id is None:
            return None
        regions = self.db.scalars(
            select(Region).where(Region.country_id == country_id).order_by(Region.name)
        ).all()
        return self._match_by_id_or_name(list(regions), message)

    def _match_sector(self, message: str, country_id: str | None) -> Sector | None:
        return self._match_by_id_or_name(self._sectors_for_country(country_id), message)

    def _sectors_for_country(self, country_id: str | None) -> list[Sector]:
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
        normalized = normalize_for_match(message)
        for row in rows:
            if str(row.id) == message.strip() or normalize_for_match(row.name) == normalized:
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

    async def _enrich_additional_and_custom_hazard_profiles_with_population_context(
        self,
        session: ChatSession,
    ) -> None:
        if not session.hazard_profiles:
            return
        enriched_profiles: dict[str, list[dict[str, object]]] = {}
        seen: set[str] = set()
        hazard_names = [
            *(session.additional_hazards or []),
            *(session.custom_hazards or []),
        ]
        for hazard in hazard_names:
            hazard_name = str(hazard or "").strip()
            hazard_key = hazard_name.casefold()
            if not hazard_name or hazard_key in seen:
                continue
            seen.add(hazard_key)
            profiles = self._stored_hazard_profiles(session, hazard_name)
            if not profiles or self._profiles_have_population_percentages(profiles):
                continue
            enriched_profiles[hazard_name] = (
                await self._additional_profiles_with_population_context(
                    session,
                    hazard_name,
                    profiles,
                )
            )
        if enriched_profiles:
            session.hazard_profiles = {
                **(session.hazard_profiles or {}),
                **enriched_profiles,
            }

    @staticmethod
    def _profiles_have_population_percentages(
        profiles: list[dict[str, object]],
    ) -> bool:
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            if (
                profile.get("regional_population_pct") is not None
                or profile.get("population_pct") is not None
                or profile.get("national_population_pct") is not None
            ):
                return True
        return False
