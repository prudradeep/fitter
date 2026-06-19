import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.services.mitigation_examples import MITIGATION_MEASURE_EXAMPLES

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


def ensure_runtime_schema() -> None:
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
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      INDEX ix_app_users_email (email)
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

        inspector = inspect(engine)
        table_names = inspector.get_table_names()
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
                if "ix_knowledge_documents_scope" not in document_indexes:
                    connection.execute(
                        text(
                            "ALTER TABLE knowledge_documents "
                            "ADD INDEX ix_knowledge_documents_scope (scope)"
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

        ensure_mitigation_measure_examples()

        inspector = inspect(engine)
        if "user_mitigation_measures" in inspector.get_table_names():
            mitigation_columns = {
                column["name"]
                for column in inspector.get_columns("user_mitigation_measures")
            }
            with engine.begin() as connection:
                if "conclusion" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN conclusion TEXT NULL AFTER reason"
                        )
                    )
                if "target_groups_json" not in mitigation_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE user_mitigation_measures "
                            "ADD COLUMN target_groups_json TEXT NULL AFTER conclusion"
                        )
                    )

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
            if "region_id" not in columns:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD COLUMN region_id INT NULL AFTER sector_id")
                )
            if "ix_user_hazards_system_hazard_id" not in indexes:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD INDEX ix_user_hazards_system_hazard_id (system_hazard_id)")
                )
            if "ix_user_hazards_region_id" not in indexes:
                connection.execute(
                    text("ALTER TABLE user_hazards ADD INDEX ix_user_hazards_region_id (region_id)")
                )
            if "fk_user_hazards_system_hazard" not in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE user_hazards ADD CONSTRAINT fk_user_hazards_system_hazard "
                        "FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL"
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
def ensure_mitigation_measure_examples() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_examples (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  sector_id INT NOT NULL,
                  measure TEXT NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_examples_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                  UNIQUE KEY uq_mitigation_example_sector_measure (sector_id, measure(500)),
                  INDEX ix_mitigation_measure_examples_sector_id (sector_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
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
        if "uq_mitigation_example_sector_measure" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD UNIQUE KEY uq_mitigation_example_sector_measure "
                    "(sector_id, measure(500))"
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

        for sector_name, measure in MITIGATION_MEASURE_EXAMPLES:
            if "sector_name" in columns:
                connection.execute(
                    text(
                        """
                        INSERT IGNORE INTO mitigation_measure_examples
                            (sector_id, sector_name, measure)
                        SELECT sectors.id, :sector_name, :measure
                        FROM sectors
                        WHERE sectors.name = :sector_name
                        """
                    ),
                    {"sector_name": sector_name, "measure": measure},
                )
            else:
                connection.execute(
                    text(
                        """
                        INSERT IGNORE INTO mitigation_measure_examples
                            (sector_id, measure)
                        SELECT sectors.id, :measure
                        FROM sectors
                        WHERE sectors.name = :sector_name
                        """
                    ),
                    {"sector_name": sector_name, "measure": measure},
                )
