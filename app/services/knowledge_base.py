import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import perf_counter

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.document_text import (
    compact_text,
    extract_docx_text,
    extract_pdf_page_texts,
    html_to_text,
)
from app.services.grounding_models import GroundingModelService
from app.services.llm_logging import log_llm_exchange, new_llm_request_id

try:
    import faiss
    import numpy as np
except ModuleNotFoundError:
    faiss = None
    np = None


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
FAISS_LOCK = Lock()
LEXICAL_WEIGHT = 0.55
SCOPE_MATCH_WEIGHT = 0.35
MAIN_KB_SCOPE = "main"
TEMPORARY_KB_SCOPE = "temporary"
VALIDATED_EVIDENCE_SCOPE = "validated_evidence"
SECTOR_PROMPT_SCOPE = "sector_prompt"
QUARANTINED_SCOPE = "quarantined"


@dataclass(frozen=True)
class ChunkDraft:
    content: str
    page_number: int | None = None


@dataclass(frozen=True)
class QueryFeatures:
    text: str
    normalized_text: str
    tokens: set[str]
    phrases: list[str]
    page_numbers: set[int]


class KnowledgeBaseService:
    def __init__(
        self,
        db: Session,
        user_id: int | None,
        scope: str = MAIN_KB_SCOPE,
        session_key: str | None = None,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.scope = scope
        self.session_key = session_key
        self.country_id = country_id
        self.region_id = region_id
        self.sector_id = sector_id
        self.settings = get_settings()
        self.grounding_models = GroundingModelService()

    async def ingest_url(
        self,
        url: str,
        title: str | None = None,
        *,
        allow_lexical_only: bool = False,
    ) -> dict[str, object]:
        drafts = await extract_url_chunks(url)
        return await self.ingest_chunks(
            drafts,
            title or url,
            "url",
            url,
            allow_lexical_only=allow_lexical_only,
        )

    async def ingest_file(
        self,
        filename: str,
        content: bytes,
        *,
        allow_lexical_only: bool = False,
    ) -> dict[str, object]:
        drafts = extract_file_chunks(filename, content)
        return await self.ingest_chunks(
            drafts,
            filename,
            file_source_type(filename),
            filename,
            allow_lexical_only=allow_lexical_only,
        )

    async def ingest_text(
        self, text: str, title: str, source_type: str, source_uri: str | None = None
    ) -> dict[str, object]:
        return await self.ingest_chunks(chunk_text(compact_text(text)), title, source_type, source_uri)

    async def ingest_chunks(
        self,
        chunks: list[ChunkDraft],
        title: str,
        source_type: str,
        source_uri: str | None = None,
        allow_lexical_only: bool = False,
    ) -> dict[str, object]:
        chunks = [chunk for chunk in chunks if chunk.content.strip()]
        if not chunks:
            return {"error": True, "detail": "No readable knowledge-base text was found."}

        if not allow_lexical_only:
            self._require_faiss()
        scope_level = self._scope_level()
        document = KnowledgeDocument(
            user_id=self.user_id,
            title=title[:255] or "Knowledge document",
            source_type=source_type,
            source_uri=source_uri,
            scope=self.scope,
            scope_level=scope_level,
            session_key=(
                self.session_key
                if self.scope in {TEMPORARY_KB_SCOPE, QUARANTINED_SCOPE}
                else None
            ),
            country_id=self.country_id if self.scope == VALIDATED_EVIDENCE_SCOPE else None,
            region_id=self.region_id if self.scope == VALIDATED_EVIDENCE_SCOPE else None,
            sector_id=self.sector_id if self.scope == VALIDATED_EVIDENCE_SCOPE else None,
        )
        self.db.add(document)
        self.db.flush()

        chunk_rows: list[KnowledgeChunk] = []
        for index, chunk in enumerate(chunks):
            row = KnowledgeChunk(
                document_id=document.id,
                user_id=self.user_id,
                chunk_index=index,
                content=chunk.content,
                source_type=source_type,
                source_uri=source_uri,
                page_number=chunk.page_number,
                scope_level=scope_level,
                country_id=document.country_id,
                region_id=document.region_id,
                sector_id=document.sector_id,
            )
            self.db.add(row)
            chunk_rows.append(row)
        self.db.flush()

        vector_indexed = False
        vector_error = ""
        if faiss is not None and np is not None:
            try:
                embeddings = await self._embed_many([row.content for row in chunk_rows])
                self._add_vectors([row.id for row in chunk_rows], embeddings)
                vector_indexed = True
            except Exception as exc:
                if not allow_lexical_only:
                    self.db.rollback()
                    raise
                vector_error = str(exc)
        elif not allow_lexical_only:
            self.db.rollback()
            self._require_faiss()
        else:
            vector_error = "FAISS or NumPy is unavailable; using DB lexical retrieval."

        self.db.commit()
        self.db.refresh(document)
        return {
            "error": False,
            "document_id": document.id,
            "title": document.title,
            "chunks": len(chunk_rows),
            "vector_indexed": vector_indexed,
            "vector_error": vector_error,
        }

    def list_documents(self) -> list[dict[str, object]]:
        rows = self.db.scalars(
            select(KnowledgeDocument)
            .where(
                *self._document_access_filters(),
                KnowledgeDocument.scope == self.scope,
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        ).all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "source_type": row.source_type,
                "source_uri": row.source_uri,
                "scope_level": row.scope_level,
                "country_id": row.country_id,
                "region_id": row.region_id,
                "sector_id": row.sector_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    async def delete_document(self, document_id: int) -> bool:
        chunk_ids = self.db.scalars(
            select(KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.id == document_id,
                *self._document_access_filters(),
                KnowledgeDocument.scope == self.scope,
            )
        ).all()
        if not chunk_ids:
            row = self.db.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    *self._document_access_filters(),
                    KnowledgeDocument.scope == self.scope,
                )
            )
            if row is None:
                return False

        self._remove_vectors(chunk_ids)
        result = self.db.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                *self._document_access_filters(),
                KnowledgeDocument.scope == self.scope,
            )
        )
        self.db.commit()
        return bool(result.rowcount)

    async def search(
        self,
        query: str,
        limit: int = 5,
        source_uris: list[str] | None = None,
    ) -> list[dict[str, object]]:
        overfetch = max(limit * 25, 100)
        source_uri_filter = [item for item in (source_uris or []) if item]
        vector_scores = await self._vector_search_scores(query, overfetch)
        candidates: dict[int, tuple[KnowledgeChunk, KnowledgeDocument, float]] = {}
        if vector_scores:
            for chunk, document in self._knowledge_rows(
                source_uri_filter,
                list(vector_scores),
            ):
                candidates[chunk.id] = (chunk, document, vector_scores.get(chunk.id, 0.0))

        lexical_rows = self._knowledge_rows(source_uri_filter)
        query_features = build_query_features(query)
        lexical_scores = {
            chunk.id: lexical_score(query_features, chunk, document)
            for chunk, document in lexical_rows
        }
        for chunk, document in sorted(
            lexical_rows,
            key=lambda row: lexical_scores.get(row[0].id, 0.0),
            reverse=True,
        )[:overfetch]:
            if lexical_scores.get(chunk.id, 0.0) <= 0:
                continue
            candidates.setdefault(chunk.id, (chunk, document, vector_scores.get(chunk.id, 0.0)))

        ranked = sorted(
            candidates.values(),
            key=lambda row: (
                row[2]
                + (LEXICAL_WEIGHT * lexical_scores.get(row[0].id, 0.0))
                + (SCOPE_MATCH_WEIGHT * self._scope_relevance_score(row[1]))
            ),
            reverse=True,
        )
        results = self._search_results(ranked, lexical_scores, limit)
        grounded = await self.grounding_models.ground_results(query, results)
        if self.scope == VALIDATED_EVIDENCE_SCOPE:
            return sorted(
                grounded,
                key=lambda result: (
                    float(result.get("scope_score") or 0.0),
                    float(result.get("score") or 0.0),
                ),
                reverse=True,
            )
        return grounded

    async def _vector_search_scores(self, query: str, overfetch: int) -> dict[int, float]:
        if faiss is None or np is None or not self._index_path.exists():
            return {}
        try:
            query_vector = await self._embed(query)
            vector = normalize_vectors([query_vector])
            with FAISS_LOCK:
                index = self._load_index(len(query_vector))
                if not index.ntotal:
                    return {}
                scores, ids = index.search(vector, min(overfetch, index.ntotal))
        except (httpx.HTTPError, ValueError):
            return {}
        return {
            int(chunk_id): float(score)
            for score, chunk_id in zip(scores[0], ids[0], strict=False)
            if int(chunk_id) >= 0
        }

    def _knowledge_rows(
        self,
        source_uri_filter: list[str],
        chunk_ids: list[int] | None = None,
    ) -> list[tuple[KnowledgeChunk, KnowledgeDocument]]:
        filters = [
            KnowledgeDocument.scope == self.scope,
            *self._document_access_filters(),
        ]
        if self.scope == TEMPORARY_KB_SCOPE:
            filters.append(KnowledgeDocument.session_key == self.session_key)
        if self.scope == VALIDATED_EVIDENCE_SCOPE:
            if self.country_id is not None:
                filters.append(
                    or_(
                        KnowledgeDocument.country_id == self.country_id,
                        KnowledgeDocument.scope_level == "global",
                    )
                )
            if self.sector_id is not None:
                filters.append(KnowledgeDocument.sector_id == self.sector_id)
            if self.region_id is not None:
                filters.append(
                    or_(
                        KnowledgeDocument.region_id == self.region_id,
                        KnowledgeDocument.region_id.is_(None),
                        KnowledgeDocument.scope_level == "global",
                    )
                )
            else:
                filters.append(
                    or_(
                        KnowledgeDocument.region_id.is_(None),
                        KnowledgeDocument.scope_level == "global",
                    )
                )
        if source_uri_filter:
            filters.append(KnowledgeDocument.source_uri.in_(source_uri_filter))
        if chunk_ids is not None:
            filters.append(KnowledgeChunk.id.in_(chunk_ids))
        rows = self.db.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(*filters)
        ).all()
        return [
            (chunk, document)
            for chunk, document in rows
            if not is_index_page_text(chunk.content)
        ]

    def _search_results(
        self,
        ranked: list[tuple[KnowledgeChunk, KnowledgeDocument, float]],
        lexical_scores: dict[int, float],
        limit: int,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for chunk, document, vector_score in ranked[:limit]:
            lexical = lexical_scores.get(chunk.id, 0.0)
            scope_score = self._scope_relevance_score(document)
            combined_score = vector_score + (LEXICAL_WEIGHT * lexical) + (SCOPE_MATCH_WEIGHT * scope_score)
            results.append(
                {
                    "document_id": document.id,
                    "title": document.title,
                    "source_type": chunk.source_type,
                    "source_uri": chunk.source_uri,
                    "page_number": chunk.page_number,
                    "score": round(float(combined_score), 4),
                    "vector_score": round(float(vector_score), 4),
                    "lexical_score": round(float(lexical), 4),
                    "scope_score": round(float(scope_score), 4),
                    "scope_level": document.scope_level,
                    "country_id": document.country_id,
                    "region_id": document.region_id,
                    "sector_id": document.sector_id,
                    "content": chunk.content,
                }
            )
        return results

    def delete_temporary_documents(self, document_ids: list[int] | None = None) -> int:
        if self.scope != TEMPORARY_KB_SCOPE or not self.session_key:
            return 0
        query = select(KnowledgeChunk.id).join(
            KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id
        ).where(
            KnowledgeDocument.user_id == self.user_id,
            KnowledgeDocument.scope == TEMPORARY_KB_SCOPE,
            KnowledgeDocument.session_key == self.session_key,
        )
        document_query = delete(KnowledgeDocument).where(
            KnowledgeDocument.user_id == self.user_id,
            KnowledgeDocument.scope == TEMPORARY_KB_SCOPE,
            KnowledgeDocument.session_key == self.session_key,
        )
        if document_ids:
            query = query.where(KnowledgeDocument.id.in_(document_ids))
            document_query = document_query.where(KnowledgeDocument.id.in_(document_ids))
        chunk_ids = list(self.db.scalars(query).all())
        self._remove_vectors(chunk_ids)
        result = self.db.execute(document_query)
        self.db.commit()
        return int(result.rowcount or 0)

    def delete_documents_by_source_uris(self, source_uris: list[str]) -> int:
        source_uris = [item for item in source_uris if item]
        if not source_uris:
            return 0
        query = (
            select(KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.user_id == self.user_id,
                KnowledgeDocument.scope == self.scope,
                KnowledgeDocument.source_uri.in_(source_uris),
            )
        )
        chunk_ids = list(self.db.scalars(query).all())
        self._remove_vectors(chunk_ids)
        result = self.db.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.user_id == self.user_id,
                KnowledgeDocument.scope == self.scope,
                KnowledgeDocument.source_uri.in_(source_uris),
            )
        )
        self.db.commit()
        return int(result.rowcount or 0)

    def promote_temporary_documents(
        self,
        *,
        target_scope: str = MAIN_KB_SCOPE,
        provenance: str | None = None,
        country_id: int | None = None,
        region_id: int | None = None,
        sector_id: int | None = None,
    ) -> int:
        if self.scope != TEMPORARY_KB_SCOPE or not self.session_key:
            return 0
        documents = self.db.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == self.user_id,
                KnowledgeDocument.scope == TEMPORARY_KB_SCOPE,
                KnowledgeDocument.session_key == self.session_key,
            )
        ).all()
        if not documents:
            return 0
        document_ids = [document.id for document in documents]
        chunk_ids = list(
            self.db.scalars(
                select(KnowledgeChunk.id).where(KnowledgeChunk.document_id.in_(document_ids))
            ).all()
        )
        vectors = self._reconstruct_vectors(chunk_ids)
        self._remove_vectors(chunk_ids)
        if vectors:
            target_service = KnowledgeBaseService(
                self.db,
                self.user_id,
                scope=target_scope,
                country_id=country_id,
                region_id=region_id,
                sector_id=sector_id,
            )
            target_service._add_vectors(list(vectors), list(vectors.values()))
        validated_at = datetime.now(timezone.utc).isoformat()
        for document in documents:
            scope_level = validated_scope_level(country_id, region_id)
            if provenance:
                original_source_type = document.source_type
                document.title = (
                    f"[{provenance}; original_source_type={original_source_type}; "
                    f"validated_at={validated_at}; "
                    f"session={self.session_key}] {document.title}"
                )[:255]
                document.source_type = provenance[:40]
                chunks = self.db.scalars(
                    select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
                ).all()
                for chunk in chunks:
                    chunk.source_type = provenance[:40]
            document.scope = target_scope
            document.session_key = self.session_key if target_scope == QUARANTINED_SCOPE else None
            if target_scope == VALIDATED_EVIDENCE_SCOPE:
                document.country_id = country_id
                document.region_id = region_id
                document.sector_id = sector_id
                document.scope_level = scope_level
            chunks = self.db.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
            ).all()
            for chunk in chunks:
                if target_scope == VALIDATED_EVIDENCE_SCOPE:
                    chunk.country_id = country_id
                    chunk.region_id = region_id
                    chunk.sector_id = sector_id
                    chunk.scope_level = scope_level
        self.db.commit()
        return len(documents)

    def _document_access_filters(self) -> list[object]:
        if self.scope == MAIN_KB_SCOPE:
            return [KnowledgeDocument.user_id.is_(None)]
        if self.scope == SECTOR_PROMPT_SCOPE:
            return [KnowledgeDocument.user_id.is_(None)]
        if self.scope == VALIDATED_EVIDENCE_SCOPE:
            return []
        return [KnowledgeDocument.user_id == self.user_id]

    def _scope_level(self) -> str:
        if self.scope == VALIDATED_EVIDENCE_SCOPE:
            return validated_scope_level(self.country_id, self.region_id)
        if self.scope == MAIN_KB_SCOPE:
            return "global"
        return "session" if self.session_key else "global"

    def _scope_relevance_score(self, document: KnowledgeDocument) -> float:
        if self.scope != VALIDATED_EVIDENCE_SCOPE:
            return 0.0
        score = 0.0
        if document.sector_id is not None and document.sector_id == self.sector_id:
            score += 1.0
        if document.country_id is not None and document.country_id == self.country_id:
            score += 0.8
        elif document.scope_level == "global":
            score += 0.2
        if self.region_id is not None:
            if document.region_id == self.region_id:
                score += 1.0
            elif document.region_id is None and document.country_id == self.country_id:
                score += 0.45
        elif document.region_id is None and document.country_id == self.country_id:
            score += 0.45
        return score

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in texts:
            embeddings.append(await self._embed(text))
        return embeddings

    async def _embed(self, text: str) -> list[float]:
        payload = {"model": self.settings.ollama_embedding_model, "prompt": text}
        request_id = new_llm_request_id()
        started_at = perf_counter()
        async with httpx.AsyncClient(
            base_url=self.settings.ollama_base_url,
            timeout=self.settings.ollama_timeout_seconds,
        ) as client:
            try:
                response = await client.post("/api/embeddings", json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as exc:
                log_llm_exchange(
                    self.settings,
                    request_id=request_id,
                    provider="ollama",
                    endpoint="/api/embeddings",
                    model=self.settings.ollama_embedding_model,
                    request=payload,
                    response=exc.response.text,
                    status_code=exc.response.status_code,
                    duration_ms=(perf_counter() - started_at) * 1000,
                    error=f"HTTP {exc.response.status_code}",
                )
                raise
            except (httpx.HTTPError, ValueError) as exc:
                log_llm_exchange(
                    self.settings,
                    request_id=request_id,
                    provider="ollama",
                    endpoint="/api/embeddings",
                    model=self.settings.ollama_embedding_model,
                    request=payload,
                    response=response.text if "response" in locals() else None,
                    status_code=response.status_code if "response" in locals() else None,
                    duration_ms=(perf_counter() - started_at) * 1000,
                    error=repr(exc),
                )
                raise
        embedding = data.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            log_llm_exchange(
                self.settings,
                request_id=request_id,
                provider="ollama",
                endpoint="/api/embeddings",
                model=self.settings.ollama_embedding_model,
                request=payload,
                response=data,
                status_code=response.status_code,
                duration_ms=(perf_counter() - started_at) * 1000,
                error="Ollama returned an empty embedding.",
            )
            raise ValueError("Ollama returned an empty embedding.")
        try:
            values = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            log_llm_exchange(
                self.settings,
                request_id=request_id,
                provider="ollama",
                endpoint="/api/embeddings",
                model=self.settings.ollama_embedding_model,
                request=payload,
                response=data,
                status_code=response.status_code,
                duration_ms=(perf_counter() - started_at) * 1000,
                error=repr(exc),
            )
            raise
        log_llm_exchange(
            self.settings,
            request_id=request_id,
            provider="ollama",
            endpoint="/api/embeddings",
            model=self.settings.ollama_embedding_model,
            request=payload,
            response=data,
            status_code=response.status_code,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return values

    def _add_vectors(self, chunk_ids: list[int], embeddings: list[list[float]]) -> None:
        vectors = normalize_vectors(embeddings)
        ids = np.array(chunk_ids, dtype="int64")
        with FAISS_LOCK:
            index = self._load_index(vectors.shape[1])
            index.add_with_ids(vectors, ids)
            self._save_index(index)

    def _remove_vectors(self, chunk_ids: list[int]) -> None:
        if not chunk_ids or not self._index_path.exists():
            return
        ids = np.array(chunk_ids, dtype="int64")
        with FAISS_LOCK:
            index = self._load_existing_index()
            index.remove_ids(ids)
            self._save_index(index)

    def _reconstruct_vectors(self, chunk_ids: list[int]) -> dict[int, list[float]]:
        if not chunk_ids or not self._index_path.exists():
            return {}
        vectors: dict[int, list[float]] = {}
        with FAISS_LOCK:
            index = self._load_existing_index()
            for chunk_id in chunk_ids:
                try:
                    vectors[chunk_id] = index.reconstruct(int(chunk_id)).tolist()
                except Exception:
                    continue
        return vectors

    def reset_index(self) -> bool:
        if faiss is None or np is None or not self._index_path.exists():
            return not self._index_path.exists()
        with FAISS_LOCK:
            existing = self._load_existing_index()
            empty = faiss.IndexIDMap2(faiss.IndexFlatIP(existing.d))
            self._save_index(empty)
        return True

    def _load_index(self, dimensions: int):
        if self._index_path.exists():
            index = self._load_existing_index()
            if index.d != dimensions:
                raise ValueError("FAISS index dimensions do not match the Ollama embedding model.")
            return index
        flat_index = faiss.IndexFlatIP(dimensions)
        return faiss.IndexIDMap2(flat_index)

    def _load_existing_index(self):
        return faiss.read_index(str(self._index_path))

    def _save_index(self, index) -> None:
        if self.scope == TEMPORARY_KB_SCOPE and index.ntotal == 0:
            try:
                self._index_path.unlink(missing_ok=True)
                return
            except OSError:
                # Some managed filesystems allow overwriting an index but not unlinking it.
                pass
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self._index_path))

    def _require_faiss(self) -> None:
        if faiss is None or np is None:
            raise ValueError("Install `faiss-cpu` and `numpy` to use the knowledge base.")

    @property
    def _index_path(self) -> Path:
        main_path = Path(self.settings.faiss_index_path)
        if self.scope == MAIN_KB_SCOPE:
            return main_path.with_name(f"{main_path.stem}.main{main_path.suffix}")
        if self.scope == TEMPORARY_KB_SCOPE:
            return main_path.with_name(f"{main_path.stem}.temporary{main_path.suffix}")
        if self.scope == SECTOR_PROMPT_SCOPE:
            return main_path.with_name(f"{main_path.stem}.sector_prompts{main_path.suffix}")
        if self.scope == VALIDATED_EVIDENCE_SCOPE:
            return main_path.with_name(f"{main_path.stem}.validated_evidence{main_path.suffix}")
        if self.scope == QUARANTINED_SCOPE:
            return main_path.with_name(f"{main_path.stem}.quarantined{main_path.suffix}")
        return main_path


