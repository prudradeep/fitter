import logging
import re

from sqlalchemy import select

from app.models import KnowledgeChunk, KnowledgeDocument, MitigationMeasureExample
from app.services.chat_session import ChatSession
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    TEMPORARY_KB_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
)
from app.services.sector_prompt_rag import SectorPromptRagService

logger = logging.getLogger(__name__)


class ChatContextRetrievalMixin:
    async def _mitigation_main_knowledge_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
        results = await self._shared_knowledge_results(session, query, main_limit=8, evidence_limit=6)
        return self._format_knowledge_results(results)

    async def _user_evidence_context_for_contradiction_check(
        self,
        session: ChatSession,
        evidence: str,
    ) -> str:
        temporary_context = await self._temporary_evidence_context(session)
        inline_evidence = self._inline_evidence_content(evidence)
        if inline_evidence:
            inline_context = self._format_full_knowledge_results(
                [
                    {
                        "title": "User-supplied evidence",
                        "source_type": "evidence",
                        "score": 1.0,
                        "content": inline_evidence,
                    }
                ]
            )
        else:
            inline_context = ""
        return "\n".join(
            part for part in (temporary_context, inline_context) if part.strip()
        ).strip()

    async def _mitigation_evidence_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence: str,
    ) -> str:
        temporary_context = await self._temporary_evidence_context(session)
        inline_evidence = self._inline_evidence_content(evidence)
        inline_results: list[dict[str, object]] = []
        if inline_evidence:
            inline_results.append(
                {
                    "title": "User-supplied evidence",
                    "source_type": "evidence",
                    "score": 1.0,
                    "content": inline_evidence,
                }
            )
        if inline_results:
            query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
            inline_results = await self.grounding_models.ground_results(query, inline_results)
        inline_context = self._format_full_knowledge_results(inline_results)
        return "\n".join(part for part in (temporary_context, inline_context) if part).strip()

    async def _sector_prompt_rag_context(
        self,
        session: ChatSession,
        query: str,
        limit: int = 5,
    ) -> str:
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                query,
                limit=limit,
            )
        except Exception:
            logger.exception("Sector-prompt RAG lookup failed")
            results = []

        formatted = SectorPromptRagService.format_results(results)
        if formatted:
            return formatted
        return "- No relevant sector-prompt RAG excerpts were found."

    async def _mitigation_knowledge_context(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        query = self._mitigation_retrieval_query(session, mitigation_measure, reason)
        shared_results = await self._shared_knowledge_results(session, query, main_limit=5, evidence_limit=4)
        temporary_results: list[dict[str, object]] = []
        if session.session_key:
            try:
                temporary_results = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope=TEMPORARY_KB_SCOPE,
                    session_key=session.session_key,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Temporary evidence lookup failed during mitigation validation")
        results = await self.grounding_models.ground_results(
            query,
            [*temporary_results, *shared_results],
        )
        return self._format_knowledge_results(results)

    async def _shared_knowledge_results(
        self,
        session: ChatSession,
        query: str,
        *,
        main_limit: int,
        evidence_limit: int,
    ) -> list[dict[str, object]]:
        try:
            main_results = await KnowledgeBaseService(
                self.db,
                None,
                scope=MAIN_KB_SCOPE,
            ).search(query, limit=main_limit)
        except Exception:
            logger.exception("Main knowledge-base lookup failed")
            main_results = []

        validated_results: list[dict[str, object]] = []
        if session.country_id is not None and session.sector_id is not None:
            try:
                validated_results = await KnowledgeBaseService(
                    self.db,
                    None,
                    scope=VALIDATED_EVIDENCE_SCOPE,
                    country_id=session.country_id,
                    region_id=session.region_id,
                    sector_id=session.sector_id,
                ).search(query, limit=evidence_limit)
            except Exception:
                logger.exception("Validated evidence lookup failed")
        return [*main_results, *validated_results]

    def _mitigation_retrieval_query(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        # Retrieval must prioritize the proposed intervention and its mechanism.
        # A long affected-profile list dilutes cross-encoder relevance and can
        # push genuinely supporting evidence below the eligibility floor.
        return (
            f"{session.selected_hazard or ''} {mitigation_measure} {reason} "
            f"{self._mitigation_target_population_text(session)} "
            f"{session.country or ''} {session.sector or ''} {session.region or ''}"
        )

    async def _temporary_evidence_context(self, session: ChatSession) -> str:
        if not session.session_key:
            return ""
        try:
            rows = self.db.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeDocument.user_id == self.user_id,
                    KnowledgeDocument.scope == "temporary",
                    KnowledgeDocument.session_key == session.session_key,
                )
                .order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            ).all()
        except Exception:
            logger.exception("Temporary evidence lookup failed during validation")
            return ""
        results = [
            {
                "document_id": document.id,
                "title": document.title,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "page_number": chunk.page_number,
                "score": None,
                "content": chunk.content,
            }
            for chunk, document in rows
        ]
        return self._format_full_knowledge_results(results)

    @staticmethod
    def _format_full_knowledge_results(results: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Knowledge source")
            page = result.get("page_number")
            page_label = f", page {page}" if page else ""
            source_uri = str(result.get("source_uri") or "").strip()
            source_label = f", source {source_uri}" if source_uri else ""
            content = str(result.get("content") or "").strip()
            if content:
                lines.append(f"- [S{index}] {title}{page_label}{source_label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_knowledge_results(results: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            title = str(result.get("title") or "Knowledge source")
            page = result.get("page_number")
            page_label = f", page {page}" if page else ""
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
                lines.append(
                    f"- [S{index}] {title}{page_label}{score_label}{nli_score_label}: "
                    f"{content[:900]}"
                )
        return "\n".join(lines)

    @staticmethod
    def _has_user_supplied_evidence(evidence: str | None) -> bool:
        return bool(evidence and evidence.strip())

    @staticmethod
    def _has_readable_evidence_content(evidence: str | None) -> bool:
        if not evidence or not evidence.strip():
            return False
        if re.search(r"Temporary evidence document ID:\s*\d+", evidence, flags=re.IGNORECASE):
            return True
        if ChatContextRetrievalMixin._has_evidence_url_reference(evidence):
            return True
        content = ChatContextRetrievalMixin._inline_evidence_content(evidence)
        if content:
            return not content.casefold().startswith("unable to extract evidence")
        lowered = evidence.casefold()
        if "unable to extract evidence" in lowered:
            return False
        if "evidence url:" in lowered:
            return True
        if re.search(
            r"^Evidence file:\s*.+\.(pdf|docx|md|txt)\b",
            evidence,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return True
        if "evidence file:" in lowered:
            return False
        return True

    @staticmethod
    def _has_evidence_url_reference(evidence: str | None) -> bool:
        if not evidence:
            return False
        return bool(
            re.search(
                r"^Evidence URL:\s*https?://\S+",
                evidence,
                flags=re.IGNORECASE | re.MULTILINE,
            )
        )

    @staticmethod
    def _inline_evidence_content(evidence: str | None) -> str:
        if not evidence or not evidence.strip():
            return ""
        lines = [line.strip() for line in evidence.splitlines() if line.strip()]
        content_lines = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.casefold().startswith("evidence content:")
            and line.split(":", 1)[1].strip()
            and not line.split(":", 1)[1].strip().casefold().startswith(
                "unable to extract evidence"
            )
        ]
        if content_lines:
            return "\n".join(content_lines)
        lowered = evidence.casefold()
        if not any(
            marker in lowered
            for marker in (
                "evidence url:",
                "evidence file:",
                "temporary evidence document id:",
                "temporary evidence indexing failed:",
                "unable to extract evidence",
            )
        ):
            return evidence.strip()
        return ""

    def _mitigation_measure_examples(self, sector_id: int | None, limit: int = 6) -> str:
        if sector_id is None:
            return ""
        query = (
            select(MitigationMeasureExample.measure)
            .where(MitigationMeasureExample.sector_id == sector_id)
            .order_by(MitigationMeasureExample.id)
            .limit(limit)
        )
        rows = self.db.scalars(query).all()
        return "\n".join(f"- {measure}" for measure in rows if str(measure or "").strip())
