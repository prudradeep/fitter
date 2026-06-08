import logging
import re
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.services.knowledge_base import ChunkDraft, KnowledgeBaseService, chunk_text
from app.services.prompt_loader import PROMPT_DIR, PROMPT_FILES, sector_prompt_name

logger = logging.getLogger(__name__)

SECTOR_PROMPT_SCOPE = "sector_prompt"
SECTOR_PROMPT_INDEX_VERSION = "v2"


class SectorPromptRagService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.service = KnowledgeBaseService(db, None, scope=SECTOR_PROMPT_SCOPE)

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

        for sector, source_uri, prompt_path in prompt_sources:
            if source_uri in existing_uris:
                skipped.append(sector)
                continue
            try:
                text = prompt_path.read_text(encoding="utf-8").strip()
                result = await self.service.ingest_chunks(
                    self._sector_prompt_chunks(text),
                    title=f"Sector prompt: {sector.title()}",
                    source_type="sector_prompt",
                    source_uri=source_uri,
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
            indexed.append(result)

        return {
            "error": bool(failures) and not indexed and not skipped,
            "indexed": indexed,
            "skipped": skipped,
            "failures": failures,
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
        return await self.service.search(query, limit=limit)

    @staticmethod
    def format_results(results: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Sector prompt")
            score = result.get("score")
            score_label = f", score {score}" if score is not None else ""
            content = str(result.get("content") or "").strip()
            if content:
                lines.append(f"- [SP{index}] {title}{score_label}: {content[:1000]}")
        return "\n".join(lines)

    @classmethod
    def _prompt_sources(cls) -> list[tuple[str, str, Path]]:
        sources: list[tuple[str, str, Path]] = []
        for sector, filename in PROMPT_FILES.items():
            prompt_path = PROMPT_DIR / filename
            if prompt_path.exists():
                sources.append((sector, cls._source_uri(sector), prompt_path))
        default_path = PROMPT_DIR / "Default_system_prompt.txt"
        if default_path.exists():
            sources.append(("default", cls._source_uri("default"), default_path))
        return sources

    @staticmethod
    def _source_uri(sector_key: str) -> str:
        return f"sector-prompt://{SECTOR_PROMPT_INDEX_VERSION}/{sector_key}"

    @staticmethod
    def _sector_prompt_chunks(text: str) -> list[ChunkDraft]:
        section_start = text.find("SECTION 5")
        if section_start == -1:
            return chunk_text(text)

        chunks: list[ChunkDraft] = []
        prefix = text[:section_start].strip()
        if prefix:
            chunks.extend(chunk_text(prefix))

        section = text[section_start:]
        hazard_pattern = re.compile(
            r"(?ms)^HAZARD\s+\d+\.\s+.+?(?=^HAZARD\s+\d+\.|\Z)"
        )
        for match in hazard_pattern.finditer(section):
            hazard_block = match.group(0).strip()
            if hazard_block:
                chunks.append(ChunkDraft(hazard_block))
        return chunks or chunk_text(text)