def normalize_vectors(embeddings: list[list[float]]):
    array = np.array(embeddings, dtype="float32")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def validated_scope_level(country_id: int | None, region_id: int | None) -> str:
    if region_id is not None:
        return "region"
    if country_id is not None:
        return "country"
    return "global"


def build_query_features(query: str) -> QueryFeatures:
    normalized_text = normalize_for_search(query)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", normalized_text)
        if token not in {"the", "and", "for", "from", "with", "page", "pdf"}
    }
    page_numbers = {
        int(match)
        for match in re.findall(r"(?:page|p\.?)\s*#?\s*(\d{1,4})", normalized_text)
    }
    phrases = [
        phrase
        for phrase in re.split(r"[\n,;]+", normalized_text)
        if len(phrase.split()) >= 3 and not phrase.startswith(("page ", "p "))
    ]
    return QueryFeatures(query, normalized_text, tokens, phrases, page_numbers)


def lexical_score(
    query: QueryFeatures, chunk: KnowledgeChunk, document: KnowledgeDocument
) -> float:
    haystack = normalize_for_search(
        " ".join(
            value
            for value in (
                document.title,
                chunk.source_uri or "",
                chunk.content,
            )
            if value
        )
    )
    content = normalize_for_search(chunk.content)
    title = normalize_for_search(document.title)
    if not query.tokens and not query.page_numbers and not query.phrases:
        return 0.0

    score = 0.0
    if query.normalized_text and query.normalized_text in haystack:
        score += 2.0
    for phrase in query.phrases:
        if phrase in haystack:
            score += 1.4
        elif phrase in content:
            score += 1.1

    if query.tokens:
        content_tokens = set(re.findall(r"[a-z0-9]{3,}", content))
        title_tokens = set(re.findall(r"[a-z0-9]{3,}", title))
        matched_content = query.tokens & content_tokens
        matched_title = query.tokens & title_tokens
        score += len(matched_content) / len(query.tokens)
        score += 0.35 * (len(matched_title) / len(query.tokens))

        ordered_hits = sum(1 for token in query.tokens if token in content)
        if ordered_hits >= min(3, len(query.tokens)):
            score += 0.25

    if query.page_numbers:
        if chunk.page_number in query.page_numbers:
            score += 1.6
        elif chunk.page_number is not None:
            score -= 0.25

    return max(score, 0.0)


