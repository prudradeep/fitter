import csv
import logging
import re
import zipfile
from collections.abc import Generator
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
MM_CSV_PATH = Path(__file__).resolve().parents[1] / "mm.csv"
MM_TARGET_GROUP_XLSX_PATH = Path(__file__).resolve().parents[1] / "MM Target group.xlsx"
SECTORAL_CHALLENGES_XLSX_PATH = Path(__file__).resolve().parents[1] / "sectoral_challenges.xlsx"
HAZARDS_XLSX_PATH = Path(__file__).resolve().parents[1] / "hazards.xlsx"
ADDITIONAL_HAZARDS_CSV_PATH = Path(__file__).resolve().parents[1] / "additionalHazards.csv"
ADDITIONAL_HAZARD_PROFILES_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "additionalHazardProfiles.csv"
)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def validate_database_connection() -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("Database connection validated")
    except Exception:
        logger.exception("Database connection validation failed")
        raise


def run_schema_sql() -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    statements = split_sql_statements(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        with engine.begin() as connection:
            for statement in statements:
                normalized = statement.lstrip().casefold()
                if normalized.startswith(("create database", "use ")):
                    continue
                connection.execute(text(statement))
        logger.info("Database schema.sql applied")
    except Exception:
        logger.exception("Applying schema.sql failed")
        raise


def seed_reference_data(*, apply_schema: bool = True) -> None:
    """Apply the base schema and reload reference data from local CSV/XLSX files."""
    if apply_schema:
        run_schema_sql()
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(seed_reference_data=True)
    logger.info("Reference data seeded")


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


def ensure_runtime_schema(*, seed_reference_data: bool = False) -> None:
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS app_users (
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      email VARCHAR(255) NOT NULL UNIQUE,
                      name VARCHAR(160) NOT NULL,
                      password_hash VARCHAR(255) NOT NULL,
                      designation VARCHAR(160) NOT NULL,
                      organisation_type VARCHAR(160) NOT NULL,
                      organisation_name VARCHAR(220) NOT NULL,
                      role VARCHAR(40) NOT NULL DEFAULT 'user',
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
                connection.execute(
                    text(
                        """
                        UPDATE app_users
                        SET role = 'admin'
                        WHERE LOWER(email) = 'admin@drtransition.local'
                        """
                    )
                )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS llm_exchange_logs (
                      id INT AUTO_INCREMENT PRIMARY KEY,
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
                    CREATE TABLE IF NOT EXISTS system_hazards (
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      sector_id INT NOT NULL,
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      country_id INT NOT NULL,
                      sector_id INT NOT NULL,
                      region_id INT NULL,
                      region_scope_key INT NOT NULL DEFAULT 0,
                      name VARCHAR(255) NOT NULL,
                      name_key VARCHAR(255) NOT NULL,
                      reason TEXT NULL,
                      evidence TEXT NULL,
                      source VARCHAR(40) NOT NULL DEFAULT 'user',
                      validation_mode VARCHAR(16) NOT NULL DEFAULT 'strict',
                      is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE,
                      created_by_user_id INT NULL,
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      custom_hazard_id INT NOT NULL,
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
                            "ALTER TABLE user_sessions ADD COLUMN user_id INT NULL "
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      user_session_id INT NOT NULL,
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
                    "country_id": "INT NULL AFTER session_key",
                    "region_id": "INT NULL AFTER country_id",
                    "sector_id": "INT NULL AFTER region_id",
                    "sync_id": "VARCHAR(64) NULL AFTER sector_id",
                    "sync_version": "INT NOT NULL DEFAULT 0 AFTER sync_id",
                    "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER sync_version",
                    "deleted_at": "DATETIME NULL AFTER updated_at",
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
                    ("ix_knowledge_documents_country_id", "country_id"),
                    ("ix_knowledge_documents_region_id", "region_id"),
                    ("ix_knowledge_documents_sector_id", "sector_id"),
                    ("ix_knowledge_documents_sync_id", "sync_id"),
                    ("ix_knowledge_documents_sync_version", "sync_version"),
                    ("ix_knowledge_documents_deleted_at", "deleted_at"),
                ):
                    if index_name not in document_indexes:
                        connection.execute(
                            text(
                                "ALTER TABLE knowledge_documents "
                                f"ADD INDEX {index_name} ({column_name})"
                            )
                        )
                connection.execute(
                    text(
                        """
                        UPDATE knowledge_documents
                        SET sync_id = REPLACE(UUID(), '-', '')
                        WHERE sync_id IS NULL OR sync_id = ''
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE knowledge_documents
                        SET sync_version = CAST(UNIX_TIMESTAMP(NOW(3)) * 1000 AS UNSIGNED)
                        WHERE sync_version IS NULL OR sync_version = 0
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE knowledge_documents documents
                        JOIN app_users users ON users.id = documents.user_id
                        SET documents.user_id = NULL
                        WHERE documents.scope = 'main'
                          AND users.role = 'admin'
                          AND documents.deleted_at IS NULL
                        """
                    )
                )
                if "ix_knowledge_documents_session_key" not in document_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents "
                            "ADD INDEX ix_knowledge_documents_session_key (session_key)"
                        )
                        )

        inspector = inspect(engine)
        if "knowledge_chunks" in inspector.get_table_names():
            chunk_columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
            chunk_indexes = {index["name"] for index in inspector.get_indexes("knowledge_chunks")}
            with engine.begin() as connection:
                if "user_id" not in chunk_columns:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD COLUMN user_id INT NULL AFTER document_id")
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
                if "ix_knowledge_chunks_user_id" not in chunk_indexes:
                    connection.execute(
                        text("ALTER TABLE knowledge_chunks ADD INDEX ix_knowledge_chunks_user_id (user_id)")
                    )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS sync_state (
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      scope VARCHAR(40) NOT NULL,
                      country_id INT NOT NULL DEFAULT 0,
                      region_id INT NOT NULL DEFAULT 0,
                      sector_id INT NOT NULL DEFAULT 0,
                      last_sync_version INT NOT NULL DEFAULT 0,
                      last_synced_at DATETIME NULL,
                      CONSTRAINT uq_sync_state_scope_context
                        UNIQUE (scope, country_id, region_id, sector_id),
                      INDEX ix_sync_state_scope (scope)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS evidence_submissions (
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      submitter_user_id INT NULL,
                      session_key VARCHAR(64) NULL,
                      country_id INT NULL,
                      region_id INT NULL,
                      sector_id INT NULL,
                      source_type VARCHAR(40) NOT NULL,
                      source_uri TEXT NULL,
                      title VARCHAR(255) NOT NULL,
                      content TEXT NULL,
                      status VARCHAR(40) NOT NULL DEFAULT 'pending',
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      CONSTRAINT fk_evidence_submissions_user
                        FOREIGN KEY (submitter_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                      CONSTRAINT fk_evidence_submissions_country
                        FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
                      CONSTRAINT fk_evidence_submissions_region
                        FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
                      CONSTRAINT fk_evidence_submissions_sector
                        FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
                      INDEX ix_evidence_submissions_user_id (submitter_user_id),
                      INDEX ix_evidence_submissions_session_key (session_key),
                      INDEX ix_evidence_submissions_country_id (country_id),
                      INDEX ix_evidence_submissions_region_id (region_id),
                      INDEX ix_evidence_submissions_sector_id (sector_id),
                      INDEX ix_evidence_submissions_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )

        if seed_reference_data:
            ensure_additional_hazards()
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
                            "ADD COLUMN user_session_id INT NULL AFTER id"
                        )
                    )
                if "system_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN system_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN custom_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN additional_hazard_id INT NULL AFTER system_hazard_id"
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
                        text("ALTER TABLE user_mitigation_measures MODIFY user_hazard_id INT NULL")
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
                    text("ALTER TABLE user_hazards ADD COLUMN system_hazard_id INT NULL AFTER user_session_id")
                )
            if "custom_hazard_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN custom_hazard_id INT NULL AFTER user_session_id")
                )
            if "region_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN region_id INT NULL AFTER sector_id")
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
                            "ADD COLUMN system_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in response_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD COLUMN custom_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in response_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_question_responses "
                            "ADD COLUMN additional_hazard_id INT NULL AFTER system_hazard_id"
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
                            "ADD COLUMN user_session_id INT NULL AFTER id"
                        )
                    )
                if "system_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN system_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "custom_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN custom_hazard_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "additional_hazard_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN additional_hazard_id INT NULL AFTER system_hazard_id"
                        )
                    )
                if "country_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN country_id INT NULL AFTER user_hazard_id"
                        )
                    )
                if "region_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN region_id INT NULL AFTER country_id"
                        )
                    )
                if "sector_id" not in dg_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_hazard_socio_demographics "
                            "ADD COLUMN sector_id INT NULL AFTER region_id"
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
                        text("ALTER TABLE user_hazard_socio_demographics MODIFY user_hazard_id INT NULL")
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      system_hazard_id INT NOT NULL,
                      sector_id INT NULL,
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
                    """
                    CREATE TABLE IF NOT EXISTS system_hazard_socio_demographic_target_populations (
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      system_hazard_socio_demographic_id INT NOT NULL,
                      question_option_id INT NOT NULL,
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
            system_dg_indexes = {
                index["name"]
                for index in inspector.get_indexes("system_hazard_socio_demographics")
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
            system_dg_indexes = {
                index["name"]
                for index in inspector.get_indexes("system_hazard_socio_demographics")
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      country VARCHAR(120) NOT NULL,
                      region VARCHAR(120) NOT NULL,
                      country_id INT NULL,
                      region_id INT NULL,
                      sector_id INT NULL,
                      system_hazard_id INT NULL,
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
                      id INT AUTO_INCREMENT PRIMARY KEY,
                      system_hazard_socio_demographic_id INT NOT NULL,
                      eurostat_population_cache_id INT NULL,
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
                        "MODIFY COLUMN eurostat_population_cache_id INT NULL"
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


def _normalize_mitigation_example_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _read_mm_csv_rows() -> list[dict[str, str]]:
    if not MM_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with MM_CSV_PATH.open(encoding=encoding, newline="") as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with MM_CSV_PATH.open(encoding="utf-8", errors="replace", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_mm_target_group_xlsx_rows() -> list[dict[str, object]]:
    if not MM_TARGET_GROUP_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(MM_TARGET_GROUP_XLSX_PATH)
    if len(rows) < 3:
        return []

    category_row = rows[0]
    header_row = rows[1]
    category_by_column: dict[int, str] = {}
    current_category = ""
    for column_index, raw_category in enumerate(category_row):
        category = str(raw_category or "").strip()
        if category:
            current_category = category
        if column_index >= 5:
            category_by_column[column_index] = current_category

    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[2:], start=3):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        sector_name = _xlsx_cell(row, 2)
        if not policy_code and not policy_title:
            continue
        for column_index in range(5, len(header_row)):
            target_group = _xlsx_cell(header_row, column_index)
            if not target_group:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "sector_name": sector_name,
                    "policy_type": _xlsx_cell(row, 3),
                    "short_description": _xlsx_cell(row, 4),
                    "target_group_category": category_by_column.get(column_index, ""),
                    "target_group": target_group,
                    "match_value": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows


def _read_xlsx_first_sheet_rows(path: Path) -> list[list[str]]:
    namespace = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as workbook_zip:
        shared_strings = _xlsx_shared_strings(workbook_zip, namespace)
        workbook = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))
        relationship_targets = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships
        }
        first_sheet = workbook.find("a:sheets/a:sheet", namespace)
        if first_sheet is None:
            return []
        relationship_id = first_sheet.attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        sheet_target = relationship_targets[relationship_id]
        sheet_path = (
            sheet_target.lstrip("/")
            if sheet_target.startswith("xl/")
            else f"xl/{sheet_target.lstrip('/')}"
        )
        sheet = ET.fromstring(workbook_zip.read(sheet_path))
        parsed_rows: list[list[str]] = []
        for row in sheet.findall(".//a:sheetData/a:row", namespace):
            values: list[str] = []
            for cell in row.findall("a:c", namespace):
                column_index = _xlsx_column_index(cell.attrib.get("r", ""))
                while len(values) <= column_index:
                    values.append("")
                values[column_index] = _xlsx_cell_value(cell, shared_strings, namespace)
            parsed_rows.append(values)
        return parsed_rows


def _xlsx_shared_strings(
    workbook_zip: zipfile.ZipFile, namespace: dict[str, str]
) -> list[str]:
    try:
        shared_root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(text.text or "" for text in item.findall(".//a:t", namespace))
        for item in shared_root.findall("a:si", namespace)
    ]


def _xlsx_cell_value(
    cell: ET.Element,
    shared_strings: list[str],
    namespace: dict[str, str],
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", namespace)).strip()
    value_node = cell.find("a:v", namespace)
    value = "" if value_node is None else str(value_node.text or "")
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)].strip()
        except (IndexError, ValueError):
            return ""
    return value.strip()


def _xlsx_column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference.upper())
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _xlsx_cell(row: list[str], index: int) -> str:
    return str(row[index] if index < len(row) else "").strip()


def _read_sectoral_challenges_xlsx_rows() -> list[dict[str, object]]:
    if not SECTORAL_CHALLENGES_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(SECTORAL_CHALLENGES_XLSX_PATH)
    if len(rows) < 2:
        return []

    header_row = rows[0]
    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[1:], start=2):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        if not policy_code and not policy_title:
            continue
        for column_index in range(2, len(header_row)):
            challenge = _xlsx_cell(header_row, column_index)
            if not challenge:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "additional_hazard": challenge,
                    "match_value": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows


def _read_hazards_xlsx_rows() -> list[dict[str, object]]:
    if not HAZARDS_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(HAZARDS_XLSX_PATH)
    if len(rows) < 3:
        return []

    sector_row = rows[0]
    header_row = rows[1]
    sector_by_column: dict[int, str] = {}
    current_sector = ""
    for column_index, raw_sector in enumerate(sector_row):
        sector = _hazards_xlsx_sector_name(str(raw_sector or ""))
        if sector:
            current_sector = sector
        if column_index >= 2:
            sector_by_column[column_index] = current_sector

    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[2:], start=3):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        if not policy_code and not policy_title:
            continue
        for column_index in range(2, len(header_row)):
            hazard_label = _hazards_xlsx_hazard_label(_xlsx_cell(header_row, column_index))
            hazard_sector = sector_by_column.get(column_index, "")
            if not hazard_label or not hazard_sector:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "hazard_sector": hazard_sector,
                    "hazard_label": hazard_label,
                    "mitigation_effect": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows


def _hazards_xlsx_sector_name(value: str) -> str:
    normalized = value.casefold()
    if "energy" in normalized:
        return "Energy"
    if "transport" in normalized:
        return "Transport"
    if "housing" in normalized:
        return "Housing"
    return ""


def _hazards_xlsx_hazard_label(value: str) -> str:
    cleaned = str(value or "").strip().strip("[]")
    cleaned = re.sub(r"(?i)^hazard\s+\d+\s*\W+\s*", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _hazards_xlsx_system_hazard_lookup_key(
    hazard_sector: str,
    hazard_label: str,
) -> tuple[str, str] | None:
    sector_key = _normalize_mitigation_example_key(hazard_sector)
    label_key = _normalize_mitigation_example_key(hazard_label)
    aliases = {
        ("energy", "higherelectricitybills"): "higherelectricitybills",
        ("energy", "increasedheatingcosts"): "heatingandcoolingcostsincrease",
        ("energy", "exposuretoenergypoverty"): "strugglingtopaybillseachmonth",
        ("energy", "homelosesmarketvalue"): "housevaluedecreasenosolar",
        ("energy", "loseincomeduetotheproductionofsolarenergy"): "missingoutonsolarsavings",
        (
            "energy",
            "facingpressureorpenaltiesinthefutureifthehomedoesnotmeetnewenergyefficiencystandardsorregulations",
        ): "newtaxesorfinesforinefficiency",
        ("energy", "morefrequentpoweroutages"): "morefrequentpowercuts",
        ("transport", "higherfuelandmaintenancecosts"): "higherfuelrepaircostsice",
        ("transport", "losingresalevalue"): "carlosesresalevalue",
        ("transport", "penaltiesassociatedtopetroldieselcar"): "newtaxesfinesforice",
        (
            "transport",
            "drivingrestrictioninspecificemissionzones",
        ): "restrictedfromtowncitycentreszezrestrictions",
        ("transport", "reducedtravelefficiency"): "longerormorecomplexjourneys",
        ("transport", "exposuretomorepollution"): "morepollutionexposure",
        ("housing", "higherelectricitybills"): "higherelectricitybills",
        ("housing", "increasedheatingcosts"): "heatingandcoolingcostsincrease",
        ("housing", "exposuretoenergypoverty"): "strugglingtopaybillseachmonth",
        ("housing", "homelosesmarketvalue"): "housevaluedecreasenosolar",
        ("housing", "loseincomeduetotheproductionofsolarenergy"): "missingoutonsolarsavings",
        ("housing", "higherhouseinsurancecosts"): "homeinsurancemoreexpensive",
        (
            "housing",
            "facingpressureorpenaltiesinthefutureifthehomedoesnotmeetnewenergyefficiencystandardsorregulations",
        ): "newtaxesorfinesforinefficiency",
        (
            "housing",
            "lawsforbiddingsellingorrentinghouseswithnoretrofittingorrenovations",
        ): "lawsforbidsellingrentingnonrenovated",
        ("housing", "morefrequentpoweroutages"): "morefrequentpowercuts",
        ("housing", "presenceofdampormold"): "homemoredampormould",
        (
            "housing",
            "moreriskedperceivedbyinsurancecompaniesofthehousewithnorenovationorretrofitting",
        ): "insurersclassifyhomeashighrisk",
        ("housing", "strongereffectsofextremeweatherevents"): "increasedsevereweatherimpacts",
        ("housing", "diseasesandhealthproblems"): "colddampleadstohealthproblems",
    }
    hazard_name_key = aliases.get((sector_key, label_key))
    if not hazard_name_key:
        return None
    return sector_key, hazard_name_key


def _read_additional_hazards_csv_rows() -> list[dict[str, str]]:
    if not ADDITIONAL_HAZARDS_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with ADDITIONAL_HAZARDS_CSV_PATH.open(encoding=encoding, newline="") as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with ADDITIONAL_HAZARDS_CSV_PATH.open(
        encoding="utf-8", errors="replace", newline=""
    ) as csv_file:
        return list(csv.DictReader(csv_file))


def _read_additional_hazard_profiles_csv_rows() -> list[dict[str, str]]:
    if not ADDITIONAL_HAZARD_PROFILES_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with ADDITIONAL_HAZARD_PROFILES_CSV_PATH.open(
                encoding=encoding, newline=""
            ) as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with ADDITIONAL_HAZARD_PROFILES_CSV_PATH.open(
        encoding="utf-8", errors="replace", newline=""
    ) as csv_file:
        return list(csv.DictReader(csv_file))


def ensure_additional_hazards() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS additional_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  country_id INT NOT NULL,
                  sector_id INT NOT NULL,
                  name VARCHAR(255) NOT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'csv',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazards_country
                    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
                  CONSTRAINT fk_additional_hazards_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_scope_name
                    UNIQUE (country_id, sector_id, name),
                  INDEX ix_additional_hazards_country_id (country_id),
                  INDEX ix_additional_hazards_sector_id (sector_id),
                  INDEX ix_additional_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        _seed_additional_hazards(connection)
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS additional_hazard_profiles (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  additional_hazard_id INT NOT NULL,
                  profile VARCHAR(255) NOT NULL,
                  evidence TEXT NULL,
                  reference TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'd4_2_pdf',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazard_profiles_hazard
                    FOREIGN KEY (additional_hazard_id)
                    REFERENCES additional_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_profile
                    UNIQUE (additional_hazard_id, profile),
                  INDEX ix_additional_hazard_profiles_hazard_id (additional_hazard_id),
                  INDEX ix_additional_hazard_profiles_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        _seed_additional_hazard_profiles(connection)
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS additional_hazard_profile_target_populations (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  additional_hazard_profile_id INT NOT NULL,
                  question_option_id INT NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazard_profile_target_profile
                    FOREIGN KEY (additional_hazard_profile_id)
                    REFERENCES additional_hazard_profiles(id) ON DELETE CASCADE,
                  CONSTRAINT fk_additional_hazard_profile_target_option
                    FOREIGN KEY (question_option_id)
                    REFERENCES question_options(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_profile_target_option
                    UNIQUE (additional_hazard_profile_id, question_option_id),
                  INDEX ix_additional_hazard_profile_target_profile (additional_hazard_profile_id),
                  INDEX ix_additional_hazard_profile_target_option (question_option_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        _seed_additional_hazard_profile_target_populations(connection)


def _seed_additional_hazards(connection) -> None:
    rows = _read_additional_hazards_csv_rows()
    if not rows:
        return

    country_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM countries")).mappings()
    }
    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }

    connection.execute(text("DELETE FROM additional_hazards WHERE source = 'csv'"))

    inserted = 0
    skipped = 0
    seen: set[tuple[int, int, str]] = set()
    for csv_index, row in enumerate(rows, start=2):
        country_name = (row.get("country") or "").strip()
        sector_name = (row.get("sector") or "").strip()
        hazard_name = (row.get("hazard name") or "").strip()
        country_id = country_by_key.get(_normalize_mitigation_example_key(country_name))
        sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
        hazard_key = _normalize_mitigation_example_key(hazard_name)
        if not country_id or not sector_id or not hazard_name:
            skipped += 1
            continue
        scope_key = (int(country_id), int(sector_id), hazard_key)
        if scope_key in seen:
            skipped += 1
            continue
        seen.add(scope_key)
        connection.execute(
            text(
                """
                INSERT INTO additional_hazards (
                    country_id,
                    sector_id,
                    name,
                    source,
                    csv_row_number
                )
                VALUES (
                    :country_id,
                    :sector_id,
                    :name,
                    'csv',
                    :csv_row_number
                )
                """
            ),
            {
                "country_id": country_id,
                "sector_id": sector_id,
                "name": hazard_name,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s additional hazards from additionalHazards.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_additional_hazard_profiles(connection) -> None:
    rows = _read_additional_hazard_profiles_csv_rows()
    if not rows:
        return

    country_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM countries")).mappings()
    }
    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    hazard_by_scope = {
        (
            int(row["country_id"]),
            int(row["sector_id"]),
            _normalize_mitigation_example_key(row["name"]),
        ): int(row["id"])
        for row in connection.execute(
            text("SELECT id, country_id, sector_id, name FROM additional_hazards")
        ).mappings()
    }

    connection.execute(
        text("DELETE FROM additional_hazard_profiles WHERE source = 'd4_2_pdf'")
    )

    inserted = 0
    skipped = 0
    seen: set[tuple[int, str]] = set()
    for csv_index, row in enumerate(rows, start=2):
        country_id = country_by_key.get(
            _normalize_mitigation_example_key((row.get("country") or "").strip())
        )
        sector_id = sector_by_key.get(
            _normalize_mitigation_example_key((row.get("sector") or "").strip())
        )
        hazard_key = _normalize_mitigation_example_key(
            (row.get("hazard name") or "").strip()
        )
        profile = (row.get("profile") or "").strip()
        if not country_id or not sector_id or not hazard_key or not profile:
            skipped += 1
            continue
        additional_hazard_id = hazard_by_scope.get((int(country_id), int(sector_id), hazard_key))
        if additional_hazard_id is None:
            skipped += 1
            continue
        scope_key = (additional_hazard_id, _normalize_mitigation_example_key(profile))
        if scope_key in seen:
            skipped += 1
            continue
        seen.add(scope_key)
        connection.execute(
            text(
                """
                INSERT INTO additional_hazard_profiles (
                    additional_hazard_id,
                    profile,
                    evidence,
                    reference,
                    source,
                    csv_row_number
                )
                VALUES (
                    :additional_hazard_id,
                    :profile,
                    :evidence,
                    :reference,
                    'd4_2_pdf',
                    :csv_row_number
                )
                """
            ),
            {
                "additional_hazard_id": additional_hazard_id,
                "profile": profile,
                "evidence": (row.get("evidence") or "").strip() or None,
                "reference": (row.get("reference") or "").strip() or None,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s additional hazard profiles from additionalHazardProfiles.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_additional_hazard_profile_target_populations(connection) -> None:
    option_by_key = {
        (
            _normalize_mitigation_example_key(row["question"]),
            _normalize_mitigation_example_key(row["option"]),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT question_options.id, evaluation_questions.question, question_options.`option`
                FROM question_options
                JOIN evaluation_questions
                  ON evaluation_questions.id = question_options.questionId
                WHERE evaluation_questions.category = 'target_population'
                  AND evaluation_questions.active = TRUE
                """
            )
        ).mappings()
    }
    profile_rows = list(
        connection.execute(
            text("SELECT id, profile FROM additional_hazard_profiles")
        ).mappings()
    )
    connection.execute(text("DELETE FROM additional_hazard_profile_target_populations"))

    inserted = 0
    for row in profile_rows:
        option_ids: set[int] = set()
        for question, option in _target_population_pairs_for_profile(str(row["profile"] or "")):
            option_id = option_by_key.get(
                (
                    _normalize_mitigation_example_key(question),
                    _normalize_mitigation_example_key(option),
                )
            )
            if option_id is not None:
                option_ids.add(option_id)
        for option_id in sorted(option_ids):
            connection.execute(
                text(
                    """
                    INSERT INTO additional_hazard_profile_target_populations (
                        additional_hazard_profile_id,
                        question_option_id
                    )
                    VALUES (:profile_id, :option_id)
                    """
                ),
                {"profile_id": int(row["id"]), "option_id": option_id},
            )
            inserted += 1

    logger.info(
        "Mapped %s additional hazard profile target-population option links",
        inserted,
    )


