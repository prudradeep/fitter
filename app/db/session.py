import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

database_url = make_url(settings.database_url)
engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": settings.database_pool_recycle_seconds,
    "future": True,
}
if not database_url.drivername.startswith("sqlite"):
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



