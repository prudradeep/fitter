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


class ChatResponse(BaseModel):
    session_id: str
    step: str
    bot_message: str
    options: list[Option] = Field(default_factory=list)
    session: SessionSummary
    input_mode: str = "text"
    error: bool = False
