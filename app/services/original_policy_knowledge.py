"""Manual preparation, indexing, and cutover for original-policy documents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    Country, KnowledgeChunk, KnowledgeDocument, OriginalPolicy,
    OriginalPolicyKnowledgeDocument, Policy,
)
from app.services.document_language import (
    detect_document_language, language_is_english, translate_chunks_to_english,
)
from app.services.knowledge_base import (
    ORIGINAL_POLICY_SCOPE, POLICY_DOCUMENT_SCOPE, ChunkDraft, KnowledgeBaseService,
    extract_file_chunks, extract_url_chunks, normalize_source_url,
    vector_id_for_chunk_id,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_FILES_PARENT = PROJECT_ROOT / "kb" / "additional"
POLICY_FILES = next(
    (path for path in _POLICY_FILES_PARENT.iterdir()
     if path.is_dir() and path.name.casefold() == "policy documents"),
    _POLICY_FILES_PARENT / "policy documents",
) if _POLICY_FILES_PARENT.is_dir() else _POLICY_FILES_PARENT / "policy documents"
PENDING_SCOPE = "policy_pending"
ORIGINAL_URL_SOURCE = "original_policy_url"
ORIGINAL_FILE_SOURCE = "original_policy_file"
PLACEHOLDERS = {"", "not provided", "see below"}


@dataclass(frozen=True)
class Source:
    uri: str
    source_type: str
    policy_ids: tuple[str, ...]
    title: str
    country_id: str | None
    fallback_url: str | None = None


def discover_sources(db: Session) -> tuple[list[Source], list[dict[str, str]]]:
    """Resolve document references within each policy's country folder."""
    rows = db.execute(
        select(OriginalPolicy, Country.name)
        .join(Country, Country.id == OriginalPolicy.country_id)
        .order_by(OriginalPolicy.excel_row_number, OriginalPolicy.id)
    ).all()
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    skipped: list[dict[str, str]] = []
    for policy, country in rows:
        reference = str(policy.policy_document or "").strip()
        if reference.casefold() in PLACEHOLDERS:
            skipped.append({"policy": policy.policy_name, "reason": "placeholder"})
            continue
        url = normalize_source_url(reference)
        basename = unquote(PurePosixPath(urlsplit(reference).path).name) if url else reference
        folder = POLICY_FILES / country
        matches = sorted(
            (path for path in folder.rglob("*") if path.is_file() and path.name.casefold() == basename.casefold()),
            key=lambda path: str(path).casefold(),
        ) if folder.is_dir() and basename else []
        if len(matches) > 1:
            skipped.append({"policy": policy.policy_name, "reason": f"ambiguous local file: {basename}"})
            continue
        if matches:
            path = matches[0]
            uri = path.relative_to(PROJECT_ROOT).as_posix()
            source_type = ORIGINAL_FILE_SOURCE
            fallback_url = url or None
        elif url:
            uri = url
            source_type = ORIGINAL_URL_SOURCE
            fallback_url = None
        else:
            skipped.append({"policy": policy.policy_name, "reason": f"file missing: {reference}"})
            continue
        key = (source_type, uri)
        if key not in grouped:
            grouped[key] = {
                "policy_ids": [], "title": policy.policy_name,
                "country_ids": set(), "fallback_url": fallback_url,
            }
        grouped[key]["policy_ids"].append(policy.id)
        grouped[key]["country_ids"].add(policy.country_id)
    sources = [
        Source(uri, kind, tuple(dict.fromkeys(value["policy_ids"])),
               str(value["title"]),
               next(iter(value["country_ids"])) if len(value["country_ids"]) == 1 else None,
               value["fallback_url"])
        for (kind, uri), value in grouped.items()
    ]
    return sources, skipped


def plan(session_factory: sessionmaker[Session] = SessionLocal) -> dict[str, object]:
    with session_factory() as db:
        sources, skipped = discover_sources(db)
        return {
            "sources": len(sources),
            "local_files": sum(source.source_type == ORIGINAL_FILE_SOURCE for source in sources),
            "remote_urls": sum(source.source_type == ORIGINAL_URL_SOURCE for source in sources),
            "policies_with_sources": len({policy_id for source in sources for policy_id in source.policy_ids}),
            "skipped": skipped,
        }


def _existing_document(db: Session, source: Source) -> KnowledgeDocument | None:
    return db.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.source_type == source.source_type,
        KnowledgeDocument.source_uri == source.uri,
        KnowledgeDocument.scope.in_((PENDING_SCOPE, ORIGINAL_POLICY_SCOPE)),
    ).limit(1))