def _target_population_pairs_for_profile(profile: str) -> list[tuple[str, str]]:
    text_key = _normalize_profile_phrase(profile)
    pairs: list[tuple[str, str]] = []

    def add(question: str, option: str) -> None:
        pair = (question, option)
        if pair not in pairs:
            pairs.append(pair)

    if any(
        term in text_key
        for term in (
            "low income",
            "lower income",
            "poorer",
            "financially fragile",
            "financial insecurity",
            "financially vulnerable",
            "energy poor",
            "energy poverty",
            "vulnerable households",
            "disadvantaged groups",
            "poverty",
            "expensive electricity",
            "price fluctuations",
            "upfront retrofit costs",
        )
    ):
        add("Level of income", "Low income")
    if any(term in text_key for term in ("middle income", "middle to low")):
        add("Level of income", "Medium income")
    if any(term in text_key for term in ("higher income", "high income")):
        add("Level of income", "High income")
    if any(term in text_key for term in ("tenant", "renting", "rental", "renters")):
        add("Tenancy status", "Tenant")
    if any(term in text_key for term in ("homeowner", "home owner", "home ownership")):
        add("Tenancy status", "Homeowner")
    if "rural" in text_key or "peripheral" in text_key or "small municipalities" in text_key:
        add("Location of residency", "Rural area")
    if "suburban" in text_key:
        add("Location of residency", "Suburban area")
    if "urban" in text_key and "suburban" not in text_key:
        add("Location of residency", "Urban area")
    if any(term in text_key for term in ("older", "elderly", "seniors", "ageing", "aging")):
        add("Age range", ">65")
    if any(term in text_key for term in ("young", "younger")):
        add("Age range", "25-35")
    if any(term in text_key for term in ("disabil", "reduced mobility", "special needs")):
        add("Disability of long-term condition", "Yes")
    if "women" in text_key:
        add("Gender", "Woman")
    if any(term in text_key for term in ("unemployed", "lost jobs", "lost their jobs")):
        add("Economic status", "Unemployed")
    if any(term in text_key for term in ("workers", "worker", "commuters", "precarious work")):
        add("Economic status", "Employed")
    if any(term in text_key for term in ("car dependent", "car dependency", "commuters")):
        add("Need of a car to perform daily activities", "Yes")
    if "displaced far from employment" in text_key:
        add("Need of a car to perform daily activities", "Yes")
    if any(term in text_key for term in ("public transport users", "public transport dependent")):
        add("Need of a car to perform daily activities", "No")
    if any(term in text_key for term in ("low educated", "low education")):
        add("Level of education", "Primary")
    if any(term in text_key for term in ("limited digital literacy", "low digital literacy", "low tech literacy")):
        add("Level of education", "Primary")
    if any(term in text_key for term in ("migrant", "migrants", "non eu")):
        add("EU citizenship", "No")
    if any(term in text_key for term in ("inefficient homes", "inefficient housing", "inefficient buildings")):
        add("Living in a house with low energy efficiency", "Yes")

    return pairs


