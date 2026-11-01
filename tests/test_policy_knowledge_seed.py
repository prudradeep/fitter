import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Country, KnowledgeChunk, KnowledgeDocument, Policy, PolicyKnowledgeDocument, Sector
from app.services.knowledge_base import ChunkDraft, POLICY_DOCUMENT_SCOPE
from app.services.policy_knowledge_seed import (
    POLICY_URL_SOURCE_TYPE,
    consolidate_policy_url_documents,
    migrate_policy_documents_from_main,
    policy_language_is_english,
    reusable_policy_reference_document,
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
            document = KnowledgeDocument(title=title, source_type=source_type, source_uri=source_uri, scope=service.scope, scope_level="global")
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
        self.assertEqual(document.scope, POLICY_DOCUMENT_SCOPE)
        self.assertEqual(document.source_type, POLICY_URL_SOURCE_TYPE)

    def test_existing_policy_document_moves_out_of_main_index(self) -> None:
        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        policy = Policy(country_id=country.id, sector_id=sector.id, policy="Energy policy")
        self.db.add(policy)
        self.db.flush()
        document = KnowledgeDocument(
            title="Energy policy document", source_type="txt", scope="main",
            policy_id=policy.id,
        )
        other = KnowledgeDocument(title="General KB", source_type="txt", scope="main")
        self.db.add_all([document, other])
        self.db.flush()
        chunk = KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Policy content",
            source_type="txt",
        )
        self.db.add(chunk)
        self.db.commit()

        vector_calls = []
        def add_vectors(service, chunk_ids, vectors):
            vector_calls.append((service.scope, chunk_ids, vectors))

        with (
            patch("app.services.policy_knowledge_seed.KnowledgeBaseService._reconstruct_vectors", return_value={chunk.id: [1.0, 0.0]}),
            patch("app.services.policy_knowledge_seed.KnowledgeBaseService._remove_vectors"),
            patch("app.services.policy_knowledge_seed.KnowledgeBaseService._add_vectors", new=add_vectors),
        ):
            self.assertEqual(migrate_policy_documents_from_main(self.db), 1)

        self.assertEqual(document.scope, POLICY_DOCUMENT_SCOPE)
        self.assertEqual(other.scope, "main")
        self.assertEqual(vector_calls, [(POLICY_DOCUMENT_SCOPE, [chunk.id], [[1.0, 0.0]])])
        self.assertEqual(migrate_policy_documents_from_main(self.db), 0)

    def test_two_policies_share_one_url_document(self) -> None:
        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        policies = [Policy(
            country_id=country.id, sector_id=sector.id,
            policy=f"Policy {number}", policy_url="https://example.org/policy.pdf",
            language="English",
        ) for number in (1, 2)]
        self.db.add_all(policies)
        self.db.commit()

        async def ingest_chunks(service, chunks, title, source_type, source_uri, **kwargs):
            document = KnowledgeDocument(
                title=title, source_type=source_type, source_uri=source_uri, scope=service.scope,
            )
            service.db.add(document)
            service.db.flush()
            service.db.add(KnowledgeChunk(
                document_id=document.id, chunk_index=0, content=chunks[0].content,
                source_type=source_type, source_uri=source_uri,
            ))
            service.db.commit()
            return {"error": False, "document_id": document.id}

        with (
            patch("app.services.policy_knowledge_seed.extract_url_chunks", AsyncMock(return_value=[ChunkDraft("Shared text")])) as extract,
            patch("app.services.policy_knowledge_seed.KnowledgeBaseService.ingest_chunks", new=ingest_chunks),
        ):
            first = asyncio.run(seed_policy_documents_from_urls(self.factory))
            second = asyncio.run(seed_policy_documents_from_urls(self.factory))

        self.assertEqual(extract.await_count, 1)
        self.assertEqual(first, {"imported": 1, "skipped": 1, "failed": 0})
        self.assertEqual(second, {"imported": 0, "skipped": 2, "failed": 0})
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)
        self.assertEqual(self.db.query(KnowledgeChunk).count(), 1)
        links = self.db.query(PolicyKnowledgeDocument).all()
        self.assertEqual({link.policy_id for link in links}, {policy.id for policy in policies})
        self.assertEqual(len({link.knowledge_document_id for link in links}), 1)

    def test_existing_identical_url_documents_are_consolidated(self) -> None:
        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        first = Policy(country_id=country.id, sector_id=sector.id, policy="First")
        second = Policy(country_id=country.id, sector_id=sector.id, policy="Second")
        self.db.add_all([first, second])
        self.db.flush()
        documents = [KnowledgeDocument(
            title=policy.policy, source_type="policy_url",
            source_uri="https://example.org/shared.pdf", scope=POLICY_DOCUMENT_SCOPE,
            policy_id=policy.id,
        ) for policy in (first, second)]
        self.db.add_all(documents)
        self.db.flush()
        self.db.add_all(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Shared text",
            source_type="policy_url", source_uri=document.source_uri,
        ) for document in documents)
        self.db.commit()

        with patch("app.services.policy_knowledge_seed.KnowledgeBaseService._remove_vectors"):
            removed = consolidate_policy_url_documents(self.db)

        self.assertEqual(removed, 1)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)
        self.assertEqual(self.db.query(KnowledgeChunk).count(), 1)
        links = self.db.query(PolicyKnowledgeDocument).all()
        self.assertEqual({link.policy_id for link in links}, {first.id, second.id})
        self.assertEqual({link.knowledge_document_id for link in links}, {
            self.db.query(KnowledgeDocument).one().id,
        })

    def test_same_url_with_different_chunks_is_still_consolidated(self) -> None:
        country = Country(name="Portugal", map_code="PT")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        policies = [Policy(country_id=country.id, sector_id=sector.id, policy=title)
                    for title in ("Cooperative support", "Net metering")]
        self.db.add_all(policies)
        self.db.flush()
        documents = [KnowledgeDocument(
            title=policy.policy, source_type="policy_url",
            source_uri="https://files.dre.pt/1s/2022/01/01000/0000300185.pdf",
            scope=POLICY_DOCUMENT_SCOPE, policy_id=policy.id,
        ) for policy in policies]
        self.db.add_all(documents)
        self.db.flush()
        self.db.add_all(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content=content,
            source_type="policy_url", source_uri=document.source_uri,
        ) for document, content in zip(documents, ("First translation", "Second translation")))
        self.db.commit()

        with patch("app.services.policy_knowledge_seed.KnowledgeBaseService._remove_vectors"):
            removed = consolidate_policy_url_documents(self.db)

        self.assertEqual(removed, 1)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)
        self.assertEqual(self.db.query(KnowledgeChunk).count(), 1)
        links = self.db.query(PolicyKnowledgeDocument).all()
        self.assertEqual({link.policy_id for link in links}, {policy.id for policy in policies})
        self.assertEqual(len({link.knowledge_document_id for link in links}), 1)

    def test_same_title_with_different_urls_remains_separate(self) -> None:
        self.db.add_all(KnowledgeDocument(
            title="National Energy and Climate Plan (NECP)",
            source_type="policy_url", source_uri=f"https://example.org/{country}.pdf",
            scope=POLICY_DOCUMENT_SCOPE,
        ) for country in ("Germany", "Ireland", "Hungary"))
        self.db.commit()

        self.assertEqual(consolidate_policy_url_documents(self.db), 0)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 3)

    def test_consolidation_keeps_document_with_chunks(self) -> None:
        empty = KnowledgeDocument(
            title="Empty", source_type="policy_url",
            source_uri="https://example.org/shared.pdf", scope=POLICY_DOCUMENT_SCOPE,
        )
        filled = KnowledgeDocument(
            title="Filled", source_type="policy_url",
            source_uri="https://example.org/shared.pdf", scope=POLICY_DOCUMENT_SCOPE,
        )
        self.db.add_all([empty, filled])
        self.db.flush()
        self.db.add(KnowledgeChunk(
            document_id=filled.id, chunk_index=0, content="Policy text",
            source_type="policy_url", source_uri=filled.source_uri,
        ))
        self.db.commit()

        with patch("app.services.policy_knowledge_seed.KnowledgeBaseService._remove_vectors"):
            self.assertEqual(consolidate_policy_url_documents(self.db), 1)

        self.assertEqual(self.db.query(KnowledgeDocument).one().id, filled.id)
        self.assertEqual(self.db.query(KnowledgeChunk).one().document_id, filled.id)

    def test_admin_submitted_existing_url_links_without_download(self) -> None:
        from app.routes.api import knowledge_policy_document_upload

        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        first = Policy(country_id=country.id, sector_id=sector.id, policy="First")
        second = Policy(country_id=country.id, sector_id=sector.id, policy="Second")
        self.db.add_all([first, second])
        self.db.flush()
        document = KnowledgeDocument(
            title="Shared", source_type="policy_url",
            source_uri="https://EXAMPLE.org/shared.pdf#first", scope=POLICY_DOCUMENT_SCOPE,
            policy_id=first.id,
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Shared text",
            source_type="policy_url", source_uri=document.source_uri,
        ))
        self.db.commit()
        request = SimpleNamespace(
            form=AsyncMock(return_value={
                "policy_id": second.id, "url": "https://example.org/shared.pdf",
            }),
        )
        with (
            patch("app.routes.api._can_manage_main_knowledge", AsyncMock(return_value=True)),
            patch("app.routes.api.payload_too_large_response", return_value=None),
            patch("app.routes.api._ingestion_progress_id", return_value="progress-1"),
            patch("app.routes.api.record_audit_event"),
            patch("app.routes.api.KnowledgeBaseService.ingest_url", AsyncMock(side_effect=AssertionError("downloaded"))),
        ):
            result = asyncio.run(knowledge_policy_document_upload(
                request, current_user=SimpleNamespace(id="admin-1"), db=self.db,
            ))

        self.assertTrue(result["reused"])
        self.assertEqual(result["document_id"], document.id)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)
        self.assertEqual(self.db.query(PolicyKnowledgeDocument).filter_by(
            policy_id=second.id, knowledge_document_id=document.id,
        ).count(), 1)

    def test_sqlite_migration_backfills_existing_policy_links(self) -> None:
        from app.db.sqlite_migrations import _027_policy_knowledge_documents

        country = Country(name="Germany", map_code="DE")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        policy = Policy(country_id=country.id, sector_id=sector.id, policy="Legacy")
        self.db.add(policy)
        self.db.flush()
        document = KnowledgeDocument(
            title="Legacy", source_type="policy_url",
            source_uri="https://example.org/legacy.pdf", scope=POLICY_DOCUMENT_SCOPE,
            policy_id=policy.id,
        )
        self.db.add(document)
        self.db.commit()

        with self.db.get_bind().begin() as connection:
            _027_policy_knowledge_documents(connection)

        self.assertEqual(self.db.query(PolicyKnowledgeDocument).filter_by(
            policy_id=policy.id, knowledge_document_id=document.id,
        ).count(), 1)

    def test_chat_submitted_existing_url_skips_download(self) -> None:
        from app.routes.api import _chat_payload

        document = KnowledgeDocument(
            title="Shared", source_type="policy_url",
            source_uri="https://example.org/shared.pdf", scope=POLICY_DOCUMENT_SCOPE,
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Shared text",
            source_type="policy_url", source_uri=document.source_uri,
        ))
        self.db.commit()
        request = SimpleNamespace(
            headers={"content-type": "multipart/form-data"},
            form=AsyncMock(return_value={
                "session_id": "session-1",
                "policy_reference_url": "https://example.org/shared.pdf#section",
            }),
        )
        with (
            patch("app.routes.api.payload_too_large_response", return_value=None),
            patch("app.routes.api._ingestion_progress_id", return_value="progress-1"),
            patch("app.routes.api.KnowledgeBaseService.ingest_url", AsyncMock(side_effect=AssertionError("downloaded"))),
        ):
            payload = asyncio.run(_chat_payload(request, self.db, "user-1"))

        self.assertIn(f"Policy reference document ID: {document.id}", payload.message)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)

    def test_chat_reuses_original_policy_instead_of_other_session_copy(self) -> None:
        from app.routes.api import _chat_payload

        url = "https://www.gesetze-im-internet.de/stromstg/StromStG.pdf"
        original = KnowledgeDocument(
            title="Electricity Tax Act", source_type="original_policy_url",
            source_uri=url, scope="original_policy",
        )
        other_session = KnowledgeDocument(
            title=url, source_type="url", source_uri=url, scope="temporary",
            user_id="user-1", session_key="other-session",
        )
        self.db.add_all([original, other_session])
        self.db.flush()
        self.db.add_all(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Policy text",
            source_type=document.source_type, source_uri=url,
        ) for document in (original, other_session))
        self.db.commit()
        request = SimpleNamespace(
            headers={"content-type": "multipart/form-data"},
            form=AsyncMock(return_value={
                "session_id": "current-session", "policy_reference_url": url,
            }),
        )
        with (
            patch("app.routes.api.payload_too_large_response", return_value=None),
            patch("app.routes.api._ingestion_progress_id", return_value="progress-1"),
            patch("app.routes.api.KnowledgeBaseService.ingest_url", AsyncMock(side_effect=AssertionError("downloaded"))),
        ):
            payload = asyncio.run(_chat_payload(request, self.db, "user-1"))

        self.assertIn(f"Policy reference document ID: {original.id}", payload.message)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 2)

    def test_private_policy_reference_reuse_is_limited_to_owner(self) -> None:
        url = "https://assets.innovazione.gov.it/maas.pdf"
        document = KnowledgeDocument(
            title="MaaS reference", source_type="url", source_uri=url,
            scope="policy_reference", user_id="user-1",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Reference text",
            source_type="url", source_uri=url,
        ))
        self.db.commit()

        self.assertEqual(reusable_policy_reference_document(
            self.db, url, user_id="user-1", session_key="new-session",
        ).id, document.id)
        self.assertIsNone(reusable_policy_reference_document(
            self.db, url, user_id="user-2", session_key="new-session",
        ))

    def test_mysql_missing_link_table_uses_server_ddl(self) -> None:
        from app.db.policy_knowledge_schema import ensure_policy_knowledge_document_schema

        connection = MagicMock()
        connection.dialect.name = "mysql"
        empty_result = MagicMock()
        empty_result.all.return_value = []
        connection.execute.side_effect = [None, empty_result, empty_result]
        with patch("app.db.policy_knowledge_schema.inspect") as inspect_schema:
            inspect_schema.return_value.get_table_names.return_value = [
                "policies", "knowledge_documents",
            ]
            changed = ensure_policy_knowledge_document_schema(connection)

        self.assertTrue(changed)
        ddl = str(connection.execute.call_args_list[0].args[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS policy_knowledge_documents", ddl)
        self.assertIn("ENGINE=InnoDB", ddl)
