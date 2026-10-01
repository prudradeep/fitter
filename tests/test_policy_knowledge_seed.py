import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Country, KnowledgeDocument, Policy, Sector
from app.services.knowledge_base import ChunkDraft
from app.services.policy_knowledge_seed import (
    POLICY_URL_SOURCE_TYPE,
    policy_language_is_english,
    seed_policy_documents_from_urls,
    translate_policy_chunks_to_english,
)


class PolicyKnowledgeSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = self.factory()

    def tearDown(self) -> None:
        self.db.close()

    def test_english_language_detection(self) -> None:
        self.assertTrue(policy_language_is_english("English"))
        self.assertTrue(policy_language_is_english(""))
        self.assertFalse(policy_language_is_english("German"))

    def test_non_english_chunks_are_translated_before_ingestion(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="English text")) as translate:
            chunks = asyncio.run(translate_policy_chunks_to_english([ChunkDraft("Deutscher Text", 2)], "German"))
        self.assertEqual(chunks, [ChunkDraft("English text", 2)])
        self.assertEqual(translate.await_count, 1)

    def test_startup_seed_links_imported_document_and_skips_it_next_time(self) -> None:
        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        policy = Policy(
            country_id=country.id,
            sector_id=sector.id,
            policy="German policy",
            policy_url="https://example.org/policy.pdf",
            language="German",
        )
        self.db.add(policy)
        self.db.commit()

        async def ingest_chunks(service, chunks, title, source_type, source_uri, **kwargs):
            document = KnowledgeDocument(title=title, source_type=source_type, source_uri=source_uri, scope="main", scope_level="global")
            service.db.add(document)
            service.db.commit()
            return {"error": False, "document_id": document.id}

        with (
            patch("app.services.policy_knowledge_seed.extract_url_chunks", AsyncMock(return_value=[ChunkDraft("Deutsch")])),
            patch("app.services.policy_knowledge_seed.translate_policy_chunks_to_english", AsyncMock(return_value=[ChunkDraft("English")])),
            patch("app.services.policy_knowledge_seed.KnowledgeBaseService.ingest_chunks", new=ingest_chunks),
        ):
            first = asyncio.run(seed_policy_documents_from_urls(self.factory))
            second = asyncio.run(seed_policy_documents_from_urls(self.factory))

        self.assertEqual(first, {"imported": 1, "skipped": 0, "failed": 0})
        self.assertEqual(second, {"imported": 0, "skipped": 1, "failed": 0})
        document = self.db.query(KnowledgeDocument).one()
        self.assertEqual(document.policy_id, policy.id)
        self.assertEqual(document.source_type, POLICY_URL_SOURCE_TYPE)
