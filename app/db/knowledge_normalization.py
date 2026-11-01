"""Backfill normalized source and usage records for pre-normalization documents."""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text

from app.db.session import engine


def _canonical_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not host:
        return None
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"[{host}]" if ":" in host else host
    if port and not default:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def backfill_knowledge_normalization() -> None:
    """Create source identities and usage records while retaining legacy metadata."""
    with engine.connect() as connection:
        needs_document_backfill = connection.execute(text("""
            SELECT kd.id
            FROM knowledge_documents kd
            LEFT JOIN knowledge_document_usages u ON u.knowledge_document_id = kd.id
            WHERE kd.source_id IS NULL OR u.id IS NULL
            LIMIT 1
        """)).first()
        needs_chunk_backfill = connection.execute(text(
            "SELECT id FROM knowledge_chunks WHERE shared_chunk_id IS NULL LIMIT 1"
        )).first()
        connection.commit()
        if needs_document_backfill is None and needs_chunk_backfill is None:
            return
        documents = connection.execute(text("""
            SELECT id, source_type, source_uri, source_id, scope, user_id, session_key,
                   scope_level, country_id, region_id, sector_id, created_at
            FROM knowledge_documents ORDER BY id
        """)).mappings().all()
        connection.commit()
        for document in documents:
            connection.close()
            connection = engine.connect()
            canonical_uri = _canonical_url(document["source_uri"])
            if canonical_uri:
                identity_value = f"url:{canonical_uri}"
            chunk_rows = connection.execute(text("""
                SELECT id, chunk_index, content, page_number, shared_chunk_id
                FROM knowledge_chunks
                WHERE document_id = :document_id
                ORDER BY chunk_index
            """), {"document_id": document["id"]}).mappings().all()
            content_hash = hashlib.sha256(
                "\n".join(chunk["content"] for chunk in chunk_rows).encode("utf-8")
            ).hexdigest()
            if not canonical_uri:
                identity_value = f"document:{content_hash if chunk_rows else document['id']}"
            identity_hash = hashlib.sha256(identity_value.encode("utf-8")).hexdigest()
            source = connection.execute(text(
                "SELECT id FROM knowledge_sources WHERE identity_hash = :identity_hash"
            ), {"identity_hash": identity_hash}).first()
            if source is None:
                source_id = str(uuid.uuid4())
                connection.execute(text("""
                    INSERT INTO knowledge_sources
                        (id, identity_hash, canonical_uri, source_type, content_sha256)
                    VALUES (:id, :identity_hash, :canonical_uri, :source_type, :content_sha256)
                """), {
                    "id": source_id,
                    "identity_hash": identity_hash,
                    "canonical_uri": canonical_uri,
                    "source_type": document["source_type"],
                    "content_sha256": content_hash,
                })
            else:
                source_id = source[0]
            connection.commit()
            source_version = connection.execute(text("""
                SELECT id FROM knowledge_source_versions
                WHERE source_id = :source_id AND content_sha256 = :content_sha256
            """), {"source_id": source_id, "content_sha256": content_hash}).first()
            if source_version is None:
                source_version_id = str(uuid.uuid4())
                connection.execute(text("""
                    INSERT INTO knowledge_source_versions (id, source_id, content_sha256)
                    VALUES (:id, :source_id, :content_sha256)
                """), {
                    "id": source_version_id,
                    "source_id": source_id,
                    "content_sha256": content_hash,
                })
            else:
                source_version_id = source_version[0]
            connection.commit()
            shared_by_index = {
                row["chunk_index"]: row["id"]
                for row in connection.execute(text("""
                    SELECT id, chunk_index FROM knowledge_source_chunks
                    WHERE source_version_id = :source_version_id
                """), {"source_version_id": source_version_id}).mappings()
            }
            missing_shared = [
                {
                    "id": str(uuid.uuid4()),
                    "source_version_id": source_version_id,
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "page_number": chunk["page_number"],
                }
                for chunk in chunk_rows
                if chunk["chunk_index"] not in shared_by_index
            ]
            for start in range(0, len(missing_shared), 100):
                batch = missing_shared[start:start + 100]
                if batch:
                    connection.execute(text("""
                        INSERT INTO knowledge_source_chunks
                            (id, source_version_id, chunk_index, content, page_number)
                        VALUES (:id, :source_version_id, :chunk_index, :content, :page_number)
                    """), batch)
                    shared_by_index.update({row["chunk_index"]: row["id"] for row in batch})
                    connection.commit()
            connection.execute(text(
                "UPDATE knowledge_documents SET source_id = :source_id WHERE id = :id AND source_id IS NULL"
            ), {"source_id": source_id, "id": document["id"]})
            connection.commit()
            links = [
                {"shared_id": shared_by_index[chunk["chunk_index"]], "id": chunk["id"]}
                for chunk in chunk_rows if chunk["shared_chunk_id"] is None
            ]
            for start in range(0, len(links), 20):
                try:
                    connection.execute(text(
                        "UPDATE knowledge_chunks SET shared_chunk_id = :shared_id WHERE id = :id"
                    ), links[start:start + 20])
                    connection.commit()
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed linking shared chunks for knowledge document {document['id']} "
                        f"at chunk batch {start // 20 + 1}"
                    ) from exc
            usage = connection.execute(text("""
                SELECT id FROM knowledge_document_usages
                WHERE knowledge_document_id = :document_id AND scope = :scope
                LIMIT 1
            """), {"document_id": document["id"], "scope": document["scope"]}).first()
            if usage is None:
                connection.execute(text("""
                    INSERT INTO knowledge_document_usages
                        (id, knowledge_document_id, scope, user_id, session_key, scope_level,
                         country_id, region_id, sector_id)
                    VALUES (:id, :document_id, :scope, :user_id, :session_key, :scope_level,
                            :country_id, :region_id, :sector_id)
                """), {
                    "id": str(uuid.uuid4()),
                    "document_id": document["id"],
                    "scope": document["scope"],
                    "user_id": document["user_id"],
                    "session_key": document["session_key"],
                    "scope_level": document["scope_level"],
                    "country_id": document["country_id"],
                    "region_id": document["region_id"],
                    "sector_id": document["sector_id"],
                })
            connection.commit()
            connection.close()
