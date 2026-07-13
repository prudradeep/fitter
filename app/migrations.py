"""Backward-compatible migration facade.

New code should import from app.db.migrations_runtime directly.
"""

from app.db.migrations_runtime import (
    ensure_runtime_schema,
    run_runtime_migrations,
    run_schema_sql,
    split_sql_statements,
)

__all__ = [
    "ensure_runtime_schema",
    "run_runtime_migrations",
    "run_schema_sql",
    "split_sql_statements",
]
