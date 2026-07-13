from pathlib import Path

from sqlalchemy import text

from app.db.session import engine


MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


def apply_versioned_migrations() -> list[str]:
    """Apply idempotent SQL migration files and record their versions."""
    if not MIGRATIONS_PATH.exists():
        return []

    applied: list[str] = []
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version VARCHAR(120) PRIMARY KEY,
                  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        rows = connection.execute(text("SELECT version FROM schema_migrations")).all()
        seen = {str(row[0]) for row in rows}
        for path in sorted(MIGRATIONS_PATH.glob("*.sql")):
            version = path.stem
            if version in seen:
                continue
            for statement in _split_sql_statements(path.read_text(encoding="utf-8")):
                connection.execute(text(statement))
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
            applied.append(version)
    return applied


def _split_sql_statements(sql: str) -> list[str]:
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
            if statement and not statement.startswith("--"):
                statements.append(statement)

    trailing = "".join(current).strip()
    if trailing and not trailing.startswith("--"):
        statements.append(trailing)
    return statements
