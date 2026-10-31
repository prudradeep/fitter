import sqlite3

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import (
    Country, KnowledgeChunk, KnowledgeDocument, OriginalPolicy,
    OriginalPolicyKnowledgeDocument, Sector,
)
from scripts.import_offline_kb import _import_rows


def test_offline_import_links_original_policy_document():
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    source.executescript("""
        CREATE TABLE countries (id TEXT, name TEXT);
        CREATE TABLE sectors (id TEXT, name TEXT);
        CREATE TABLE regions (id TEXT, name TEXT, country_id TEXT);
        CREATE TABLE policies (
            id TEXT, excel_row_number INTEGER, policy TEXT, policy_url TEXT,
            country_id TEXT, sector_id TEXT
        );
        CREATE TABLE original_policies (id TEXT, policy_name TEXT, country_id TEXT);
        CREATE TABLE original_policy_knowledge_documents (
            knowledge_document_id TEXT, original_policy_id TEXT
        );
        CREATE TABLE knowledge_documents (
            id TEXT, title TEXT, source_type TEXT, source_uri TEXT, scope TEXT,
            scope_level TEXT, user_id TEXT, session_key TEXT, custom_hazard_id TEXT,
            policy_id TEXT, country_id TEXT, region_id TEXT, sector_id TEXT
        );
        CREATE TABLE knowledge_chunks (
            id TEXT, document_id TEXT, chunk_index INTEGER, content TEXT,
            source_type TEXT, source_uri TEXT, page_number INTEGER, user_id TEXT,
            country_id TEXT, region_id TEXT, sector_id TEXT, scope_level TEXT
        );
        INSERT INTO countries VALUES ('source-country', 'Germany');
        INSERT INTO sectors VALUES ('source-sector', 'Energy');
        INSERT INTO original_policies VALUES ('source-policy', 'Original policy', 'source-country');
        INSERT INTO knowledge_documents VALUES (
            'source-document', 'Original policy', 'original_policy_file',
            'kb/additional/policy documents/Germany/policy.pdf', 'original_policy',
            'global', NULL, NULL, NULL, NULL, 'source-country', NULL, NULL
        );
        INSERT INTO knowledge_chunks VALUES (
            'source-chunk', 'source-document', 0, 'English text',
            'original_policy_file', 'kb/additional/policy documents/Germany/policy.pdf',
            2, NULL, 'source-country', NULL, NULL, 'global'
        );
        INSERT INTO original_policy_knowledge_documents VALUES (
            'source-document', 'source-policy'
        );
    """)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        country = Country(name="Germany")
        sector = Sector(name="Energy")
        db.add_all([country, sector])
        db.flush()
        db.add(OriginalPolicy(country_id=country.id, policy_name="Original policy", excel_row_number=2))
        db.commit()

    result = _import_rows(source, factory, dry_run=False)
    assert result["imported_documents"] == 1
    assert result["imported_chunks"] == 1
    assert _import_rows(source, factory, dry_run=False)["skipped_existing"] == 1
    with factory() as db:
        document = db.scalar(select(KnowledgeDocument))
        chunk = db.scalar(select(KnowledgeChunk))
        link = db.scalar(select(OriginalPolicyKnowledgeDocument))
        policy = db.scalar(select(OriginalPolicy))
        assert document.id == "source-document" and document.policy_id is None
        assert document.scope == "original_policy"
        assert chunk.document_id == document.id and chunk.page_number == 2
        assert link.original_policy_id == policy.id
        assert link.knowledge_document_id == document.id
    source.close()
