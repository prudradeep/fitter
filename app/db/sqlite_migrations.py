"""Versioned SQLite migrations for Dr Transition client/offline databases.

The server keeps its MySQL migrations in ``db/migrations``.  Client/offline
SQLite databases are upgraded here because the MySQL migration SQL contains
constructs that SQLite cannot execute (``information_schema``, ``UUID()``,
``AFTER``, ``ADD INDEX``, etc.).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.db.session import engine

logger = logging.getLogger(__name__)

Migration = tuple[str, Callable[[Connection], None]]


def _table_exists(connection: Connection, table: str) -> bool:
    return table in inspect(connection).get_table_names()


def _columns(connection: Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(column["name"]) for column in inspect(connection).get_columns(table)}


def _indexes(connection: Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(index["name"]) for index in inspect(connection).get_indexes(table) if index.get("name")}


def _add_column(connection: Connection, table: str, column: str, ddl: str) -> None:
    if not _table_exists(connection, table) or column in _columns(connection, table):
        return
    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {ddl}'))


def _create_index(connection: Connection, table: str, index: str, columns: str) -> None:
    if not _table_exists(connection, table) or index in _indexes(connection, table):
        return
    connection.execute(text(f'CREATE INDEX IF NOT EXISTS "{index}" ON "{table}" ({columns})'))


def _001_app_rate_limits(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS app_rate_limits (
          rate_limit_key VARCHAR(255) NOT NULL PRIMARY KEY,
          attempts INTEGER NOT NULL DEFAULT 0,
          window_started_at REAL NOT NULL DEFAULT 0,
          locked_until REAL NOT NULL DEFAULT 0,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))


def _002_auth_session_audit(connection: Connection) -> None:
    _add_column(connection, "app_users", "session_version", "session_version INTEGER NOT NULL DEFAULT 1")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_logs (
          id CHAR(36) PRIMARY KEY,
          user_id CHAR(36) NULL,
          action VARCHAR(120) NOT NULL,
          status VARCHAR(40) NOT NULL DEFAULT 'success',
          target_type VARCHAR(80) NULL,
          target_id VARCHAR(160) NULL,
          request_id VARCHAR(64) NULL,
          ip_address VARCHAR(80) NULL,
          user_agent VARCHAR(255) NULL,
          details TEXT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL
        )
    """))
    for name, cols in (
        ("ix_audit_logs_user_id", "user_id"),
        ("ix_audit_logs_action", "action"),
        ("ix_audit_logs_target_type", "target_type"),
        ("ix_audit_logs_target_id", "target_id"),
        ("ix_audit_logs_request_id", "request_id"),
        ("ix_audit_logs_created_at", "created_at"),
    ):
        _create_index(connection, "audit_logs", name, cols)


def _003_user_mitigation_sync_columns(connection: Connection) -> None:
    for column, ddl in (
        ("user_session_id", "user_session_id CHAR(36) NULL"),
        ("custom_hazard_id", "custom_hazard_id CHAR(36) NULL"),
        ("system_hazard_id", "system_hazard_id CHAR(36) NULL"),
        ("additional_hazard_id", "additional_hazard_id CHAR(36) NULL"),
    ):
        _add_column(connection, "user_mitigation_measures", column, ddl)
    for name, cols in (
        ("ix_user_mitigation_measures_user_session_id", "user_session_id"),
        ("ix_user_mitigation_measures_custom_hazard_id", "custom_hazard_id"),
        ("ix_user_mitigation_measures_system_hazard_id", "system_hazard_id"),
        ("ix_user_mitigation_measures_additional_hazard_id", "additional_hazard_id"),
    ):
        _create_index(connection, "user_mitigation_measures", name, cols)

    # MySQL migration 003 also makes user_hazard_id nullable. SQLite cannot
    # alter column nullability in place. Fresh clients get the current model
    # definition from Base.metadata.create_all(). Existing installations that
    # still have NOT NULL here are reported so packaging/upgrades can migrate
    # them with an application-specific table rebuild if needed.
    if _table_exists(connection, "user_mitigation_measures"):
        info = {str(c["name"]): c for c in inspect(connection).get_columns("user_mitigation_measures")}
        column = info.get("user_hazard_id")
        if column and not bool(column.get("nullable", True)):
            logger.warning(
                "SQLite user_mitigation_measures.user_hazard_id is still NOT NULL; "
                "SQLite requires a table rebuild to relax nullability"
            )


