"""Background import of policy URLs listed in ``Policies.xlsx``."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.models import (
    CustomHazardPolicyReference, KnowledgeChunk, KnowledgeDocument, Policy,
    PolicyKnowledgeDocument,
)
from app.services.document_language import language_is_english, translate_chunks_to_english
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    ORIGINAL_POLICY_SCOPE,
    POLICY_DOCUMENT_SCOPE,
    POLICY_REFERENCE_SCOPE,
    TEMPORARY_KB_SCOPE,
    ChunkDraft,
    KnowledgeBaseService,
    extract_url_chunks,
    normalize_source_url,
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
        select(KnowledgeDocument.id).outerjoin(
            PolicyKnowledgeDocument,
            PolicyKnowledgeDocument.knowledge_document_id == KnowledgeDocument.id,
        ).where(
            (PolicyKnowledgeDocument.policy_id == policy_id) | (KnowledgeDocument.policy_id == policy_id),
            KnowledgeDocument.scope == POLICY_DOCUMENT_SCOPE,
        ).limit(1)
    ) is not None


def _link_policy(db: Session, document: KnowledgeDocument, policy_id: str) -> None:
    if any(isinstance(row, PolicyKnowledgeDocument)
           and row.policy_id == policy_id
           and row.knowledge_document_id == document.id for row in db.new):
        return
    exists = db.scalar(select(PolicyKnowledgeDocument.id).where(
        PolicyKnowledgeDocument.policy_id == policy_id,
        PolicyKnowledgeDocument.knowledge_document_id == document.id,
    ).limit(1))
    if exists is None:
        db.add(PolicyKnowledgeDocument(policy_id=policy_id, knowledge_document_id=document.id))


def _document_for_url(db: Session, url: str) -> KnowledgeDocument | None:
    normalized = normalize_source_url(url)
    if not normalized:
        return None
    documents = db.scalars(
        select(KnowledgeDocument)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.scope == POLICY_DOCUMENT_SCOPE,
            KnowledgeDocument.source_type.in_((POLICY_URL_SOURCE_TYPE, "url")),
            KnowledgeDocument.user_id.is_(None),
        )
        .distinct()
        .order_by(KnowledgeDocument.faiss_indexed.desc(), KnowledgeDocument.created_at, KnowledgeDocument.id)
    ).all()
    return next((document for document in documents
                 if normalize_source_url(document.source_uri or "") == normalized), None)


def reusable_policy_reference_document(
    db: Session, url: str, *, user_id: str, session_key: str,
) -> KnowledgeDocument | None:
    """Reuse URL text only when this user and session may read the document."""
    normalized = normalize_source_url(url)
    if not normalized:
        return None
    rows = db.scalars(
        select(KnowledgeDocument)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.source_type.in_(("url", "policy_url", "original_policy_url")),
            or_(
                and_(
                    KnowledgeDocument.user_id == user_id,
                    KnowledgeDocument.scope == TEMPORARY_KB_SCOPE,
                    KnowledgeDocument.session_key == session_key,
                ),
                and_(
                    KnowledgeDocument.user_id == user_id,
                    KnowledgeDocument.scope == POLICY_REFERENCE_SCOPE,
                    or_(KnowledgeDocument.session_key.is_(None),
                        KnowledgeDocument.session_key == session_key),
                ),
                and_(
                    KnowledgeDocument.user_id.is_(None),
                    KnowledgeDocument.scope.in_((POLICY_DOCUMENT_SCOPE, ORIGINAL_POLICY_SCOPE)),
                ),
            ),
        )
        .distinct()
    ).all()
    priority = {
        TEMPORARY_KB_SCOPE: 0,
        POLICY_DOCUMENT_SCOPE: 1,
        ORIGINAL_POLICY_SCOPE: 2,
        POLICY_REFERENCE_SCOPE: 3,
    }
    return next(iter(sorted(
        (document for document in rows
         if normalize_source_url(document.source_uri or "") == normalized),
        key=lambda document: (priority[document.scope], document.created_at or datetime.max, document.id),
    )), None)


def consolidate_policy_url_documents(db: Session) -> int:
    """Keep one indexed document per URL and preserve every policy link."""
    documents = db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.scope == POLICY_DOCUMENT_SCOPE,
        KnowledgeDocument.source_type.in_((POLICY_URL_SOURCE_TYPE, "url")),
        KnowledgeDocument.user_id.is_(None),
    )).all()
    document_ids = [document.id for document in documents]
    if not document_ids:
        return 0
    chunk_counts = dict(db.execute(select(
        KnowledgeChunk.document_id, func.count(KnowledgeChunk.id),
    ).where(KnowledgeChunk.document_id.in_(document_ids)).group_by(KnowledgeChunk.document_id)).all())
    link_counts = dict(db.execute(select(
        PolicyKnowledgeDocument.knowledge_document_id, func.count(PolicyKnowledgeDocument.id),
    ).where(PolicyKnowledgeDocument.knowledge_document_id.in_(document_ids))
       .group_by(PolicyKnowledgeDocument.knowledge_document_id)).all())
    documents.sort(key=lambda document: (
        not bool(chunk_counts.get(document.id)),
        -int(bool(document.faiss_indexed)),
        -chunk_counts.get(document.id, 0),
        -link_counts.get(document.id, 0),
        document.created_at or datetime.max,
        document.id,
    ))
    by_url: dict[str, KnowledgeDocument] = {}
    removed = 0
    for document in documents:
        url = normalize_source_url(document.source_uri or "")
        if not url:
            continue
        canonical = by_url.setdefault(url, document)
        if document.policy_id:
            _link_policy(db, canonical, document.policy_id)
        for policy_id in db.scalars(select(PolicyKnowledgeDocument.policy_id).where(
            PolicyKnowledgeDocument.knowledge_document_id == document.id,
        )).all():
            _link_policy(db, canonical, policy_id)
        if canonical.id == document.id:
            continue
        duplicate_chunks = db.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document.id,
        ).order_by(KnowledgeChunk.chunk_index)).all()
        references = db.scalars(select(CustomHazardPolicyReference).where(
            CustomHazardPolicyReference.knowledge_document_id == document.id,
        )).all()
        existing_hazards = set(db.scalars(select(CustomHazardPolicyReference.custom_hazard_id).where(
            CustomHazardPolicyReference.knowledge_document_id == canonical.id,
        )).all())
        for reference in references:
            if reference.custom_hazard_id not in existing_hazards:
                db.add(CustomHazardPolicyReference(
                    custom_hazard_id=reference.custom_hazard_id,
                    knowledge_document_id=canonical.id,
                ))
        db.commit()
        KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)._remove_vectors(
            [chunk.id for chunk in duplicate_chunks]
        )
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
        db.execute(delete(CustomHazardPolicyReference).where(
            CustomHazardPolicyReference.knowledge_document_id == document.id,
        ))
        db.execute(delete(PolicyKnowledgeDocument).where(
            PolicyKnowledgeDocument.knowledge_document_id == document.id,
        ))
        db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id == document.id))
        db.commit()
        removed += 1
    db.commit()
    return removed


def migrate_policy_documents_from_main(db: Session) -> int:
    """Move linked policy documents and their existing vectors to the policy index."""
    documents = db.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.scope == MAIN_KB_SCOPE,
            KnowledgeDocument.policy_id.is_not(None),
            KnowledgeDocument.user_id.is_(None),
        )
    ).all()
    if not documents:
        return 0
    chunks = db.scalars(
        select(KnowledgeChunk).where(
            KnowledgeChunk.document_id.in_([document.id for document in documents])
        )
    ).all()
    chunk_ids = [chunk.id for chunk in chunks]
    main_service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    policy_service = KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)
    vectors = main_service._reconstruct_vectors(chunk_ids)
    if vectors:
        policy_service._remove_vectors(list(vectors))
        policy_service._add_vectors(list(vectors), list(vectors.values()))
    main_service._remove_vectors(chunk_ids)
    chunks_by_document: dict[str, list[KnowledgeChunk]] = {}
    for chunk in chunks:
        chunk.faiss_indexed = int(chunk.id in vectors)
        chunks_by_document.setdefault(chunk.document_id, []).append(chunk)
    for document in documents:
        document.scope = POLICY_DOCUMENT_SCOPE
        if document.policy_id:
            _link_policy(db, document, document.policy_id)
        document.faiss_indexed = int(bool(chunks_by_document.get(document.id)) and all(
            chunk.faiss_indexed for chunk in chunks_by_document[document.id]
        ))
    db.flush()
    columns = {column["name"] for column in inspect(db.connection()).get_columns("knowledge_documents")}
    if {"sync_revision", "sync_updated_at"} <= columns:
        updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        for document in documents:
            db.execute(
                text("""
                    UPDATE knowledge_documents
                    SET sync_revision = COALESCE(sync_revision, 0) + 1,
                        sync_updated_at = :updated_at
                    WHERE id = :document_id
                """),
                {"updated_at": updated_at, "document_id": document.id},
            )
    db.commit()
    logger.info("Moved %s linked policy documents to the policy index", len(documents))
    return len(documents)


async def seed_policy_documents_from_urls(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, int]:
    """Import policy URLs with bounded preparation concurrency and serial writes."""
    imported = skipped = failed = 0
    with session_factory() as db:
        consolidate_policy_url_documents(db)
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
        pending_by_url: dict[str, list[str]] = {}
        for policy in policies:
            url = str(policy.policy_url or "").strip()
            if not url or _already_seeded(db, policy.id):
                skipped += 1
                continue
            existing = _document_for_url(db, url)
            if existing is not None:
                _link_policy(db, existing, policy.id)
                skipped += 1
                continue
            url_key = normalize_source_url(url) or url
            if url_key in pending_by_url:
                pending_by_url[url_key].append(policy.id)
                skipped += 1
                continue
            pending_by_url[url_key] = [policy.id]
            pending.append(
                _PolicySeedItem(
                    policy_id=policy.id,
                    url=url,
                    title=str(policy.policy or url),
                    language=policy.language,
                )
            )
        db.commit()

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
                existing = _document_for_url(db, item.item.url)
                if existing is not None:
                    for policy_id in pending_by_url[normalize_source_url(item.item.url) or item.item.url]:
                        _link_policy(db, existing, policy_id)
                    db.commit()
                    skipped += 1
                    continue
                try:
                    result = await KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE).ingest_chunks(
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
                    for policy_id in pending_by_url[normalize_source_url(item.item.url) or item.item.url]:
                        _link_policy(db, document, policy_id)
                    # Keep the first policy as legacy metadata for sync clients.
                    # All associations, including this one, live in the link table.
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
