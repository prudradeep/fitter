from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection


_INTEGER_COLUMN_RE = re.compile(
    r"^(tinyint|smallint|mediumint|int|integer|bigint)(?:\(\d+\))?(?:\s+unsigned)?$",
    re.IGNORECASE,
)


def mysql_column_type(
    connection: Connection,
    *,
    table_name: str,
    column_name: str,
    default: str = "INT",
) -> str:
    if connection.dialect.name != "mysql":
        return default

    column_type = connection.execute(
        text(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar()
    if not column_type:
        return default

    normalized = re.sub(r"\s+", " ", str(column_type).strip())
    if not _INTEGER_COLUMN_RE.fullmatch(normalized):
        return default
    normalized = re.sub(r"\(\d+\)", "", normalized)
    normalized = normalized.replace("integer", "int")
    return normalized.upper()


def mysql_question_option_id_type(connection: Connection) -> str:
    return mysql_column_type(
        connection,
        table_name="question_options",
        column_name="id",
        default="CHAR(36)",
    )