def _link_policies(db: Session, document: KnowledgeDocument, policy_ids: tuple[str, ...]) -> None:
    existing = set(db.scalars(select(OriginalPolicyKnowledgeDocument.original_policy_id).where(
        OriginalPolicyKnowledgeDocument.knowledge_document_id == document.id,
    )).all())
    for policy_id in policy_ids:
        if policy_id not in existing:
            db.add(OriginalPolicyKnowledgeDocument(
                original_policy_id=policy_id, knowledge_document_id=document.id,
            ))


async def _extract(source: Source) -> tuple[list[ChunkDraft], str, str]:
    if source.source_type == ORIGINAL_FILE_SOURCE:
        path = PROJECT_ROOT / source.uri
        try:
            chunks = extract_file_chunks(path.name, path.read_bytes())
            if chunks:
                return chunks, source.uri, source.source_type
        except Exception:
            if not source.fallback_url:
                raise
        if not source.fallback_url:
            raise ValueError(f"No readable text in {source.uri}")
        chunks = await extract_url_chunks(source.fallback_url, get_settings().max_url_ingest_bytes)
        return chunks, source.fallback_url, ORIGINAL_URL_SOURCE
    chunks = await extract_url_chunks(source.uri, get_settings().max_url_ingest_bytes)
    return chunks, source.uri, source.source_type


async def prepare(session_factory: sessionmaker[Session] = SessionLocal) -> dict[str, object]:
    """Store translated chunks without generating embeddings or FAISS vectors."""
    with session_factory() as db:
        sources, skipped = discover_sources(db)
    result: dict[str, object] = {"prepared": 0, "existing": 0, "failed": [], "skipped": skipped}
    for source in sources:
        with session_factory() as db:
            existing = _existing_document(db, source)
            if existing is not None:
                _link_policies(db, existing, source.policy_ids)
                db.commit()
                result["existing"] += 1
                continue
        try:
            chunks, uri, source_type = await _extract(source)
            if not chunks:
                raise ValueError("No readable text was extracted")
            language = await detect_document_language(chunks)
            if not language_is_english(language):
                if not get_settings().enable_english_translation:
                    raise ValueError(f"English translation is disabled for {language}")
                chunks = list(await translate_chunks_to_english(chunks, language))
            if not chunks or any(not chunk.content.strip() for chunk in chunks):
                raise ValueError("English chunks are empty")
            with session_factory() as db:
                actual = Source(uri, source_type, source.policy_ids, source.title, source.country_id)
                document = _existing_document(db, actual)
                if document is None:
                    document = KnowledgeDocument(
                        title=source.title[:255], source_type=source_type, source_uri=uri,
                        scope=PENDING_SCOPE, scope_level="global", country_id=source.country_id,
                        faiss_indexed=0,
                    )
                    db.add(document)
                    db.flush()
                    db.add_all([
                        KnowledgeChunk(
                            document_id=document.id, chunk_index=index, content=chunk.content,
                            source_type=source_type, source_uri=uri, page_number=chunk.page_number,
                            scope_level="global", country_id=source.country_id, faiss_indexed=0,
                        ) for index, chunk in enumerate(chunks)
                    ])
                _link_policies(db, document, source.policy_ids)
                db.commit()
            result["prepared"] += 1
            logger.info("Prepared original policy source %s (%s chunks)", uri, len(chunks))
        except Exception as exc:
            logger.warning("Original policy preparation failed source=%s: %s", source.uri, exc)
            result["failed"].append({"source": source.uri, "error": str(exc)})
    return result


async def index(session_factory: sessionmaker[Session] = SessionLocal) -> dict[str, object]:
    """Explicitly embed pending chunks into the original-policy FAISS index."""
    with session_factory() as db:
        document_ids = db.scalars(select(KnowledgeDocument.id).where(
            KnowledgeDocument.scope == PENDING_SCOPE,
            KnowledgeDocument.source_type.in_((ORIGINAL_URL_SOURCE, ORIGINAL_FILE_SOURCE)),
        ).join(
            OriginalPolicyKnowledgeDocument,
            OriginalPolicyKnowledgeDocument.knowledge_document_id == KnowledgeDocument.id,
        ).distinct().order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)).all()
    result: dict[str, object] = {"indexed": 0, "failed": []}
    for document_id in document_ids:
        with session_factory() as db:
            document = db.get(KnowledgeDocument, document_id)
            if document is None or document.scope != PENDING_SCOPE:
                continue
            chunks = db.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document_id,
            ).order_by(KnowledgeChunk.chunk_index)).all()
            if not chunks:
                result["failed"].append({"document_id": document_id, "error": "No chunks"})
                continue
            service = KnowledgeBaseService(db, None, scope=ORIGINAL_POLICY_SCOPE)
            try:
                embeddings = await service._embed_many([chunk.content for chunk in chunks])
                if len(embeddings) != len(chunks):
                    raise ValueError("Embedding count differs from chunk count")
                await service._rebuild_index_if_needed(embeddings[0])
                service._add_vectors([chunk.id for chunk in chunks], embeddings)
                for chunk in chunks:
                    chunk.faiss_indexed = 1
                document.faiss_indexed = 1
                document.scope = ORIGINAL_POLICY_SCOPE
                db.commit()
                result["indexed"] += 1
                logger.info("Indexed original policy document %s (%s chunks)", document_id, len(chunks))
            except Exception as exc:
                db.rollback()
                logger.warning("Original policy indexing failed document_id=%s: %s", document_id, exc)
                result["failed"].append({"document_id": document_id, "error": str(exc)})
    return result