def _normalize_profile_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


def _resolve_mitigation_profile_id(
    profile_label: str,
    system_hazard_id: int | None,
    profile_rows: list[dict[str, object]],
) -> int | None:
    if system_hazard_id is None:
        return None

    profile_key = _normalize_mitigation_example_key(profile_label)
    if not profile_key:
        return None

    same_hazard_rows = [
        row for row in profile_rows if row.get("system_hazard_id") == system_hazard_id
    ]
    exact_matches: list[int] = []
    fallback_matches: list[int] = []
    for row in same_hazard_rows:
        row_id = row.get("id")
        if not isinstance(row_id, int):
            continue
        row_keys = {
            _normalize_mitigation_example_key(str(row.get("profile") or "")),
            _normalize_mitigation_example_key(str(row.get("variable_name") or "")),
        }
        if profile_key in row_keys:
            exact_matches.append(row_id)
            continue
        if any(profile_key and profile_key in row_key for row_key in row_keys):
            fallback_matches.append(row_id)

    if exact_matches:
        return exact_matches[0]
    if len(fallback_matches) == 1:
        return fallback_matches[0]
    return None


def _seed_mm_csv_mitigation_measure_examples(connection) -> None:
    rows = _read_mm_csv_rows()
    if not rows:
        return

    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    hazard_by_key = {
        (row["sector_id"], _normalize_mitigation_example_key(row["name"])): row["id"]
        for row in connection.execute(
            text("SELECT id, sector_id, name FROM system_hazards")
        ).mappings()
    }
    profile_rows = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, system_hazard_id, sector_id, variable_name, profile
                FROM system_hazard_socio_demographics
                """
            )
        ).mappings()
    ]

    connection.execute(
        text("DELETE FROM mitigation_measure_examples WHERE source = 'mm_csv'")
    )

    inserted = 0
    skipped = 0
    for csv_index, row in enumerate(rows, start=2):
        sector_name = (row.get("Sector") or "").strip()
        hazard_name = (row.get("Hazard") or "").strip()
        profile_label = (
            row.get("affected predictor / indicator categories") or ""
        ).strip()
        measure = (row.get("Twin-transition mitigation measure") or "").strip()
        sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
        if not sector_id or not measure:
            skipped += 1
            continue

        system_hazard_id = hazard_by_key.get(
            (sector_id, _normalize_mitigation_example_key(hazard_name))
        )
        profile_id = _resolve_mitigation_profile_id(
            profile_label,
            system_hazard_id if isinstance(system_hazard_id, int) else None,
            profile_rows,
        )
        connection.execute(
            text(
                """
                INSERT INTO mitigation_measure_examples (
                    sector_id,
                    system_hazard_id,
                    system_hazard_socio_demographic_id,
                    profile_label,
                    measure,
                    policy_case_study,
                    country_city,
                    implementation_summary,
                    evidence,
                    reference_links,
                    source,
                    csv_row_number
                )
                VALUES (
                    :sector_id,
                    :system_hazard_id,
                    :profile_id,
                    :profile_label,
                    :measure,
                    :policy_case_study,
                    :country_city,
                    :implementation_summary,
                    :evidence,
                    :reference_links,
                    'mm_csv',
                    :csv_row_number
                )
                """
            ),
            {
                "sector_id": sector_id,
                "system_hazard_id": system_hazard_id,
                "profile_id": profile_id,
                "profile_label": profile_label or None,
                "measure": measure,
                "policy_case_study": (
                    row.get("Policy case study across Europe only") or ""
                ).strip()
                or None,
                "country_city": (row.get("Country / city") or "").strip() or None,
                "implementation_summary": (
                    row.get("Policy implementation summary") or ""
                ).strip()
                or None,
                "evidence": (
                    row.get("Evidence of success / why credible") or ""
                ).strip()
                or None,
                "reference_links": (row.get("Reference links") or "").strip()
                or None,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s mitigation measure examples from mm.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_mm_target_group_xlsx(connection) -> None:
    rows = _read_mm_target_group_xlsx_rows()
    if not rows:
        return

    _ensure_mm_target_group_question_options(connection)

    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    country_by_map_code = {
        str(row["map_code"] or "").casefold(): int(row["id"])
        for row in connection.execute(
            text("SELECT id, map_code FROM countries WHERE map_code IS NOT NULL")
        ).mappings()
    }
    option_by_group = _mm_target_group_option_map(connection)

    connection.execute(
        text("DELETE FROM mitigation_measure_target_groups WHERE source = 'xlsx'")
    )
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_additional_hazards WHERE source = 'xlsx'")
    )
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_system_hazards WHERE source = 'xlsx'")
    )
    connection.execute(text("DELETE FROM mitigation_measure_policies WHERE source = 'xlsx'"))

    policy_ids: dict[tuple[str, int | None], int] = {}
    policy_rows: dict[tuple[str, int | None], dict[str, object]] = {}
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        if not policy_code:
            continue
        sector_name = str(row.get("sector_name") or "").strip()
        for sector_id in _mm_target_group_sector_ids(sector_name, sector_by_key):
            policy_key = (policy_code, sector_id)
            if policy_key in policy_rows:
                continue
            policy_rows[policy_key] = {
                "policy_code": policy_code,
                "policy_title": str(row.get("policy_title") or "").strip(),
                "country_id": _mm_policy_country_id(policy_code, country_by_map_code),
                "sector_id": sector_id,
                "policy_type": str(row.get("policy_type") or "").strip() or None,
                "short_description": str(row.get("short_description") or "").strip() or None,
                "source": "xlsx",
                "excel_row_number": row.get("excel_row_number"),
            }
    for policy_row in policy_rows.values():
        result = connection.execute(
            text(
                """
                INSERT INTO mitigation_measure_policies (
                    policy_code,
                    policy_title,
                    country_id,
                    sector_id,
                    policy_type,
                    short_description,
                    source,
                    excel_row_number
                )
                VALUES (
                    :policy_code,
                    :policy_title,
                    :country_id,
                    :sector_id,
                    :policy_type,
                    :short_description,
                    :source,
                    :excel_row_number
                )
                """
            ),
            policy_row,
        )
        policy_ids[
            (str(policy_row["policy_code"]), policy_row.get("sector_id"))
        ] = int(result.lastrowid)

    inserted = 0
    skipped = 0
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        match_value = str(row.get("match_value") or "").strip()
        if match_value.casefold() == "no":
            skipped += 1
            continue
        question_option_id = option_by_group.get(
            (
                _normalize_mitigation_example_key(
                    str(row.get("target_group_category") or "")
                ),
                _normalize_mitigation_example_key(str(row.get("target_group") or "")),
            )
        )
        if question_option_id is None:
            skipped += 1
            continue
        sector_name = str(row.get("sector_name") or "").strip()
        for sector_id in _mm_target_group_sector_ids(sector_name, sector_by_key):
            policy_id = policy_ids.get((policy_code, sector_id))
            if policy_id is None:
                skipped += 1
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO mitigation_measure_target_groups (
                        mitigation_measure_policy_id,
                        question_option_id,
                        match_value,
                        source,
                        excel_column_number
                    )
                    VALUES (
                        :policy_id,
                        :question_option_id,
                        :match_value,
                        'xlsx',
                        :excel_column_number
                    )
                    """
                ),
                {
                    "policy_id": policy_id,
                    "question_option_id": question_option_id,
                    "match_value": match_value or None,
                    "excel_column_number": row.get("excel_column_number"),
                },
            )
            inserted += 1

    logger.info(
        "Loaded %s mitigation policies and %s target-group mappings from "
        "MM Target group.xlsx; skipped %s mappings",
        len(policy_ids),
        inserted,
        skipped,
    )
    _seed_sectoral_challenge_policy_additional_hazards(connection)


