import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    TEMPORARY_KB_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
    normalize_source_url,
)
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


class KnowledgeBaseUrlReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.db.close()

    def _add_url_document(
        self,
        url: str,
        *,
        scope: str,
        user_id: str | None = None,
        session_key: str | None = None,
        sector_id: str | None = None,
    ) -> KnowledgeDocument:
        document = KnowledgeDocument(
            user_id=user_id,
            title="Stored evidence",
            source_type="url",
            source_uri=url,
            scope=scope,
            scope_level="global" if scope == MAIN_KB_SCOPE else "session",
            session_key=session_key,
            sector_id=sector_id,
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id=user_id,
                chunk_index=0,
                content="Previously extracted evidence content.",
                source_type="url",
                source_uri=url,
                scope_level=document.scope_level,
                sector_id=sector_id,
            )
        )
        self.db.commit()
        return document

    def test_url_normalization_preserves_query_and_removes_fragment(self) -> None:
        self.assertEqual(
            normalize_source_url("HTTPS://Example.COM:443/report/?version=1#section"),
            "https://example.com/report?version=1",
        )
        self.assertNotEqual(
            normalize_source_url("https://example.com/report?version=1"),
            normalize_source_url("https://example.com/report?version=2"),
        )

    def test_ingest_url_reuses_accessible_document_without_fetching(self) -> None:
        document = self._add_url_document(
            "HTTPS://Example.COM:443/report/?version=1#section",
            scope=MAIN_KB_SCOPE,
        )
        service = KnowledgeBaseService(
            self.db,
            "current-user",
            scope=TEMPORARY_KB_SCOPE,
            session_key="session-1",
        )
        fetch = AsyncMock(side_effect=AssertionError("URL must not be fetched again"))
        with patch("app.services.knowledge_base.extract_url_chunks", fetch):
            result = asyncio.run(
                service.ingest_url(
                    "https://example.com/report?version=1",
                    reuse_existing=True,
                    allow_lexical_only=True,
                )
            )

        self.assertTrue(result["reused"])
        self.assertEqual(result["document_id"], document.id)
        self.assertEqual(result["scope"], MAIN_KB_SCOPE)
        fetch.assert_not_awaited()
        self.assertEqual(len(self.db.scalars(select(KnowledgeDocument)).all()), 1)

    def test_private_document_from_another_session_is_not_reused(self) -> None:
        self._add_url_document(
            "https://example.com/private",
            scope=TEMPORARY_KB_SCOPE,
            user_id="another-user",
            session_key="another-session",
        )
        service = KnowledgeBaseService(
            self.db,
            "current-user",
            scope=TEMPORARY_KB_SCOPE,
            session_key="session-1",
        )
        self.assertIsNone(
            service.find_reusable_url_document("https://example.com/private")
        )

    def test_validated_evidence_requires_matching_sector_context(self) -> None:
        document = self._add_url_document(
            "https://example.com/validated",
            scope=VALIDATED_EVIDENCE_SCOPE,
            sector_id="transport-sector",
        )
        matching = KnowledgeBaseService(
            self.db,
            "current-user",
            scope=TEMPORARY_KB_SCOPE,
            session_key="session-1",
            sector_id="transport-sector",
        )
        mismatched = KnowledgeBaseService(
            self.db,
            "current-user",
            scope=TEMPORARY_KB_SCOPE,
            session_key="session-1",
            sector_id="housing-sector",
        )
        self.assertEqual(
            matching.find_reusable_url_document("https://example.com/validated")[
                "document_id"
            ],
            document.id,
        )
        self.assertIsNone(
            mismatched.find_reusable_url_document("https://example.com/validated")
        )

    def test_reused_document_chunks_respect_access_rules(self) -> None:
        document = self._add_url_document(
            "https://example.com/public",
            scope=MAIN_KB_SCOPE,
        )
        public_service = KnowledgeBaseService(self.db, None, scope=MAIN_KB_SCOPE)
        results = public_service.document_results(document.id)
        self.assertEqual([item["content"] for item in results], [
            "Previously extracted evidence content."
        ])

        chat_service = ChatService.__new__(ChatService)
        chat_service.db = self.db
        chat_service.user_id = "current-user"
        context = chat_service._reused_evidence_context(
            ChatSession(session_key="session-1"),
            (
                f"Reused evidence document ID: {document.id}\n"
                f"Reused evidence scope: {MAIN_KB_SCOPE}"
            ),
        )
        self.assertIn("Previously extracted evidence content.", context)


if __name__ == "__main__":
    unittest.main()
