import logging
import re
import time

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.knowledge_base import ChunkDraft, KnowledgeBaseService, chunk_text
from app.services.prompt_loader import PROMPT_FILES, load_sector_prompt, sector_prompt_name

logger = logging.getLogger(__name__)

SECTOR_PROMPT_SCOPE = "sector_prompt"
SECTOR_PROMPT_INDEX_VERSION = "v4"


class SectorPromptRagService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.service = KnowledgeBaseService(db, None, scope=SECTOR_PROMPT_SCOPE)

    async def rebuild(self) -> dict[str, object]:
        cleanup = self._clear_all_indexes()
        if not cleanup.get("removed_faiss"):
            return {
                "error": True,
                "indexed": [],
                "skipped": [],
                "failures": [
                    {
                        "sector": "all",
                        "detail": str(
                            cleanup.get("faiss_error")
                            or "Could not remove the old sector-prompt FAISS index."
                        ),
                    }
                ],
                "cleanup": cleanup,
            }
        result = await self.ensure_indexed()
        return {**result, "cleanup": cleanup}

    async def ensure_indexed(self, force: bool = False) -> dict[str, object]:
        prompt_sources = self._prompt_sources()
        existing_uris = {
            str(document.get("source_uri") or "")
            for document in self.service.list_documents()
        }
        indexed: list[dict[str, object]] = []
        skipped: list[str] = []
        failures: list[dict[str, str]] = []

        if force:
            self.service.delete_documents_by_source_uris(
                [source_uri for _, source_uri, _ in prompt_sources]
            )
            existing_uris = set()

        for sector, source_uri, text in prompt_sources:
            try:
                chunks = self._sector_prompt_chunks(text)
                expected_hazard_blocks = sum(
                    1 for chunk in chunks if chunk.content.startswith("HAZARD")
                )
                existing_hazard_blocks = self._stored_hazard_block_count(source_uri)
                if (
                    source_uri in existing_uris
                    and existing_hazard_blocks == expected_hazard_blocks
                    and not self._stored_hazard_blocks_have_rule_lines(source_uri)
                ):
                    skipped.append(sector)
                    continue
                if source_uri in existing_uris:
                    self.service.delete_documents_by_source_uris([source_uri])
                result = await self.service.ingest_chunks(
                    chunks,
                    title=f"Sector prompt: {sector.title()}",
                    source_type="sector_prompt",
                    source_uri=source_uri,
                    allow_lexical_only=True,
                )
            except (OSError, httpx.HTTPError, ValueError) as exc:
                logger.exception("Failed to index sector prompt %s", sector)
                failures.append({"sector": sector, "detail": str(exc)})
                continue
            if result.get("error"):
                failures.append(
                    {
                        "sector": sector,
                        "detail": str(result.get("detail") or "Could not index prompt."),
                    }
                )
                continue
            if not result.get("vector_indexed"):
                logger.warning(
                    "Stored sector prompt %s in DB without vectors: %s",
                    sector,
                    result.get("vector_error") or "embedding service unavailable",
                )
            indexed.append(result)

        return {
            "error": bool(failures) and not indexed and not skipped,
            "indexed": indexed,
            "skipped": skipped,
            "failures": failures,
        }

    def _clear_all_indexes(self) -> dict[str, object]:
        document_ids = list(
            self.db.scalars(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.scope == SECTOR_PROMPT_SCOPE
                )
            ).all()
        )
        chunk_count = 0
        if document_ids:
            chunk_count = int(
                self.db.scalar(
                    select(func.count(KnowledgeChunk.id)).where(
                        KnowledgeChunk.document_id.in_(document_ids)
                    )
                )
                or 0
            )
            self.db.execute(
                delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids))
            )
        self.db.commit()

        index_path = self.service._index_path
        removed_faiss = not index_path.exists()
        reset_faiss = False
        faiss_error = ""
        if index_path.exists():
            try:
                index_path.unlink()
                removed_faiss = True
            except OSError as exc:
                faiss_error = str(exc)
                backup_path = index_path.with_name(
                    f"{index_path.name}.stale-{int(time.time())}"
                )
                try:
                    index_path.replace(backup_path)
                    removed_faiss = True
                    faiss_error = ""
                except OSError as replace_exc:
                    try:
                        reset_faiss = self.service.reset_index()
                        removed_faiss = reset_faiss
                        if reset_faiss:
                            faiss_error = ""
                    except Exception as reset_exc:
                        faiss_error = str(reset_exc or replace_exc)
                        logger.exception("Could not clear sector-prompt FAISS index")

        return {
            "deleted_documents": len(document_ids),
            "deleted_chunks": chunk_count,
            "removed_faiss": removed_faiss,
            "reset_faiss": reset_faiss,
            "faiss_error": faiss_error,
        }

    async def search(self, sector: str | None, query: str, limit: int = 5) -> list[dict[str, object]]:
        sector_key = sector_prompt_name(sector)
        await self.ensure_indexed()
        results = await self.service.search(
            f"{sector_key} {query}".strip(),
            limit=limit,
            source_uris=[self._source_uri(sector_key)],
        )
        if results:
            return results
        return await self.service.search(
            query,
            limit=limit,
            source_uris=[self._source_uri("default")],
        )

    async def hazard_blocks(self, sector: str | None) -> list[dict[str, object]]:
        sector_key = sector_prompt_name(sector)
        await self.ensure_indexed()
        source_uri = self._source_uri(sector_key)
        rows = self.db.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.user_id.is_(None),
                KnowledgeDocument.scope == SECTOR_PROMPT_SCOPE,
                KnowledgeDocument.source_uri == source_uri,
                func.lower(KnowledgeChunk.content).like("hazard%"),
            )
            .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()
        return [
            {
                "document_id": document.id,
                "title": document.title,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "page_number": chunk.page_number,
                "score": None,
                "vector_score": None,
                "lexical_score": None,
                "content": chunk.content,
            }
            for chunk, document in rows
        ]

    def _stored_hazard_block_count(self, source_uri: str) -> int:
        count = self.db.scalar(
            select(func.count(KnowledgeChunk.id))
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.user_id.is_(None),
                KnowledgeDocument.scope == SECTOR_PROMPT_SCOPE,
                KnowledgeDocument.source_uri == source_uri,
                func.lower(KnowledgeChunk.content).like("hazard%"),
            )
        )
        return int(count or 0)

    def _stored_hazard_blocks_have_rule_lines(self, source_uri: str) -> bool:
        rows = self.db.scalars(
            select(KnowledgeChunk.content)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.user_id.is_(None),
                KnowledgeDocument.scope == SECTOR_PROMPT_SCOPE,
                KnowledgeDocument.source_uri == source_uri,
                func.lower(KnowledgeChunk.content).like("hazard%"),
            )
        ).all()
        return any(has_rule_lines(content) for content in rows)

    @staticmethod
    def format_results(
        results: list[dict[str, object]],
        content_limit: int | None = 1000,
    ) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Sector prompt")
            score = result.get("score")
            score_label = f", score {score}" if score is not None else ""
            nli_label = result.get("nli_label")
            nli_score = result.get("nli_score")
            nli_score_label = (
                f", NLI {nli_label} {nli_score}"
                if nli_label is not None and nli_score is not None
                else ""
            )
            content = str(result.get("content") or "").strip()
            if content:
                excerpt = _clean_excerpt(content, content_limit)
                lines.append(f"- [SP{index}] {title}{score_label}{nli_score_label}: {excerpt}")
        return "\n".join(lines)

    @classmethod
    def _prompt_sources(cls) -> list[tuple[str, str, str]]:
        sources: list[tuple[str, str, str]] = []
        for sector in PROMPT_FILES:
            text = load_sector_prompt(sector)
            if text:
                sources.append((sector, cls._source_uri(sector), text))
        default_text = load_sector_prompt("default")
        if default_text:
            sources.append(("default", cls._source_uri("default"), default_text))
        return sources

    @staticmethod
    def _source_uri(sector_key: str) -> str:
        return f"sector-prompt://{SECTOR_PROMPT_INDEX_VERSION}/{sector_key}"

    @staticmethod
    def _sector_prompt_chunks(text: str) -> list[ChunkDraft]:
        section = section_five_primary_data(text)
        if not section:
            return chunk_text(text)

        chunks: list[ChunkDraft] = []
        hazard_pattern = re.compile(
            r"(?ims)^HAZARD\s+\d+\s*[\.:–-]\s+.+?(?=^HAZARD\s+\d+\s*[\.:–-]|\Z)"
        )
        for match in hazard_pattern.finditer(section):
            hazard_block = strip_rule_lines(match.group(0)).strip()
            if hazard_block:
                chunks.append(ChunkDraft(hazard_block))
        return chunks or chunk_text(text)


def section_five_primary_data(text: str) -> str:
    start_match = re.search(
        r"(?im)^\s*(?:SECTION\s+)?5\s*[\.:–-]\s+PER-HAZARD CONFIRMED PREDICTORS\b.*$",
        text,
    )
    if not start_match:
        start_match = re.search(r"(?im)^\s*(?:SECTION\s+)?5\s*[\.:–-]", text)
    if not start_match:
        return ""
    remainder = text[start_match.start() :]
    end_match = re.search(r"(?im)^\s*(?:SECTION\s+)?6\s*[\.:–-]", remainder)
    return remainder[: end_match.start()].strip() if end_match else remainder.strip()


def strip_rule_lines(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped and re.fullmatch(r"[─═\-_=]{6,}", stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def has_rule_lines(text: str) -> bool:
    return any(
        bool(re.fullmatch(r"[─═\-_=]{6,}", line.strip()))
        for line in str(text or "").splitlines()
    )


def _clean_excerpt(content: str, content_limit: int | None) -> str:
    text = str(content or "").strip()
    if not content_limit or len(text) <= content_limit:
        return text
    truncated = text[:content_limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0].rstrip()
    return f"{truncated}…"