def normalize_for_search(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("_", " ")).strip()


def is_index_page_text(text: str) -> bool:
    normalized = normalize_for_search(text)
    if not normalized:
        return False
    starts_like_index = normalized.startswith(
        (
            "contents ",
            "table of contents ",
            "index ",
            "list of figures ",
            "list of tables ",
        )
    )
    dotted_leaders = len(re.findall(r"\.{4,}\s*\d{1,4}\b", text))
    numbered_entries = len(
        re.findall(
            r"\b\d+(?:\.\d+){0,4}\.?\s+[A-Z][A-Za-z0-9,;:()&/\-\s]{8,}?\s*\.{3,}\s*\d{1,4}\b",
            text,
        )
    )
    content_words = len(re.findall(r"[A-Za-z]{4,}", text))
    index_signal = dotted_leaders + numbered_entries
    if starts_like_index and index_signal >= 2:
        return True
    if index_signal >= 6 and content_words < 260:
        return True
    if starts_like_index and normalized.count("....") >= 3:
        return True
    return False


def chunk_text(text: str, page_number: int | None = None) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(ChunkDraft(chunk, page_number))
        if end == len(text):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return chunks


def file_source_type(filename: str) -> str:
    lowered = filename.casefold()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith(".docx"):
        return "docx"
    if lowered.endswith(".md"):
        return "md"
    if lowered.endswith(".txt"):
        return "txt"
    return "file"


