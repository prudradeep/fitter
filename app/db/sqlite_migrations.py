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


def _009_custom_hazard_summary(connection: Connection) -> None:
    _add_column(connection, "custom_hazards", "summary", "summary TEXT NULL")


def _010_policy_reference_custom_hazard(connection: Connection) -> None:
    _add_column(
        connection,
        "knowledge_documents",
        "custom_hazard_id",
        "custom_hazard_id CHAR(36) NULL REFERENCES custom_hazards(id) ON DELETE SET NULL",
    )
    _create_index(
        connection,
        "knowledge_documents",
        "ix_knowledge_documents_custom_hazard_id",
        "custom_hazard_id",
    )


def _011_custom_hazard_policy_references(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS custom_hazard_policy_references (
          custom_hazard_id CHAR(36) NOT NULL,
          knowledge_document_id CHAR(36) NOT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (custom_hazard_id, knowledge_document_id),
          FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE CASCADE,
          FOREIGN KEY (knowledge_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """))
    _create_index(
        connection,
        "custom_hazard_policy_references",
        "ix_custom_hazard_policy_references_document_id",
        "knowledge_document_id",
    )
    connection.execute(text("""
        INSERT OR IGNORE INTO custom_hazard_policy_references (
          custom_hazard_id,
          knowledge_document_id
        )
        SELECT custom_hazard_id, id
        FROM knowledge_documents
        WHERE custom_hazard_id IS NOT NULL
          AND scope = 'policy_reference'
    """))


def _012_mitigation_creation_details(connection: Connection) -> None:
    _add_column(
        connection,
        "user_mitigation_measures",
        "creation_details_json",
        "creation_details_json TEXT NULL",
    )


def _013_custom_hazard_policy_link(connection: Connection) -> None:
    _add_column(
        connection,
        "custom_hazards",
        "mitigation_measure_policy_id",
        "mitigation_measure_policy_id CHAR(36) NULL REFERENCES mitigation_measure_policies(id) ON DELETE SET NULL",
    )
    _create_index(
        connection,
        "custom_hazards",
        "ix_custom_hazards_mitigation_measure_policy_id",
        "mitigation_measure_policy_id",
    )


def _014_policy_knowledge_documents(connection: Connection) -> None:
    _add_column(
        connection,
        "knowledge_documents",
        "mitigation_measure_policy_id",
        "mitigation_measure_policy_id CHAR(36) NULL REFERENCES mitigation_measure_policies(id) ON DELETE SET NULL",
    )
    _create_index(
        connection,
        "knowledge_documents",
        "ix_knowledge_documents_mitigation_measure_policy_id",
        "mitigation_measure_policy_id",
    )


def _015_policies(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS policies (
          id CHAR(36) PRIMARY KEY,
          country_id CHAR(36) NOT NULL,
          sector_id CHAR(36) NOT NULL,
          policy TEXT NOT NULL,
          policy_url TEXT NULL,
          language VARCHAR(120) NULL,
          policy_type VARCHAR(120) NULL,
          source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
          excel_row_number INTEGER NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
          FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE
        )
    """))
    for name, cols in (
        ("ix_policies_country_id", "country_id"),
        ("ix_policies_sector_id", "sector_id"),
        ("ix_policies_source", "source"),
    ):
        _create_index(connection, "policies", name, cols)


def _016_policy_context_links(connection: Connection) -> None:
    """Link policy-context records to the canonical policies table."""
    _add_column(
        connection,
        "custom_hazards",
        "policy_id",
        "policy_id CHAR(36) NULL REFERENCES policies(id) ON DELETE SET NULL",
    )
    _create_index(connection, "custom_hazards", "ix_custom_hazards_policy_id", "policy_id")
    _add_column(
        connection,
        "knowledge_documents",
        "policy_id",
        "policy_id CHAR(36) NULL REFERENCES policies(id) ON DELETE SET NULL",
    )
    _create_index(connection, "knowledge_documents", "ix_knowledge_documents_policy_id", "policy_id")
    # Preserve legacy links only when the legacy title exactly identifies one
    # canonical policy in the same country and sector. Ambiguous links remain
    # unset and can be reattached by an administrator.
    if (
        _table_exists(connection, "mitigation_measure_policies")
        and "mitigation_measure_policy_id" in _columns(connection, "knowledge_documents")
        and "mitigation_measure_policy_id" in _columns(connection, "custom_hazards")
    ):
        connection.execute(text("""
            UPDATE knowledge_documents
            SET policy_id = (
                SELECT policies.id
                FROM mitigation_measure_policies legacy
                JOIN policies
                  ON policies.country_id = legacy.country_id
                 AND policies.sector_id = legacy.sector_id
                 AND LOWER(TRIM(policies.policy)) = LOWER(TRIM(legacy.policy_title))
                WHERE legacy.id = knowledge_documents.mitigation_measure_policy_id
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM policies candidates
                      WHERE candidates.country_id = legacy.country_id
                        AND candidates.sector_id = legacy.sector_id
                        AND LOWER(TRIM(candidates.policy)) = LOWER(TRIM(legacy.policy_title))
                  )
                LIMIT 1
            )
            WHERE policy_id IS NULL
              AND mitigation_measure_policy_id IS NOT NULL
        """))
        connection.execute(text("""
            UPDATE custom_hazards
            SET policy_id = (
                SELECT policies.id
                FROM mitigation_measure_policies legacy
                JOIN policies
                  ON policies.country_id = legacy.country_id
                 AND policies.sector_id = legacy.sector_id
                 AND LOWER(TRIM(policies.policy)) = LOWER(TRIM(legacy.policy_title))
                WHERE legacy.id = custom_hazards.mitigation_measure_policy_id
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM policies candidates
                      WHERE candidates.country_id = legacy.country_id
                        AND candidates.sector_id = legacy.sector_id
                        AND LOWER(TRIM(candidates.policy)) = LOWER(TRIM(legacy.policy_title))
                  )
                LIMIT 1
            )
            WHERE policy_id IS NULL
              AND mitigation_measure_policy_id IS NOT NULL
        """))


def _017_faiss_index_flags(connection: Connection) -> None:
    _add_column(connection, "knowledge_documents", "faiss_indexed", "faiss_indexed INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "knowledge_chunks", "faiss_indexed", "faiss_indexed INTEGER NOT NULL DEFAULT 0")


def _018_crowd_sourced_policies(connection: Connection) -> None:
    _add_column(
        connection, "policies", "created_by_user_id",
        "created_by_user_id CHAR(36) NULL REFERENCES app_users(id) ON DELETE SET NULL",
    )
    _add_column(
        connection, "policies", "is_crowd_sourced",
        "is_crowd_sourced BOOLEAN NOT NULL DEFAULT 0",
    )
    _create_index(connection, "policies", "ix_policies_created_by_user_id", "created_by_user_id")


def _019_policy_hazard_links(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS policy_hazard_links (
            id VARCHAR(36) PRIMARY KEY,
            policy_id VARCHAR(36) NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
            system_hazard_id VARCHAR(36) REFERENCES system_hazards(id) ON DELETE CASCADE,
            additional_hazard_id VARCHAR(36) REFERENCES additional_hazards(id) ON DELETE CASCADE,
            rationale TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_policy_system_hazard UNIQUE (policy_id, system_hazard_id),
            CONSTRAINT uq_policy_additional_hazard UNIQUE (policy_id, additional_hazard_id),
            CONSTRAINT chk_policy_hazard_target CHECK (
                (system_hazard_id IS NULL) <> (additional_hazard_id IS NULL)
            )
        )
    """))
    for column in ("policy_id", "system_hazard_id", "additional_hazard_id"):
        _create_index(connection, "policy_hazard_links", f"ix_policy_hazard_links_{column}", column)


def _020_unique_policy_titles(connection: Connection) -> None:
    """Merge duplicate displayed policy titles and enforce country/sector/title uniqueness."""
    if not _table_exists(connection, "policies"):
        return
    connection.execute(text("""
        CREATE TEMP TABLE policy_title_duplicates AS
        SELECT id AS duplicate_id, canonical_id
        FROM (
          SELECT id,
                 FIRST_VALUE(id) OVER (
                   PARTITION BY country_id, sector_id,
                     LOWER(TRIM(CASE WHEN INSTR(policy, CHAR(10)) > 0
                       THEN SUBSTR(policy, 1, INSTR(policy, CHAR(10)) - 1)
                       ELSE policy END))
                   ORDER BY LENGTH(policy) DESC, created_at, id
                 ) AS canonical_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY country_id, sector_id,
                     LOWER(TRIM(CASE WHEN INSTR(policy, CHAR(10)) > 0
                       THEN SUBSTR(policy, 1, INSTR(policy, CHAR(10)) - 1)
                       ELSE policy END))
                   ORDER BY LENGTH(policy) DESC, created_at, id
                 ) AS row_num
          FROM policies
        ) ranked
        WHERE row_num > 1
    """))
    tables = {row[0] for row in connection.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )).all()}
    for table in ("knowledge_documents", "custom_hazards"):
        if table in tables:
            connection.execute(text(f"""
                UPDATE {table}
                SET policy_id = (SELECT canonical_id FROM policy_title_duplicates
                                 WHERE duplicate_id = {table}.policy_id)
                WHERE policy_id IN (SELECT duplicate_id FROM policy_title_duplicates)
            """))
    if "policy_hazard_links" in tables:
        connection.execute(text("""
            DELETE FROM policy_hazard_links
            WHERE policy_id IN (SELECT duplicate_id FROM policy_title_duplicates)
              AND EXISTS (
                SELECT 1 FROM policy_title_duplicates d
                JOIN policy_hazard_links existing ON existing.policy_id = d.canonical_id
                WHERE d.duplicate_id = policy_hazard_links.policy_id
                  AND existing.system_hazard_id IS policy_hazard_links.system_hazard_id
                  AND existing.additional_hazard_id IS policy_hazard_links.additional_hazard_id
              )
        """))
        connection.execute(text("""
            UPDATE policy_hazard_links
            SET policy_id = (SELECT canonical_id FROM policy_title_duplicates
                             WHERE duplicate_id = policy_hazard_links.policy_id)
            WHERE policy_id IN (SELECT duplicate_id FROM policy_title_duplicates)
        """))
    connection.execute(text("""
        DELETE FROM policies
        WHERE id IN (SELECT duplicate_id FROM policy_title_duplicates)
    """))
    connection.execute(text("DROP TABLE policy_title_duplicates"))
    connection.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_policies_country_sector_title
        ON policies(country_id, sector_id,
          LOWER(TRIM(CASE WHEN INSTR(policy, CHAR(10)) > 0
            THEN SUBSTR(policy, 1, INSTR(policy, CHAR(10)) - 1)
            ELSE policy END)))
    """))


