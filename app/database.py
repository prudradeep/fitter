"""Backward-compatible database facade.

New code should import from app.db.session, app.db.migrations_runtime,
or app.seed.reference_data directly.
"""

from app.db.session import Base, SessionLocal, engine, get_db, validate_database_connection
from app.db.migrations_runtime import run_schema_sql, split_sql_statements
from app.seed.reference_data import (
    seed_reference_data,
    ensure_additional_hazards,
    ensure_mitigation_measure_examples,
    _ensure_hazards_xlsx_policy_system_hazards,
)

__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "validate_database_connection",
    "run_schema_sql",
    "split_sql_statements",
    "seed_reference_data",
    "ensure_additional_hazards",
    "ensure_mitigation_measure_examples",
    "_ensure_hazards_xlsx_policy_system_hazards",
]
