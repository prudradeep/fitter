import re
from dataclasses import dataclass, fields
from uuid import uuid4

from app.schemas import SessionSummary


@dataclass
class ChatSession:
    session_key: str | None = None
    country_id: int | None = None
    country: str | None = None
    region_id: int | None = None
    region: str | None = None
    sector_id: int | None = None
    sector: str | None = None
    phase: str = "wizard"
    hazards: list[str] | None = None
    hazard_profiles: dict[str, list[dict[str, str] | str] | str] | None = None
    hazard_rankings: dict[str, dict[str, object]] | None = None
    custom_hazards: list[str] | None = None
    pending_hazard: str | None = None
    selected_hazard: str | None = None
    selected_hazard_record_id: int | None = None
    socio_demographic_findings: str | None = None
    socio_demographic_profiles: list[str] | None = None
    additional_dgs: list[str] | None = None
    pending_additional_dgs: list[str] | None = None
    additional_dg_answers: list[dict[str, str | int]] | None = None
    stats_conversation: list[dict[str, str]] | None = None
    dg_reason: str | None = None
    dg_evidence: str | None = None
    pending_mitigation_measure: str | None = None
    pending_mitigation_reason: str | None = None
    pending_mitigation_evidence: str | None = None
    pending_mitigation_clarity_dimension: str | None = None
    mitigation_clarity_turns: int = 0
    mitigation_clarification_history: list[dict[str, str]] | None = None
    mitigation_frozen_inputs: dict[str, str] | None = None
    suggested_mitigation_measure_id: int | None = None
    suggested_mitigation_measure_name: str | None = None
    mitigation_measure: str | None = None
    mitigation_reason: str | None = None
    mitigation_target_population: list[str] | None = None
    mitigation_record_id: int | None = None
    mitigation_validation: dict[str, object] | None = None
    mitigation_grounded_synthesis: str | None = None
    evaluation_questions: list[dict[str, str | int]] | None = None
    evaluation_index: int = 0
    evaluation_answers: list[dict[str, str | int | None]] | None = None
    target_population_questions: list[dict[str, object]] | None = None
    target_population_index: int = 0
    target_population_answers: list[dict[str, object]] | None = None
    saved_target_population_answers: str | None = None
    accepted_custom_hazard: str | None = None
    accepted_custom_hazard_reason: str | None = None
    accepted_custom_hazard_evidence: str | None = None
    accepted_custom_hazard_record_id: int | None = None
    pending_fuzzy_option: str | None = None
    stats_dialog_conversation: list[dict[str, str]] | None = None

    def summary(self) -> SessionSummary:
        system_hazard_count = len(
            [hazard for hazard in (self.hazards or []) if self._hazard_has_profiles(hazard)]
        )
        regional_hazard_count = len(
            [
                hazard
                for hazard in (self.custom_hazards or [])
                if self._hazard_has_profiles(hazard)
            ]
        )
        affected_profile_details = self._affected_profile_details()
        seen_profiles: set[str] = set()
        deduped_affected_profiles: list[str] = []
        deduped_affected_profile_details: list[dict[str, object]] = []
        for item in affected_profile_details:
            profile = str(item.get("name") or "").strip()
            key = profile.casefold()
            if key and key not in seen_profiles:
                seen_profiles.add(key)
                deduped_affected_profiles.append(profile)
                deduped_affected_profile_details.append(item)
        benefited_profiles = self._target_population_profiles(self.target_population_answers or [])
        return SessionSummary(
            country=self.country,
            region=self.region,
            sector=self.sector,
            selected_hazard=self.selected_hazard,
            mitigation_measure=self.mitigation_measure,
            benefited_profiles=benefited_profiles,
            mitigation_review=self._mitigation_review_summary(self.mitigation_validation),
            target_population_questions=self.target_population_questions or [],
            target_population_answers=[dict(answer) for answer in (self.target_population_answers or [])],
            hazard_count=system_hazard_count + regional_hazard_count,
            top_hazards=self._top_hazard_population_summary(),
            affected_profile_count=self.eligible_hazard_profile_count(),
            affected_profiles=deduped_affected_profiles,
            affected_profile_details=deduped_affected_profile_details,
            mitigation_measure_count=1 if self.mitigation_measure else 0,
        )

    def eligible_hazard_profile_count(self) -> int:
        unique_profiles: set[str] = set()
        stored_profiles = self.hazard_profiles or {}
        for hazard in self.hazards or []:
            profiles = stored_profiles.get(hazard)
            if profiles is None:
                hazard_key = hazard.casefold()
                profiles = next(
                    (
                        value
                        for stored_hazard, value in stored_profiles.items()
                        if str(stored_hazard).casefold() == hazard_key
                    ),
                    [],
                )
            profile_values = [profiles] if isinstance(profiles, str) else list(profiles or [])
            for profile in profile_values:
                if isinstance(profile, dict):
                    name = str(profile.get("name") or profile.get("profile") or "").strip()
                else:
                    name = str(profile or "").strip()
                if name:
                    unique_profiles.add(name.casefold())
        return len(unique_profiles)

    def _hazard_has_profiles(self, hazard: str) -> bool:
        stored_profiles = self.hazard_profiles or {}
        values = stored_profiles.get(hazard)
        if values is None:
            key = str(hazard or "").strip().casefold()
            values = next(
                (
                    stored_values
                    for stored_hazard, stored_values in stored_profiles.items()
                    if str(stored_hazard or "").strip().casefold() == key
                ),
                None,
            )
        items = [values] if isinstance(values, str) else list(values or [])
        return any(
            (
                isinstance(item, dict)
                and bool(str(item.get("name") or item.get("profile") or "").strip())
            )
            or (isinstance(item, str) and bool(item.strip()))
            for item in items
        )

    def _affected_profile_details(self) -> list[dict[str, object]]:
        details: list[dict[str, object]] = []
        if self.selected_hazard:
            stored_profiles = self.hazard_profiles or {}
            values = stored_profiles.get(self.selected_hazard)
            if values is None:
                selected_key = self.selected_hazard.strip().casefold()
                values = next(
                    (
                        stored_values
                        for stored_hazard, stored_values in stored_profiles.items()
                        if str(stored_hazard or "").strip().casefold() == selected_key
                    ),
                    None,
                )
            for profile in [values] if isinstance(values, str) else list(values or []):
                if isinstance(profile, dict):
                    name = str(profile.get("name") or profile.get("profile") or "").strip()
                    variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip()
                    variable_type = str(profile.get("variable_type") or "").strip()
                else:
                    name = str(profile or "").strip()
                    variable_name = ""
                    variable_type = ""
                if name:
                    details.append(
                        {
                            "name": name,
                            "variable_name": variable_name,
                            "variable_type": self._profile_variable_type(variable_name, variable_type),
                        }
                    )
        if not details:
            if self.socio_demographic_profiles:
                details.extend({"name": profile, "variable_name": "", "variable_type": "individual"} for profile in self.socio_demographic_profiles)
            elif self.socio_demographic_findings:
                details.extend(
                    {"name": profile, "variable_name": "", "variable_type": "individual"}
                    for profile in self._profile_lines(self.socio_demographic_findings)
                )
        details.extend(
            {"name": profile, "variable_name": "", "variable_type": "individual"}
            for profile in self.additional_dgs or []
        )
        return details

    @staticmethod
    def _profile_variable_type(variable_name: str, variable_type: str = "") -> str:
        if variable_type.strip().casefold() == "macro":
            return "macro"
        if variable_name.strip().casefold().startswith("macro_"):
            return "macro"
        return "individual"

    def _top_hazard_population_summary(self) -> list[dict[str, object]]:
        rankings = self.hazard_rankings or {}
        rows: list[dict[str, object]] = []
        profiled_hazards = [
            hazard for hazard in (self.hazards or []) if self._hazard_has_profiles(hazard)
        ]
        for hazard in profiled_hazards[:3]:
            ranking = rankings.get(hazard)
            profiles = ranking.get("profiles", []) if isinstance(ranking, dict) else []
            regional_values: list[float] = []
            national_values: list[float] = []
            for profile in profiles if isinstance(profiles, list) else []:
                if not isinstance(profile, dict):
                    continue
                try:
                    regional_values.append(float(profile["population_pct"]))
                except (KeyError, TypeError, ValueError):
                    pass
                try:
                    national_values.append(float(profile["national_population_pct"]))
                except (KeyError, TypeError, ValueError):
                    pass
            rows.append(
                {
                    "hazard": hazard,
                    "regional_population_pct": (
                        ranking.get("regional_population_pct")
                        if isinstance(ranking, dict)
                        and ranking.get("regional_population_pct") is not None
                        else self._average_percentage(regional_values)
                    ),
                    "national_population_pct": (
                        ranking.get("national_population_pct")
                        if isinstance(ranking, dict)
                        and ranking.get("national_population_pct") is not None
                        else self._average_percentage(national_values)
                    ),
                }
            )
        return rows

    @staticmethod
    def _average_percentage(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    @staticmethod
    def _profile_lines(markdown_text: str) -> list[str]:
        profiles: list[str] = []
        for line in markdown_text.splitlines():
            match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
            if match:
                profiles.append(match.group(1).strip())
        return profiles or ([markdown_text.strip()] if markdown_text.strip() else [])

    @staticmethod
    def _target_population_profiles(answers: list[dict[str, object]]) -> list[str]:
        profiles: list[str] = []
        seen: set[str] = set()
        for answer in answers:
            selected = answer.get("selected")
            values = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for profile in values:
                label = str(profile).strip()
                key = label.casefold()
                if label and key not in seen:
                    seen.add(key)
                    profiles.append(label)
        return profiles

    @staticmethod
    def _mitigation_review_summary(validation: dict[str, object] | None) -> dict[str, object] | None:
        if not isinstance(validation, dict):
            return None
        dimensions = validation.get("dimensions")
        supported_dimensions: list[dict[str, str]] = []
        if isinstance(dimensions, dict):
            for name, value in dimensions.items():
                if not isinstance(value, dict):
                    continue
                status = str(value.get("status") or "").strip()
                if status.casefold() != "supported":
                    continue
                supported_dimensions.append(
                    {
                        "name": str(name or "").strip(),
                        "explanation": str(value.get("explanation") or "").strip(),
                    }
                )
        return {
            "confidence_score": validation.get("confidence_score"),
            "grounding_status": validation.get("outcome"),
            "supported_dimensions": supported_dimensions,
            "verdict_stability": validation.get("verdict_stability"),
            "support_corpus": validation.get("support_label"),
            "explanation": str(validation.get("reason") or "").strip(),
        }


class ChatSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, ChatSession]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        if session_id:
            session = ChatSession()
            self._sessions[session_id] = session
            return session_id, session
        new_session_id = str(uuid4())
        session = ChatSession()
        self._sessions[new_session_id] = session
        return new_session_id, session

    def reset(self, session_id: str | None = None) -> tuple[str, ChatSession]:
        if session_id:
            self._sessions.pop(session_id, None)
        new_session_id = str(uuid4())
        session = ChatSession()
        self._sessions[new_session_id] = session
        return new_session_id, session

    def put(self, session_id: str, data: dict[str, object]) -> ChatSession:
        field_names = {field.name for field in fields(ChatSession)}
        session = ChatSession(**{key: value for key, value in data.items() if key in field_names})
        self._sessions[session_id] = session
        return session


session_store = ChatSessionStore()