def _021_original_policies(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policies (
            id VARCHAR(36) PRIMARY KEY,
            country_id VARCHAR(36) NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            policy_name VARCHAR(512) NOT NULL UNIQUE,
            objective TEXT,
            policy_document TEXT,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    _create_index(connection, "original_policies", "ix_original_policies_country", "country_id")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_sectors (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            sector_id VARCHAR(36) NOT NULL REFERENCES sectors(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_sector UNIQUE (original_policy_id, sector_id)
        )
    """))
    _create_index(connection, "original_policy_sectors", "ix_original_policy_sectors_policy", "original_policy_id")
    _create_index(connection, "original_policy_sectors", "ix_original_policy_sectors_sector", "sector_id")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_codes (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            code VARCHAR(80) NOT NULL,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_code UNIQUE (original_policy_id, code)
        )
    """))
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_system_hazard (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_code_id VARCHAR(36) NOT NULL REFERENCES original_policy_codes(id) ON DELETE CASCADE,
            system_hazard_id VARCHAR(36) NOT NULL REFERENCES system_hazards(id) ON DELETE CASCADE,
            relationship VARCHAR(40) NOT NULL,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_code_hazard_relationship
                UNIQUE (original_policy_code_id, system_hazard_id, relationship)
        )
    """))
    _create_index(connection, "original_policy_codes", "ix_original_policy_codes_policy", "original_policy_id")
    _create_index(connection, "original_policy_system_hazard", "ix_original_policy_system_hazard_code", "original_policy_code_id")
    _create_index(connection, "original_policy_system_hazard", "ix_original_policy_system_hazard_hazard", "system_hazard_id")


def _022_unique_original_policy_names(connection: Connection) -> None:
    """Move source codes into their own table without losing hazard links."""
    if "original_policy_code_id" in _columns(connection, "original_policy_system_hazard"):
        return
    policies = connection.execute(text("""
        SELECT id, code, policy_name, objective, policy_document, excel_row_number, created_at
        FROM original_policies
        ORDER BY excel_row_number, id
    """)).mappings().all()
    links = connection.execute(text("""
        SELECT id, original_policy_id, system_hazard_id, relationship, excel_row_number, created_at
        FROM original_policy_system_hazard
    """)).mappings().all()
    canonical = {}
    for policy in policies:
        canonical.setdefault(str(policy["policy_name"]), policy)
    connection.execute(text("DROP TABLE IF EXISTS original_policy_links_backup"))
    connection.execute(text("DROP TABLE IF EXISTS original_policy_system_hazard_new"))
    connection.execute(text("DROP TABLE IF EXISTS original_policy_codes_new"))
    connection.execute(text("DROP TABLE IF EXISTS original_policies_new"))
    connection.execute(text("""
        CREATE TABLE original_policies_new (
            id VARCHAR(36) PRIMARY KEY,
            policy_name VARCHAR(512) NOT NULL UNIQUE,
            objective TEXT,
            policy_document TEXT,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    if canonical:
        connection.execute(text("""
            INSERT INTO original_policies_new
                (id, policy_name, objective, policy_document, excel_row_number, created_at)
            VALUES (:id, :policy_name, :objective, :policy_document, :excel_row_number, :created_at)
        """), [dict(policy) for policy in canonical.values()])
    connection.execute(text("""
        CREATE TABLE original_policy_codes_new (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies_new(id) ON DELETE CASCADE,
            code VARCHAR(80) NOT NULL,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_code UNIQUE (original_policy_id, code)
        )
    """))
    if policies:
        connection.execute(text("""
            INSERT INTO original_policy_codes_new
                (id, original_policy_id, code, excel_row_number, created_at)
            VALUES (:id, :original_policy_id, :code, :excel_row_number, :created_at)
        """), [{
            "id": policy["id"],
            "original_policy_id": canonical[str(policy["policy_name"])]["id"],
            "code": policy["code"],
            "excel_row_number": policy["excel_row_number"],
            "created_at": policy["created_at"],
        } for policy in policies])
    connection.execute(text("""
        CREATE TABLE original_policy_system_hazard_new (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_code_id VARCHAR(36) NOT NULL REFERENCES original_policy_codes_new(id) ON DELETE CASCADE,
            system_hazard_id VARCHAR(36) NOT NULL REFERENCES system_hazards(id) ON DELETE CASCADE,
            relationship VARCHAR(40) NOT NULL,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_code_hazard_relationship
                UNIQUE (original_policy_code_id, system_hazard_id, relationship)
        )
    """))
    if links:
        connection.execute(text("""
            INSERT INTO original_policy_system_hazard_new
                (id, original_policy_code_id, system_hazard_id, relationship, excel_row_number, created_at)
            VALUES (:id, :original_policy_code_id, :system_hazard_id, :relationship, :excel_row_number, :created_at)
        """), [{
            "id": link["id"],
            "original_policy_code_id": link["original_policy_id"],
            "system_hazard_id": link["system_hazard_id"],
            "relationship": link["relationship"],
            "excel_row_number": link["excel_row_number"],
            "created_at": link["created_at"],
        } for link in links])
    connection.execute(text("DROP TABLE original_policy_system_hazard"))
    connection.execute(text("DROP TABLE original_policies"))
    connection.execute(text("ALTER TABLE original_policies_new RENAME TO original_policies"))
    connection.execute(text("ALTER TABLE original_policy_codes_new RENAME TO original_policy_codes"))
    connection.execute(text("ALTER TABLE original_policy_system_hazard_new RENAME TO original_policy_system_hazard"))
    _create_index(connection, "original_policy_codes", "ix_original_policy_codes_policy", "original_policy_id")
    _create_index(connection, "original_policy_system_hazard", "ix_original_policy_system_hazard_code", "original_policy_code_id")
    _create_index(connection, "original_policy_system_hazard", "ix_original_policy_system_hazard_hazard", "system_hazard_id")


def _023_original_policy_country_sectors(connection: Connection) -> None:
    _add_column(
        connection, "original_policies", "country_id",
        "country_id VARCHAR(36) NULL REFERENCES countries(id) ON DELETE CASCADE",
    )
    _create_index(connection, "original_policies", "ix_original_policies_country", "country_id")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_sectors (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            sector_id VARCHAR(36) NOT NULL REFERENCES sectors(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_sector UNIQUE (original_policy_id, sector_id)
        )
    """))
    _create_index(connection, "original_policy_sectors", "ix_original_policy_sectors_policy", "original_policy_id")
    _create_index(connection, "original_policy_sectors", "ix_original_policy_sectors_sector", "sector_id")


def _024_original_policy_knowledge_documents(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_knowledge_documents (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            knowledge_document_id VARCHAR(36) NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_knowledge_document UNIQUE (original_policy_id, knowledge_document_id)
        )
    """))
    _create_index(connection, "original_policy_knowledge_documents", "ix_original_policy_knowledge_policy", "original_policy_id")
    _create_index(connection, "original_policy_knowledge_documents", "ix_original_policy_knowledge_document", "knowledge_document_id")


def _025_original_policy_mitigation_relations(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS original_policy_mitigation_policy_relations (
            id VARCHAR(36) PRIMARY KEY,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            mitigation_measure_policy_id VARCHAR(36) NOT NULL REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
            source_mitigation_title TEXT NOT NULL,
            excel_row_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_original_policy_mitigation_policy
                UNIQUE (original_policy_id, mitigation_measure_policy_id)
        )
    """))
    _create_index(connection, "original_policy_mitigation_policy_relations", "ix_original_policy_mitigation_original", "original_policy_id")
    _create_index(connection, "original_policy_mitigation_policy_relations", "ix_original_policy_mitigation_mitigation", "mitigation_measure_policy_id")


def _026_original_policy_picker_links(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS policy_original_sources (
            id VARCHAR(36) PRIMARY KEY,
            policy_id VARCHAR(36) NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
            original_policy_id VARCHAR(36) NOT NULL REFERENCES original_policies(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_policy_original_source_policy UNIQUE (policy_id)
        )
    """))
    _create_index(connection, "policy_original_sources", "ix_policy_original_sources_policy", "policy_id")
    _create_index(connection, "policy_original_sources", "ix_policy_original_sources_original", "original_policy_id")


