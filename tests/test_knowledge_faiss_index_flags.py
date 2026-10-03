import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.db.sqlite_migrations import _017_faiss_index_flags
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services import knowledge_base as kb


@unittest.skipIf(kb.faiss is None or kb.np is None, "FAISS is unavailable")
class KnowledgeFaissIndexFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_existing_vector_is_marked_and_only_missing_chunk_is_embedded(self) -> None:
        document = KnowledgeDocument(title="Synced policy", source_type="txt", scope="policy_document")
        self.db.add(document)
        self.db.flush()
        existing = KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Already present", source_type="txt"
        )
        missing = KnowledgeChunk(
            document_id=document.id, chunk_index=1, content="Needs indexing", source_type="txt"
        )
        self.db.add_all([existing, missing])
        self.db.commit()

        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "knowledge.policy_reference.faiss"
            with patch.object(kb.KnowledgeBaseService, "_index_path", new_callable=PropertyMock, return_value=index_path):
                service = kb.KnowledgeBaseService(self.db, None, scope="policy_document")
                service._add_vectors([existing.id], [[1.0, 0.0]])
                with patch.object(service, "_embed", AsyncMock(return_value=[0.0, 1.0])) as embed:
                    result = asyncio.run(service.ensure_indexed_from_database())
                self.assertEqual(result["chunks"], 1)
                embed.assert_awaited_once_with("Needs indexing")
                self.assertEqual(kb.faiss.read_index(str(index_path)).ntotal, 2)

        self.assertEqual(existing.faiss_indexed, 1)
        self.assertEqual(missing.faiss_indexed, 1)
        self.assertEqual(document.faiss_indexed, 1)

    def test_failed_embedding_keeps_chunk_and_document_pending(self) -> None:
        document = KnowledgeDocument(title="Synced KB", source_type="txt", scope="main")
        self.db.add(document)
        self.db.flush()
        chunk = KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Retry me", source_type="txt"
        )
        self.db.add(chunk)
        self.db.commit()

        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "knowledge.main.faiss"
            with patch.object(kb.KnowledgeBaseService, "_index_path", new_callable=PropertyMock, return_value=index_path):
                service = kb.KnowledgeBaseService(self.db, None, scope="main")
                with patch.object(service, "_embed", AsyncMock(side_effect=RuntimeError("offline"))):
                    result = asyncio.run(service.ensure_indexed_from_database())

        self.assertEqual(result["failed"], 1)
        self.assertEqual(chunk.faiss_indexed, 0)
        self.assertEqual(document.faiss_indexed, 0)

    def test_server_without_ollama_keeps_synced_chunks_pending_without_http(self) -> None:
        document = KnowledgeDocument(title="Server KB", source_type="txt", scope="main")
        self.db.add(document)
        self.db.flush()
        chunk = KnowledgeChunk(
            document_id=document.id, chunk_index=0, content="Server text", source_type="txt"
        )
        self.db.add(chunk)
        self.db.commit()
        service = kb.KnowledgeBaseService(self.db, None, scope="main")
        service.settings = service.settings.model_copy(update={
            "ollama_base_url": "", "ollama_embedding_model": "",
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "knowledge.main.faiss"
            with (
                patch.object(kb.KnowledgeBaseService, "_index_path", new_callable=PropertyMock, return_value=index_path),
                patch.object(service, "_embed", AsyncMock()) as embed,
            ):
                result = asyncio.run(service.ensure_indexed_from_database())

        embed.assert_not_awaited()
        self.assertEqual(result["failed"], 1)
        self.assertIn("OLLAMA_BASE_URL", result["error"])
        self.assertEqual(chunk.faiss_indexed, 0)
        self.assertEqual(document.faiss_indexed, 0)

    def test_server_lexical_ingest_skips_embedding_when_ollama_is_blank(self) -> None:
        service = kb.KnowledgeBaseService(self.db, None, scope="main")
        service.settings = service.settings.model_copy(update={
            "ollama_base_url": "", "ollama_embedding_model": "",
        })

        with patch.object(service, "_embed_many", AsyncMock()) as embed:
            result = asyncio.run(service.ingest_chunks(
                [kb.ChunkDraft("Server text")], "Server KB", "txt",
                allow_lexical_only=True,
            ))

        embed.assert_not_awaited()
        self.assertFalse(result["vector_indexed"])
        self.assertIn("OLLAMA_BASE_URL", result["vector_error"])
        self.assertEqual(self.db.query(KnowledgeChunk).one().faiss_indexed, 0)

    def test_embedding_rejects_url_without_protocol_before_http(self) -> None:
        service = kb.KnowledgeBaseService(self.db, None, scope="main")
        service.settings = service.settings.model_copy(update={
            "ollama_base_url": "localhost:11434",
        })

        with self.assertRaisesRegex(ValueError, "OLLAMA_BASE_URL"):
            asyncio.run(service._embed("Text"))


class FaissFlagMigrationTests(unittest.TestCase):
    def test_existing_sqlite_rows_get_pending_flags(self) -> None:
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE knowledge_documents (id TEXT PRIMARY KEY)"))
                connection.execute(text("CREATE TABLE knowledge_chunks (id TEXT PRIMARY KEY)"))
                connection.execute(text("INSERT INTO knowledge_documents (id) VALUES ('document-1')"))
                connection.execute(text("INSERT INTO knowledge_chunks (id) VALUES ('chunk-1')"))
                _017_faiss_index_flags(connection)
                _017_faiss_index_flags(connection)
                self.assertEqual(
                    connection.execute(text("SELECT faiss_indexed FROM knowledge_documents")).scalar_one(),
                    0,
                )
                self.assertEqual(
                    connection.execute(text("SELECT faiss_indexed FROM knowledge_chunks")).scalar_one(),
                    0,
                )
                self.assertIn(
                    "faiss_indexed",
                    {column["name"] for column in inspect(connection).get_columns("knowledge_chunks")},
                )
        finally:
            engine.dispose()