def _seed_sectoral_challenge_policy_additional_hazards(connection) -> None:
    rows = _read_sectoral_challenges_xlsx_rows()
    if not rows:
        return

    policy_ids_by_code_country: dict[tuple[str, int], list[int]] = {}
    for row in connection.execute(
        text(
            """
            SELECT id, policy_code, country_id
            FROM mitigation_measure_policies
            WHERE source = 'xlsx'
              AND country_id IS NOT NULL
            """
        )
    ).mappings():
        policy_ids_by_code_country.setdefault(
            (str(row["policy_code"]), int(row["country_id"])),
            [],
        ).append(int(row["id"]))
    hazard_by_country_name = {
        (
            int(row["country_id"]),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT id, country_id, name
                FROM additional_hazards
                """
            )
        ).mappings()
    }
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_additional_hazards WHERE source = 'xlsx'")
    )

    inserted = 0
    skipped = 0
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        match_value = str(row.get("match_value") or "").strip()
        if match_value.casefold() == "not addressed":
            skipped += 1
            continue
        hazard_key = _normalize_mitigation_example_key(
            str(row.get("additional_hazard") or "")
        )
        inserted_for_cell = False
        for country_id in {
            country_id
            for stored_policy_code, country_id in policy_ids_by_code_country
            if stored_policy_code == policy_code
        }:
            additional_hazard_id = hazard_by_country_name.get((country_id, hazard_key))
            if additional_hazard_id is None:
                continue
            for policy_id in policy_ids_by_code_country.get((policy_code, country_id), []):
                connection.execute(
                    text(
                        """
                        INSERT INTO mitigation_measure_policy_additional_hazards (
                            mitigation_measure_policy_id,
                            additional_hazard_id,
                            match_value,
                            source,
                            excel_row_number,
                            excel_column_number
                        )
                        VALUES (
                            :policy_id,
                            :additional_hazard_id,
                            :match_value,
                            'xlsx',
                            :excel_row_number,
                            :excel_column_number
                        )
                        """
                    ),
                    {
                        "policy_id": policy_id,
                        "additional_hazard_id": additional_hazard_id,
                        "match_value": match_value or None,
                        "excel_row_number": row.get("excel_row_number"),
                        "excel_column_number": row.get("excel_column_number"),
                    },
                )
                inserted += 1
                inserted_for_cell = True
        if not inserted_for_cell:
            skipped += 1

    logger.info(
        "Loaded %s mitigation-policy additional-hazard mappings from "
        "sectoral_challenges.xlsx; skipped %s challenge cells",
        inserted,
        skipped,
    )


def _seed_hazards_xlsx_policy_system_hazards(connection) -> None:
    rows = _read_hazards_xlsx_rows()
    if not rows:
        return

    policy_ids_by_code_country: dict[tuple[str, int], list[int]] = {}
    for row in connection.execute(
        text(
            """
            SELECT id, policy_code, country_id
            FROM mitigation_measure_policies
            WHERE source = 'xlsx'
              AND country_id IS NOT NULL
            """
        )
    ).mappings():
        policy_ids_by_code_country.setdefault(
            (str(row["policy_code"]), int(row["country_id"])),
            [],
        ).append(int(row["id"]))

    hazard_by_sector_name = {
        (
            _normalize_mitigation_example_key(str(row["sector_name"] or "")),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT system_hazards.id, sectors.name AS sector_name, system_hazards.name
                FROM system_hazards
                JOIN sectors ON sectors.id = system_hazards.sector_id
                """
            )
        ).mappings()
    }

    connection.execute(
        text("DELETE FROM mitigation_measure_policy_system_hazards WHERE source = 'xlsx'")
    )

    inserted = 0
    skipped = 0
    for row in rows:
        mitigation_effect = str(row.get("mitigation_effect") or "").strip()
        if not mitigation_effect or mitigation_effect.casefold() == "not applicable":
            skipped += 1
            continue

        hazard_lookup_key = _hazards_xlsx_system_hazard_lookup_key(
            str(row.get("hazard_sector") or ""),
            str(row.get("hazard_label") or ""),
        )
        if hazard_lookup_key is None:
            skipped += 1
            continue

        system_hazard_id = hazard_by_sector_name.get(hazard_lookup_key)
        if system_hazard_id is None:
            skipped += 1
            continue

        policy_code = str(row.get("policy_code") or "").strip()
        inserted_for_cell = False
        for country_id in {
            country_id
            for stored_policy_code, country_id in policy_ids_by_code_country
            if stored_policy_code == policy_code
        }:
            for policy_id in policy_ids_by_code_country.get((policy_code, country_id), []):
                connection.execute(
                    text(
                        """
                        INSERT INTO mitigation_measure_policy_system_hazards (
                            mitigation_measure_policy_id,
                            system_hazard_id,
                            mitigation_effect,
                            source,
                            excel_row_number,
                            excel_column_number
                        )
                        VALUES (
                            :policy_id,
                            :system_hazard_id,
                            :mitigation_effect,
                            'xlsx',
                            :excel_row_number,
                            :excel_column_number
                        )
                        ON DUPLICATE KEY UPDATE
                            mitigation_effect = VALUES(mitigation_effect),
                            excel_row_number = VALUES(excel_row_number),
                            excel_column_number = VALUES(excel_column_number)
                        """
                    ),
                    {
                        "policy_id": policy_id,
                        "system_hazard_id": system_hazard_id,
                        "mitigation_effect": mitigation_effect,
                        "excel_row_number": row.get("excel_row_number"),
                        "excel_column_number": row.get("excel_column_number"),
                    },
                )
                inserted += 1
                inserted_for_cell = True
        if not inserted_for_cell:
            skipped += 1

    logger.info(
        "Loaded %s mitigation-policy system-hazard effect mappings from "
        "hazards.xlsx; skipped %s hazard cells",
        inserted,
        skipped,
    )


