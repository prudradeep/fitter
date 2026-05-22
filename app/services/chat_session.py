from dataclasses import dataclass, fields
from uuid import uuid4

from app.schemas import SessionSummary


@dataclass
class ChatSession:
    country_id: int | None = None
    country: str | None = None
    region_id: int | None = None
    region: str | None = None
    sector_id: int | None = None
    sector: str | None = None
    phase: str = "wizard"
    hazards: list[str] | None = None
    custom_hazards: list[str] | None = None
    pending_hazard: str | None = None
    selected_hazard: str | None = None
    selected_hazard_record_id: int | None = None
    socio_demographic_findings: str | None = None
    additional_dgs: list[str] | None = None
    stats_conversation: list[dict[str, str]] | None = None
    dg_reason: str | None = None
    dg_evidence: str | None = None
    mitigation_measure: str | None = None
    mitigation_reason: str | None = None
    mitigation_record_id: int | None = None
    evaluation_questions: list[dict[str, str | int]] | None = None
    evaluation_index: int = 0
    evaluation_answers: list[dict[str, str | int | None]] | None = None
    target_population_questions: list[dict[str, object]] | None = None
    target_population_index: int = 0
    target_population_answers: list[dict[str, str | int]] | None = None
    saved_target_population_answers: str | None = None
    accepted_custom_hazard: str | None = None
    accepted_custom_hazard_reason: str | None = None
    accepted_custom_hazard_evidence: str | None = None
    accepted_custom_hazard_record_id: int | None = None
    pending_fuzzy_option: str | None = None

    def summary(self) -> SessionSummary:
        return SessionSummary(country=self.country, region=self.region, sector=self.sector)


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