def _027_policy_knowledge_documents(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS policy_knowledge_documents (
            id VARCHAR(36) PRIMARY KEY,
            policy_id VARCHAR(36) NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
            knowledge_document_id VARCHAR(36) NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_policy_knowledge_document UNIQUE (policy_id, knowledge_document_id)
        )
    """))
    _create_index(connection, "policy_knowledge_documents", "ix_policy_knowledge_policy", "policy_id")
    _create_index(connection, "policy_knowledge_documents", "ix_policy_knowledge_document", "knowledge_document_id")
    connection.execute(text("""
        INSERT OR IGNORE INTO policy_knowledge_documents (id, policy_id, knowledge_document_id)
        SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
               substr(lower(hex(randomblob(2))), 2) || '-a' ||
               substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
               policy_id, id
        FROM knowledge_documents
        WHERE policy_id IS NOT NULL AND scope = 'policy_document'
    """))


def _028_normalized_knowledge_sources(connection: Connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS knowledge_sources (
            id VARCHAR(36) PRIMARY KEY,
            identity_hash VARCHAR(64) NOT NULL UNIQUE,
            canonical_uri TEXT NULL,
            source_type VARCHAR(40) NOT NULL,
            content_sha256 VARCHAR(64) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    _create_index(connection, "knowledge_sources", "ix_knowledge_sources_identity_hash", "identity_hash")
    _create_index(connection, "knowledge_sources", "ix_knowledge_sources_content_sha256", "content_sha256")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS knowledge_source_versions (
            id VARCHAR(36) PRIMARY KEY,
            source_id VARCHAR(36) NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            content_sha256 VARCHAR(64) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_knowledge_source_version_hash UNIQUE (source_id, content_sha256)
        )
    """))
    _create_index(connection, "knowledge_source_versions", "ix_knowledge_source_versions_source", "source_id")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS knowledge_source_chunks (
            id VARCHAR(36) PRIMARY KEY,
            source_version_id VARCHAR(36) NOT NULL REFERENCES knowledge_source_versions(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            page_number INTEGER NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_knowledge_source_chunk_position UNIQUE (source_version_id, chunk_index)
        )
    """))
    _create_index(connection, "knowledge_source_chunks", "ix_knowledge_source_chunks_version", "source_version_id")
    _add_column(connection, "knowledge_documents", "source_id", "source_id VARCHAR(36) NULL REFERENCES knowledge_sources(id) ON DELETE SET NULL")
    _create_index(connection, "knowledge_documents", "ix_knowledge_documents_source_id", "source_id")
    _add_column(connection, "knowledge_chunks", "shared_chunk_id", "shared_chunk_id VARCHAR(36) NULL REFERENCES knowledge_source_chunks(id) ON DELETE SET NULL")
    _create_index(connection, "knowledge_chunks", "ix_knowledge_chunks_shared_chunk_id", "shared_chunk_id")
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS knowledge_document_usages (
            id VARCHAR(36) PRIMARY KEY,
            knowledge_document_id VARCHAR(36) NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            scope VARCHAR(32) NOT NULL,
            user_id VARCHAR(36) NULL REFERENCES app_users(id) ON DELETE CASCADE,
            session_key VARCHAR(64) NULL,
            scope_level VARCHAR(20) NOT NULL DEFAULT 'global',
            country_id VARCHAR(36) NULL REFERENCES countries(id) ON DELETE SET NULL,
            region_id VARCHAR(36) NULL REFERENCES regions(id) ON DELETE SET NULL,
            sector_id VARCHAR(36) NULL REFERENCES sectors(id) ON DELETE SET NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    for index_name, column in (
        ("ix_knowledge_document_usages_document", "knowledge_document_id"),
        ("ix_knowledge_document_usages_scope", "scope"),
        ("ix_knowledge_document_usages_user", "user_id"),
        ("ix_knowledge_document_usages_session", "session_key"),
        ("ix_knowledge_document_usages_country", "country_id"),
        ("ix_knowledge_document_usages_region", "region_id"),
        ("ix_knowledge_document_usages_sector", "sector_id"),
    ):
        _create_index(connection, "knowledge_document_usages", index_name, column)
    connection.execute(text("""
        INSERT INTO knowledge_document_usages
            (id, knowledge_document_id, scope, user_id, session_key, scope_level,
             country_id, region_id, sector_id, created_at)
        SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
               substr(lower(hex(randomblob(2))), 2) || '-a' ||
               substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
               kd.id, kd.scope, kd.user_id, kd.session_key, kd.scope_level,
               kd.country_id, kd.region_id, kd.sector_id, kd.created_at
        FROM knowledge_documents kd
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_document_usages u
            WHERE u.knowledge_document_id = kd.id AND u.scope = kd.scope
              AND ifnull(u.user_id, '') = ifnull(kd.user_id, '')
              AND ifnull(u.session_key, '') = ifnull(kd.session_key, '')
        )
    """))


def _029_knowledge_chunk_reuse_index(connection: Connection) -> None:
    _create_index(
        connection,
        "knowledge_chunks",
        "ix_knowledge_chunks_document_chunk",
        "document_id, chunk_index",
    )


MIGRATIONS: tuple[Migration, ...] = (
    ("001_app_rate_limits", _001_app_rate_limits),
    ("002_auth_session_audit", _002_auth_session_audit),
    ("003_user_mitigation_sync_columns", _003_user_mitigation_sync_columns),
    ("004_user_question_response_hazard_columns", _004_user_question_response_hazard_columns),
    ("005_app_user_encrypted_sync_payload", _005_app_user_encrypted_sync_payload),
    ("006_prompts", _006_prompts),
    ("007_user_mitigation_system_inquiry", _007_user_mitigation_system_inquiry),
    ("008_system_inquiry_telemetry_events", _008_system_inquiry_telemetry_events),
    ("009_custom_hazard_summary", _009_custom_hazard_summary),
    ("010_policy_reference_custom_hazard", _010_policy_reference_custom_hazard),
    ("011_custom_hazard_policy_references", _011_custom_hazard_policy_references),
    ("012_mitigation_creation_details", _012_mitigation_creation_details),
    ("013_custom_hazard_policy_link", _013_custom_hazard_policy_link),
    ("014_policy_knowledge_documents", _014_policy_knowledge_documents),
    ("015_policies", _015_policies),
    ("016_policy_context_links", _016_policy_context_links),
    ("017_faiss_index_flags", _017_faiss_index_flags),
    ("018_crowd_sourced_policies", _018_crowd_sourced_policies),
    ("019_policy_hazard_links", _019_policy_hazard_links),
    ("020_unique_policy_titles", _020_unique_policy_titles),
    ("021_original_policies", _021_original_policies),
    ("022_unique_original_policy_names", _022_unique_original_policy_names),
    ("023_original_policy_country_sectors", _023_original_policy_country_sectors),
    ("024_original_policy_knowledge_documents", _024_original_policy_knowledge_documents),
    ("025_original_policy_mitigation_relations", _025_original_policy_mitigation_relations),
    ("026_original_policy_picker_links", _026_original_policy_picker_links),
    ("027_policy_knowledge_documents", _027_policy_knowledge_documents),
    ("028_normalized_knowledge_sources", _028_normalized_knowledge_sources),
    ("029_knowledge_chunk_reuse_index", _029_knowledge_chunk_reuse_index),
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
