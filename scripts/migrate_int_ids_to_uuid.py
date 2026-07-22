from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import MetaData, inspect, select, text

from app.db.session import Base, engine


BACKUP_SUFFIX = "__int_id_backup"


def main() -> None:
    """Convert an existing integer-ID database to UUID primary keys.

    The migration renames current ORM tables to backup tables, creates the UUID
    schema from SQLAlchemy metadata, then copies rows while remapping every FK.
    Keep a full database backup before running this script.
    """

    metadata = Base.metadata
    table_names = [table.name for table in metadata.sorted_tables]
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        if not any(name in existing_tables for name in table_names):
            print("No ORM tables found to migrate.")
            return
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table_name in reversed(table_names):
            if table_name not in existing_tables:
                continue
            backup_name = f"{table_name}{BACKUP_SUFFIX}"
            if backup_name in existing_tables:
                raise RuntimeError(f"Backup table already exists: {backup_name}")
            connection.execute(text(f"RENAME TABLE {table_name} TO {backup_name}"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    metadata.create_all(bind=engine)

    backup_metadata = MetaData()
    backup_metadata.reflect(bind=engine, only=[f"{name}{BACKUP_SUFFIX}" for name in table_names if _table_exists(f"{name}{BACKUP_SUFFIX}")])
    id_map: dict[str, dict[Any, str]] = defaultdict(dict)

    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            backup = backup_metadata.tables.get(f"{table.name}{BACKUP_SUFFIX}")
            if backup is None:
                continue
            if "id" not in backup.c or "id" not in table.c:
                continue
            rows = connection.execute(select(backup)).mappings().all()
            for row in rows:
                old_id = row.get("id")
                if old_id is None:
                    continue
                existing_sync_id = str(row.get("sync_id") or "").strip()
                id_map[table.name][old_id] = existing_sync_id if _looks_like_uuid(existing_sync_id) else str(uuid.uuid4())

        for table in metadata.sorted_tables:
            backup = backup_metadata.tables.get(f"{table.name}{BACKUP_SUFFIX}")
            if backup is None:
                continue
            rows = connection.execute(select(backup)).mappings().all()
            for row in rows:
                values: dict[str, Any] = {}
                for column in table.columns:
                    if column.name not in row:
                        continue
                    value = row[column.name]
                    if column.name == "id":
                        value = id_map[table.name].get(value, str(uuid.uuid4()))
                    elif column.foreign_keys and value is not None:
                        ref_table = next(iter(column.foreign_keys)).column.table.name
                        value = id_map[ref_table].get(value)
                    values[column.name] = value
                if values:
                    connection.execute(table.insert().values(**values))
    print("UUID ID migration completed. Backup tables have suffix:", BACKUP_SUFFIX)


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _table_exists(table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


if __name__ == "__main__":
    main()
