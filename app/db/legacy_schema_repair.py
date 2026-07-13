"""Operator-only legacy schema repair entry points.

This module intentionally sits outside the routine migration path. Use it only
for controlled local or installer recovery after taking a database backup.
"""

import logging

from app.db.migrations_runtime import ensure_runtime_schema
from app.db.session import Base, engine

logger = logging.getLogger(__name__)


def run_legacy_schema_repair(*, seed_reference_data: bool = False) -> None:
    logger.warning("Running legacy schema repair; do not use this path in production deploys")
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(seed_reference_data=seed_reference_data)
    logger.info("Legacy schema repair applied")
