import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net https://code.highcharts.com 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://code.highcharts.com; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)

DEFAULT_PRODUCTION_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net https://code.highcharts.com; "
    "style-src-elem 'self'; "
    "style-src-attr 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://code.highcharts.com; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "upgrade-insecure-requests"
)


def _env_files() -> tuple[Path, ...]:
    if getattr(sys, "frozen", False):
        base = os.getenv("PROGRAMDATA")
        return (Path(base) / "DrTransition" / ".env",) if base else tuple()
    return (Path.cwd() / ".env",)


class Settings(BaseSettings):
    app_name: str = "Dr Transition"
    app_env: str = "development"
    app_debug: bool = False
    secret_key: str = Field(default="development-only-secret")
    auth_cookie_secure: bool | None = None
    auth_cookie_max_age_seconds: int = 60 * 60 * 24 * 14
    csrf_protection_enabled: bool | None = None
    database_auto_migrate: bool = False

    database_url: str = "mysql+pymysql://dr_transition:dr_transition_password@localhost:3306/dr_transition"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 3600
    database_connect_timeout_seconds: int = 10

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral-nemo"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_timeout_seconds: int = 1200
    llm_log_enabled: bool | None = None
    llm_log_to_file: bool | None = None
    llm_log_to_db: bool | None = None
    llm_log_include_payloads: bool | None = None
    llm_log_allow_production_payloads: bool = False
    llm_log_path: str = "data/service-runtime/logs/llm_requests.jsonl"
    llm_log_max_payload_chars: int = 120_000
    llm_log_max_text_chars: int = 8_000

    faiss_index_path: str = "data/knowledge.faiss"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_session_import_bytes: int = 10 * 1024 * 1024
    max_url_ingest_bytes: int = 10 * 1024 * 1024
    max_json_bytes: int = 1 * 1024 * 1024
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
    structured_logs: bool = False
    request_id_header: str = "X-Request-ID"
    access_log_enabled: bool = True
    access_log_suppressed_paths: str = "/health,/health/live,/health/ready"
    access_log_sample_rate: float = 1.0
    content_security_policy: str = DEFAULT_DEVELOPMENT_CSP
    referrer_policy: str = "strict-origin-when-cross-origin"
    permissions_policy: str = "camera=(), microphone=(self), geolocation=(), payment=(), usb=()"
    strict_transport_security: str = "max-age=31536000; includeSubDomains"
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 15 * 60
    login_rate_limit_lockout_seconds: int = 15 * 60
    signup_rate_limit_attempts: int = 5
    signup_rate_limit_window_seconds: int = 60 * 60
    signup_rate_limit_lockout_seconds: int = 60 * 60
    password_rate_limit_attempts: int = 5
    password_rate_limit_window_seconds: int = 15 * 60
    password_rate_limit_lockout_seconds: int = 15 * 60
    rate_limit_retention_days: int = 7
    temporary_knowledge_retention_hours: int = 24
    llm_log_retention_days: int = 30

    model_config = SettingsConfigDict(env_file=_env_files(), env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_safe_runtime_defaults(self) -> "Settings":
        if not self.is_development:
            unsafe_secrets = {"", "development-only-secret", "change-this-secret-key-before-production"}
            if self.secret_key.strip() in unsafe_secrets:
                raise ValueError("SECRET_KEY must be set to a strong unique value outside development.")
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false outside development.")
            if self.database_auto_migrate:
                raise ValueError(
                    "DATABASE_AUTO_MIGRATE must be false outside development; "
                    "run migrations explicitly before deployment."
                )
            unsafe_database_markers = {"dr_transition_password", "drtransition_password"}
            if any(marker in self.database_url for marker in unsafe_database_markers):
                raise ValueError("DATABASE_URL must not use a sample local-only database password.")
            if self.llm_log_include_payloads and not self.llm_log_allow_production_payloads:
                raise ValueError(
                    "LLM_LOG_INCLUDE_PAYLOADS requires LLM_LOG_ALLOW_PRODUCTION_PAYLOADS=true "
                    "outside development."
                )
            if self.content_security_policy == DEFAULT_DEVELOPMENT_CSP:
                self.content_security_policy = DEFAULT_PRODUCTION_CSP
        return self

    @property
    def is_development(self) -> bool:
        return self.app_env.strip().casefold() in {"", "dev", "development", "local", "test"}

    @property
    def use_secure_auth_cookie(self) -> bool:
        return (not self.is_development) if self.auth_cookie_secure is None else self.auth_cookie_secure

    @property
    def use_csrf_protection(self) -> bool:
        return (not self.is_development) if self.csrf_protection_enabled is None else self.csrf_protection_enabled

    @property
    def include_llm_log_payloads(self) -> bool:
        return self.is_development if self.llm_log_include_payloads is None else self.llm_log_include_payloads

    @property
    def use_llm_logging(self) -> bool:
        return self.is_development if self.llm_log_enabled is None else self.llm_log_enabled

    @property
    def write_llm_log_to_file(self) -> bool:
        return self.is_development if self.llm_log_to_file is None else self.llm_log_to_file

    @property
    def write_llm_log_to_db(self) -> bool:
        return self.is_development if self.llm_log_to_db is None else self.llm_log_to_db

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def access_log_suppressed_path_set(self) -> set[str]:
        return {
            path.strip()
            for path in self.access_log_suppressed_paths.split(",")
            if path.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