def _indexed_policy_ids(db: Session) -> set[str]:
    rows = db.execute(select(
        OriginalPolicyKnowledgeDocument.original_policy_id, KnowledgeDocument,
    ).join(KnowledgeDocument, KnowledgeDocument.id == OriginalPolicyKnowledgeDocument.knowledge_document_id).where(
        KnowledgeDocument.scope == ORIGINAL_POLICY_SCOPE,
        KnowledgeDocument.faiss_indexed == 1,
        KnowledgeDocument.source_type.in_((ORIGINAL_URL_SOURCE, ORIGINAL_FILE_SOURCE)),
    )).all()
    return {policy_id for policy_id, _ in rows}


async def cleanup(session_factory: sessionmaker[Session] = SessionLocal) -> dict[str, object]:
    """Remove XLSX policy documents only after original sources are indexed."""
    with session_factory() as db:
        sources, _ = discover_sources(db)
        required = {policy_id for source in sources for policy_id in source.policy_ids}
        if not required:
            raise ValueError("No eligible original-policy sources were found; cleanup is not allowed")
        missing = required - _indexed_policy_ids(db)
        if missing:
            raise ValueError(f"Original policy indexing is incomplete for {len(missing)} policies")
        original_docs = db.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.scope == ORIGINAL_POLICY_SCOPE,
            KnowledgeDocument.source_type.in_((ORIGINAL_URL_SOURCE, ORIGINAL_FILE_SOURCE)),
        )).all()
        service = KnowledgeBaseService(db, None, scope=ORIGINAL_POLICY_SCOPE)
        index = service._load_existing_index()
        import faiss
        actual_ids = {int(value) for value in faiss.vector_to_array(index.id_map)}
        expected_ids = {
            vector_id_for_chunk_id(chunk_id)
            for chunk_id in db.scalars(select(KnowledgeChunk.id).join(
                KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id,
            ).where(
                KnowledgeDocument.scope == ORIGINAL_POLICY_SCOPE,
                KnowledgeChunk.faiss_indexed == 1,
            )).all()
        }
        if actual_ids != expected_ids:
            raise ValueError(
                f"Original-policy FAISS index differs from indexed chunks "
                f"(missing={len(expected_ids - actual_ids)}, extra={len(actual_ids - expected_ids)})"
            )
        for document in original_docs:
            chunks = db.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document.id,
            )).all()
            if not chunks or not all(chunk.faiss_indexed and vector_id_for_chunk_id(chunk.id) in actual_ids for chunk in chunks):
                raise ValueError(f"Original policy document is not fully indexed: {document.id}")
        old_ids = db.scalars(select(KnowledgeDocument.id).join(
            Policy, Policy.id == KnowledgeDocument.policy_id,
        ).where(
            KnowledgeDocument.scope == POLICY_DOCUMENT_SCOPE,
            Policy.source == "xlsx",
        )).all()
    removed = 0
    for document_id in old_ids:
        with session_factory() as db:
            if await KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE).delete_document(document_id):
                removed += 1
    with session_factory() as db:
        service = KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)
        actual_ids = (
            {int(value) for value in faiss.vector_to_array(service._load_existing_index().id_map)}
            if service._index_path.exists() else set()
        )
        expected_ids = {
            vector_id_for_chunk_id(chunk_id)
            for chunk_id in db.scalars(select(KnowledgeChunk.id).join(
                KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id,
            ).where(
                KnowledgeDocument.scope.in_((POLICY_DOCUMENT_SCOPE, "policy_reference")),
                KnowledgeChunk.faiss_indexed == 1,
            )).all()
        }
        if actual_ids != expected_ids:
            raise ValueError("Policy FAISS index differs from remaining indexed chunks after cleanup")
    return {"removed_old_policy_documents": removed, "retained_original_documents": len(original_docs)}
