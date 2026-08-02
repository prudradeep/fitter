import logging
import re

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from pathlib import Path

from app.config import get_settings
from app.db.reference_schema import ensure_reference_data_schema
from app.db.schema_type_helpers import mysql_question_option_id_type
from app.db.session import Base, engine  # noqa: F401
from app.db.versioned_migrations import apply_versioned_migrations
from app.seed.reference_data import (
    ensure_additional_hazards,
    ensure_mitigation_measure_examples,
    ensure_system_hazards_from_sector_prompts,
    _ensure_hazards_xlsx_policy_system_hazards,
)


logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"


def default_seed_user_role() -> str:
    mode = str(get_settings().sync_mode or "").strip().casefold()
    return "admin" if mode == "server" else "user"


def run_schema_sql(*, include_basic_data: bool | None = None) -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    if include_basic_data is None:
        include_basic_data = should_apply_schema_basic_data()
    statements = split_sql_statements(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        with engine.begin() as connection:
            for statement in statements:
                normalized = statement.lstrip().casefold()
                if normalized.startswith(("create database", "use ")):
                    continue
                if is_schema_data_statement(statement) and not include_basic_data:
                    continue
                statement = _adapt_question_option_fk_type(connection, statement)
                connection.execute(text(statement))
        logger.info("Database schema.sql applied")
    except SQLAlchemyError:
        logger.exception("Applying schema.sql failed")
        raise


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    for char in sql:
        current.append(char)

        if quote:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"', "`"}:
            quote = char
            continue

        if char == ";":
            statement = "".join(current).strip().rstrip(";").strip()
            current = []
            if statement:
                statements.append(statement)

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)

    return [statement for statement in statements if not statement.startswith("--")]


def should_apply_schema_basic_data() -> bool:
    return str(get_settings().sync_mode or "").strip().casefold() == "server"


def is_schema_data_statement(statement: str) -> bool:
    normalized = statement.lstrip().casefold()
    return normalized.startswith(("insert ", "replace "))


def _adapt_question_option_fk_type(connection, statement: str) -> str:
    normalized = statement.casefold()
    if (
        "references question_options(id)" not in normalized
        or "question_option_id int" not in normalized
    ):
        return statement

    question_option_id_type = mysql_question_option_id_type(connection)
    return re.sub(
        r"\bquestion_option_id\s+INT\b",
        f"question_option_id {question_option_id_type}",
        statement,
        flags=re.IGNORECASE,
    )

def ensure_runtime_schema(*, seed_reference_data: bool = False) -> None:
    try:
        with engine.begin() as connection:
            question_option_id_type = mysql_question_option_id_type(connection)
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS app_users (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      email VARCHAR(255) NOT NULL UNIQUE,
                      name VARCHAR(160) NOT NULL,
                      password_hash VARCHAR(255) NOT NULL,
                      session_version INT NOT NULL DEFAULT 1,
                      designation VARCHAR(160) NOT NULL,
                      organisation_type VARCHAR(160) NOT NULL,
                      organisation_name VARCHAR(220) NOT NULL,
                      role VARCHAR(40) NOT NULL DEFAULT 'user',
                      sync_encrypted_payload TEXT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      INDEX ix_app_users_email (email)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            inspector = inspect(engine)
            if "app_users" in inspector.get_table_names():
                user_columns = {
                    column["name"] for column in inspector.get_columns("app_users")
                }
                if "role" not in user_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE app_users ADD COLUMN role "
                            "VARCHAR(40) NOT NULL DEFAULT 'user' AFTER organisation_name"
                        )
                    )
                if "session_version" not in user_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE app_users ADD COLUMN session_version "
                            "INT NOT NULL DEFAULT 1 AFTER password_hash"
                        )
                    )
                if "sync_encrypted_payload" not in user_columns:
                    connection.execute(
                        text("ALTER TABLE app_users ADD COLUMN sync_encrypted_payload TEXT NULL AFTER role")
                    )
                connection.execute(
                    text(
                        """
                        UPDATE app_users
                        SET role = :role
                        WHERE LOWER(email) = 'admin@drtransition.local'
                        """
                    ),
                    {"role": default_seed_user_role()},
                )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS llm_exchange_logs (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      request_id VARCHAR(64) NOT NULL,
                      provider VARCHAR(80) NOT NULL,
                      endpoint VARCHAR(255) NOT NULL,
                      model VARCHAR(255) NOT NULL,
                      status_code INT NULL,
                      duration_ms DOUBLE NULL,
                      request_payload LONGTEXT NOT NULL,
                      response_payload LONGTEXT NULL,
                      error TEXT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      INDEX ix_llm_exchange_logs_request_id (request_id),
                      INDEX ix_llm_exchange_logs_provider (provider),
                      INDEX ix_llm_exchange_logs_endpoint (endpoint),
                      INDEX ix_llm_exchange_logs_model (model),
                      INDEX ix_llm_exchange_logs_created_at (created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS app_rate_limits (
                      rate_limit_key VARCHAR(255) NOT NULL PRIMARY KEY,
                      attempts INT NOT NULL DEFAULT 0,
                      window_started_at DOUBLE NOT NULL DEFAULT 0,
                      locked_until DOUBLE NOT NULL DEFAULT 0,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
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
                      CONSTRAINT fk_audit_logs_user
                        FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                      INDEX ix_audit_logs_user_id (user_id),
                      INDEX ix_audit_logs_action (action),
                      INDEX ix_audit_logs_target_type (target_type),
                      INDEX ix_audit_logs_target_id (target_id),
                      INDEX ix_audit_logs_request_id (request_id),
                      INDEX ix_audit_logs_created_at (created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS system_hazards (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      sector_id CHAR(36) NOT NULL,
                      name VARCHAR(255) NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_system_hazards_sector
                        FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                      CONSTRAINT uq_system_hazard_sector_name UNIQUE (sector_id, name),
                      INDEX ix_system_hazards_sector_id (sector_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS custom_hazards (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      country_id CHAR(36) NOT NULL,
                      sector_id CHAR(36) NOT NULL,
                      region_id CHAR(36) NULL,
                      region_scope_key CHAR(36) NOT NULL DEFAULT '',
                      name VARCHAR(255) NOT NULL,
                      name_key VARCHAR(255) NOT NULL,
                      reason TEXT NULL,
                      evidence TEXT NULL,
                      source VARCHAR(40) NOT NULL DEFAULT 'user',
                      validation_mode VARCHAR(16) NOT NULL DEFAULT 'strict',
                      is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE,
                      created_by_user_id CHAR(36) NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_custom_hazards_country
                        FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
                      CONSTRAINT fk_custom_hazards_sector
                        FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                      CONSTRAINT fk_custom_hazards_region
                        FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
                      CONSTRAINT fk_custom_hazards_created_by
                        FOREIGN KEY (created_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                      CONSTRAINT uq_custom_hazard_scope_name
                        UNIQUE (country_id, sector_id, region_scope_key, name_key),
                      INDEX ix_custom_hazards_country_id (country_id),
                      INDEX ix_custom_hazards_sector_id (sector_id),
                      INDEX ix_custom_hazards_region_id (region_id),
                      INDEX ix_custom_hazards_visibility (validation_mode, is_crowd_sourced),
                      INDEX ix_custom_hazards_created_by_user_id (created_by_user_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS custom_hazard_profiles (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      custom_hazard_id CHAR(36) NOT NULL,
                      profile TEXT NOT NULL,
                      profile_key VARCHAR(255) NOT NULL,
                      variable_name VARCHAR(160) NULL,
                      explanation TEXT NULL,
                      statistical_basis TEXT NULL,
                      source VARCHAR(40) NOT NULL DEFAULT 'custom_hazard_extraction',
                      metadata_json TEXT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_custom_hazard_profiles_hazard
                        FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE CASCADE,
                      CONSTRAINT uq_custom_hazard_profile UNIQUE (custom_hazard_id, profile_key),
                      INDEX ix_custom_hazard_profiles_custom_hazard_id (custom_hazard_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )

        inspector = inspect(engine)
        if "custom_hazards" in inspector.get_table_names():
            custom_hazard_columns = {
                column["name"] for column in inspector.get_columns("custom_hazards")
            }
            custom_hazard_indexes = {
                index["name"] for index in inspector.get_indexes("custom_hazards")
            }
            with engine.begin() as connection:
                if "validation_mode" not in custom_hazard_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE custom_hazards "
                            "ADD COLUMN validation_mode VARCHAR(16) NOT NULL DEFAULT 'strict' "
                            "AFTER source"
                        )
                    )
                if "is_crowd_sourced" not in custom_hazard_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE custom_hazards "
                            "ADD COLUMN is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE "
                            "AFTER validation_mode"
                        )
                    )
                if "ix_custom_hazards_visibility" not in custom_hazard_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE custom_hazards "
                            "ADD INDEX ix_custom_hazards_visibility "
                            "(validation_mode, is_crowd_sourced)"
                        )
                    )

        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        if "evaluation_questions" in table_names:
            evaluation_columns = {
                column["name"] for column in inspector.get_columns("evaluation_questions")
            }
            with engine.begin() as connection:
                if "chart_title" not in evaluation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE evaluation_questions "
                            "ADD COLUMN chart_title VARCHAR(160) NULL AFTER category"
                        )
                    )
                connection.execute(
                    text(
                        """
                        UPDATE evaluation_questions
                        SET chart_title = CASE
                          WHEN category = 'The transformative impact' AND sort_order = 1 THEN 'Direct effect'
                          WHEN category = 'The transformative impact' AND sort_order = 2 THEN 'Systemic impact'
                          WHEN category = 'The transformative impact' AND sort_order = 3 THEN 'Societal transformation & equity'
                          WHEN category = 'Feasibility and Implementation' AND sort_order = 1 THEN 'Accessibility'
                          WHEN category = 'Feasibility and Implementation' AND sort_order = 2 THEN 'Affordability'
                          WHEN category = 'Feasibility and Implementation' AND sort_order = 3 THEN 'Acceptability'
                          WHEN category = 'Feasibility and Implementation' AND sort_order = 4 THEN 'Availability & timing'
                          ELSE chart_title
                        END
                        WHERE chart_title IS NULL OR chart_title = ''
                        """
                    )
                )
        if "countries" in table_names:
            country_columns = {column["name"] for column in inspector.get_columns("countries")}
            country_indexes = {index["name"] for index in inspector.get_indexes("countries")}

            with engine.begin() as connection:
                if "map_code" not in country_columns:
                    connection.execute(
                        text("ALTER TABLE countries ADD COLUMN map_code VARCHAR(8) NULL AFTER name")
                    )
                if "map_path" not in country_columns:
                    connection.execute(
                        text("ALTER TABLE countries ADD COLUMN map_path VARCHAR(255) NULL AFTER map_code")
                    )
                if "ix_countries_map_code" not in country_indexes:
                    connection.execute(
                        text("ALTER TABLE countries ADD INDEX ix_countries_map_code (map_code)")
                    )
                connection.execute(
                    text(
                        """
                        UPDATE countries
                        SET
                          map_code = CASE name
                            WHEN 'Germany' THEN 'DE'
                            WHEN 'Hungary' THEN 'HU'
                            WHEN 'Ireland' THEN 'IE'
                            WHEN 'Italy' THEN 'IT'
                            WHEN 'Portugal' THEN 'PT'
                            WHEN 'Spain' THEN 'ES'
                            ELSE map_code
                          END,
                          map_path = CASE name
                            WHEN 'Germany' THEN 'countries/de/de-all.geo.json'
                            WHEN 'Hungary' THEN 'countries/hu/hu-all.geo.json'
                            WHEN 'Ireland' THEN 'countries/ie/ie-all.geo.json'
                            WHEN 'Italy' THEN 'countries/it/it-all.geo.json'
                            WHEN 'Portugal' THEN 'countries/pt/pt-all.geo.json'
                            WHEN 'Spain' THEN 'countries/es/es-all.geo.json'
                            ELSE map_path
                          END
                        WHERE name IN ('Germany', 'Hungary', 'Ireland', 'Italy', 'Portugal', 'Spain')
                        """
                    )
                )

        if "user_sessions" in table_names:
            session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
            session_indexes = {index["name"] for index in inspector.get_indexes("user_sessions")}
            session_foreign_keys = {
                fk["name"] for fk in inspector.get_foreign_keys("user_sessions")
            }

            with engine.begin() as connection:
                if "title" not in session_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD COLUMN title VARCHAR(220) "
                            "NULL AFTER session_key"
                        )
                    )
                if "title_is_manual" not in session_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD COLUMN title_is_manual "
                            "BOOLEAN NOT NULL DEFAULT FALSE AFTER title"
                        )
                    )
                if "session_data" not in session_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD COLUMN session_data TEXT "
                            "NULL AFTER title"
                        )
                    )
                if "user_id" not in session_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD COLUMN user_id CHAR(36) NULL "
                            "AFTER session_key"
                        )
                    )
                if "ix_user_sessions_title" not in session_indexes:
                    connection.execute(
                        text("ALTER TABLE user_sessions ADD INDEX ix_user_sessions_title (title)")
                    )
                if "ix_user_sessions_user_id" not in session_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD INDEX "
                            "ix_user_sessions_user_id (user_id)"
                        )
                    )
                if "fk_user_sessions_user" not in session_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_sessions ADD CONSTRAINT fk_user_sessions_user "
                            "FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL"
                        )
                    )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS user_chat_messages (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      user_session_id CHAR(36) NOT NULL,
                      role VARCHAR(20) NOT NULL,
                      content TEXT NOT NULL,
                      is_error BOOLEAN NOT NULL DEFAULT FALSE,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_user_chat_messages_session
                        FOREIGN KEY (user_session_id) REFERENCES user_sessions(id)
                        ON DELETE CASCADE,
                      INDEX ix_user_chat_messages_session_id (user_session_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )

        inspector = inspect(engine)
        if "knowledge_documents" in inspector.get_table_names():
            document_columns = {
                column["name"] for column in inspector.get_columns("knowledge_documents")
            }
            document_indexes = {
                index["name"] for index in inspector.get_indexes("knowledge_documents")
            }
            with engine.begin() as connection:
                if "scope" not in document_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents ADD COLUMN scope "
                            "VARCHAR(20) NOT NULL DEFAULT 'main' AFTER source_uri"
                        )
                    )
                if "session_key" not in document_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents ADD COLUMN session_key "
                            "VARCHAR(64) NULL AFTER scope"
                        )
                    )
                new_scope_columns = {
                    "scope_level": "VARCHAR(20) NOT NULL DEFAULT 'global' AFTER session_key",
                    "country_id": "INT NULL AFTER session_key",
                    "region_id": "INT NULL AFTER country_id",
                    "sector_id": "INT NULL AFTER region_id",
                }
                for column_name, column_definition in new_scope_columns.items():
                    if column_name not in document_columns:
                        connection.execute(
                            text(
                                "ALTER TABLE knowledge_documents "
                                f"ADD COLUMN {column_name} {column_definition}"
                            )
                        )
                        document_columns.add(column_name)
                if "ix_knowledge_documents_scope" not in document_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents "
                            "ADD INDEX ix_knowledge_documents_scope (scope)"
                        )
                    )
                for index_name, column_name in (
                    ("ix_knowledge_documents_scope_level", "scope_level"),
                    ("ix_knowledge_documents_country_id", "country_id"),
                    ("ix_knowledge_documents_region_id", "region_id"),
                    ("ix_knowledge_documents_sector_id", "sector_id"),
                ):
                    if index_name not in document_indexes:
                        connection.execute(
                            text(
                                "ALTER TABLE knowledge_documents "
                                f"ADD INDEX {index_name} ({column_name})"
                            )
                        )
                if "ix_knowledge_documents_session_key" not in document_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents "
                            "ADD INDEX ix_knowledge_documents_session_key (session_key)"
                        )
                    )
                connection.execute(
                    text(
                        """
                        UPDATE knowledge_documents
                        SET scope_level = CASE
                          WHEN scope = 'validated_evidence' AND region_id IS NOT NULL THEN 'region'
                          WHEN scope = 'validated_evidence' AND country_id IS NOT NULL THEN 'country'
                          WHEN scope = 'temporary' THEN 'session'
                          ELSE 'global'
                        END
                        WHERE scope_level IS NULL
                           OR scope_level = ''
                           OR (scope = 'validated_evidence' AND scope_level = 'global' AND country_id IS NOT NULL)
                        """
                    )
                )

        inspector = inspect(engine)
        if "knowledge_chunks" in inspector.get_table_names():
            chunk_columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
            chunk_indexes = {index["name"] for index in inspector.get_indexes("knowledge_chunks")}
            with engine.begin() as connection:
                if "user_id" not in chunk_columns:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD COLUMN user_id CHAR(36) NULL AFTER document_id")
                    )
                if "source_type" not in chunk_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_chunks ADD COLUMN source_type "
                            "VARCHAR(40) NOT NULL DEFAULT 'document' AFTER content"
                        )
                    )
                if "source_uri" not in chunk_columns:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD COLUMN source_uri TEXT NULL AFTER source_type")
                    )
                if "page_number" not in chunk_columns:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD COLUMN page_number INT NULL AFTER source_uri")
                    )
                new_chunk_scope_columns = {
                    "scope_level": "VARCHAR(20) NOT NULL DEFAULT 'global' AFTER page_number",
                    "country_id": "INT NULL AFTER scope_level",
                    "region_id": "INT NULL AFTER country_id",
                    "sector_id": "INT NULL AFTER region_id",
                }
                for column_name, column_definition in new_chunk_scope_columns.items():
                    if column_name not in chunk_columns:
                        connection.execute(
                            text(
                                "ALTER TABLE knowledge_chunks "
                                f"ADD COLUMN {column_name} {column_definition}"
                            )
                        )
                        chunk_columns.add(column_name)
                if "ix_knowledge_chunks_user_id" not in chunk_indexes:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD INDEX ix_knowledge_chunks_user_id (user_id)")
                    )
                for index_name, column_name in (
                    ("ix_knowledge_chunks_scope_level", "scope_level"),
                    ("ix_knowledge_chunks_country_id", "country_id"),
                    ("ix_knowledge_chunks_region_id", "region_id"),
                    ("ix_knowledge_chunks_sector_id", "sector_id"),
                ):
                    if index_name not in chunk_indexes:
                        connection.execute(
                            text(
                                "ALTER TABLE knowledge_chunks "
                                f"ADD INDEX {index_name} ({column_name})"
                            )
                        )
                connection.execute(
                    text(
                        """
                        UPDATE knowledge_chunks kc
                        JOIN knowledge_documents kd ON kd.id = kc.document_id
                        SET kc.scope_level = kd.scope_level,
                            kc.country_id = kd.country_id,
                            kc.region_id = kd.region_id,
                            kc.sector_id = kd.sector_id
                        WHERE NOT (kc.scope_level <=> kd.scope_level)
                           OR NOT (kc.country_id <=> kd.country_id)
                           OR NOT (kc.region_id <=> kd.region_id)
                           OR NOT (kc.sector_id <=> kd.sector_id)
                        """
                    )
                )

        if seed_reference_data:
            ensure_reference_data_schema()
            ensure_additional_hazards()
            ensure_system_hazards_from_sector_prompts()
            ensure_mitigation_measure_examples()

        with engine.begin() as connection:
            _ensure_hazards_xlsx_policy_system_hazards(connection)

        inspector = inspect(engine)
        if "user_mitigation_measures" in inspector.get_table_names():
            mitigation_columns = {
                column["name"]
                for column in inspector.get_columns("user_mitigation_measures")
            }
            mitigation_indexes = {
                index["name"] for index in inspector.get_indexes("user_mitigation_measures")
            }
            mitigation_foreign_keys = {
                fk["name"] for fk in inspector.get_foreign_keys("user_mitigation_measures")
            }
            with engine.begin() as connection:
                if "user_session_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN user_session_id CHAR(36) NULL AFTER id"
                        )
                    )
                if "system_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN system_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN additional_hazard_id CHAR(36) NULL AFTER system_hazard_id"
                        )
                    )
                if "target_population" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN target_population TEXT NULL AFTER reason"
                        )
                    )
                if "conclusion" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN conclusion TEXT NULL AFTER target_population"
                        )
                    )
                if "target_groups_json" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN target_groups_json TEXT NULL AFTER conclusion"
                        )
                    )
                if "validation_mode" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN validation_mode VARCHAR(16) NOT NULL DEFAULT 'strict' "
                            "AFTER target_groups_json"
                        )
                    )
                if "is_crowd_sourced" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE "
                            "AFTER validation_mode"
                        )
                    )
                if "ix_user_mitigation_measures_system_hazard_id" not in mitigation_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD INDEX ix_user_mitigation_measures_system_hazard_id (system_hazard_id)"
                        )
                    )
                if "ix_user_mitigation_measures_custom_hazard_id" not in mitigation_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD INDEX ix_user_mitigation_measures_custom_hazard_id (custom_hazard_id)"
                        )
                    )
                if "ix_user_mitigation_measures_user_session_id" not in mitigation_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD INDEX ix_user_mitigation_measures_user_session_id (user_session_id)"
                        )
                    )
                if "ix_user_mitigation_measures_visibility" not in mitigation_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD INDEX ix_user_mitigation_measures_visibility "
                            "(validation_mode, is_crowd_sourced)"
                        )
                    )
                if "fk_user_mitigation_measures_session" not in mitigation_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD CONSTRAINT fk_user_mitigation_measures_session "
                            "FOREIGN KEY (user_session_id) REFERENCES user_sessions(id) ON DELETE CASCADE"
                        )
                    )
                if "ix_user_mitigation_measures_additional_hazard_id" not in mitigation_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD INDEX ix_user_mitigation_measures_additional_hazard_id (additional_hazard_id)"
                        )
                    )
                if "fk_user_mitigation_measures_system_hazard" not in mitigation_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD CONSTRAINT fk_user_mitigation_measures_system_hazard "
                            "FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE"
                        )
                    )
                if "fk_user_mitigation_measures_custom_hazard" not in mitigation_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD CONSTRAINT fk_user_mitigation_measures_custom_hazard "
                            "FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE CASCADE"
                        )
                    )
                if (
                    "fk_user_mitigation_measures_additional_hazard" not in mitigation_foreign_keys
                    and "additional_hazards" in inspector.get_table_names()
                ):
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD CONSTRAINT fk_user_mitigation_measures_additional_hazard "
                            "FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE CASCADE"
                        )
                    )
                try:
                    connection.execute(
                        text("ALTER TABLE user_mitigation_measures MODIFY user_hazard_id CHAR(36) NULL")
                    )
                except Exception:
                    logger.exception("Failed to relax user_mitigation_measures.user_hazard_id")

        if "user_hazards" not in inspector.get_table_names():
            return

        columns = {column["name"] for column in inspector.get_columns("user_hazards")}
        indexes = {index["name"] for index in inspector.get_indexes("user_hazards")}
        foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("user_hazards")}

        with engine.begin() as connection:
            if "system_hazard_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN system_hazard_id CHAR(36) NULL AFTER user_session_id")
                )
            if "custom_hazard_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER user_session_id")
                )
            if "region_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN region_id CHAR(36) NULL AFTER sector_id")
                )
            if "validation_mode" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards "
                        "ADD COLUMN validation_mode VARCHAR(16) NOT NULL DEFAULT 'strict' AFTER source"
                    )
                )
            if "is_crowd_sourced" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards "
                        "ADD COLUMN is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE AFTER validation_mode"
                    )
                )
            if "ix_user_hazards_system_hazard_id" not in indexes:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD INDEX ix_user_hazards_system_hazard_id (system_hazard_id)")
                )
            if "ix_user_hazards_custom_hazard_id" not in indexes:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD INDEX ix_user_hazards_custom_hazard_id (custom_hazard_id)")
                )
            if "ix_user_hazards_region_id" not in indexes:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD INDEX ix_user_hazards_region_id (region_id)")
                )
            if "ix_user_hazards_visibility" not in indexes:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards "
                        "ADD INDEX ix_user_hazards_visibility (validation_mode, is_crowd_sourced)"
                    )
                )
            if "fk_user_hazards_system_hazard" not in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards ADD CONSTRAINT fk_user_hazards_system_hazard "
                        "FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL"
                    )
                )
            if "fk_user_hazards_custom_hazard" not in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards ADD CONSTRAINT fk_user_hazards_custom_hazard "
                        "FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE SET NULL"
                    )
                )
            if "fk_user_hazards_region" not in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards ADD CONSTRAINT fk_user_hazards_region "
                        "FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL"
                    )
                )

        inspector = inspect(engine)
        if "user_question_responses" in inspector.get_table_names():
            response_columns = {
                column["name"] for column in inspector.get_columns("user_question_responses")
            }
            response_indexes = {
                index["name"] for index in inspector.get_indexes("user_question_responses")
            }
            response_foreign_keys = {
                fk["name"] for fk in inspector.get_foreign_keys("user_question_responses")
            }
            with engine.begin() as connection:
                if "system_hazard_id" not in response_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD COLUMN system_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in response_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in response_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD COLUMN additional_hazard_id CHAR(36) NULL AFTER system_hazard_id"
                        )
                    )
                if "ix_user_question_responses_system_hazard_id" not in response_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD INDEX ix_user_question_responses_system_hazard_id (system_hazard_id)"
                        )
                    )
                if "ix_user_question_responses_custom_hazard_id" not in response_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD INDEX ix_user_question_responses_custom_hazard_id (custom_hazard_id)"
                        )
                    )
                if "ix_user_question_responses_additional_hazard_id" not in response_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD INDEX ix_user_question_responses_additional_hazard_id (additional_hazard_id)"
                        )
                    )
                if "fk_user_question_responses_system_hazard" not in response_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD CONSTRAINT fk_user_question_responses_system_hazard "
                            "FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL"
                        )
                    )
                if "fk_user_question_responses_custom_hazard" not in response_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD CONSTRAINT fk_user_question_responses_custom_hazard "
                            "FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE SET NULL"
                        )
                    )
                if (
                    "fk_user_question_responses_additional_hazard" not in response_foreign_keys
                    and "additional_hazards" in inspector.get_table_names()
                ):
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD CONSTRAINT fk_user_question_responses_additional_hazard "
                            "FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE SET NULL"
                        )
                    )

        inspector = inspect(engine)
        if "user_hazard_socio_demographics" in inspector.get_table_names():
            dg_columns = {
                column["name"]
                for column in inspector.get_columns("user_hazard_socio_demographics")
            }
            dg_indexes = {
                index["name"]
                for index in inspector.get_indexes("user_hazard_socio_demographics")
            }
            dg_foreign_keys = {
                fk["name"]
                for fk in inspector.get_foreign_keys("user_hazard_socio_demographics")
            }
            with engine.begin() as connection:
                if "user_session_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN user_session_id CHAR(36) NULL AFTER id"
                        )
                    )
                if "system_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN system_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN additional_hazard_id CHAR(36) NULL AFTER system_hazard_id"
                        )
                    )
                if "country_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN country_id CHAR(36) NULL AFTER user_hazard_id"
                        )
                    )
                if "region_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN region_id CHAR(36) NULL AFTER country_id"
                        )
                    )
                if "sector_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN sector_id CHAR(36) NULL AFTER region_id"
                        )
                    )
                if "variable_name" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN variable_name VARCHAR(160) NULL AFTER sector_id"
                        )
                    )
                if "explanation" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN explanation TEXT NULL AFTER profile"
                        )
                    )
                if "statistical_basis" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN statistical_basis TEXT NULL AFTER explanation"
                        )
                    )
                if "metadata_json" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN metadata_json TEXT NULL AFTER source"
                        )
                    )
                if "ix_user_hazard_socio_demographics_country_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_country_id (country_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_region_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_region_id (region_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_sector_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_sector_id (sector_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_user_session_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_user_session_id (user_session_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_system_hazard_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_system_hazard_id (system_hazard_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_custom_hazard_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_custom_hazard_id (custom_hazard_id)"
                        )
                    )
                if "ix_user_hazard_socio_demographics_additional_hazard_id" not in dg_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD INDEX ix_user_hazard_socio_demographics_additional_hazard_id (additional_hazard_id)"
                        )
                    )
                if "fk_user_hazard_dgs_session" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_session "
                            "FOREIGN KEY (user_session_id) REFERENCES user_sessions(id) ON DELETE CASCADE"
                        )
                    )
                if "fk_user_hazard_dgs_system_hazard" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_system_hazard "
                            "FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE"
                        )
                    )
                if "fk_user_hazard_dgs_custom_hazard" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_custom_hazard "
                            "FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE CASCADE"
                        )
                    )
                if (
                    "fk_user_hazard_dgs_additional_hazard" not in dg_foreign_keys
                    and "additional_hazards" in inspector.get_table_names()
                ):
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_additional_hazard "
                            "FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE CASCADE"
                        )
                    )
                try:
                    connection.execute(
                        text("ALTER TABLE user_hazard_socio_demographics MODIFY user_hazard_id CHAR(36) NULL")
                    )
                except Exception:
                    logger.exception("Failed to relax user_hazard_socio_demographics.user_hazard_id")
                if "fk_user_hazard_dgs_country" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_country "
                            "FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL"
                        )
                    )
                if "fk_user_hazard_dgs_region" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_region "
                            "FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL"
                        )
                    )
                if "fk_user_hazard_dgs_sector" not in dg_foreign_keys:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD CONSTRAINT fk_user_hazard_dgs_sector "
                            "FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL"
                        )
                    )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS system_hazard_socio_demographics (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      system_hazard_id CHAR(36) NOT NULL,
                      sector_id CHAR(36) NULL,
                      variable_name VARCHAR(160) NULL,
                      variable_type VARCHAR(40) NOT NULL DEFAULT 'individual',
                      profile TEXT NOT NULL,
                      explanation TEXT NULL,
                      statistical_basis TEXT NULL,
                      source VARCHAR(40) NOT NULL DEFAULT 'sector_prompt',
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_system_hazard_dgs_hazard
                        FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
                      CONSTRAINT fk_system_hazard_dgs_sector
                        FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
                      INDEX ix_system_hazard_socio_demographics_hazard_id (system_hazard_id),
                      INDEX ix_system_hazard_socio_demographics_sector_id (sector_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS system_hazard_socio_demographic_target_populations (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      system_hazard_socio_demographic_id CHAR(36) NOT NULL,
                      question_option_id {question_option_id_type} NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT fk_system_dg_target_population_system_dg
                        FOREIGN KEY (system_hazard_socio_demographic_id)
                        REFERENCES system_hazard_socio_demographics(id) ON DELETE CASCADE,
                      CONSTRAINT fk_system_dg_target_population_option
                        FOREIGN KEY (question_option_id)
                        REFERENCES question_options(id) ON DELETE CASCADE,
                      CONSTRAINT uq_system_dg_target_population_option
                        UNIQUE (system_hazard_socio_demographic_id, question_option_id),
                      INDEX ix_system_dg_target_population_system_dg
                        (system_hazard_socio_demographic_id),
                      INDEX ix_system_dg_target_population_option (question_option_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            inspector = inspect(engine)
            system_dg_columns = {
                column["name"]
                for column in inspector.get_columns("system_hazard_socio_demographics")
            }
            foreign_key_rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT CONSTRAINT_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'system_hazard_socio_demographics'
                      AND COLUMN_NAME IN ('country_id', 'region_id')
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                    """
                )
            ).all()
            for row in foreign_key_rows:
                foreign_key_name = str(row[0] or "").strip()
                if foreign_key_name:
                    connection.execute(
                        text(
                            "ALTER TABLE system_hazard_socio_demographics "
                            f"DROP FOREIGN KEY `{foreign_key_name}`"
                        )
                    )
            inspector = inspect(engine)
            system_dg_columns = {
                column["name"]
                for column in inspector.get_columns("system_hazard_socio_demographics")
            }
            if "affected_population_pct_regional" in system_dg_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographics "
                        "DROP COLUMN affected_population_pct_regional"
                    )
                )
            if "affected_population_pct_national" in system_dg_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographics "
                        "DROP COLUMN affected_population_pct_national"
                    )
                )
            if "affected_population_updated_at" in system_dg_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographics "
                        "DROP COLUMN affected_population_updated_at"
                    )
                )
            if "metadata_json" in system_dg_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographics "
                        "DROP COLUMN metadata_json"
                    )
                )
            inspector = inspect(engine)
            system_dg_columns = {
                column["name"]
                for column in inspector.get_columns("system_hazard_socio_demographics")
            }
            if "variable_type" not in system_dg_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographics "
                        "ADD COLUMN variable_type VARCHAR(40) NOT NULL DEFAULT 'individual' AFTER variable_name"
                    )
                )
            connection.execute(
                text(
                    """
                    UPDATE system_hazard_socio_demographics
                    SET variable_type = CASE
                      WHEN variable_name LIKE 'macro\\_%' THEN 'macro'
                      ELSE 'individual'
                    END
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS eurostat_population_cache (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      country VARCHAR(120) NOT NULL,
                      region VARCHAR(120) NOT NULL,
                      country_id CHAR(36) NULL,
                      region_id CHAR(36) NULL,
                      sector_id CHAR(36) NULL,
                      system_hazard_id CHAR(36) NULL,
                      profile VARCHAR(255) NOT NULL,
                      response_json TEXT NOT NULL,
                      expires_at DATETIME NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      CONSTRAINT fk_eurostat_population_country
                        FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
                      CONSTRAINT fk_eurostat_population_region
                        FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE,
                      CONSTRAINT fk_eurostat_population_sector
                        FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                      CONSTRAINT fk_eurostat_population_hazard
                        FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
                      CONSTRAINT uq_eurostat_population_lookup
                        UNIQUE (country_id, region_id, sector_id, system_hazard_id, profile),
                      INDEX ix_eurostat_population_cache_country (country),
                      INDEX ix_eurostat_population_cache_region (region),
                      INDEX ix_eurostat_population_cache_profile (profile),
                      INDEX ix_eurostat_population_cache_expires_at (expires_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            inspector = inspect(engine)
            cache_columns = {
                column["name"] for column in inspector.get_columns("eurostat_population_cache")
            }
            cache_id_columns = (
                ("country_id", "countries"),
                ("region_id", "regions"),
                ("sector_id", "sectors"),
                ("system_hazard_id", "system_hazards"),
            )
            for column_name, _ in cache_id_columns:
                if column_name not in cache_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE eurostat_population_cache "
                            f"ADD COLUMN {column_name} INT NULL"
                        )
                    )
            connection.execute(
                text(
                    "UPDATE eurostat_population_cache ep "
                    "JOIN countries c ON LOWER(c.name) = LOWER(ep.country) "
                    "OR LOWER(c.map_code) = LOWER(ep.country) "
                    "SET ep.country_id = c.id WHERE ep.country_id IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE eurostat_population_cache ep "
                    "JOIN regions r ON r.country_id = ep.country_id "
                    "AND LOWER(r.name) = LOWER(ep.region) "
                    "SET ep.region_id = r.id WHERE ep.region_id IS NULL"
                )
            )
            inspector = inspect(engine)
            cache_foreign_key_columns = {
                tuple(foreign_key.get("constrained_columns") or [])
                for foreign_key in inspector.get_foreign_keys("eurostat_population_cache")
            }
            for column_name, table_name in cache_id_columns:
                if (column_name,) not in cache_foreign_key_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE eurostat_population_cache "
                            f"ADD CONSTRAINT fk_eurostat_population_{column_name} "
                            f"FOREIGN KEY ({column_name}) REFERENCES {table_name}(id) "
                            "ON DELETE CASCADE"
                        )
                    )
            expected_lookup_columns = [
                "country_id",
                "region_id",
                "sector_id",
                "system_hazard_id",
                "profile",
            ]
            lookup_index = next(
                (
                    index
                    for index in inspector.get_indexes("eurostat_population_cache")
                    if index["name"] == "uq_eurostat_population_lookup"
                ),
                None,
            )
            if lookup_index and lookup_index.get("column_names") != expected_lookup_columns:
                connection.execute(
                    text(
                        "ALTER TABLE eurostat_population_cache "
                        "DROP INDEX uq_eurostat_population_lookup"
                    )
                )
                lookup_index = None
            if lookup_index is None:
                connection.execute(
                    text(
                        "ALTER TABLE eurostat_population_cache "
                        "ADD CONSTRAINT uq_eurostat_population_lookup "
                        "UNIQUE (country_id, region_id, sector_id, system_hazard_id, profile)"
                    )
                )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS system_hazard_socio_demographic_population_matches (
                      id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
                      system_hazard_socio_demographic_id CHAR(36) NOT NULL,
                      eurostat_population_cache_id CHAR(36) NULL,
                      match_status INT NOT NULL DEFAULT 1,
                      attempt_count INT NOT NULL DEFAULT 0,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      CONSTRAINT fk_system_dg_population_match_system_dg
                        FOREIGN KEY (system_hazard_socio_demographic_id)
                        REFERENCES system_hazard_socio_demographics(id) ON DELETE CASCADE,
                      CONSTRAINT fk_system_dg_population_match_eurostat_cache
                        FOREIGN KEY (eurostat_population_cache_id)
                        REFERENCES eurostat_population_cache(id) ON DELETE CASCADE,
                      CONSTRAINT uq_system_dg_eurostat_cache_match
                        UNIQUE (system_hazard_socio_demographic_id, eurostat_population_cache_id),
                      INDEX ix_system_dg_population_match_system_dg (system_hazard_socio_demographic_id),
                      INDEX ix_system_dg_population_match_eurostat_cache (eurostat_population_cache_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            inspector = inspect(engine)
            match_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "system_hazard_socio_demographic_population_matches"
                )
            }
            if "eurostat_population_cache_id" in match_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographic_population_matches "
                        "MODIFY COLUMN eurostat_population_cache_id CHAR(36) NULL"
                    )
                )
            if "match_status" not in match_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographic_population_matches "
                        "ADD COLUMN match_status INT NOT NULL DEFAULT 1 AFTER eurostat_population_cache_id"
                    )
                )
            if "attempt_count" not in match_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographic_population_matches "
                        "ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 AFTER match_status"
                    )
                )
            if "matched_profiles_json" in match_columns:
                connection.execute(
                    text(
                        "ALTER TABLE system_hazard_socio_demographic_population_matches "
                        "DROP COLUMN matched_profiles_json"
                    )
                )
    except Exception:
        logger.exception("Runtime schema migration failed")
        raise



def repair_partial_installer_schema() -> bool:
    """Repair local databases left incomplete by an interrupted installer seed."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    missing_required_tables = {
        "custom_hazards",
        "custom_hazard_profiles",
        "user_hazards",
        "user_hazard_socio_demographics",
    } - table_names
    if missing_required_tables:
        logger.warning(
            "Repairing partial installer schema; missing tables: %s",
            ", ".join(sorted(missing_required_tables)),
        )
        if {
            "user_hazards",
            "user_hazard_socio_demographics",
        } & missing_required_tables:
            run_schema_sql()
        ensure_runtime_schema(seed_reference_data=False)
        return True

    user_hazard_columns = {
        column["name"] for column in inspector.get_columns("user_hazards")
    }
    dg_columns = {
        column["name"]
        for column in inspector.get_columns("user_hazard_socio_demographics")
    }
    missing_columns = []
    if "custom_hazard_id" not in user_hazard_columns:
        missing_columns.append("user_hazards.custom_hazard_id")
    for column_name in (
        "user_session_id",
        "custom_hazard_id",
        "system_hazard_id",
        "additional_hazard_id",
    ):
        if column_name not in dg_columns:
            missing_columns.append(f"user_hazard_socio_demographics.{column_name}")

    if not missing_columns:
        return False

    logger.warning(
        "Repairing partial installer schema; missing columns: %s",
        ", ".join(missing_columns),
    )
    ensure_runtime_schema(seed_reference_data=False)
    return True



def run_runtime_migrations(
    *,
    apply_base_schema: bool = False,
    include_basic_data: bool | None = None,
    seed_reference_data: bool = False,
) -> None:
    """Apply schema changes through an explicit migration entry point.

    Normal application startup should not create, alter, or drop legacy-repair
    objects. Production deploys should use this versioned path only.
    """
    if apply_base_schema:
        run_schema_sql(include_basic_data=include_basic_data)
    apply_versioned_migrations()
    repair_partial_installer_schema()
    if seed_reference_data:
        ensure_additional_hazards()
        ensure_system_hazards_from_sector_prompts()
        ensure_mitigation_measure_examples()
        with engine.begin() as connection:
            _ensure_hazards_xlsx_policy_system_hazards(connection)
    logger.info("Runtime database migrations applied")