def _004_user_question_response_hazard_columns(connection: Connection) -> None:
    for column, ddl in (
        ("custom_hazard_id", "custom_hazard_id CHAR(36) NULL"),
        ("system_hazard_id", "system_hazard_id CHAR(36) NULL"),
        ("additional_hazard_id", "additional_hazard_id CHAR(36) NULL"),
    ):
        _add_column(connection, "user_question_responses", column, ddl)
    for name, cols in (
        ("ix_user_question_responses_custom_hazard_id", "custom_hazard_id"),
        ("ix_user_question_responses_system_hazard_id", "system_hazard_id"),
        ("ix_user_question_responses_additional_hazard_id", "additional_hazard_id"),
    ):
        _create_index(connection, "user_question_responses", name, cols)


def _005_app_user_encrypted_sync_payload(connection: Connection) -> None:
    _add_column(connection, "app_users", "sync_encrypted_payload", "sync_encrypted_payload TEXT NULL")


def _006_prompts(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS prompts (
          id CHAR(36) PRIMARY KEY,
          prompt_key VARCHAR(255) NOT NULL UNIQUE,
          category VARCHAR(80) NOT NULL DEFAULT 'llm',
          model VARCHAR(120) NULL,
          display_name VARCHAR(255) NOT NULL,
          content TEXT NOT NULL,
          source_path VARCHAR(255) NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    _create_index(connection, "prompts", "ix_prompts_prompt_key", "prompt_key")
    _create_index(connection, "prompts", "ix_prompts_category", "category")
    _create_index(connection, "prompts", "ix_prompts_model", "model")


def _007_user_mitigation_system_inquiry(connection: Connection) -> None:
    _add_column(connection, "user_mitigation_measures", "system_inquiry_json", "system_inquiry_json TEXT NULL")


def _008_system_inquiry_telemetry_events(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS system_inquiry_telemetry_events (
          id CHAR(36) PRIMARY KEY,
          event_key VARCHAR(64) NOT NULL UNIQUE,
          payload_json TEXT NOT NULL,
          status VARCHAR(20) NOT NULL DEFAULT 'queued',
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          synced_at DATETIME NULL
        )
    """))
    _create_index(connection, "system_inquiry_telemetry_events", "ix_system_inquiry_telemetry_event_key", "event_key")
    _create_index(connection, "system_inquiry_telemetry_events", "ix_system_inquiry_telemetry_status", "status")


MIGRATIONS: tuple[Migration, ...] = (
    ("001_app_rate_limits", _001_app_rate_limits),
    ("002_auth_session_audit", _002_auth_session_audit),
    ("003_user_mitigation_sync_columns", _003_user_mitigation_sync_columns),
    ("004_user_question_response_hazard_columns", _004_user_question_response_hazard_columns),
    ("005_app_user_encrypted_sync_payload", _005_app_user_encrypted_sync_payload),
    ("006_prompts", _006_prompts),
    ("007_user_mitigation_system_inquiry", _007_user_mitigation_system_inquiry),
    ("008_system_inquiry_telemetry_events", _008_system_inquiry_telemetry_events),
)


def apply_sqlite_migrations() -> list[str]:
    """Apply pending SQLite client/offline migrations and record versions."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("apply_sqlite_migrations() is only valid for SQLite databases")

    applied: list[str] = []
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version VARCHAR(120) PRIMARY KEY,
              applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        seen = {str(row[0]) for row in connection.execute(text("SELECT version FROM schema_migrations")).all()}
        for version, migration in MIGRATIONS:
            if version in seen:
                continue
            migration(connection)
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
            applied.append(version)
    if applied:
        logger.info("Applied SQLite migrations: %s", ", ".join(applied))
    return applied
