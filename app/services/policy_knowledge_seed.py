"""Background import of policy URLs listed in ``Policies.xlsx``."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.models import KnowledgeDocument, Policy
from app.services.document_language import language_is_english, translate_chunks_to_english
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    ChunkDraft,
    KnowledgeBaseService,
    extract_url_chunks,
)

logger = logging.getLogger(__name__)

POLICY_URL_SOURCE_TYPE = "policy_url"
POLICY_IMPORT_CONCURRENCY = 2


@dataclass(frozen=True)
class _PolicySeedItem:
    policy_id: str
    url: str
    title: str
    language: str | None


@dataclass(frozen=True)
class _PreparedPolicyDocument:
    item: _PolicySeedItem
    chunks: list[ChunkDraft] | None = None
    error: str | None = None


def policy_language_is_english(language: str | None) -> bool:
    """Treat a blank source-language cell as English."""
    return not str(language or "").strip() or language_is_english(language)


async def translate_policy_chunks_to_english(
    chunks: list[ChunkDraft], language: str | None
) -> list[ChunkDraft]:
    """Translate extracted chunks before they are persisted or embedded."""
    if policy_language_is_english(language):
        return chunks

    return list(await translate_chunks_to_english(chunks, str(language or "")))


def _already_seeded(db: Session, policy_id: str) -> bool:
    return db.scalar(
        select(KnowledgeDocument.id).where(
            KnowledgeDocument.policy_id == policy_id,
            KnowledgeDocument.scope == MAIN_KB_SCOPE,
        ).limit(1)
    ) is not None


async def seed_policy_documents_from_urls(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, int]:
    """Import policy URLs with bounded preparation concurrency and serial writes."""
    imported = skipped = failed = 0
    with session_factory() as db:
        policies = db.scalars(
            select(Policy)
            .where(Policy.policy_url.is_not(None), Policy.policy_url != "")
            .order_by(Policy.excel_row_number, Policy.id)
        ).all()
        policies.sort(
            key=lambda policy: (
                not policy_language_is_english(policy.language),
                policy.excel_row_number is None,
                policy.excel_row_number or 0,
                policy.id,
            )
        )
        pending: list[_PolicySeedItem] = []
        for policy in policies:
            url = str(policy.policy_url or "").strip()
            if not url or _already_seeded(db, policy.id):
                skipped += 1
                continue
            pending.append(
                _PolicySeedItem(
                    policy_id=policy.id,
                    url=url,
                    title=str(policy.policy or url),
                    language=policy.language,
                )
            )

    # Keep only two potentially expensive URL/translation operations active at
    # once. Persist each prepared batch serially because SQLite and FAISS both
    # have single-writer constraints in the desktop/offline runtime.
    for offset in range(0, len(pending), POLICY_IMPORT_CONCURRENCY):
        batch = pending[offset : offset + POLICY_IMPORT_CONCURRENCY]
        prepared = await asyncio.gather(
            *(_prepare_policy_document(item) for item in batch)
        )
        with session_factory() as db:
            for item in prepared:
                if item.error:
                    failed += 1
                    logger.warning(
                        "Policy knowledge import failed policy_id=%s url=%s detail=%s",
                        item.item.policy_id,
                        item.item.url,
                        item.error,
                    )
                    continue
                policy = db.get(Policy, item.item.policy_id)
                if policy is None or _already_seeded(db, item.item.policy_id):
                    skipped += 1
                    continue
                try:
                    result = await KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE).ingest_chunks(
                        item.chunks or [],
                        item.item.title,
                        POLICY_URL_SOURCE_TYPE,
                        item.item.url,
                        # A temporary embedding outage must not prevent lexical KB search.
                        allow_lexical_only=True,
                    )
                    if result.get("error"):
                        raise ValueError(str(result.get("detail") or "Knowledge ingestion failed."))
                    document = db.get(KnowledgeDocument, str(result["document_id"]))
                    if document is None:
                        raise RuntimeError("Policy document was stored but could not be linked.")
                    document.policy_id = policy.id
                    document.country_id = policy.country_id
                    document.sector_id = policy.sector_id
                    db.commit()
                    imported += 1
                    logger.info("Seeded policy knowledge policy_id=%s url=%s", policy.id, item.item.url)
                except Exception as exc:
                    db.rollback()
                    failed += 1
                    logger.warning(
                        "Policy knowledge import failed policy_id=%s url=%s detail=%s",
                        item.item.policy_id,
                        item.item.url,
                        exc,
                    )
    return {"imported": imported, "skipped": skipped, "failed": failed}


async def _prepare_policy_document(item: _PolicySeedItem) -> _PreparedPolicyDocument:
    """Download and translate one policy without opening a DB write transaction."""
    try:
        chunks = await extract_url_chunks(item.url)
        if not chunks:
            raise ValueError("No readable text was found at the policy URL.")
        english_chunks = await translate_policy_chunks_to_english(chunks, item.language)
        return _PreparedPolicyDocument(item=item, chunks=english_chunks)
    except Exception as exc:
        return _PreparedPolicyDocument(item=item, error=str(exc))
