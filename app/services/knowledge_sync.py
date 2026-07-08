from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Awaitable, Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeChunk, KnowledgeDocument, SyncState
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    SECTOR_PROMPT_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
)

SYNCABLE_SCOPES = (MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, SECTOR_PROMPT_SCOPE)
SYNC_LOCK = asyncio.Lock()
SYNC_STATUS: dict[str, object] = {
    "running": False,
    "scope": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "last_result": None,
}


class KnowledgeSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    async def pull_all(
        self,
        *,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> dict[str, object]:
        return await self._run_locked(
            "all",
            lambda: self._pull_all_unlocked(
                country_id=country_id,
                region_id=region_id,
                sector_id=sector_id,
            ),
        )

    async def _pull_all_unlocked(
        self,
        *,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> dict[str, object]:
        results: dict[str, object] = {}
        for scope in SYNCABLE_SCOPES:
            if scope == VALIDATED_EVIDENCE_SCOPE and (country_id is None or sector_id is None):
                results[scope] = {
                    "skipped": True,
                    "detail": "country_id and sector_id are required for validated evidence sync.",
                }
                continue
            results[scope] = await self._pull_scope_unlocked(
                scope,
                country_id=country_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
                region_id=region_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
                sector_id=sector_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
            )
        return {"error": False, "scopes": results}

    async def pull_scope(
        self,
        scope: str,
        *,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> dict[str, object]:
        return await self._run_locked(
            scope,
            lambda: self._pull_scope_unlocked(
                scope,
                country_id=country_id,
                region_id=region_id,
                sector_id=sector_id,
            ),
        )

    async def _pull_scope_unlocked(
        self,
        scope: str,
        *,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> dict[str, object]:
        if scope not in SYNCABLE_SCOPES:
            return {"error": True, "detail": f"Unsupported sync scope: {scope}"}
        if not self.settings.central_api_base_url.strip():
            return {"error": True, "detail": "CENTRAL_API_BASE_URL is not configured."}

        state = self._state(scope, country_id, region_id, sector_id)
        base_params: dict[str, object] = {
            "scope": scope,
            "since": state.last_sync_version,
            "limit": max(1, min(int(self.settings.sync_batch_size or 100), 500)),
        }
        if scope == VALIDATED_EVIDENCE_SCOPE:
            base_params.update(
                {
                    "country_id": country_id,
                    "region_id": region_id,
                    "sector_id": sector_id,
                }
            )

        headers = _auth_headers(
            self.settings.central_sync_token.strip()
            or self.settings.central_api_token.strip()
        )

        changed = 0
        deleted = 0
        pages = 0
        latest_version = state.last_sync_version
        cursor: dict[str, object] | None = None
        async with httpx.AsyncClient(
            base_url=self.settings.central_api_base_url.rstrip("/"),
            timeout=self.settings.sync_timeout_seconds,
            headers=headers,
        ) as client:
            while True:
                params = dict(base_params)
                if cursor:
                    params["cursor_version"] = cursor.get("version")
                    params["cursor_id"] = cursor.get("id")
                response = await client.get("/api/sync/knowledge/changes", params=params)
                response.raise_for_status()
                payload = response.json()
                documents = payload.get("documents")
                if not isinstance(documents, list):
                    return {"error": True, "detail": "Central sync response did not include documents."}

                pages += 1
                latest_version = max(
                    latest_version,
                    int(payload.get("latest_version") or latest_version),
                )
                for item in documents:
                    if not isinstance(item, dict):
                        continue
                    if item.get("deleted"):
                        deleted += self._delete_document(str(item.get("sync_id") or ""))
                        continue
                    self._upsert_document(scope, item)
                    changed += 1

                next_cursor = payload.get("next_cursor")
                if not payload.get("has_more") or not isinstance(next_cursor, dict):
                    break
                cursor = next_cursor

        state.last_sync_version = max(state.last_sync_version, latest_version)
        state.last_synced_at = datetime.utcnow()
        self.db.commit()

        rebuild = {"rebuilt": False, "chunks": 0}
        if changed or deleted:
            service = KnowledgeBaseService(
                self.db,
                None,
                scope=scope,
                country_id=country_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
                region_id=region_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
                sector_id=sector_id if scope == VALIDATED_EVIDENCE_SCOPE else None,
            )
            rebuild = await service.rebuild_index_from_db()

        return {
            "error": False,
            "changed": changed,
            "deleted": deleted,
            "pages": pages,
            "last_sync_version": state.last_sync_version,
            "index": rebuild,
        }

    async def submit_evidence(
        self,
        *,
        user_id: int | None,
        session_key: str | None,
        country_id: int | None,
        region_id: int | None,
        sector_id: int | None,
        sources: list[dict[str, object]],
    ) -> dict[str, object]:
        if not sources:
            return {"error": False, "submitted": 0}
        if not self.settings.central_api_base_url.strip():
            return {"error": True, "detail": "CENTRAL_API_BASE_URL is not configured.", "submitted": 0}
        headers = _auth_headers(
            self.settings.central_evidence_token.strip()
            or self.settings.central_api_token.strip()
        )
        payload = {
            "client_id": self.settings.central_client_id.strip() or None,
            "user_id": user_id,
            "session_key": session_key,
            "country_id": country_id,
            "region_id": region_id,
            "sector_id": sector_id,
            "sources": sources,
        }
        async with httpx.AsyncClient(
            base_url=self.settings.central_api_base_url.rstrip("/"),
            timeout=self.settings.sync_timeout_seconds,
            headers=headers,
        ) as client:
            response = await client.post("/api/sync/evidence/submit", json=payload)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"error": True, "detail": "Unexpected response."}

    def _state(
        self,
        scope: str,
        country_id: int | None,
        region_id: int | None,
        sector_id: int | None,
    ) -> SyncState:
        row = self.db.scalar(
            select(SyncState).where(
                SyncState.scope == scope,
                SyncState.country_id == (country_id or 0),
                SyncState.region_id == (region_id or 0),
                SyncState.sector_id == (sector_id or 0),
            )
        )
        if row is not None:
            return row
        row = SyncState(
            scope=scope,
            country_id=country_id or 0,
            region_id=region_id or 0,
            sector_id=sector_id or 0,
        )
        self.db.add(row)
        self.db.flush()
        return row

    async def _run_locked(
        self,
        scope: str,
        action: Callable[[], Awaitable[dict[str, object]]],
    ) -> dict[str, object]:
        if SYNC_LOCK.locked():
            return {
                "error": True,
                "locked": True,
                "detail": "A sync job is already running.",
                "status": sync_status(),
            }
        async with SYNC_LOCK:
            SYNC_STATUS.update(
                {
                    "running": True,
                    "scope": scope,
                    "last_started_at": datetime.utcnow().isoformat(),
                    "last_error": None,
                }
            )
            try:
                result = await action()
                SYNC_STATUS["last_result"] = result
                return result
            except Exception as exc:
                SYNC_STATUS["last_error"] = str(exc)
                raise
            finally:
                SYNC_STATUS.update(
                    {
                        "running": False,
                        "last_finished_at": datetime.utcnow().isoformat(),
                    }
                )

    def _delete_document(self, sync_id: str) -> int:
        if not sync_id:
            return 0
        row = self.db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.sync_id == sync_id))
        if row is None:
            return 0
        self.db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == row.id))
        self.db.delete(row)
        return 1

    def _upsert_document(self, scope: str, item: dict[str, object]) -> None:
        sync_id = str(item.get("sync_id") or "").strip()
        if not sync_id:
            return
        self._delete_document(sync_id)
        document = KnowledgeDocument(
            user_id=None,
            title=str(item.get("title") or "Synced knowledge document")[:255],
            source_type=str(item.get("source_type") or "sync")[:40],
            source_uri=str(item.get("source_uri") or "") or None,
            scope=scope,
            sync_id=sync_id,
            sync_version=int(item.get("sync_version") or 0),
            country_id=_int_or_none(item.get("country_id")),
            region_id=_int_or_none(item.get("region_id")),
            sector_id=_int_or_none(item.get("sector_id")),
        )
        self.db.add(document)
        self.db.flush()
        chunks = item.get("chunks")
        if not isinstance(chunks, list):
            return
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            content = str(chunk.get("content") or "").strip()
            if not content:
                continue
            self.db.add(
                KnowledgeChunk(
                    document_id=document.id,
                    user_id=None,
                    chunk_index=int(chunk.get("chunk_index") or index),
                    content=content,
                    source_type=str(chunk.get("source_type") or document.source_type)[:40],
                    source_uri=str(chunk.get("source_uri") or "") or None,
                    page_number=_int_or_none(chunk.get("page_number")),
                )
            )


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_status() -> dict[str, object]:
    return {**SYNC_STATUS, "locked": SYNC_LOCK.locked()}


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}