def _ensure_hazards_xlsx_policy_system_hazards(connection) -> None:
    table_exists = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = 'mitigation_measure_policy_system_hazards'
            """
        )
    ).scalar()
    if not table_exists:
        return
    existing = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM mitigation_measure_policy_system_hazards
            WHERE source = 'xlsx'
            """
        )
    ).scalar()
    if int(existing or 0) > 0:
        return
    _seed_hazards_xlsx_policy_system_hazards(connection)


def _mm_target_group_sector_ids(
    sector_name: str,
    sector_by_key: dict[str, int],
) -> list[int | None]:
    exact_sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
    if exact_sector_id is not None:
        return [int(exact_sector_id)]

    normalized = _normalize_mitigation_example_key(sector_name)
    sector_ids: list[int] = []
    for sector_label, sector_id in sector_by_key.items():
        if sector_label and sector_label in normalized:
            sector_ids.append(int(sector_id))
    if sector_ids:
        return sorted(set(sector_ids))
    return [None]


def _mm_policy_country_id(
    policy_code: str,
    country_by_map_code: dict[str, int],
) -> int | None:
    prefix = str(policy_code or "").split("_", 1)[0].strip().casefold()
    if prefix == "h":
        prefix = "hu"
    return country_by_map_code.get(prefix)


