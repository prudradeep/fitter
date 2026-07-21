import re
import unittest
from pathlib import Path

from app.db.session import Base
from app.services.sync_service import SYNC_COLUMN_NAMES


def schema_columns() -> dict[str, set[str]]:
    schema = Path("schema.sql").read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+`?(\w+)`?\s*\((.*?)\)\s*ENGINE=",
        schema,
        re.S | re.I,
    ):
        table, body = match.group(1), match.group(2)
        tables[table] = _column_names_from_create_body(body)
    return tables


def _column_names_from_create_body(body: str) -> set[str]:
    columns: set[str] = set()
    entries: list[str] = []
    token: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            entries.append("".join(token).strip())
            token = []
        else:
            token.append(char)
    trailing = "".join(token).strip()
    if trailing:
        entries.append(trailing)
    for entry in entries:
        line = " ".join(entry.strip().split())
        if not line:
            continue
        first = line.split()[0].strip("`")
        if first.upper() in {"CONSTRAINT", "INDEX", "UNIQUE", "PRIMARY", "FOREIGN", "KEY"}:
            continue
        if re.match(r"^[A-Za-z_]\w*$", first):
            columns.add(first)
    return columns


class SchemaAlignmentTests(unittest.TestCase):
    def test_schema_sql_columns_match_models(self) -> None:
        schema = schema_columns()
        missing_tables: list[str] = []
        mismatches: list[str] = []

        for table in Base.metadata.sorted_tables:
            model_columns = {column.name for column in table.columns} - SYNC_COLUMN_NAMES
            schema_table_columns = schema.get(table.name)
            if schema_table_columns is None:
                missing_tables.append(table.name)
                continue
            missing_from_schema = sorted(model_columns - schema_table_columns)
            extra_in_schema = sorted(schema_table_columns - model_columns)
            if missing_from_schema or extra_in_schema:
                mismatches.append(
                    f"{table.name}: missing={missing_from_schema}, extra={extra_in_schema}"
                )

        self.assertEqual(missing_tables, [])
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
