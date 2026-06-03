from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