def _ensure_mm_target_group_question_options(connection) -> None:
    age_question_id = connection.execute(
        text(
            """
            SELECT id
            FROM evaluation_questions
            WHERE category = 'target_population'
              AND question = 'Age range'
            LIMIT 1
            """
        )
    ).scalar()
    if age_question_id is None:
        return
    existing = connection.execute(
        text(
            """
            SELECT id
            FROM question_options
            WHERE questionId = :question_id
              AND `option` = '18-25'
            LIMIT 1
            """
        ),
        {"question_id": int(age_question_id)},
    ).scalar()
    if existing is None:
        connection.execute(
            text(
                """
                INSERT INTO question_options (questionId, `option`)
                VALUES (:question_id, '18-25')
                """
            ),
            {"question_id": int(age_question_id)},
        )


def _mm_target_group_option_map(connection) -> dict[tuple[str, str], int]:
    rows = connection.execute(
        text(
            """
            SELECT evaluation_questions.question, question_options.`option`, question_options.id
            FROM question_options
            JOIN evaluation_questions
              ON evaluation_questions.id = question_options.questionId
            WHERE evaluation_questions.category = 'target_population'
              AND evaluation_questions.active = TRUE
            """
        )
    ).mappings()
    option_by_key = {
        (
            _normalize_mitigation_example_key(str(row["question"] or "")),
            _normalize_mitigation_example_key(str(row["option"] or "")),
        ): int(row["id"])
        for row in rows
    }
    aliases: dict[tuple[str, str], tuple[str, str]] = {
        ("livinginlowenergyefficiencyhome", "livesinlowefficiencyhome"): (
            "livinginahousewithlowenergyefficiency",
            "yes",
        ),
        ("livinginlowenergyefficiencyhome", "livesinefficienthome"): (
            "livinginahousewithlowenergyefficiency",
            "no",
        ),
        ("needsacarfordailyactivities", "cardependent"): (
            "needofacartoperformdailyactivities",
            "yes",
        ),
        ("needsacarfordailyactivities", "notcardependent"): (
            "needofacartoperformdailyactivities",
            "no",
        ),
        ("eucitizenship", "eucitizen"): ("eucitizenship", "yes"),
        ("eucitizenship", "noneucitizen"): ("eucitizenship", "no"),
        ("disabilityorlongtermcondition", "hasdisabilitycondition"): (
            "disabilityoflongtermcondition",
            "yes",
        ),
        ("disabilityorlongtermcondition", "nodisabilitycondition"): (
            "disabilityoflongtermcondition",
            "no",
        ),
        ("levelofincome", "low"): ("levelofincome", "lowincome"),
        ("levelofincome", "medium"): ("levelofincome", "mediumincome"),
        ("levelofincome", "high"): ("levelofincome", "highincome"),
        ("levelofeducation", "furtherformaleducation"): (
            "levelofeducation",
            "furthernormaleducation",
        ),
        ("careresponsibilitymainactivity", "yesnonremunerated"): (
            "careresponsibilityasthemainactivity",
            "yesnonremunerated",
        ),
        ("careresponsibilitymainactivity", "yesremunerated"): (
            "careresponsibilityasthemainactivity",
            "yesremunerated",
        ),
        ("careresponsibilitymainactivity", "no"): (
            "careresponsibilityasthemainactivity",
            "no",
        ),
    }
    mapped = dict(option_by_key)
    for source_key, target_key in aliases.items():
        if target_key in option_by_key:
            mapped[source_key] = option_by_key[target_key]
    return mapped


