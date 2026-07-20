from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import Column, DateTime, Integer, String, Table, inspect, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.schema import UniqueConstraint

from app.config import get_settings
from app.db.session import Base

SYNC_NAMESPACE = uuid.UUID("6b09c4c5-8a21-491f-9f52-98df34b63bd8")
SYNC_COLUMN_NAMES = {
    "sync_id",
    "origin_device_id",
    "sync_revision",
    "sync_updated_at",
    "sync_deleted_at",
}
INTERNAL_TABLES = {"schema_migrations", "sync_state"}
DEFAULT_EXCLUDED_TABLES = {"app_rate_limits"}
LOG_TABLES = {"audit_logs", "llm_exchange_logs"}
KNOWLEDGE_TABLES = {"knowledge_documents", "knowledge_chunks"}
KNOWLEDGE_SCOPES = ("main", "validated_evidence", "sector_prompt")
EXCLUDED_KNOWLEDGE_SCOPES = {"temporary"}


@dataclass(frozen=True)
class SyncApplyResult:
    tables: int
    inserted: int
    updated: int
    skipped: int
    knowledge_scopes_dirty: tuple[str, ...] = ()


class SyncService:
    """Table-driven database sync using global row identities.

    Existing integer primary keys remain local. Sync payloads identify rows by
    `sync_id` and describe foreign keys through referenced rows' `sync_id`s.
    """

    def __init__(self, db: Session, *, device_id: str | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.device_id = device_id or self._settings_device_id()

    def ensure_schema(self) -> None:
        self._ensure_metadata_columns()
        connection = self.db.connection()
        dialect = connection.dialect.name
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                  scope VARCHAR(120) PRIMARY KEY,
                  value TEXT NULL,
                  updated_at DATETIME NULL
                )
                """
            )
        )
        inspector = inspect(connection)
        for table in self.sync_tables():
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column_name, ddl in self._sync_column_ddls(dialect).items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            indexes = {index["name"] for index in inspector.get_indexes(table.name)}
            index_name = f"ix_{table.name}_sync_id"
            if index_name not in indexes:
                connection.execute(text(f"CREATE UNIQUE INDEX {index_name} ON {table.name} (sync_id)"))
        self.db.commit()

    def export_bundle(self) -> dict[str, Any]:
        self.ensure_schema()
        self.ensure_row_sync_ids()
        exported_at = utc_now()
        return {
            "format": "dr-transition-sync-v1",
            "device_id": self.device_id,
            "exported_at": exported_at.isoformat().replace("+00:00", "Z"),
            "tables": [
                {
                    "name": table.name,
                    "rows": [self._serialize_row(table, row) for row in self._table_rows(table)],
                }
                for table in self.sync_tables()
            ],
        }

    def apply_bundle(self, payload: dict[str, Any]) -> SyncApplyResult:
        self.ensure_schema()
        if payload.get("format") != "dr-transition-sync-v1":
            raise ValueError("Unsupported sync bundle format.")
        table_payloads = {
            str(item.get("name")): item.get("rows") or []
            for item in payload.get("tables") or []
            if isinstance(item, dict)
        }
        inserted = updated_count = skipped = tables = 0
        dirty_knowledge_scopes: set[str] = set()
        for table in self.sync_tables():
            rows = table_payloads.get(table.name)
            if not rows:
                continue
            tables += 1
            for row in rows:
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                action = self._upsert_row(table, row)
                if action == "inserted":
                    inserted += 1
                elif action == "updated":
                    updated_count += 1
                else:
                    skipped += 1
                if action in {"inserted", "updated"} and table.name in KNOWLEDGE_TABLES:
                    scope = self._knowledge_scope_for_payload(table, row)
                    if scope:
                        dirty_knowledge_scopes.add(scope)
        self._mark_knowledge_indexes_dirty(dirty_knowledge_scopes)
        self.db.commit()
        return SyncApplyResult(
            tables=tables,
            inserted=inserted,
            updated=updated_count,
            skipped=skipped,
            knowledge_scopes_dirty=tuple(sorted(dirty_knowledge_scopes)),
        )

    async def exchange_with_server(self) -> dict[str, Any]:
        server_url = str(self.settings.sync_server_url or "").strip().rstrip("/")
        token = str(self.settings.sync_api_token or "").strip()
        if not server_url:
            raise ValueError("SYNC_SERVER_URL is not configured.")
        if not token:
            raise ValueError("SYNC_API_TOKEN is not configured.")

        outbound = self.export_bundle()
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{server_url}/api/sync/exchange",
                json=outbound,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
        bundle = data.get("bundle") if isinstance(data, dict) else None
        if not isinstance(bundle, dict):
            raise ValueError("Sync server response did not include a bundle.")
        applied = self.apply_bundle(bundle)
        return {
            "error": False,
            "pushed": {
                "tables": len(outbound.get("tables") or []),
                "rows": sum(len(table.get("rows") or []) for table in outbound.get("tables") or []),
            },
            "server_applied": data.get("applied"),
            "pulled": {
                "tables": applied.tables,
                "inserted": applied.inserted,
                "updated": applied.updated,
                "skipped": applied.skipped,
                "knowledge_scopes_dirty": list(applied.knowledge_scopes_dirty),
            },
        }

    def sync_tables(self) -> list[Table]:
        include_logs = bool(self.settings.sync_include_logs)
        tables = []
        for table in Base.metadata.sorted_tables:
            if table.name in INTERNAL_TABLES or table.name in DEFAULT_EXCLUDED_TABLES:
                continue
            if table.name in LOG_TABLES and not include_logs:
                continue
            if len(table.primary_key.columns) != 1:
                continue
            tables.append(table)
        return tables

    def _ensure_metadata_columns(self) -> None:
        for table in self.sync_tables():
            if "sync_id" not in table.c:
                table.append_column(Column("sync_id", String(36), nullable=True))
            if "origin_device_id" not in table.c:
                table.append_column(Column("origin_device_id", String(36), nullable=True))
            if "sync_revision" not in table.c:
                table.append_column(Column("sync_revision", Integer, nullable=True))
            if "sync_updated_at" not in table.c:
                table.append_column(Column("sync_updated_at", DateTime, nullable=True))
            if "sync_deleted_at" not in table.c:
                table.append_column(Column("sync_deleted_at", DateTime, nullable=True))

    def ensure_row_sync_ids(self) -> None:
        now = utc_now()
        for table in self.sync_tables():
            for row in self._table_rows(table):
                if row.get("sync_id"):
                    continue
                sync_id = self._deterministic_sync_id(table, row) or str(uuid.uuid4())
                pk_col = only_pk(table)
                self.db.execute(
                    update(table)
                    .where(pk_col == row[pk_col.name])
                    .values(
                        sync_id=sync_id,
                        origin_device_id=row.get("origin_device_id") or self.device_id,
                        sync_revision=row.get("sync_revision") or 1,
                        sync_updated_at=row.get("sync_updated_at") or now,
                    )
                )
        self.db.commit()

    def knowledge_index_dirty_scopes(self) -> list[str]:
        self.ensure_schema()
        rows = self.db.execute(
            text(
                """
                SELECT scope
                FROM sync_state
                WHERE scope LIKE 'knowledge_index_dirty:%'
                  AND value = '1'
                ORDER BY scope
                """
            )
        ).all()
        return [
            str(row[0]).split(":", 1)[1]
            for row in rows
            if ":" in str(row[0])
        ]

    def _upsert_row(self, table: Table, payload_row: dict[str, Any]) -> str:
        sync_id = str(payload_row.get("sync_id") or "").strip()
        if not sync_id:
            return "skipped"
        if table.name == "knowledge_documents":
            scope = str(payload_row.get("scope") or "").strip()
            if scope in EXCLUDED_KNOWLEDGE_SCOPES:
                return "skipped"
        if table.name == "knowledge_chunks":
            scope = self._knowledge_scope_for_payload(table, payload_row)
            if scope in EXCLUDED_KNOWLEDGE_SCOPES:
                return "skipped"
        pk_col = only_pk(table)
        existing_pk = self._pk_for_sync_id(table, sync_id)
        values: dict[str, Any] = {}
        fk_sync_ids = payload_row.get("__fk_sync_ids") if isinstance(payload_row.get("__fk_sync_ids"), dict) else {}
        for column in table.columns:
            if column.name == pk_col.name:
                continue
            if column.name in SYNC_COLUMN_NAMES:
                values[column.name] = payload_row.get(column.name)
                continue
            if column.name in fk_sync_ids:
                resolved_fk = self._pk_for_sync_id(self._referenced_table(column), str(fk_sync_ids[column.name]))
                if resolved_fk is None and not column.nullable:
                    return "skipped"
                values[column.name] = resolved_fk
                continue
            if column.name in payload_row:
                raw_value = payload_row.get(column.name)
                values[column.name] = coerce_datetime(raw_value) if isinstance(column.type, DateTime) else raw_value
        values["sync_id"] = sync_id
        values["origin_device_id"] = values.get("origin_device_id") or payload_row.get("origin_device_id") or self.device_id
        values["sync_revision"] = int(values.get("sync_revision") or payload_row.get("sync_revision") or 1)
        values["sync_updated_at"] = coerce_datetime(values.get("sync_updated_at")) or utc_now()
        values["sync_deleted_at"] = coerce_datetime(values.get("sync_deleted_at"))
        natural_pk = existing_pk or self._pk_for_natural_key(table, payload_row)
        if natural_pk is not None:
            self.db.execute(update(table).where(pk_col == natural_pk).values(**values))
            return "updated"
        self.db.execute(table.insert().values(**values))
        return "inserted"

    def _serialize_row(self, table: Table, row: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        fk_sync_ids: dict[str, str] = {}
        pk_name = only_pk(table).name
        for column in table.columns:
            if column.name == pk_name:
                continue
            value = row.get(column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            serialized[column.name] = value
            if column.foreign_keys and value is not None:
                ref_table = self._referenced_table(column)
                ref_sync_id = self._sync_id_for_pk(ref_table, value)
                if ref_sync_id:
                    fk_sync_ids[column.name] = ref_sync_id
        if fk_sync_ids:
            serialized["__fk_sync_ids"] = fk_sync_ids
        return serialized

    def _table_rows(self, table: Table) -> list[dict[str, Any]]:
        query = select(table)
        if table.name == "knowledge_documents":
            query = query.where(table.c.scope.notin_(EXCLUDED_KNOWLEDGE_SCOPES))
        elif table.name == "knowledge_chunks":
            document_table = Base.metadata.tables["knowledge_documents"]
            query = query.where(
                table.c.document_id.in_(
                    select(document_table.c.id).where(
                        document_table.c.scope.notin_(EXCLUDED_KNOWLEDGE_SCOPES)
                    )
                )
            )
        return [dict(row._mapping) for row in self.db.execute(query).all()]

    def _sync_id_for_pk(self, table: Table, pk_value: Any) -> str | None:
        pk_col = only_pk(table)
        row = self.db.execute(select(table.c.sync_id).where(pk_col == pk_value)).first()
        return str(row[0]) if row and row[0] else None

    def _pk_for_sync_id(self, table: Table, sync_id: str) -> Any | None:
        pk_col = only_pk(table)
        row = self.db.execute(select(pk_col).where(table.c.sync_id == sync_id)).first()
        return row[0] if row else None

    def _pk_for_natural_key(self, table: Table, payload_row: dict[str, Any]) -> Any | None:
        constraint = first_unique_constraint(table)
        if constraint is None:
            if table.name == "app_users" and payload_row.get("email"):
                row = self.db.execute(select(only_pk(table)).where(table.c.email == payload_row["email"])).first()
                return row[0] if row else None
            return None
        conditions = []
        fk_sync_ids = payload_row.get("__fk_sync_ids") if isinstance(payload_row.get("__fk_sync_ids"), dict) else {}
        for column in constraint.columns:
            if column.name in fk_sync_ids:
                ref_pk = self._pk_for_sync_id(self._referenced_table(column), str(fk_sync_ids[column.name]))
                if ref_pk is None:
                    return None
                conditions.append(column == ref_pk)
            elif column.name in payload_row and payload_row.get(column.name) is not None:
                conditions.append(column == payload_row.get(column.name))
            else:
                return None
        row = self.db.execute(select(only_pk(table)).where(*conditions)).first()
        return row[0] if row else None

    def _knowledge_scope_for_payload(self, table: Table, payload_row: dict[str, Any]) -> str | None:
        if table.name == "knowledge_documents":
            scope = str(payload_row.get("scope") or "").strip()
            return scope if scope in KNOWLEDGE_SCOPES else None
        if table.name != "knowledge_chunks":
            return None
        fk_sync_ids = payload_row.get("__fk_sync_ids") if isinstance(payload_row.get("__fk_sync_ids"), dict) else {}
        document_sync_id = str(fk_sync_ids.get("document_id") or "").strip()
        if not document_sync_id:
            return None
        document_table = Base.metadata.tables["knowledge_documents"]
        row = self.db.execute(
            select(document_table.c.scope).where(document_table.c.sync_id == document_sync_id)
        ).first()
        scope = str(row[0] if row else "").strip()
        return scope if scope in KNOWLEDGE_SCOPES else None

    def _mark_knowledge_indexes_dirty(self, scopes: set[str]) -> None:
        for scope in sorted(scope for scope in scopes if scope in KNOWLEDGE_SCOPES):
            state_scope = f"knowledge_index_dirty:{scope}"
            self.db.execute(
                text("DELETE FROM sync_state WHERE scope = :scope"),
                {"scope": state_scope},
            )
            self.db.execute(
                text(
                    """
                    INSERT INTO sync_state (scope, value, updated_at)
                    VALUES (:scope, '1', :updated_at)
                    """
                ),
                {
                    "scope": state_scope,
                    "updated_at": utc_now(),
                },
            )

    def _deterministic_sync_id(self, table: Table, row: dict[str, Any]) -> str | None:
        constraint = first_unique_constraint(table)
        if constraint is None:
            if table.name == "user_sessions" and row.get("session_key"):
                return deterministic_uuid(table.name, [row["session_key"]])
            if table.name == "app_users" and row.get("email"):
                return None
            return None
        parts: list[str] = []
        for column in constraint.columns:
            value = row.get(column.name)
            if value is None:
                return None
            if column.foreign_keys:
                ref_sync_id = self._sync_id_for_pk(self._referenced_table(column), value)
                if not ref_sync_id:
                    return None
                parts.append(ref_sync_id)
            else:
                parts.append(str(value).strip().casefold())
        return deterministic_uuid(table.name, parts)

    def _referenced_table(self, column: Column[Any]) -> Table:
        foreign_key = next(iter(column.foreign_keys))
        return foreign_key.column.table

    def _settings_device_id(self) -> str:
        configured = str(self.settings.sync_device_id or "").strip()
        if configured:
            return configured
        source = f"{self.settings.database_url}|{self.settings.app_name}"
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(SYNC_NAMESPACE, digest))

    @staticmethod
    def _sync_column_ddls(dialect: str) -> dict[str, str]:
        datetime_type = "DATETIME"
        return {
            "sync_id": "sync_id VARCHAR(36) NULL",
            "origin_device_id": "origin_device_id VARCHAR(36) NULL",
            "sync_revision": "sync_revision INT NULL",
            "sync_updated_at": f"sync_updated_at {datetime_type} NULL",
            "sync_deleted_at": f"sync_deleted_at {datetime_type} NULL",
        }


def only_pk(table: Table) -> Column[Any]:
    return next(iter(table.primary_key.columns))


def first_unique_constraint(table: Table) -> UniqueConstraint | None:
    constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.columns
    ]
    return sorted(constraints, key=lambda constraint: len(constraint.columns))[0] if constraints else None


def deterministic_uuid(table_name: str, parts: list[Any]) -> str:
    return str(uuid.uuid5(SYNC_NAMESPACE, f"{table_name}|" + "|".join(str(part) for part in parts)))


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None
