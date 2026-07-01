import logging

from sqlalchemy import select

from app.models import Country, Region, Sector
from app.schemas import ChatResponse
from app.services.chat_options import option_list
from app.services.chat_session import ChatSession
from app.services.message_renderer import render_message

logger = logging.getLogger(__name__)


class ChatSelectionStepsMixin:
    async def _select_country(
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