def ensure_mitigation_measure_examples() -> None:
    inspector = inspect(engine)
    if "mitigation_measure_target_groups" in inspector.get_table_names():
        target_group_columns = {
            column["name"]
            for column in inspector.get_columns("mitigation_measure_target_groups")
        }
        if "mitigation_measure_policy_id" not in target_group_columns:
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE mitigation_measure_target_groups"))

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_examples (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  sector_id INT NOT NULL,
                  system_hazard_id INT NULL,
                  system_hazard_socio_demographic_id INT NULL,
                  profile_label VARCHAR(255) NULL,
                  measure TEXT NOT NULL,
                  policy_case_study TEXT NULL,
                  country_city VARCHAR(255) NULL,
                  implementation_summary TEXT NULL,
                  evidence TEXT NULL,
                  reference_links TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'seed',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_examples_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_examples_hazard
                    FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL,
                  CONSTRAINT fk_mitigation_examples_profile
                    FOREIGN KEY (system_hazard_socio_demographic_id)
                    REFERENCES system_hazard_socio_demographics(id) ON DELETE SET NULL,
                  INDEX ix_mitigation_measure_examples_sector_id (sector_id),
                  INDEX ix_mitigation_measure_examples_hazard_id (system_hazard_id),
                  INDEX ix_mitigation_measure_examples_profile_id (system_hazard_socio_demographic_id),
                  INDEX ix_mitigation_measure_examples_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policies (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  policy_code VARCHAR(80) NOT NULL,
                  policy_title TEXT NOT NULL,
                  country_id INT NULL,
                  sector_id INT NULL,
                  policy_type VARCHAR(120) NULL,
                  short_description TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policies_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
                  CONSTRAINT fk_mitigation_policies_country
                    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
                  CONSTRAINT uq_mitigation_policy_code_sector_source UNIQUE (policy_code, sector_id, source),
                  INDEX ix_mitigation_policies_policy_code (policy_code),
                  INDEX ix_mitigation_policies_country_id (country_id),
                  INDEX ix_mitigation_policies_sector_id (sector_id),
                  INDEX ix_mitigation_policies_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_target_groups (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  question_option_id INT NOT NULL,
                  match_value VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_target_groups_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_target_groups_option
                    FOREIGN KEY (question_option_id)
                    REFERENCES question_options(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_target_group_xlsx_cell
                    UNIQUE (mitigation_measure_policy_id, question_option_id),
                  INDEX ix_mitigation_target_groups_policy_id (mitigation_measure_policy_id),
                  INDEX ix_mitigation_target_groups_option_id (question_option_id),
                  INDEX ix_mitigation_target_groups_match_value (match_value),
                  INDEX ix_mitigation_target_groups_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policy_additional_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  additional_hazard_id INT NOT NULL,
                  match_value VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policy_hazards_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_policy_hazards_additional_hazard
                    FOREIGN KEY (additional_hazard_id)
                    REFERENCES additional_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_policy_additional_hazard
                    UNIQUE (mitigation_measure_policy_id, additional_hazard_id),
                  INDEX ix_mitigation_policy_hazards_policy_id (mitigation_measure_policy_id),
                  INDEX ix_mitigation_policy_hazards_additional_hazard_id (additional_hazard_id),
                  INDEX ix_mitigation_policy_hazards_match_value (match_value),
                  INDEX ix_mitigation_policy_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policy_system_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  system_hazard_id INT NOT NULL,
                  mitigation_effect VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policy_system_hazards_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_policy_system_hazards_hazard
                    FOREIGN KEY (system_hazard_id)
                    REFERENCES system_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_policy_system_hazard
                    UNIQUE (mitigation_measure_policy_id, system_hazard_id),
                  INDEX ix_mitigation_policy_system_hazards_policy_id
                    (mitigation_measure_policy_id),
                  INDEX ix_mitigation_policy_system_hazards_hazard_id
                    (system_hazard_id),
                  INDEX ix_mitigation_policy_system_hazards_effect (mitigation_effect),
                  INDEX ix_mitigation_policy_system_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

    inspector = inspect(engine)
    policy_columns = {
        column["name"]
        for column in inspector.get_columns("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else set()
    policy_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else {}
    policy_foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else set()
    with engine.begin() as connection:
        if "country_id" not in policy_columns:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD COLUMN country_id INT NULL AFTER policy_title"
                )
            )
            policy_columns.add("country_id")
        connection.execute(
            text(
                """
                UPDATE mitigation_measure_policies policies
                JOIN countries
                  ON LOWER(countries.map_code) = LOWER(SUBSTRING_INDEX(policies.policy_code, '_', 1))
                SET policies.country_id = countries.id
                WHERE policies.country_id IS NULL
                """
            )
        )
        if "ix_mitigation_policies_country_id" not in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD INDEX ix_mitigation_policies_country_id (country_id)"
                )
            )
            policy_indexes["ix_mitigation_policies_country_id"] = {}
        if "fk_mitigation_policies_country" not in policy_foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_policies "
                        "ADD CONSTRAINT fk_mitigation_policies_country "
                        "FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation policy country foreign key; continuing",
                    exc_info=True,
                )
        if "uq_mitigation_policy_code_source" in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "DROP INDEX uq_mitigation_policy_code_source"
                )
            )
            policy_indexes.pop("uq_mitigation_policy_code_source", None)
        if "uq_mitigation_policy_code_sector_source" not in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD CONSTRAINT uq_mitigation_policy_code_sector_source "
                    "UNIQUE (policy_code, sector_id, source)"
                )
            )

    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("mitigation_measure_examples")
    }
    indexes = {
        index["name"]
        for index in inspector.get_indexes("mitigation_measure_examples")
    }
    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("mitigation_measure_examples")
    }

    with engine.begin() as connection:
        if "sector_id" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD COLUMN sector_id INT NULL AFTER id"
                )
            )
            columns.add("sector_id")
        new_columns = {
            "system_hazard_id": "INT NULL AFTER sector_id",
            "system_hazard_socio_demographic_id": "INT NULL AFTER system_hazard_id",
            "profile_label": "VARCHAR(255) NULL AFTER system_hazard_socio_demographic_id",
            "policy_case_study": "TEXT NULL AFTER measure",
            "country_city": "VARCHAR(255) NULL AFTER policy_case_study",
            "implementation_summary": "TEXT NULL AFTER country_city",
            "evidence": "TEXT NULL AFTER implementation_summary",
            "reference_links": "TEXT NULL AFTER evidence",
            "source": "VARCHAR(40) NOT NULL DEFAULT 'seed' AFTER reference_links",
            "csv_row_number": "INT NULL AFTER source",
        }
        for column_name, column_definition in new_columns.items():
            if column_name not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
                )
                columns.add(column_name)
        if "sector_name" in columns:
            connection.execute(
                text(
                    """
                    UPDATE mitigation_measure_examples examples
                    JOIN sectors ON LOWER(sectors.name) = LOWER(examples.sector_name)
                    SET examples.sector_id = sectors.id
                    WHERE examples.sector_id IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    DELETE newer
                    FROM mitigation_measure_examples newer
                    JOIN mitigation_measure_examples older
                      ON newer.id > older.id
                     AND newer.sector_id = older.sector_id
                     AND newer.measure = older.measure
                    WHERE newer.sector_id IS NOT NULL
                    """
                )
            )
            if "uq_mitigation_example_sector_measure" in indexes:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP INDEX uq_mitigation_example_sector_measure"
                    )
                )
                indexes.remove("uq_mitigation_example_sector_measure")
            if "ix_mitigation_measure_examples_sector_name" in indexes:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP INDEX ix_mitigation_measure_examples_sector_name"
                    )
                )
                indexes.remove("ix_mitigation_measure_examples_sector_name")
            connection.execute(
                text(
                    "DELETE FROM mitigation_measure_examples "
                    "WHERE sector_id IS NULL"
                )
            )
            if "fk_mitigation_examples_sector" in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP FOREIGN KEY fk_mitigation_examples_sector"
                    )
                )
                foreign_keys.remove("fk_mitigation_examples_sector")
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "MODIFY COLUMN sector_id INT NOT NULL"
                )
            )
            connection.execute(
                text("ALTER TABLE mitigation_measure_examples DROP COLUMN sector_name")
            )
            columns.remove("sector_name")
        if "ix_mitigation_measure_examples_sector_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_sector_id (sector_id)"
                )
            )
        if "uq_mitigation_example_sector_measure" in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "DROP INDEX uq_mitigation_example_sector_measure"
                )
            )
            indexes.remove("uq_mitigation_example_sector_measure")
        if "ix_mitigation_measure_examples_hazard_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_hazard_id (system_hazard_id)"
                )
            )
        if "ix_mitigation_measure_examples_profile_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_profile_id "
                    "(system_hazard_socio_demographic_id)"
                )
            )
        if "ix_mitigation_measure_examples_source" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_source (source)"
                )
            )
        if "fk_mitigation_examples_sector" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_sector "
                        "FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example sector foreign key; continuing",
                    exc_info=True,
                )
        if "fk_mitigation_examples_hazard" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_hazard "
                        "FOREIGN KEY (system_hazard_id) "
                        "REFERENCES system_hazards(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example hazard foreign key; continuing",
                    exc_info=True,
                )
        if "fk_mitigation_examples_profile" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_profile "
                        "FOREIGN KEY (system_hazard_socio_demographic_id) "
                        "REFERENCES system_hazard_socio_demographics(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example profile foreign key; continuing",
                    exc_info=True,
                )

        _seed_mm_csv_mitigation_measure_examples(connection)
        _seed_mm_target_group_xlsx(connection)
        _seed_hazards_xlsx_policy_system_hazards(connection)
