import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)

DEFAULT_PRODUCTION_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src-elem 'self'; "
    "style-src-attr 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "upgrade-insecure-requests"
)


def _env_files() -> tuple[Path, ...]:
    explicit_env_file = os.getenv("ENV_FILE")
    if explicit_env_file:
        return (Path(explicit_env_file),)
    if getattr(sys, "frozen", False):
        base = os.getenv("PROGRAMDATA")
        return (Path(base) / "DrTransition" / ".env",) if base else tuple()
    return (Path.cwd() / ".env",)


def _frozen_program_data_path(relative_path: str) -> str:
    if not getattr(sys, "frozen", False):
        return relative_path
    path = Path(relative_path)
    if path.is_absolute():
        return relative_path
    base = os.getenv("PROGRAMDATA")
    if not base:
        return relative_path
    return str(Path(base) / "DrTransition" / path)


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
    ollama_model: str = "qwen3.5:4b"
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
    prompt_source: str = "auto"

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
    system_inquiry_profile_retention_days: int = 365
    sync_enabled: bool = False
    sync_mode: str = "client"
    sync_server_url: str = ""
    sync_api_token: str = ""
    sync_device_id: str = ""
    sync_batch_size: int = 500
    sync_include_logs: bool = False
    sync_server_expose_app_apis: bool = False
    sync_auto_on_startup: bool = True
    sync_interval_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=_env_files(), env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_safe_runtime_defaults(self) -> "Settings":
        self.prompt_source = self.prompt_source.strip().casefold()
        if self.prompt_source not in {"auto", "db", "file"}:
            raise ValueError("PROMPT_SOURCE must be one of: auto, db, file")
        self.faiss_index_path = _frozen_program_data_path(self.faiss_index_path)
        self.llm_log_path = _frozen_program_data_path(self.llm_log_path)
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
