import asyncio
import logging
import re

from app.llm import ask_llm_chat
from app.models import UserMitigationMeasure
from app.services.chat_json import parse_json_array, parse_json_object
from app.services.chat_options import fuzzy_score, normalize, normalize_for_match
from app.services.chat_parsers import is_llm_unavailable_response, parse_llm_hazard_list
from app.services.chat_session import ChatSession
from app.services.hazard_profile_parsing import (
    clean_hazard_profile_item,
    humanize_predictor_label,
    profile_from_predictor_entry,
)
from app.services.prompt_loader import render_prompt_template
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger("app.services.chat_hazard_creation")


class ChatHazardGenerationMixin:
    async def _get_hazards_from_llm(self, session: ChatSession) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_and_profiles_from_sector_rag(session)
        if hazard_items:
            return hazard_items

        hazards = await self._get_hazard_names_from_llm(session)
        profile_lists = await asyncio.gather(
            *(self._get_hazard_profiles_from_llm(session, hazard) for hazard in hazards)
        )
        return [
            {"hazard": hazard, "profiles": profiles}
            for hazard, profiles in zip(hazards, profile_lists, strict=False)
        ]

    async def _get_hazard_names_from_llm(self, session: ChatSession) -> list[str]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            "HAZARD confirmed predictors ranked concern",
            limit=25,
        )
        context = render_prompt_template(
            "llm/hazard_names_extraction.txt",
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_names_extraction_user.txt",
                    sector=session.sector,
                ),
            }
        ]

        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return parse_llm_hazard_list(response)

    async def _get_hazard_profiles_from_llm(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        hazard_block = await self._sector_prompt_hazard_block_from_rag(
            session,
            hazard,
        )
        if hazard_block:
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                return []
            profiles = self._profiles_from_hazard_block(hazard_block)
            if profiles:
                return profiles

        context = render_prompt_template(
            "llm/hazard_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block or "- No relevant sector-prompt RAG excerpts were found.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return self._parse_hazard_profile_items(response)

    async def _get_hazards_and_profiles_from_sector_rag(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        rag_service = SectorPromptRagService(self.db)
        try:
            results = await rag_service.hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block extraction lookup failed")
            results = []
        if not results:
            try:
                results = await rag_service.search(
                    session.sector,
                    "HAZARD confirmed predictors PREDICTOR Plain-English Direction",
                    limit=40,
                )
            except Exception:
                logger.exception("Sector-prompt RAG hazard/profile extraction lookup failed")
                results = []
        sector_context = SectorPromptRagService.format_results(results, content_limit=6000)
        if not sector_context:
            return []

        context = render_prompt_template(
            "llm/hazards_and_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazards_and_profiles_extraction_user.txt"
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=9000,
        )
        hazard_items = self._parse_hazard_items_response(response)
        expected_hazards = self._hazard_names_from_sector_prompt(
            "\n\n".join(str(result.get("content") or "") for result in results)
        )
        if expected_hazards:
            hazard_items = await self._complete_sector_rag_hazard_items(
                session,
                hazard_items,
                expected_hazards,
                results,
            )
        return hazard_items

    async def _complete_sector_rag_hazard_items(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        expected_hazards: list[str],
        rag_results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        items_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        rag_text = "\n\n".join(str(result.get("content") or "") for result in rag_results)
        completed: list[dict[str, object]] = []
        for hazard in expected_hazards:
            item = items_by_hazard.get(normalize(hazard))
            hazard_block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            deterministic_profiles = self._profiles_from_hazard_block(hazard_block)
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                profiles = []
            elif predictor_entries:
                profiles = deterministic_profiles
            else:
                profiles = list(item.get("profiles", [])) if item else []
            completed.append({"hazard": hazard, "profiles": profiles})
        return completed

    async def _sector_prompt_rag_hazard_count(self, sector: str | None) -> int:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(sector)
            return sum(
                1
                for result in results
                if self._profiles_from_hazard_block(str(result.get("content") or ""))
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-count lookup failed")
            return 0

    async def _sector_prompt_profiles_match_rag(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
    ) -> bool:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG profile-integrity lookup failed")
            return True
        rag_text = "\n\n".join(str(result.get("content") or "") for result in results)
        stored_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        for hazard in self._hazard_names_from_sector_prompt(rag_text):
            block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            expected = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in self._profiles_from_hazard_block(block)
            )
            if not expected:
                continue
            item = stored_by_hazard.get(normalize(hazard))
            actual = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in (item.get("profiles", []) if item else [])
                if isinstance(profile, dict)
            )
            if actual != expected:
                return False
        expected_hazards = {
            normalize(hazard)
            for hazard in self._hazard_names_from_sector_prompt(rag_text)
            if self._profiles_from_hazard_block(
                self._confirmed_predictor_hazard_block(rag_text, hazard)
            )
        }
        return set(stored_by_hazard) == expected_hazards

    def _parse_hazard_items_response(self, response: str) -> list[dict[str, object]]:
        parsed = parse_json_array(response)
        if not isinstance(parsed, list):
            return []
        hazard_items: list[dict[str, object]] = []
        seen_hazards: set[str] = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            hazard = self._clean_sector_hazard_name(
                str(item.get("hazard") or item.get("name") or "")
            )
            if not hazard:
                continue
            hazard_key = normalize(hazard)
            if hazard_key in seen_hazards:
                continue
            seen_hazards.add(hazard_key)
            raw_profiles = item.get("profiles")
            profiles: list[dict[str, str]] = []
            if isinstance(raw_profiles, list):
                profiles = [
                    profile
                    for profile in (
                        self._clean_hazard_profile_item(profile)
                        for profile in raw_profiles
                    )
                    if profile
                ]
            hazard_items.append({"hazard": hazard[:180], "profiles": profiles})
        return hazard_items

    @staticmethod
    def _clean_hazard_profile_item(value: object) -> dict[str, str]:
        return clean_hazard_profile_item(value)

    async def _sector_prompt_hazard_block_from_rag(
        self,
        session: ChatSession,
        hazard: str,
    ) -> str:
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                f'HAZARD {hazard} confirmed predictors PREDICTOR',
                limit=3,
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block lookup failed")
            return ""
        combined = "\n\n".join(
            str(result.get("content") or "").strip()
            for result in results
            if str(result.get("content") or "").strip()
        )
        if not combined:
            return ""
        return self._confirmed_predictor_hazard_block(combined, hazard)

    @classmethod
    def _hazard_names_from_sector_prompt(cls, sector_prompt: str) -> list[str]:
        prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
        hazards: list[str] = []
        for match in re.finditer(r"(?m)^HAZARD\s+\d+\.\s+(.+?)\s*$", prompt):
            hazard = cls._clean_sector_hazard_name(match.group(1))
            if hazard and normalize_for_match(hazard) not in {
                normalize_for_match(existing) for existing in hazards
            }:
                hazards.append(hazard)
        return hazards

    @staticmethod
    def _clean_sector_hazard_name(value: str) -> str:
        hazard = re.sub(r"\s+", " ", str(value or "")).strip()
        hazard = re.sub(r"(?i)^HAZARD\s+\d+\.\s*", "", hazard).strip()
        hazard = strip_rule_lines(hazard).strip()
        if re.fullmatch(r"[─═\-_=]{6,}", hazard):
            return ""
        return hazard

    @classmethod
    def _profiles_from_hazard_block(cls, hazard_block: str) -> list[dict[str, str]]:
        profiles: list[dict[str, str]] = []
        for entry in cls._confirmed_predictor_entries(hazard_block):
            profile = cls._profile_from_predictor_entry(entry)
            if profile:
                profiles.append(profile)
        return profiles

    @classmethod
    def _profile_from_predictor_entry(cls, entry: str) -> dict[str, str]:
        return profile_from_predictor_entry(entry)

    @staticmethod
    def _humanize_predictor_label(value: str) -> str:
        return humanize_predictor_label(value)

    async def _get_hazard_block_profiles_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        hazard_block: str,
        expected_count: int | None = None,
        predictor_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        predictor_id_list = predictor_ids or []
        count_rule = (
            f"- Return exactly {expected_count} profile objects: one for each confirmed PREDICTOR entry in the block.\n"
            if expected_count
            else "- Return one profile object for each confirmed PREDICTOR entry in the block.\n"
        )
        id_rule = (
            "- Use this predictor checklist and return one object for each ID in this order: "
            + ", ".join(predictor_id_list)
            + ".\n"
            if predictor_id_list
            else ""
        )
        context = render_prompt_template(
            "llm/hazard_block_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_block_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                    count_rule=count_rule,
                    id_rule=id_rule,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=900,
        )
        profiles = self._parse_hazard_profile_items(response)
        if expected_count and len(profiles) != expected_count:
            retry_response = await ask_llm_chat(
                context=context,
                messages=[
                    *messages,
                    {
                        "role": "assistant",
                        "content": response,
                    },
                    {
                        "role": "user",
                        "content": render_prompt_template(
                            "llm/hazard_block_profiles_retry_user.txt",
                            profile_count=len(profiles),
                            expected_count=expected_count,
                            checklist=(
                                "Use this checklist in order: "
                                + ", ".join(predictor_id_list)
                                + ". "
                                if predictor_id_list
                                else ""
                            ),
                        ),
                    },
                ],
                temperature=0,
                max_tokens=900,
            )
            retry_profiles = self._parse_hazard_profile_items(retry_response)
            if len(retry_profiles) == expected_count:
                return retry_profiles
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            if len(predictor_entries) == expected_count:
                single_profiles = await asyncio.gather(
                    *(
                        self._get_single_predictor_profile_from_llm(session, hazard, entry)
                        for entry in predictor_entries
                    )
                )
                fallback_profiles = [
                    profile
                    for profile in single_profiles
                    if profile and profile.get("name", "").strip()
                ]
                if len(fallback_profiles) == expected_count:
                    return fallback_profiles
        return profiles

    async def _get_single_predictor_profile_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        predictor_entry: str,
    ) -> dict[str, str]:
        context = render_prompt_template(
            "llm/single_predictor_profile_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            predictor_entry=predictor_entry,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/single_predictor_profile_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=300,
        )
        if is_llm_unavailable_response(response):
            return {}
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return {}
        name = str(parsed.get("name") or parsed.get("profile") or "").strip().strip("`*_ ")
        explanation = str(
            parsed.get("explanation")
            or parsed.get("reason")
            or parsed.get("description")
            or ""
        ).strip().strip("`*_ ")
        variable_name = str(
            parsed.get("variable_name")
            or parsed.get("variable")
            or parsed.get("predictor")
            or ""
        ).strip().strip("`*_ ")
        statistical_basis = str(
            parsed.get("statistical_basis")
            or parsed.get("basis")
            or parsed.get("statistical_evidence")
            or ""
        ).strip().strip("`*_ ")
        source = str(parsed.get("source") or "sector_prompt").strip().strip("`*_ ")
        if not name:
            return {}
        return {
            "name": name[:120],
            "profile": name[:120],
            "explanation": explanation[:260],
            "variable_name": variable_name[:160],
            "statistical_basis": statistical_basis[:600],
            "source": source[:40] if source else "sector_prompt",
            "metadata": parsed,
        }

    @staticmethod
    def _confirmed_predictor_count(hazard_block: str) -> int | None:
        match = re.search(r"\b(\d+)\s+confirmed predictors?\b", hazard_block, re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _confirmed_predictor_ids(hazard_block: str) -> list[str]:
        return re.findall(r"^PREDICTOR\s+([0-9]+[A-Z]):", hazard_block, re.MULTILINE)

    @staticmethod
    def _confirmed_predictor_entries(hazard_block: str) -> list[str]:
        entry_pattern = re.compile(
            r"(?ms)^PREDICTOR\s+[0-9]+[A-Z]:.*?(?=^PREDICTOR\s+[0-9]+[A-Z]:|^COUNTRY PATTERN|^PREDICTORS NOT CONFIRMED|\Z)"
        )
        return [match.group(0).strip() for match in entry_pattern.finditer(hazard_block)]

    def _expected_hazard_profile_count(self, session: ChatSession, hazard: str) -> int | None:
        _ = session, hazard
        return None



    def _mitigation_record_for_match(
        self, session: ChatSession, match: str
    ) -> UserMitigationMeasure | None:
        records = self._existing_mitigation_records_for_selected_hazard(session)
        if not records:
            return None
        normalized_match = normalize(match)
        for record in records:
            if normalize(record.measure) == normalized_match:
                return record
        best_record = None
        best_score = 0.0
        for record in records:
            score = fuzzy_score(match, record.measure)
            if score > best_score:
                best_record = record
                best_score = score
        return best_record if best_score >= 0.45 else None



    @staticmethod
    def _sector_family(sector: str | None) -> str:
        normalized = normalize_for_match(sector or "")
        if "transport" in normalized or "mobility" in normalized:
            return "transport"
        if "housing" in normalized or "home" in normalized or "building" in normalized:
            return "housing"
        if "energy" in normalized or "electric" in normalized or "power" in normalized:
            return "energy"
        return normalized

    @staticmethod
    def _sector_display_name(sector_family: str) -> str:
        return {
            "energy": "Energy",
            "housing": "Housing",
            "transport": "Transport",
        }.get(sector_family, sector_family.title())
