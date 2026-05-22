import logging
from collections.abc import Generator
from pathlib import Path

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
    except Exception:
        logger.exception("Runtime schema migration failed")
        raise