def extract_file_chunks(filename: str, content: bytes) -> list[ChunkDraft]:
    lowered = filename.casefold()
    if lowered.endswith(".pdf"):
        return extract_pdf_chunks(content)
    if lowered.endswith(".docx"):
        return chunk_text(compact_text(extract_docx_text(content)))
    if lowered.endswith((".md", ".txt")):
        return chunk_text(compact_text(content.decode("utf-8", errors="ignore")))
    return []


async def extract_url_chunks(url: str) -> list[ChunkDraft]:
    if not url.casefold().startswith(("http://", "https://")):
        return []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "").casefold()
    content = response.content
    if "pdf" in content_type or url.casefold().split("?", 1)[0].endswith(".pdf"):
        return extract_pdf_chunks(content)
    if (
        "wordprocessingml.document" in content_type
        or url.casefold().split("?", 1)[0].endswith(".docx")
    ):
        return chunk_text(compact_text(extract_docx_text(content)))
    text = content.decode(response.encoding or "utf-8", errors="ignore")
    if "html" in content_type or "<html" in text[:500].casefold():
        text = html_to_text(text)
    return chunk_text(compact_text(text))


def extract_pdf_chunks(content: bytes) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for index, raw_text in enumerate(extract_pdf_page_texts(content), start=1):
        if is_index_page_text(raw_text):
            continue
        page_text = compact_text(raw_text)
        chunks.extend(chunk_text(page_text, index))
    return chunks
