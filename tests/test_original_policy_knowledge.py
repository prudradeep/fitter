import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.seed_data import seed_main_kb_from_files

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import (
    Country, KnowledgeChunk, KnowledgeDocument, OriginalPolicy,
    OriginalPolicyKnowledgeDocument,
)
from app.services.knowledge_base import (
    ORIGINAL_POLICY_SCOPE, POLICY_DOCUMENT_SCOPE, ChunkDraft, KnowledgeBaseService,
)
from app.services.original_policy_knowledge import (
    PENDING_SCOPE, cleanup, index, plan, prepare,
)


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        country = Country(name="Germany")
        db.add(country)
        db.flush()
        db.add_all([
            OriginalPolicy(country_id=country.id, policy_name="First policy",
                           policy_document="https://example.org/law.pdf", excel_row_number=2),
            OriginalPolicy(country_id=country.id, policy_name="Second policy",
                           policy_document="https://example.org/law.pdf", excel_row_number=3),
        ])
        db.commit()
    return factory


def test_original_policy_has_its_own_faiss_file():
    with _database()() as db:
        original = KnowledgeBaseService(db, None, scope=ORIGINAL_POLICY_SCOPE)
        policy = KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)
        assert original._index_path.name == "knowledge.original_policy.faiss"
        assert policy._index_path.name == "knowledge.policy_reference.faiss"
        assert original._index_path != policy._index_path


def test_prepare_stores_english_chunks_without_embedding_or_faiss():
    factory = _database()
    assert plan(factory)["sources"] == 1
    with (
        patch("app.services.original_policy_knowledge._extract", AsyncMock(return_value=(
            [ChunkDraft("Deutscher Text", 4)], "https://example.org/law.pdf", "original_policy_url",
        ))),
        patch("app.services.original_policy_knowledge.detect_document_language", AsyncMock(return_value="de")),
        patch("app.services.original_policy_knowledge.translate_chunks_to_english", AsyncMock(
            return_value=[ChunkDraft("English text", 4)]
        )) as translate,
        patch("app.services.original_policy_knowledge.KnowledgeBaseService._embed_many") as embed,
    ):
        first = asyncio.run(prepare(factory))
        second = asyncio.run(prepare(factory))
    assert first["prepared"] == 1 and first["failed"] == []
    assert second["existing"] == 1
    assert translate.await_count == 1
    embed.assert_not_called()
    with factory() as db:
        documents = db.scalars(select(KnowledgeDocument)).all()
        chunks = db.scalars(select(KnowledgeChunk)).all()
        links = db.scalars(select(OriginalPolicyKnowledgeDocument)).all()
        assert len(documents) == 1 and documents[0].scope == PENDING_SCOPE
        assert documents[0].faiss_indexed == 0
        assert len(chunks) == 1 and chunks[0].content == "English text"
        assert chunks[0].page_number == 4 and chunks[0].faiss_indexed == 0
        assert len(links) == 2


def test_index_requires_explicit_call_and_cleanup_checks_coverage():
    factory = _database()
    with factory() as db:
        policy = db.scalar(select(OriginalPolicy).where(OriginalPolicy.policy_name == "First policy"))
        document = KnowledgeDocument(
            title="First policy", source_type="original_policy_url",
            source_uri="https://example.org/law.pdf", scope=PENDING_SCOPE,
        )
        db.add(document)
        db.flush()
        db.add(KnowledgeChunk(document_id=document.id, chunk_index=0, content="English text",
                              source_type="original_policy_url", source_uri=document.source_uri))
        db.add(OriginalPolicyKnowledgeDocument(
            original_policy_id=policy.id, knowledge_document_id=document.id,
        ))
        db.commit()
    try:
        asyncio.run(cleanup(factory))
        assert False, "cleanup should require all original policies to be indexed"
    except ValueError as exc:
        assert "incomplete" in str(exc)
    with (
        patch("app.services.original_policy_knowledge.KnowledgeBaseService._embed_many", AsyncMock(
            return_value=[[1.0, 0.0]]
        )) as embed,
        patch("app.services.original_policy_knowledge.KnowledgeBaseService._rebuild_index_if_needed", AsyncMock(
            return_value=False
        )),
        patch("app.services.original_policy_knowledge.KnowledgeBaseService._add_vectors") as add_vectors,
    ):
        result = asyncio.run(index(factory))
    assert result["indexed"] == 1 and result["failed"] == []
    assert embed.await_count == 1
    add_vectors.assert_called_once()
    with factory() as db:
        document = db.scalar(select(KnowledgeDocument))
        chunk = db.scalar(select(KnowledgeChunk))
        assert document.scope == "original_policy" and document.faiss_indexed == 1
        assert chunk.faiss_indexed == 1


def test_main_kb_startup_seed_excludes_country_policy_documents(tmp_path):
    kb = tmp_path / "kb"
    policy_dir = kb / "additional" / "policy documents" / "Germany"
    policy_dir.mkdir(parents=True)
    (policy_dir / "policy.pdf").write_bytes(b"policy")
    (kb / "general.pdf").write_bytes(b"general")
    existing_index = tmp_path / "knowledge.main.faiss"
    existing_index.write_bytes(b"index")
    service = MagicMock()
    service._index_path = existing_index
    service.ingest_file = AsyncMock(return_value={"error": False})
    database = MagicMock()
    database.__enter__.return_value = MagicMock()
    with (
        patch("app.seed_data.KB_ROOT", kb),
        patch("app.seed_data.SessionLocal", return_value=database),
        patch("app.seed_data.KnowledgeBaseService", return_value=service),
    ):
        result = asyncio.run(seed_main_kb_from_files(overwrite=True))
    assert result["failures"] == []
    assert service.ingest_file.await_count == 1
    assert service.ingest_file.await_args.args[0] == "kb/general.pdf"
