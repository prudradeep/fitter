import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

database_url = make_url(settings.database_url)
is_sqlite = database_url.drivername.startswith("sqlite")


def _ensure_sqlite_parent_directory() -> None:
    if not is_sqlite:
        return
    database_path = database_url.database
    if not database_path or database_path in {":memory:"}:
        return
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
    finally:
        cursor.close()


_ensure_sqlite_parent_directory()
engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": settings.database_pool_recycle_seconds,
    "future": True,
}
if is_sqlite:
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options.update(
        {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_timeout": settings.database_pool_timeout_seconds,
        }
    )
    if database_url.drivername.startswith("mysql"):
        engine_options["connect_args"] = {
            "connect_timeout": settings.database_connect_timeout_seconds,
        }

engine = create_engine(
    settings.database_url,
    **engine_options,
)
if is_sqlite:
    event.listen(engine, "connect", _configure_sqlite_connection)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

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



