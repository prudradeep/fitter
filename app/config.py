import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[Path, ...]:
    candidates = [Path(".env")]
    for env_name in ("PROGRAMDATA", "LOCALAPPDATA"):
        base = os.getenv(env_name)
        if base:
            candidates.append(Path(base) / "DrTransition" / ".env")
    return tuple(candidates)


class Settings(BaseSettings):
    app_name: str = "Dr Transition"
    app_env: str = "development"
    app_debug: bool = False
    secret_key: str = Field(default="development-only-secret")

    database_url: str = "mysql+pymysql://dr_transition:dr_transition_password@localhost:3306/dr_transition"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral-nemo"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_timeout_seconds: int = 1200

    faiss_index_path: str = "data/knowledge.faiss"
    reranker_url: str = ""
    reranker_timeout_seconds: int = 60
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    nli_url: str = ""
    nli_timeout_seconds: int = 60
    nli_model: str = "cross-encoder/nli-deberta-v3-small"
    mitigation_verdict_samples: int = 3
    mitigation_contradiction_resamples: int = 2
    mitigation_contradiction_confirmation_fraction: float = 0.4
    mitigation_verdict_temperature: float = 0.25
    mitigation_support_score_floor: float = 0.15
    eurostat_base_url: str = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
    eurostat_timeout_seconds: int = 20
    eurostat_cache_expiry_months: int = 3

    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=_env_files(), env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
