import json
import logging
import re

from sqlalchemy import and_, delete, func, or_, select

from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    CustomHazard,
    CustomHazardProfile,
    EurostatPopulationCache,
    EvaluationQuestion,
    QuestionOption,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
    SystemHazardSocioDemographicTargetPopulation,
    UserHazard,
    UserHazardSocioDemographic,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)
from app.schemas import Option
from app.services.chat_formatters import (
    evidence_is_provided,
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_json import parse_json_object
from app.services.chat_options import best_fuzzy_label, normalize, normalize_for_match
from app.services.chat_session import ChatSession

logger = logging.getLogger("app.services.chat_hazard_creation")


class ChatHazardCatalogMixin:
    def _hazard_options(self, session: ChatSession) -> list[Option]:
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(self._primary_hazard_names(session), start=1)
        ]
        if self._additional_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show hazards added by experts"))
        if self._custom_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show co-created hazards"))
        return options

    def _additional_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._additional_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    def _custom_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._custom_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    @staticmethod
    def _primary_hazard_names(session: ChatSession) -> list[str]:
        additional_keys = {normalize(hazard) for hazard in (session.additional_hazards or [])}
        custom_keys = {normalize(hazard) for hazard in (session.custom_hazards or [])}
        return [
            hazard
            for hazard in hazard_names(session)
            if normalize(hazard) not in additional_keys
            and normalize(hazard) not in custom_keys
        ]

    @classmethod
    def _additional_hazard_options(cls, session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.additional_hazards or [])
            if hazard and cls._stored_hazard_profiles(session, hazard)
        ]

    @classmethod
    def _custom_hazard_options(cls, session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.custom_hazards or [])
            if hazard and cls._stored_hazard_profiles(session, hazard)
        ]

    def _saved_custom_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        shared_rows = self.db.scalars(
            select(CustomHazard)
            .where(
                CustomHazard.country_id == session.country_id,
                CustomHazard.sector_id == session.sector_id,
                CustomHazard.region_scope_key == (session.region_id or ""),
                or_(
                    CustomHazard.created_by_user_id == self.user_id,
                    and_(
                        CustomHazard.validation_mode == "strict",
                        CustomHazard.is_crowd_sourced.is_(True),
                    ),
                ),
            )
            .order_by(CustomHazard.name)
        ).all()
        legacy_rows = self.db.scalars(
            select(UserHazard)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.country_id == session.country_id,
                UserHazard.sector_id == session.sector_id,
                UserHazard.region_id.is_(None)
                if session.region_id is None
                else UserHazard.region_id == session.region_id,
                UserHazard.source == "custom",
                or_(
                    UserSession.user_id == self.user_id,
                    and_(
                        UserHazard.validation_mode == "strict",
                        UserHazard.is_crowd_sourced.is_(True),
                    ),
                ),
            )
            .order_by(UserHazard.name)
        ).all()

        seen: set[str] = set()
        hazards: list[str] = []
        evidence_statuses: dict[str, bool] = {}
        evidence_by_hazard: dict[str, str] = {}
        summaries_by_hazard: dict[str, str] = {}
        system_names = {normalize(hazard) for hazard in (session.hazards or [])}
        for row in [*shared_rows, *legacy_rows]:
            name = str(getattr(row, "name", row) or "").strip()
            key = normalize(name)
            evidence = str(getattr(row, "evidence", None) or "").strip()
            has_evidence = evidence_is_provided(evidence)
            evidence_statuses[key] = evidence_statuses.get(key, False) or has_evidence
            if has_evidence and not evidence_by_hazard.get(key):
                evidence_by_hazard[key] = evidence
            summary = str(getattr(row, "summary", None) or "").strip()
            if summary and not summaries_by_hazard.get(key):
                summaries_by_hazard[key] = summary
            if key in seen or key in system_names:
                continue
            seen.add(key)
            hazards.append(name)
        session.custom_hazard_evidence_statuses = evidence_statuses
        session.custom_hazard_evidence = evidence_by_hazard
        session.custom_hazard_summaries = summaries_by_hazard
        return hazards

    def _additional_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        rows = self.db.execute(
            select(AdditionalHazard, AdditionalHazardProfile)
            .outerjoin(
                AdditionalHazardProfile,
                AdditionalHazardProfile.additional_hazard_id == AdditionalHazard.id,
            )
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(
                AdditionalHazard.csv_row_number,
                AdditionalHazard.name,
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [
            str(profile_row.id)
            for _, profile_row in rows
            if profile_row is not None and profile_row.id is not None
        ]
        target_population_by_profile = (
            self._additional_hazard_profile_target_population_map(profile_ids)
            if profile_ids
            else {}
        )

        existing_names = {
            normalize(hazard)
            for hazard in [
                *(session.hazards or []),
                *(session.custom_hazards or []),
            ]
        }
        seen: set[str] = set()
        hazards: list[str] = []
        profiles_by_hazard: dict[str, list[dict[str, str]]] = {}
        for hazard_row, profile_row in rows:
            hazard = str(hazard_row.name or "").strip()
            key = normalize(hazard)
            if not hazard or key in existing_names:
                continue
            if key not in seen:
                seen.add(key)
                hazards.append(hazard)
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            mapped_targets = target_population_by_profile.get(str(profile_row.id), [])
            profiles_by_hazard.setdefault(hazard, []).append(
                {
                    "name": str(profile_row.profile).strip(),
                    "profile": str(profile_row.profile).strip(),
                    "explanation": str(profile_row.evidence or "").strip(),
                    "statistical_basis": str(profile_row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        str(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        if profiles_by_hazard:
            session.hazard_profiles = {
                **(session.hazard_profiles or {}),
                **profiles_by_hazard,
            }
        return hazards

    def _additional_hazard_profiles_for_custom_hazard(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        hazard_rows = self.db.execute(
            select(AdditionalHazard.id, AdditionalHazard.name)
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(AdditionalHazard.csv_row_number, AdditionalHazard.name)
        ).all()
        matched_names = {
            normalize(name)
            for name in self._local_similar_hazards(
                hazard,
                [str(row.name or "").strip() for row in hazard_rows],
            )
        }
        matched_hazard_ids = [
            str(row.id)
            for row in hazard_rows
            if normalize(str(row.name or "").strip()) in matched_names
        ]
        if not matched_hazard_ids:
            return []

        profile_rows = self.db.scalars(
            select(AdditionalHazardProfile)
            .where(AdditionalHazardProfile.additional_hazard_id.in_(matched_hazard_ids))
            .order_by(
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [str(row.id) for row in profile_rows if row.id is not None]
        target_population_by_profile = self._additional_hazard_profile_target_population_map(
            profile_ids
        )
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in profile_rows:
            name = str(row.profile or "").strip()
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            mapped_targets = target_population_by_profile.get(str(row.id), [])
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.evidence or "").strip(),
                    "statistical_basis": str(row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        str(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        return profiles

    def _is_saved_custom_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.custom_hazards or []))

    def _is_additional_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.additional_hazards or []))

    def _additional_hazard_profile_target_population_map(
        self, profile_ids: list[str]
    ) -> dict[str, list[dict[str, object]]]:
        if not profile_ids:
            return {}
        rows = self.db.execute(
            select(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id
                == AdditionalHazardProfileTargetPopulation.question_option_id,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id.in_(
                    profile_ids
                ),
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                EvaluationQuestion.sort_order,
                QuestionOption.id,
            )
        ).all()
        mapped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            profile_id = str(row.additional_hazard_profile_id)
            mapped.setdefault(profile_id, []).append(
                {
                    "option_id": str(row.id),
                    "label": f"{row.question}: {row.option}",
                }
            )
        return mapped

    def _target_population_answers_for_saved_hazard(
        self, session: ChatSession, hazard: str
    ) -> str:
        if session.country_id is None or session.sector_id is None:
            return ""

        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is not None:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserSession, UserSession.id == UserQuestionResponse.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserQuestionResponse.custom_hazard_id == custom_hazard_id,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        else:
            rows = []
        if not rows:
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

    def _stored_hazard_items_for_context(
        self, session_id: str, session: ChatSession
    ) -> list[dict[str, object]]:
        if session.sector_id is None:
            return []
        try:
            query = (
                select(SystemHazard, SystemHazardSocioDemographic)
                .outerjoin(
                    SystemHazardSocioDemographic,
                    and_(
                        SystemHazardSocioDemographic.system_hazard_id == SystemHazard.id,
                        SystemHazardSocioDemographic.sector_id == session.sector_id,
                    ),
                )
                .where(SystemHazard.sector_id == session.sector_id)
                .order_by(SystemHazard.id, SystemHazardSocioDemographic.id)
            )
            rows = self.db.execute(query).all()
        except Exception:
            logger.exception("Failed to load stored hazards and profiles")
            return []

        items_by_hazard: dict[str, dict[str, object]] = {}
        seen_profiles: dict[str, set[str]] = {}
        for hazard, profile_row in rows:
            item = items_by_hazard.setdefault(
                hazard.id,
                {"hazard": hazard.name, "profiles": []},
            )
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            seen = seen_profiles.setdefault(hazard.id, set())
            profile_name = str(profile_row.profile).strip()
            key = normalize(profile_name)
            if key in seen:
                continue
            seen.add(key)
            item_profiles = item.setdefault("profiles", [])
            if isinstance(item_profiles, list):
                item_profiles.append(
                    {
                        "name": profile_name,
                        "profile": profile_name,
                        "explanation": str(profile_row.explanation or ""),
                        "variable_name": str(profile_row.variable_name or ""),
                        "variable_type": self._profile_variable_type(profile_row.variable_name),
                        "statistical_basis": str(profile_row.statistical_basis or ""),
                        "source": str(profile_row.source or "sector_prompt"),
                    }
                )
        return [
            item
            for item in items_by_hazard.values()
            if self._hazard_item_has_profiles(item)
        ]

    @staticmethod
    def _hazard_item_has_profiles(item: dict[str, object]) -> bool:
        profiles = item.get("profiles")
        if not isinstance(profiles, list):
            return False
        return any(
            (
                isinstance(profile, dict)
                and bool(str(profile.get("name") or profile.get("profile") or "").strip())
            )
            or (isinstance(profile, str) and bool(profile.strip()))
            for profile in profiles
        )

    async def _refresh_hazards_and_profiles_from_llm(
        self,
        session_id: str,
        session: ChatSession,
        *,
        replace_sector_hazards: bool = False,
    ) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_from_llm(session)
        valid_hazard_items = [
            item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
            and normalize(str(item.get("hazard") or "")) != normalize("Analysis not available")
            and self._hazard_item_has_profiles(item)
        ]
        if replace_sector_hazards and not valid_hazard_items:
            logger.warning(
                "Keeping existing hazards because refresh returned no usable sector hazards"
            )
            return self._stored_hazard_items_for_context(session_id, session)
        if replace_sector_hazards:
            self._delete_sector_hazards_and_generated_dgs(session)
        await self._match_system_profiles_to_target_populations(valid_hazard_items)
        self._persist_hazard_items_for_context(
            session_id,
            session,
            valid_hazard_items,
            replace_generated_profiles=True,
        )
        if replace_sector_hazards:
            self._relink_user_system_hazards(session)
        return valid_hazard_items

    async def _match_system_profiles_to_target_populations(
        self, hazard_items: list[dict[str, object]]
    ) -> None:
        option_rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, QuestionOption.id)
        ).all()
        if not option_rows:
            return

        profiles_by_key: dict[str, dict[str, object]] = {}
        for hazard_index, item in enumerate(hazard_items):
            for profile_index, profile in enumerate(item.get("profiles", [])):
                if not isinstance(profile, dict):
                    continue
                key = f"{hazard_index}:{profile_index}"
                profiles_by_key[key] = profile
        if not profiles_by_key:
            return

        for key, profile in profiles_by_key.items():
            profile["target_population_option_ids"] = sorted(
                self._deterministic_target_population_option_ids(profile, option_rows)
            )

    async def backfill_system_profile_target_populations(self) -> int:
        rows = self.db.execute(
            select(SystemHazardSocioDemographic, SystemHazard.name)
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .order_by(SystemHazardSocioDemographic.id)
        ).all()
        matched_rows = 0
        batch_size = 20
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            hazard_items: list[dict[str, object]] = []
            profiles_by_id: dict[str, dict[str, object]] = {}
            for row, hazard_name in batch:
                profile = {
                    "name": str(row.profile or ""),
                    "profile": str(row.profile or ""),
                    "variable_name": str(row.variable_name or ""),
                    "variable_type": self._profile_variable_type(row.variable_name),
                    "explanation": str(row.explanation or ""),
                    "statistical_basis": str(row.statistical_basis or ""),
                }
                profiles_by_id[str(row.id)] = profile
                hazard_items.append(
                    {"hazard": str(hazard_name or ""), "profiles": [profile]}
                )
            await self._match_system_profiles_to_target_populations(hazard_items)
            for profile_id, profile in profiles_by_id.items():
                option_ids = profile.get("target_population_option_ids")
                self._store_system_target_population_matches(profile_id, option_ids)
                if isinstance(option_ids, list) and option_ids:
                    matched_rows += 1
        return matched_rows

    @staticmethod
    def _deterministic_target_population_option_ids(
        profile: dict[str, object], option_rows: list[object]
    ) -> set[str]:
        identity_text = normalize_for_match(
            " ".join(
                str(profile.get(field) or "")
                for field in ("name", "profile", "variable_name")
            )
        )

        explanation_text = normalize_for_match(str(profile.get("explanation") or ""))
        statistical_text = normalize_for_match(str(profile.get("statistical_basis") or ""))
        profile_text = " ".join(
            value for value in (identity_text, explanation_text, statistical_text) if value
        )
        padded_profile = f" {profile_text} "
        padded_explanation = f" {explanation_text} "
        matched: set[str] = set()
        for row in option_rows:
            question = normalize_for_match(str(row.question))
            option = normalize_for_match(str(row.option))
            if not option or option in {"yes", "no", "other"}:
                continue
            if f" {option} " in padded_profile:
                matched.add(str(row.id))
                continue
            aliases = {
                ("gender", "woman"): ("women", "female"),
                ("gender", "male"): (" men ", " man "),
                ("age range", "65"): ("older", "older people", "older adults", "elderly", "senior"),
                ("level of income", "low income"): (
                    "poor households",
                    "income poor",
                    "utility arrears",
                    "utility bill arrears",
                    "energy arrears",
                    "households with utility arrears",
                ),
                ("tenancy status", "tenant"): ("renters", "rented housing"),
                ("tenancy status", "homeowner"): ("homeowners", "owner occupier"),
            }.get((question, option), ())
            if any(
                f" {normalize_for_match(alias)} " in padded_profile
                for alias in aliases
            ):
                matched.add(str(row.id))

        age_match = re.search(r"\bage(?: group)?\s+(\d{1,2})\s*(\+|and over)?", profile_text)
        if age_match:
            minimum_age = int(age_match.group(1))
            if minimum_age >= 65:
                allowed_age_options = {"65"}
            elif minimum_age >= 35:
                allowed_age_options = {"35 65", "65"}
            elif minimum_age >= 25:
                allowed_age_options = {"25 35", "35 65", "65"}
            else:
                allowed_age_options = {"18", "25 35", "35 65", "65"}
            for row in option_rows:
                if normalize_for_match(str(row.question)) != "age range":
                    continue
                option = normalize_for_match(str(row.option))
                if option in allowed_age_options:
                    matched.add(str(row.id))

        yes_question_markers = {
            "living in a house with low energy efficiency": (
                "low energy efficiency", "energy inefficient", "ber rating e g", "cold home"
            ),
            "need of a car to perform daily activities": (
                "car dependent", "need a car", "car reliance"
            ),
            "care responsibility as the main activity": (
                "care responsibility", "carer", "caregiver"
            ),
            "eu citizenship": ("eu citizen", "eu citizenship"),
            "disability of long term condition": (
                "disability", "disabled", "long term condition"
            ),
        }
        for row in option_rows:
            if normalize_for_match(str(row.option)) != "yes":
                continue
            markers = yes_question_markers.get(normalize_for_match(str(row.question)), ())
            if any(f" {normalize_for_match(marker)} " in padded_profile for marker in markers):
                matched.add(str(row.id))

        explanation_markers = {
            ("living in a house with low energy efficiency", "yes"): (
                "inability to keep home warm",
                "cannot keep home warm",
                "cold home",
                "home quality problem",
                "damp draughts mould",
                "pre 1945 housing",
            ),
            ("level of income", "low income"): (
                "struggling to pay bills",
                "utility bill arrears",
                "utility arrears",
                "cannot afford",
                "unable to afford",
                "often need help support",
            ),
            ("level of income", "high income"): (
                "higher income respondents",
                "high income respondents",
            ),
            ("tenancy status", "homeowner"): (
                "owns outright",
                "owner occupier",
            ),
            ("tenancy status", "tenant"): (
                "rented home",
                "rented housing",
                "private renter",
                "social renter",
            ),
            ("level of education", "further normal education"): (
                "further education or training after school",
            ),
        }
        for row in option_rows:
            key = (
                normalize_for_match(str(row.question)),
                normalize_for_match(str(row.option)),
            )
            if (
                key == ("level of income", "low income")
                and any(
                    marker in f" {identity_text} "
                    for marker in (" higher income ", " high income ")
                )
            ):
                continue
            markers = explanation_markers.get(key, ())
            if any(
                f" {normalize_for_match(marker)} " in padded_explanation
                for marker in markers
            ):
                matched.add(str(row.id))
        return matched

    def _historical_evaluation_series(
        self, session: ChatSession, limit: int = 4
    ) -> list[dict[str, object]]:
        if self.user_id is None or not session.evaluation_answers or session.sector_id is None:
            return []
        question_ids = [
            str(answer["question_id"])
            for answer in session.evaluation_answers
            if answer.get("question_id") is not None
        ]
        if not question_ids:
            return []
        query = (
            select(
                UserMitigationMeasure.id,
                UserMitigationMeasure.measure,
                EvaluationQuestion.id.label("question_id"),
                UserQuestionResponse.score,
                UserMitigationMeasure.created_at,
            )
            .join(
                UserQuestionResponse,
                UserQuestionResponse.mitigation_measure_id == UserMitigationMeasure.id,
            )
            .join(
                EvaluationQuestion,
                EvaluationQuestion.id == UserQuestionResponse.question_id,
            )
            .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.user_id == self.user_id,
                UserSession.sector_id == session.sector_id,
                UserSession.region_id.is_(None)
                if session.region_id is None
                else UserSession.region_id == session.region_id,
                EvaluationQuestion.id.in_(question_ids),
                UserQuestionResponse.score.is_not(None),
            )
            .order_by(UserMitigationMeasure.id.desc(), UserQuestionResponse.id)
        )
        if session.mitigation_record_id is not None:
            query = query.where(UserMitigationMeasure.id != session.mitigation_record_id)
        rows = self.db.execute(query).all()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            mitigation_id = str(row.id)
            if mitigation_id not in grouped and len(grouped) >= limit:
                continue
            group = grouped.setdefault(
                mitigation_id,
                {
                    "id": mitigation_id,
                    "measure": str(row.measure or "Prior mitigation"),
                    "scores": {},
                },
            )
            scores = group["scores"]
            if isinstance(scores, dict):
                scores[str(row.question_id)] = int(row.score)

        series: list[dict[str, object]] = []
        for group in grouped.values():
            scores = group.get("scores")
            if not isinstance(scores, dict) or not scores:
                continue
            measure = normalize_markdown_text(str(group.get("measure") or "Prior mitigation"))
            series.append(
                {
                    "name": f"#{group['id']} — {measure[:64]}",
                    "values": [scores.get(question_id) for question_id in question_ids],
                    "current": False,
                }
            )
        return series

    def _persist_hazard_items_for_context(
        self,
        session_id: str,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        *,
        replace_generated_profiles: bool = False,
    ) -> None:
        for item in hazard_items:
            hazard = str(item.get("hazard") or "").strip()
            if not hazard:
                continue
            system_hazard = self._ensure_system_hazard(session, hazard)
            if system_hazard is None:
                continue
            self._delete_user_linked_system_generated_socio_demographics(session, hazard)
            if replace_generated_profiles:
                self._delete_generated_system_socio_demographics(system_hazard.id, session)
            for profile in item.get("profiles", []):
                if isinstance(profile, dict):
                    profile_data = profile
                else:
                    profile_data = {"name": str(profile or "").strip()}
                profile_name = str(
                    profile_data.get("name") or profile_data.get("profile") or ""
                ).strip()
                if not profile_name:
                    continue
                self._store_system_socio_demographic(
                    session,
                    system_hazard.id,
                    profile_data,
                )
        self._normalize_stored_sdp_variable_names(session)

    def _delete_sector_hazards_and_generated_dgs(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            generated_user_dgs = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                    UserHazardSocioDemographic.source == "llm",
                )
            ).all()
            for row in generated_user_dgs:
                self.db.delete(row)

            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            )
            self.db.execute(
                delete(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear old sector hazards and generated DGs")
            raise

    def _relink_user_system_hazards(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_hazards = {
                normalize(hazard.name): hazard.id
                for hazard in self.db.scalars(
                    select(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
                ).all()
            }
            user_hazards = self.db.scalars(
                select(UserHazard).where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                )
            ).all()
            for hazard in user_hazards:
                hazard.system_hazard_id = system_hazards.get(normalize(hazard.name))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to relink refreshed system hazards")

    def _normalize_stored_sdp_variable_names(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_rows = self.db.scalars(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            user_rows = self.db.scalars(
                select(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            changed = False
            for row in [*system_rows, *user_rows]:
                normalized = self._valid_sdp_variable_name(session, row.variable_name)
                if normalized and normalized != row.variable_name:
                    row.variable_name = normalized
                    changed = True
                if isinstance(row, SystemHazardSocioDemographic):
                    variable_type = self._profile_variable_type(row.variable_name)
                    if row.variable_type != variable_type:
                        row.variable_type = variable_type
                        changed = True
            if changed:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to normalize stored socio-demographic variable names")

    def _delete_generated_system_socio_demographics(
        self, system_hazard_id: str, session: ChatSession
    ) -> None:
        try:
            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear generated system socio-demographic profiles")

    def _delete_user_linked_system_generated_socio_demographics(
        self, session: ChatSession, hazard: str
    ) -> None:
        try:
            rows = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.name == hazard,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazardSocioDemographic.source == "llm",
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            ).all()
            for row in rows:
                self.db.delete(row)
            if rows:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear user-linked generated system socio-demographic rows")

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

    @staticmethod
    def _custom_hazard_name_key(name: str) -> str:
        return normalize_for_match(name)[:255]

    def _custom_hazard_id_for_context(self, session: ChatSession, hazard: str) -> str | None:
        if session.country_id is None or session.sector_id is None or not hazard.strip():
            return None
        if normalize(hazard) == normalize(session.accepted_custom_hazard or ""):
            if session.accepted_custom_hazard_id is not None:
                return session.accepted_custom_hazard_id
        try:
            hazard_id = self.db.scalar(
                select(CustomHazard.id).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or ""),
                    CustomHazard.name_key == self._custom_hazard_name_key(hazard),
                    or_(
                        CustomHazard.created_by_user_id == self.user_id,
                        and_(
                            CustomHazard.validation_mode == "strict",
                            CustomHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            )
            return str(hazard_id) if hazard_id else None
        except Exception:
            logger.exception("Failed to load shared custom hazard id")
            return None

    def _ensure_custom_hazard(
        self,
        session: ChatSession,
        name: str,
        *,
        reason: str | None = None,
        evidence: str | None = None,
        summary: str | None = None,
    ) -> CustomHazard | None:
        if session.country_id is None or session.sector_id is None or not name.strip():
            return None
        name_key = self._custom_hazard_name_key(name)
        if not name_key:
            return None
        try:
            hazard = self.db.scalar(
                select(CustomHazard).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or ""),
                    CustomHazard.name_key == name_key,
                    or_(
                        CustomHazard.created_by_user_id == self.user_id,
                        and_(
                            CustomHazard.validation_mode == "strict",
                            CustomHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            )
            if hazard is None:
                hazard = CustomHazard(
                    country_id=session.country_id,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    region_scope_key=session.region_id or "",
                    name=name.strip(),
                    name_key=name_key,
                    source="user",
                    created_by_user_id=self.user_id,
                )
                self.db.add(hazard)
            hazard.name = name.strip()
            hazard.region_id = session.region_id
            hazard.region_scope_key = session.region_id or ""
            if reason is not None:
                hazard.reason = reason.strip() or None
            if evidence is not None:
                hazard.evidence = evidence.strip() or None
            if summary is not None:
                hazard.summary = summary.strip() or None
            hazard.validation_mode = session.validation_mode if session.validation_mode in {"strict", "easy"} else "strict"
            hazard.is_crowd_sourced = (
                hazard.validation_mode == "strict" and bool(session.crowd_sourcing_enabled)
            )
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard")
            return None

    def _store_custom_hazard_profile(
        self,
        custom_hazard_id: str | None,
        profile: dict[str, object],
    ) -> None:
        if custom_hazard_id is None:
            return
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        profile_key = normalize_for_match(profile_name)[:255]
        if not profile_name or not profile_key:
            return
        try:
            row = self.db.scalar(
                select(CustomHazardProfile).where(
                    CustomHazardProfile.custom_hazard_id == custom_hazard_id,
                    CustomHazardProfile.profile_key == profile_key,
                )
            )
            if row is None:
                row = CustomHazardProfile(
                    custom_hazard_id=custom_hazard_id,
                    profile=profile_name,
                    profile_key=profile_key,
                )
                self.db.add(row)
            row.profile = profile_name
            row.variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip() or None
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "custom_hazard_extraction").strip()[:40] or "custom_hazard_extraction"
            row.metadata_json = self._metadata_to_json(dict(profile))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard profile")

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
            custom_hazard = None
            if source == "custom":
                custom_hazard = self._ensure_custom_hazard(
                    session,
                    name,
                    reason=reason,
                    evidence=evidence,
                )
            hazard = self.db.scalar(
                select(UserHazard).where(
                    UserHazard.user_session_id == user_session.id,
                    UserHazard.name == name,
                )
            )
            if hazard is None:
                hazard = UserHazard(
                    user_session_id=user_session.id,
                    custom_hazard_id=custom_hazard.id if custom_hazard else None,
                    system_hazard_id=system_hazard.id if system_hazard else None,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    name=name,
                    source=source,
                )
                self.db.add(hazard)
            hazard.source = source
            if custom_hazard is not None:
                hazard.custom_hazard_id = custom_hazard.id
            if system_hazard is not None:
                hazard.system_hazard_id = system_hazard.id
            hazard.sector_id = session.sector_id
            hazard.region_id = session.region_id
            if reason is not None:
                hazard.reason = reason
            if evidence is not None:
                hazard.evidence = evidence
            hazard.validation_mode = session.validation_mode if session.validation_mode in {"strict", "easy"} else "strict"
            hazard.is_crowd_sourced = (
                hazard.validation_mode == "strict" and bool(session.crowd_sourcing_enabled)
            )
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist hazard")
            return None

    def _selected_hazard_reference(
        self,
        session_id: str,
        session: ChatSession,
        hazard_name: str | None = None,
    ) -> dict[str, int | None]:
        hazard = (hazard_name or session.selected_hazard or session.accepted_custom_hazard or "").strip()
        reference = {
            "user_session_id": None,
            "user_hazard_id": None,
            "custom_hazard_id": None,
            "system_hazard_id": None,
            "additional_hazard_id": None,
        }
        user_session = self._ensure_user_session(session_id, session)
        if user_session is not None:
            reference["user_session_id"] = user_session.id
        if not hazard:
            return reference
        source = self._selected_user_hazard_source(session, hazard)
        if source == "system":
            system_hazard = self._ensure_system_hazard(session, hazard)
            reference["system_hazard_id"] = system_hazard.id if system_hazard else None
            return reference
        if source == "additional":
            reference["additional_hazard_id"] = self._selected_additional_hazard_id(session, hazard)
            return reference

        custom_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=session.accepted_custom_hazard_evidence,
        )
        if custom_hazard is not None:
            session.accepted_custom_hazard_id = custom_hazard.id
            session.accepted_custom_hazard = hazard
            reference["custom_hazard_id"] = custom_hazard.id
            return reference

        if session.selected_hazard_record_id is not None:
            existing = self.db.get(UserHazard, session.selected_hazard_record_id)
            if existing is not None:
                reference["user_hazard_id"] = existing.id
                return reference
        record = self._ensure_user_hazard(
            session_id,
            session,
            hazard,
            source=source,
        )
        if record is None:
            return reference
        session.selected_hazard_record_id = record.id
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_record_id = record.id
        reference["user_hazard_id"] = record.id
        return reference

    def _selected_user_hazard_source(self, session: ChatSession, hazard: str) -> str:
        if self._is_saved_custom_hazard(session, hazard) or normalize(hazard) == normalize(
            session.accepted_custom_hazard or ""
        ):
            return "custom"
        if self._is_additional_hazard(session, hazard):
            return "additional"
        return "system"

    def _selected_additional_hazard_id(self, session: ChatSession, hazard: str) -> str | None:
        if session.country_id is None or session.sector_id is None:
            return None
        hazard_id = self.db.scalar(
            select(AdditionalHazard.id).where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
                func.lower(AdditionalHazard.name) == hazard.casefold(),
            )
        )
        return str(hazard_id) if hazard_id else None

    def _store_socio_demographic(
        self,
        session: ChatSession,
        profile: str,
        *,
        user_hazard_id: str | None = None,
        custom_hazard_id: str | None = None,
        system_hazard_id: str | None = None,
        additional_hazard_id: str | None = None,
        source: str,
        variable_name: str | None = None,
        explanation: str | None = None,
        statistical_basis: str | None = None,
        metadata: dict[str, object] | None = None,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ) or not profile.strip():
            return
        try:
            user_session = self._ensure_user_session(session.session_key, session)
            if user_session is None:
                return
            clean_profile = profile.strip()
            context_query = (
                select(UserHazardSocioDemographic)
                .where(
                    func.lower(UserHazardSocioDemographic.profile) == clean_profile.casefold(),
                    UserHazardSocioDemographic.user_session_id == user_session.id,
                    UserHazardSocioDemographic.user_hazard_id == user_hazard_id,
                    UserHazardSocioDemographic.custom_hazard_id == custom_hazard_id,
                    UserHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    UserHazardSocioDemographic.additional_hazard_id == additional_hazard_id,
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            row = self.db.scalar(context_query.limit(1))
            if row is None:
                row = UserHazardSocioDemographic(
                    user_session_id=user_session.id,
                    user_hazard_id=user_hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    profile=clean_profile,
                    source=source,
                )
                self.db.add(row)
            row.user_session_id = user_session.id
            row.user_hazard_id = user_hazard_id
            row.custom_hazard_id = custom_hazard_id
            row.system_hazard_id = system_hazard_id
            row.additional_hazard_id = additional_hazard_id
            row.country_id = session.country_id
            row.region_id = session.region_id
            row.sector_id = session.sector_id
            row.source = source
            if variable_name is not None:
                row.variable_name = self._valid_sdp_variable_name(session, variable_name) or None
            if explanation is not None:
                row.explanation = explanation.strip() or None
            if statistical_basis is not None:
                row.statistical_basis = statistical_basis.strip() or None
            if metadata is not None:
                row.metadata_json = self._metadata_to_json(metadata)
            if reason is not None:
                row.reason = reason
            if evidence is not None:
                row.evidence = evidence
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist socio-demographic profile")

    def _store_system_socio_demographic(
        self,
        session: ChatSession,
        system_hazard_id: str,
        profile: dict[str, object],
    ) -> None:
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        explanation = str(profile.get("explanation") or "").strip()
        statistical_basis = str(
            profile.get("statistical_basis") or profile.get("basis") or ""
        ).strip()
        source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard_id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.country_id = None
            row.region_id = None
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = explanation or None
            row.statistical_basis = statistical_basis or None
            row.source = source
            self.db.commit()
            self.db.refresh(row)
            if "target_population_option_ids" in profile:
                self._store_system_target_population_matches(
                    row.id,
                    profile.get("target_population_option_ids"),
                )
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile")

    def _store_system_target_population_matches(
        self, system_profile_id: str, option_ids: object
    ) -> None:
        requested_ids: set[str] = set()
        if isinstance(option_ids, list):
            for option_id in option_ids:
                option_id_text = str(option_id or "").strip()
                if not option_id_text:
                    continue
                requested_ids.add(option_id_text)
        valid_ids = set(
            self.db.scalars(
                select(QuestionOption.id)
                .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
                .where(
                    QuestionOption.id.in_(requested_ids),
                    EvaluationQuestion.active.is_(True),
                    EvaluationQuestion.category == "target_population",
                )
            ).all()
        ) if requested_ids else set()
        existing = self.db.scalars(
            select(SystemHazardSocioDemographicTargetPopulation).where(
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id
                == system_profile_id
            )
        ).all()
        existing_by_option = {row.question_option_id: row for row in existing}
        for option_id, row in existing_by_option.items():
            if option_id not in valid_ids:
                self.db.delete(row)
        for option_id in valid_ids:
            if option_id not in existing_by_option:
                self.db.add(
                    SystemHazardSocioDemographicTargetPopulation(
                        system_hazard_socio_demographic_id=system_profile_id,
                        question_option_id=option_id,
                    )
                )
        self.db.commit()

    def _store_matched_profile_population_references(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
        matched_population_profiles: list[dict[str, object]],
    ) -> None:
        system_profile = self._ensure_system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return
        try:
            valid_cache_ids: set[str] = set()
            for matched_profile in matched_population_profiles:
                cache_id = str(matched_profile.get("eurostat_population_cache_id") or "").strip()
                if not cache_id:
                    continue
                cache_row = self.db.get(EurostatPopulationCache, cache_id)
                if (
                    cache_row is None
                    or cache_row.country_id != session.country_id
                    or cache_row.region_id != session.region_id
                    or cache_row.sector_id != session.sector_id
                    or cache_row.system_hazard_id != system_profile.system_hazard_id
                ):
                    continue
                valid_cache_ids.add(cache_id)
            if not valid_cache_ids:
                return
            existing_rows = self.db.scalars(
                select(SystemHazardSocioDemographicPopulationMatch).where(
                    SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                    == system_profile.id,
                )
            ).all()
            rows_by_cache_id = {
                row.eurostat_population_cache_id: row
                for row in existing_rows
                if row.eurostat_population_cache_id is not None
            }
            for row in existing_rows:
                if (
                    row.eurostat_population_cache_id is not None
                    and row.eurostat_population_cache_id not in valid_cache_ids
                ):
                    row.match_status = 0
            for cache_id in valid_cache_ids:
                row = rows_by_cache_id.get(cache_id)
                if row is None:
                    row = SystemHazardSocioDemographicPopulationMatch(
                        system_hazard_socio_demographic_id=system_profile.id,
                        eurostat_population_cache_id=cache_id,
                    )
                    self.db.add(row)
                row.match_status = 1
                row.attempt_count = 0
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist matched profile population reference")

    def _system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self.db.scalar(
            select(SystemHazard).where(
                SystemHazard.sector_id == session.sector_id,
                SystemHazard.name == hazard,
            )
        )
        if system_hazard is None:
            return None
        return self.db.scalar(
            select(SystemHazardSocioDemographic).where(
                SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
                func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
            )
        )

    def _ensure_system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self._ensure_system_hazard(session, hazard)
        if system_hazard is None:
            return None
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard.id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile row")
            return None

    @staticmethod
    def _metadata_to_json(metadata: dict[str, object]) -> str:
        try:
            return json.dumps(metadata, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _metadata_from_json(value: str | None) -> dict[str, object]:
        if not value:
            return {}
        parsed = parse_json_object(value)
        return parsed if isinstance(parsed, dict) else {}

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
