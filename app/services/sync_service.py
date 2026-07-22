from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from binascii import Error as BinasciiError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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
INTERNAL_TABLES = {"schema_migrations", "sync_state", "sync_clients"}
DEFAULT_EXCLUDED_TABLES = {"app_rate_limits"}
LOG_TABLES = {"audit_logs", "llm_exchange_logs"}
KNOWLEDGE_TABLES = {"knowledge_documents", "knowledge_chunks"}
ENCRYPTED_SYNC_TABLES = {"app_users"}
USER_DATA_TABLES = {
    "app_users",
    "custom_hazards",
    "custom_hazard_profiles",
    "user_activities",
    "user_chat_messages",
    "user_hazards",
    "user_hazard_socio_demographics",
    "user_mitigation_measures",
    "user_question_responses",
    "user_sessions",
}
ENCRYPTED_ROW_MARKER = "__encrypted_row"
ENCRYPTED_ROW_ALGORITHM = "aesgcm-sha256-v1"
APP_USER_ENCRYPTED_AT_REST_ALGORITHM = "aesgcm-sha256-db-v1"
APP_USER_PLACEHOLDER_DOMAIN = "synced-user.local"
APP_USER_ENCRYPTED_COLUMNS = (
    "email",
    "name",
    "password_hash",
    "session_version",
    "designation",
    "organisation_type",
    "organisation_name",
    "role",
)
USER_DATA_SYNC_ENABLED_SCOPE = "client_user_data_sync:enabled"
USER_DATA_SYNC_ENABLED_AT_SCOPE = "client_user_data_sync:enabled_at"
KNOWLEDGE_SCOPES = ("main", "validated_evidence", "sector_prompt")
EXCLUDED_KNOWLEDGE_SCOPES = {"temporary"}
CLIENT_EXPORT_KNOWLEDGE_SCOPES = {"validated_evidence"}
ADMIN_CLIENT_EXPORT_KNOWLEDGE_SCOPES = {"main", "validated_evidence", "sector_prompt"}
SERVER_ACCEPTED_INBOUND_KNOWLEDGE_SCOPES = {"validated_evidence"}
SERVER_ACCEPTED_ADMIN_INBOUND_KNOWLEDGE_SCOPES = {"main", "validated_evidence", "sector_prompt"}
SYNC_CLIENT_COLUMNS = (
    "id",
    "client_name",
    "token_hash",
    "user_email",
    "can_sync_main_kb",
    "can_sync_sector_prompts",
    "can_reindex_sector_prompts",
    "can_sync_validated_kb",
    "can_sync_user_data",
    "active",
    "created_at",
    "updated_at",
)


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

    def __init__(self, db: Session, *, device_id: str | None = None, sync_token: str | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.device_id = device_id or self._settings_device_id()
        self.sync_token = str(sync_token or "").strip()

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
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sync_clients (
                  id CHAR(36) PRIMARY KEY,
                  client_name VARCHAR(160) NOT NULL,
                  token_hash VARCHAR(64) NOT NULL UNIQUE,
                  user_email VARCHAR(255) NULL,
                  can_sync_main_kb BOOLEAN NOT NULL DEFAULT FALSE,
                  can_sync_sector_prompts BOOLEAN NOT NULL DEFAULT FALSE,
                  can_reindex_sector_prompts BOOLEAN NOT NULL DEFAULT FALSE,
                  can_sync_validated_kb BOOLEAN NOT NULL DEFAULT TRUE,
                  can_sync_user_data BOOLEAN NOT NULL DEFAULT TRUE,
                  active BOOLEAN NOT NULL DEFAULT TRUE,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NULL
                )
                """
            )
        )
        inspector = inspect(connection)
        if "app_users" in inspector.get_table_names():
            existing_user_columns = {column["name"] for column in inspector.get_columns("app_users")}
            if "sync_encrypted_payload" not in existing_user_columns:
                connection.execute(text("ALTER TABLE app_users ADD COLUMN sync_encrypted_payload TEXT NULL"))
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

    def export_bundle(
        self,
        *,
        include_admin_knowledge: bool = False,
        admin_user_email: str | None = None,
        include_app_users: bool = True,
        include_user_data: bool = True,
        user_data_enabled_at: datetime | None = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        self.ensure_row_sync_ids(
            include_admin_knowledge=include_admin_knowledge,
            include_user_data=include_user_data,
            user_data_enabled_at=user_data_enabled_at,
        )
        exported_at = utc_now()
        return {
            "format": "dr-transition-sync-v1",
            "device_id": self.device_id,
            "exported_at": exported_at.isoformat().replace("+00:00", "Z"),
            "admin_knowledge_sync": bool(include_admin_knowledge),
            "admin_user_email": str(admin_user_email or "").strip().casefold() if include_admin_knowledge else "",
            "tables": [
                {
                    "name": table.name,
                    "rows": [
                        self._serialize_payload_row(table, row)
                        for row in self._table_rows(
                            table,
                            include_admin_knowledge=include_admin_knowledge,
                            include_user_data=include_user_data,
                            user_data_enabled_at=user_data_enabled_at,
                        )
                    ],
                }
                for table in self._export_tables(include_app_users=include_app_users)
            ],
        }

    def apply_bundle(
        self,
        payload: dict[str, Any],
        *,
        current_user_email: str | None = None,
        sync_client: dict[str, Any] | None = None,
    ) -> SyncApplyResult:
        self.ensure_schema()
        if payload.get("format") != "dr-transition-sync-v1":
            raise ValueError("Unsupported sync bundle format.")
        admin_knowledge_scopes = self._admin_knowledge_sync_scopes(payload, sync_client=sync_client)
        admin_knowledge_sync = bool(admin_knowledge_scopes)
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
                row = self._decrypt_payload_row(table, row)
                if row is None:
                    skipped += 1
                    continue
                action = self._upsert_row(
                    table,
                    row,
                    admin_knowledge_sync=admin_knowledge_sync,
                    current_user_email=current_user_email,
                    admin_knowledge_scopes=admin_knowledge_scopes,
                )
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

    async def exchange_with_server(
        self,
        *,
        include_admin_knowledge: bool = False,
        admin_user_email: str | None = None,
        current_user_email: str | None = None,
    ) -> dict[str, Any]:
        server_url = str(self.settings.sync_server_url or "").strip().rstrip("/")
        token = str(self.settings.sync_api_token or "").strip()
        if not server_url:
            raise ValueError("SYNC_SERVER_URL is not configured.")
        if not token:
            raise ValueError("SYNC_API_TOKEN is not configured.")

        user_data_enabled_at = self.user_data_sync_enabled_at()
        outbound = self.export_bundle(
            include_admin_knowledge=include_admin_knowledge,
            admin_user_email=admin_user_email,
            include_app_users=True,
            include_user_data=user_data_enabled_at is not None,
            user_data_enabled_at=user_data_enabled_at,
        )
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
        applied = self.apply_bundle(bundle, current_user_email=current_user_email)
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

    def _export_tables(self, *, include_app_users: bool = True) -> list[Table]:
        return [
            table
            for table in self.sync_tables()
            if include_app_users or table.name != "app_users"
        ]

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

    def ensure_row_sync_ids(
        self,
        *,
        include_admin_knowledge: bool = False,
        include_user_data: bool = True,
        user_data_enabled_at: datetime | None = None,
    ) -> None:
        now = utc_now()
        for table in self.sync_tables():
            for row in self._table_rows(
                table,
                include_admin_knowledge=include_admin_knowledge,
                include_user_data=include_user_data,
                user_data_enabled_at=user_data_enabled_at,
            ):
                if row.get("sync_id"):
                    continue
                pk_col = only_pk(table)
                sync_id = str(row.get(pk_col.name) or "").strip() or self._deterministic_sync_id(table, row) or str(uuid.uuid4())
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

    def user_data_sync_status(self) -> dict[str, Any]:
        self.ensure_schema()
        enabled_at = self.user_data_sync_enabled_at()
        return {
            "enabled": enabled_at is not None,
            "enabled_at": enabled_at.isoformat() + "Z" if enabled_at else None,
        }

    def user_data_sync_enabled_at(self) -> datetime | None:
        self.ensure_schema()
        enabled = self._sync_state_value(USER_DATA_SYNC_ENABLED_SCOPE) == "1"
        if not enabled:
            return None
        return coerce_datetime(self._sync_state_value(USER_DATA_SYNC_ENABLED_AT_SCOPE)) or utc_now()

    def set_user_data_sync_enabled(self, enabled: bool) -> dict[str, Any]:
        self.ensure_schema()
        enabled_at = self.user_data_sync_enabled_at()
        now = utc_now()
        if enabled:
            self._set_sync_state(USER_DATA_SYNC_ENABLED_SCOPE, "1", now)
            self._set_sync_state(USER_DATA_SYNC_ENABLED_AT_SCOPE, (enabled_at or now).isoformat(), now)
        else:
            self._set_sync_state(USER_DATA_SYNC_ENABLED_SCOPE, "0", now)
        self.db.commit()
        return self.user_data_sync_status()

    def _upsert_row(
        self,
        table: Table,
        payload_row: dict[str, Any],
        *,
        admin_knowledge_sync: bool = False,
        admin_knowledge_scopes: set[str] | None = None,
        current_user_email: str | None = None,
    ) -> str:
        sync_id = str(payload_row.get("sync_id") or "").strip()
        if not sync_id:
            return "skipped"
        if table.name == "app_users":
            payload_row = self._app_user_payload_for_client_storage(
                table,
                payload_row,
                current_user_email=current_user_email,
            )
        if table.name == "knowledge_documents":
            scope = str(payload_row.get("scope") or "").strip()
            if self._should_skip_inbound_knowledge_scope(
                scope,
                admin_knowledge_sync=admin_knowledge_sync,
                admin_knowledge_scopes=admin_knowledge_scopes,
            ):
                return "skipped"
        if table.name == "knowledge_chunks":
            scope = self._knowledge_scope_for_payload(table, payload_row)
            if self._should_skip_inbound_knowledge_scope(
                scope,
                admin_knowledge_sync=admin_knowledge_sync,
                admin_knowledge_scopes=admin_knowledge_scopes,
            ):
                return "skipped"
        pk_col = only_pk(table)
        payload_pk = str(payload_row.get(pk_col.name) or "").strip()
        existing_pk = self._pk_for_sync_id(table, sync_id)
        if existing_pk is None and payload_pk:
            existing_pk = self.db.execute(select(pk_col).where(pk_col == payload_pk)).scalar_one_or_none()
        values: dict[str, Any] = {}
        fk_sync_ids = payload_row.get("__fk_sync_ids") if isinstance(payload_row.get("__fk_sync_ids"), dict) else {}
        for column in table.columns:
            if column.name == pk_col.name:
                continue
            if column.name in SYNC_COLUMN_NAMES:
                values[column.name] = payload_row.get(column.name)
                continue
            if column.name in fk_sync_ids:
                resolved_fk = self._resolve_fk_value(
                    column,
                    str(fk_sync_ids[column.name]),
                    payload_row.get(column.name),
                )
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
        insert_values = dict(values)
        if payload_pk:
            insert_values[pk_col.name] = payload_pk
        self.db.execute(table.insert().values(**insert_values))
        return "inserted"

    def _serialize_row(self, table: Table, row: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        fk_sync_ids: dict[str, str] = {}
        for column in table.columns:
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

    def _serialize_payload_row(self, table: Table, row: dict[str, Any]) -> dict[str, Any]:
        serialized = self._serialize_row(table, row)
        if table.name not in ENCRYPTED_SYNC_TABLES:
            return serialized
        return self._encrypt_payload_row(table, serialized)

    def _encrypt_payload_row(self, table: Table, row: dict[str, Any]) -> dict[str, str]:
        nonce = os.urandom(12)
        plaintext = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(self._sync_encryption_key()).encrypt(
            nonce,
            plaintext,
            self._sync_encryption_aad(table),
        )
        return {
            "__encryption": ENCRYPTED_ROW_ALGORITHM,
            ENCRYPTED_ROW_MARKER: base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii"),
        }

    def _decrypt_payload_row(self, table: Table, row: dict[str, Any]) -> dict[str, Any] | None:
        if table.name not in ENCRYPTED_SYNC_TABLES:
            return row
        if ENCRYPTED_ROW_MARKER not in row:
            return row
        if row.get("__encryption") != ENCRYPTED_ROW_ALGORITHM:
            return None
        try:
            encrypted = base64.urlsafe_b64decode(str(row[ENCRYPTED_ROW_MARKER]).encode("ascii"))
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            plaintext = AESGCM(self._sync_encryption_key()).decrypt(
                nonce,
                ciphertext,
                self._sync_encryption_aad(table),
            )
            decrypted = json.loads(plaintext.decode("utf-8"))
        except (BinasciiError, InvalidTag, ValueError, TypeError, json.JSONDecodeError):
            return None
        return decrypted if isinstance(decrypted, dict) else None

    def _sync_encryption_key(self) -> bytes:
        secret = str(self.sync_token or self.settings.sync_api_token or self.settings.secret_key or "").strip()
        if not secret:
            secret = self.device_id
        return hashlib.sha256(secret.encode("utf-8")).digest()

    def _sync_encryption_aad(self, table: Table) -> bytes:
        return f"dr-transition-sync:{table.name}:{ENCRYPTED_ROW_ALGORITHM}".encode("utf-8")

    def _app_user_payload_for_client_storage(
        self,
        table: Table,
        payload_row: dict[str, Any],
        *,
        current_user_email: str | None = None,
    ) -> dict[str, Any]:
        if str(self.settings.sync_mode or "").strip().casefold() != "client":
            return payload_row
        if self._should_store_app_user_clear(table, payload_row, current_user_email=current_user_email):
            row = dict(payload_row)
            row["sync_encrypted_payload"] = None
            return row
        sync_id = str(payload_row.get("sync_id") or "").strip()
        encrypted_payload = self._encrypt_app_user_at_rest(payload_row)
        row = dict(payload_row)
        row["email"] = self._encrypted_app_user_placeholder_email(sync_id)
        row["name"] = "Encrypted synced user"
        row["password_hash"] = "encrypted-synced-user"
        row["session_version"] = 1
        row["designation"] = "Encrypted"
        row["organisation_type"] = "Encrypted"
        row["organisation_name"] = "Encrypted"
        row["role"] = "user"
        row["sync_encrypted_payload"] = encrypted_payload
        return row

    def _should_store_app_user_clear(
        self,
        table: Table,
        payload_row: dict[str, Any],
        *,
        current_user_email: str | None = None,
    ) -> bool:
        origin_device_id = str(payload_row.get("origin_device_id") or "").strip()
        if origin_device_id and origin_device_id == self.device_id:
            return True
        email = str(payload_row.get("email") or "").strip().casefold()
        if email and email == str(current_user_email or "").strip().casefold():
            return True
        if not email:
            return False
        existing = self.db.execute(
            select(table.c.sync_encrypted_payload).where(table.c.email == email)
        ).first()
        return bool(existing and existing[0] is None)

    def _encrypt_app_user_at_rest(self, payload_row: dict[str, Any]) -> str:
        nonce = os.urandom(12)
        stored = {
            column: payload_row.get(column)
            for column in APP_USER_ENCRYPTED_COLUMNS
            if column in payload_row
        }
        plaintext = json.dumps(stored, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(self._sync_encryption_key()).encrypt(
            nonce,
            plaintext,
            b"dr-transition-sync:app_users:db-at-rest",
        )
        return json.dumps(
            {
                "__encryption": APP_USER_ENCRYPTED_AT_REST_ALGORITHM,
                "payload": base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _encrypted_app_user_placeholder_email(sync_id: str) -> str:
        safe_sync_id = "".join(ch for ch in sync_id.lower() if ch.isalnum() or ch == "-") or str(uuid.uuid4())
        return f"encrypted-sync-user+{safe_sync_id}@{APP_USER_PLACEHOLDER_DOMAIN}"

    def _table_rows(
        self,
        table: Table,
        *,
        include_admin_knowledge: bool = False,
        include_user_data: bool = True,
        user_data_enabled_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if table.name in USER_DATA_TABLES and table.name != "app_users" and not include_user_data:
            return []
        query = select(table)
        if table.name == "knowledge_documents":
            query = query.where(table.c.scope.in_(self._exportable_knowledge_scopes(include_admin_knowledge)))
        elif table.name == "knowledge_chunks":
            document_table = Base.metadata.tables["knowledge_documents"]
            query = query.where(
                table.c.document_id.in_(
                    select(document_table.c.id).where(
                        document_table.c.scope.in_(self._exportable_knowledge_scopes(include_admin_knowledge))
                    )
                )
            )
        if table.name in USER_DATA_TABLES and user_data_enabled_at is not None and "created_at" in table.c:
            query = query.where(table.c.created_at >= user_data_enabled_at)
        if (
            table.name == "app_users"
            and str(self.settings.sync_mode or "").strip().casefold() == "client"
            and "sync_encrypted_payload" in table.c
        ):
            query = query.where(table.c.sync_encrypted_payload.is_(None))
        return [dict(row._mapping) for row in self.db.execute(query).all()]

    def _sync_id_for_pk(self, table: Table, pk_value: Any) -> str | None:
        pk_col = only_pk(table)
        row = self.db.execute(select(table.c.sync_id).where(pk_col == pk_value)).first()
        return str(row[0]) if row and row[0] else None

    def _pk_for_sync_id(self, table: Table, sync_id: str) -> Any | None:
        pk_col = only_pk(table)
        row = self.db.execute(select(pk_col).where(table.c.sync_id == sync_id)).first()
        return row[0] if row else None

    def _resolve_fk_value(self, column: Column, ref_sync_id: str, raw_value: Any) -> Any | None:
        ref_table = self._referenced_table(column)
        resolved = self._pk_for_sync_id(ref_table, ref_sync_id)
        if resolved is not None:
            return resolved
        if raw_value in (None, ""):
            return None
        pk_col = only_pk(ref_table)
        row = self.db.execute(select(pk_col).where(pk_col == raw_value)).first()
        return row[0] if row else None

    def _pk_for_natural_key(self, table: Table, payload_row: dict[str, Any]) -> Any | None:
        natural_key_columns = first_unique_key_columns(table)
        if not natural_key_columns:
            return None
        conditions = []
        fk_sync_ids = payload_row.get("__fk_sync_ids") if isinstance(payload_row.get("__fk_sync_ids"), dict) else {}
        for column in natural_key_columns:
            if column.name in fk_sync_ids:
                ref_pk = self._resolve_fk_value(
                    column,
                    str(fk_sync_ids[column.name]),
                    payload_row.get(column.name),
                )
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
            return scope if scope in {*KNOWLEDGE_SCOPES, *EXCLUDED_KNOWLEDGE_SCOPES} else None
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
        return scope if scope in {*KNOWLEDGE_SCOPES, *EXCLUDED_KNOWLEDGE_SCOPES} else None

    def _exportable_knowledge_scopes(self, include_admin_knowledge: bool = False) -> tuple[str, ...]:
        if str(self.settings.sync_mode or "").strip().casefold() == "client":
            if include_admin_knowledge:
                return tuple(sorted(ADMIN_CLIENT_EXPORT_KNOWLEDGE_SCOPES))
            return tuple(sorted(CLIENT_EXPORT_KNOWLEDGE_SCOPES))
        return KNOWLEDGE_SCOPES

    def _should_skip_inbound_knowledge_scope(
        self,
        scope: str | None,
        *,
        admin_knowledge_sync: bool = False,
        admin_knowledge_scopes: set[str] | None = None,
    ) -> bool:
        if not scope or scope in EXCLUDED_KNOWLEDGE_SCOPES:
            return True
        if str(self.settings.sync_mode or "").strip().casefold() == "server":
            if admin_knowledge_sync:
                return scope not in (admin_knowledge_scopes or set())
            return scope not in SERVER_ACCEPTED_INBOUND_KNOWLEDGE_SCOPES
        return scope not in KNOWLEDGE_SCOPES

    def admin_sync_allowed(
        self, payload: dict[str, Any], *, sync_client: dict[str, Any] | None = None
    ) -> bool:
        return bool(self._admin_knowledge_sync_scopes(payload, sync_client=sync_client))

    def _admin_knowledge_sync_scopes(
        self, payload: dict[str, Any], *, sync_client: dict[str, Any] | None = None
    ) -> set[str]:
        if not bool(payload.get("admin_knowledge_sync")):
            return set()
        if str(self.settings.sync_mode or "").strip().casefold() != "server":
            return set(SERVER_ACCEPTED_ADMIN_INBOUND_KNOWLEDGE_SCOPES)
        client = sync_client or {}
        if not client or not bool(client.get("active")):
            return set()
        scopes: set[str] = set()
        if bool(client.get("can_sync_validated_kb")):
            scopes.add("validated_evidence")
        if bool(client.get("can_sync_main_kb")):
            scopes.add("main")
        if bool(client.get("can_sync_sector_prompts")):
            scopes.add("sector_prompt")
        return scopes & SERVER_ACCEPTED_ADMIN_INBOUND_KNOWLEDGE_SCOPES

    def sync_client_for_token(self, token: str) -> dict[str, Any] | None:
        token = str(token or "").strip()
        if not token:
            return None
        self.ensure_schema()
        token_hash = self._sync_token_hash(token)
        row = self.db.execute(
            text(
                """
                SELECT id, client_name, token_hash, user_email,
                       can_sync_main_kb, can_sync_sector_prompts,
                       can_reindex_sector_prompts, can_sync_validated_kb,
                       can_sync_user_data, active
                FROM sync_clients
                WHERE token_hash = :token_hash
                LIMIT 1
                """
            ),
            {"token_hash": token_hash},
        ).mappings().first()
        if row is not None:
            client = dict(row)
            client["active"] = bool(client.get("active"))
            for key in (
                "can_sync_main_kb",
                "can_sync_sector_prompts",
                "can_reindex_sector_prompts",
                "can_sync_validated_kb",
                "can_sync_user_data",
            ):
                client[key] = bool(client.get(key))
            return client if client["active"] else None
        legacy = str(self.settings.sync_api_token or "").strip()
        if legacy and token == legacy:
            return {
                "id": "legacy-env-token",
                "client_name": "Legacy environment token",
                "token_hash": token_hash,
                "user_email": "",
                "can_sync_main_kb": False,
                "can_sync_sector_prompts": False,
                "can_reindex_sector_prompts": False,
                "can_sync_validated_kb": True,
                "can_sync_user_data": True,
                "active": True,
            }
        return None

    def upsert_sync_client(
        self,
        *,
        token: str,
        client_name: str,
        user_email: str | None = None,
        can_sync_main_kb: bool = False,
        can_sync_sector_prompts: bool = False,
        can_reindex_sector_prompts: bool = False,
        can_sync_validated_kb: bool = True,
        can_sync_user_data: bool = True,
        active: bool = True,
    ) -> str:
        token = str(token or "").strip()
        if not token:
            raise ValueError("Sync client token is required.")
        self.ensure_schema()
        client_id = str(uuid.uuid4())
        token_hash = self._sync_token_hash(token)
        now = utc_now()
        values = {
            "client_name": client_name.strip()[:160] or "Sync client",
            "token_hash": token_hash,
            "user_email": str(user_email or "").strip().casefold() or None,
            "can_sync_main_kb": bool(can_sync_main_kb),
            "can_sync_sector_prompts": bool(can_sync_sector_prompts),
            "can_reindex_sector_prompts": bool(can_reindex_sector_prompts),
            "can_sync_validated_kb": bool(can_sync_validated_kb),
            "can_sync_user_data": bool(can_sync_user_data),
            "active": bool(active),
            "updated_at": now,
        }
        existing = self.db.execute(
            text("SELECT id FROM sync_clients WHERE token_hash = :token_hash"),
            {"token_hash": token_hash},
        ).first()
        if existing:
            self.db.execute(
                text(
                    """
                    UPDATE sync_clients
                    SET client_name = :client_name,
                        user_email = :user_email,
                        can_sync_main_kb = :can_sync_main_kb,
                        can_sync_sector_prompts = :can_sync_sector_prompts,
                        can_reindex_sector_prompts = :can_reindex_sector_prompts,
                        can_sync_validated_kb = :can_sync_validated_kb,
                        can_sync_user_data = :can_sync_user_data,
                        active = :active,
                        updated_at = :updated_at
                    WHERE token_hash = :token_hash
                    """
                ),
                values,
            )
        else:
            self.db.execute(
                text(
                    """
                    INSERT INTO sync_clients (
                      id, client_name, token_hash, user_email,
                      can_sync_main_kb, can_sync_sector_prompts,
                      can_reindex_sector_prompts, can_sync_validated_kb,
                      can_sync_user_data, active, updated_at
                    )
                    VALUES (
                      :id, :client_name, :token_hash, :user_email,
                      :can_sync_main_kb, :can_sync_sector_prompts,
                      :can_reindex_sector_prompts, :can_sync_validated_kb,
                      :can_sync_user_data, :active, :updated_at
                    )
                    """
                ),
                {"id": client_id, **values},
            )
        self.db.commit()
        return token_hash

    @staticmethod
    def _sync_token_hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

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

    def _sync_state_value(self, scope: str) -> str | None:
        row = self.db.execute(
            text("SELECT value FROM sync_state WHERE scope = :scope"),
            {"scope": scope},
        ).first()
        return str(row[0]) if row and row[0] is not None else None

    def _set_sync_state(self, scope: str, value: str, updated_at: datetime) -> None:
        self.db.execute(text("DELETE FROM sync_state WHERE scope = :scope"), {"scope": scope})
        self.db.execute(
            text(
                """
                INSERT INTO sync_state (scope, value, updated_at)
                VALUES (:scope, :value, :updated_at)
                """
            ),
            {"scope": scope, "value": value, "updated_at": updated_at},
        )

    def _deterministic_sync_id(self, table: Table, row: dict[str, Any]) -> str | None:
        natural_key_columns = first_unique_key_columns(table)
        if not natural_key_columns:
            return None
        parts: list[str] = []
        for column in natural_key_columns:
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


def first_unique_key_columns(table: Table) -> tuple[Column[Any], ...]:
    constraint = first_unique_constraint(table)
    constraint_columns = tuple(constraint.columns) if constraint is not None else tuple()
    unique_columns = tuple(column for column in table.columns if column.unique)
    candidates = [columns for columns in (constraint_columns, *[(column,) for column in unique_columns]) if columns]
    if not candidates:
        return tuple()
    return tuple(sorted(candidates, key=lambda columns: (len(columns), [column.name for column in columns]))[0])


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
