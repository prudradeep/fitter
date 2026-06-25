from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=16000)
    session_id: str | None = Field(default=None, max_length=64)


class Option(BaseModel):
    id: int
    label: str

    model_config = ConfigDict(from_attributes=True)


class SessionSummary(BaseModel):
    country: str | None = None
    region: str | None = None
    sector: str | None = None
    selected_hazard: str | None = None
    mitigation_measure: str | None = None
    benefited_profiles: list[str] = Field(default_factory=list)
    mitigation_review: dict[str, object] | None = None
    target_population_questions: list[dict[str, object]] = Field(default_factory=list)
    target_population_answers: list[dict[str, object]] = Field(default_factory=list)
    hazard_count: int = 0
    top_hazards: list[dict[str, object]] = Field(default_factory=list)
    affected_profile_count: int = 0
    affected_profiles: list[str] = Field(default_factory=list)
    affected_profile_details: list[dict[str, object]] = Field(default_factory=list)
    mitigation_measure_count: int = 0
    practical_considerations: list[str] = Field(default_factory=list)
    additional_hazards: list[str] = Field(default_factory=list)
    additional_hazard_population: list[dict[str, object]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    step: str
    bot_message: str
    options: list[Option] = Field(default_factory=list)
    other_options: list[str] = Field(default_factory=list)
    session: SessionSummary
    input_mode: str = "text"
    input_values: dict[str, str] = Field(default_factory=dict)
    error: bool = False
    validation_details: dict[str, object] | None = None
