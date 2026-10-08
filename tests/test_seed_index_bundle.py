from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import faiss
import numpy as np
import pytest

from app.services.seed_index_bundle import install_offline_seed_bundle, install_seed_indexes


SCRIPT = Path(__file__).resolve().parents[1] / "packaging/windows/scripts/create-seed-index-bundle.py"
SPEC = importlib.util.spec_from_file_location("create_seed_index_bundle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def _source_database(path: Path, *, synced: bool = True) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE policies (id TEXT PRIMARY KEY, source TEXT, created_by_user_id TEXT);
            CREATE TABLE app_users (id TEXT PRIMARY KEY, email TEXT);
            CREATE TABLE llm_exchange_logs (id TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE knowledge_documents (
                id TEXT PRIMARY KEY, scope TEXT, source_type TEXT, source_uri TEXT,
                user_id TEXT, session_key TEXT, custom_hazard_id TEXT, policy_id TEXT,
                faiss_indexed INTEGER, sync_id TEXT, sync_deleted_at TEXT
            );
            CREATE TABLE knowledge_chunks (
                id TEXT PRIMARY KEY, document_id TEXT, user_id TEXT,
                faiss_indexed INTEGER, sync_id TEXT, sync_deleted_at TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO knowledge_documents VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, 1, ?, NULL)",
            ("document-1", "main", "pdf", "kb/seed.pdf", "document-sync" if synced else None),
        )
        db.execute(
            "INSERT INTO knowledge_chunks VALUES (?, ?, NULL, 1, ?, NULL)",
            ("chunk-1", "document-1", "chunk-sync" if synced else None),
        )
        db.execute("INSERT INTO app_users VALUES ('user-1', 'private@example.com')")
        db.execute("INSERT INTO llm_exchange_logs VALUES ('log-1', 'private model input')")


def _source_index(path: Path, *, chunk_id: str = "chunk-1") -> None:
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(2))
    index.add_with_ids(
        np.array([[1.0, 0.0]], dtype="float32"),
        np.array([EXPORTER.vector_id(chunk_id)], dtype="int64"),
    )
    faiss.write_index(index, str(path))


def _settings(db_path: Path, index_base: Path, *, model: str = "nomic-embed-text") -> SimpleNamespace:
    return SimpleNamespace(
        is_client_mode=True,
        sync_enabled=True,
        sync_mode="client",
        sync_server_url="https://sync.example",
        ollama_embedding_model=model,
        database_url=f"sqlite:///{db_path.as_posix()}",
        faiss_index_path=str(index_base),
    )


def test_exported_index_installs_only_for_matching_fresh_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "source.db"
    index_base = tmp_path / "knowledge.faiss"
    _source_database(database)
    _source_index(tmp_path / "knowledge.main.faiss")
    bundle = tmp_path / "bundle"
    manifest = EXPORTER.export_bundle(
        database, index_base, bundle, "nomic-embed-text", "https://sync.example", ["main"]
    )
    assert manifest["indexes"][0]["vectors"] == 1
    assert not any(path.suffix == ".db" for path in bundle.iterdir())

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    client_db = tmp_path / "client.db"
    destination = tmp_path / "client" / "knowledge.faiss"
    settings = _settings(client_db, destination)
    assert install_seed_indexes(settings, bundle) == "installed main"
    assert faiss.read_index(str(destination.with_name("knowledge.main.faiss"))).ntotal == 1
    metadata_path = destination.with_name("knowledge.main.faiss.metadata.json")
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["model"] == "nomic-embed-text"
    assert install_seed_indexes(settings, bundle) == "client index already exists"

    bedrock = _settings(tmp_path / "bedrock.db", tmp_path / "bedrock" / "knowledge.faiss")
    bedrock.embedding_provider = "bedrock"
    assert install_seed_indexes(bedrock, bundle) == "embedding provider differs"
    assert not (tmp_path / "bedrock" / "knowledge.main.faiss").exists()

    other_model = _settings(tmp_path / "other.db", tmp_path / "other" / "knowledge.faiss", model="other-model")
    assert install_seed_indexes(other_model, bundle) == "embedding model differs"
    assert not (tmp_path / "other" / "knowledge.main.faiss").exists()

    existing_db = tmp_path / "existing.db"
    with sqlite3.connect(existing_db) as db:
        db.execute("CREATE TABLE knowledge_documents (scope TEXT)")
        db.execute("INSERT INTO knowledge_documents VALUES ('main')")
    existing = _settings(existing_db, tmp_path / "existing" / "knowledge.faiss")
    assert install_seed_indexes(existing, bundle) == "client knowledge already exists"


@pytest.mark.parametrize("invalid", ["unsynced", "wrong_index", "user_owned"])
def test_export_rejects_unusable_or_private_indexes(tmp_path: Path, invalid: str) -> None:
    database = tmp_path / "source.db"
    _source_database(database, synced=invalid != "unsynced")
    index_base = tmp_path / "knowledge.faiss"
    _source_index(tmp_path / "knowledge.main.faiss", chunk_id="other" if invalid == "wrong_index" else "chunk-1")
    if invalid == "user_owned":
        with sqlite3.connect(database) as db:
            db.execute("UPDATE knowledge_documents SET user_id = 'user-1'")
    with pytest.raises(ValueError):
        EXPORTER.export_bundle(
            database, index_base, tmp_path / "bundle", "nomic-embed-text",
            "https://sync.example", ["main"],
        )


def test_tampered_bundle_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "source.db"
    _source_database(database)
    index_base = tmp_path / "knowledge.faiss"
    _source_index(tmp_path / "knowledge.main.faiss")
    bundle = tmp_path / "bundle"
    EXPORTER.export_bundle(database, index_base, bundle, "nomic-embed-text", "https://sync.example", ["main"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["indexes"][0]["sha256"] = "0" * 64
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    target = tmp_path / "target" / "knowledge.faiss"
    assert install_seed_indexes(_settings(tmp_path / "target.db", target), bundle) == "invalid bundle"
    assert not target.with_name("knowledge.main.faiss").exists()


def test_offline_bundle_includes_matching_clean_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "source.db"
    _source_database(database, synced=False)
    index_base = tmp_path / "knowledge.faiss"
    _source_index(tmp_path / "knowledge.main.faiss")
    bundle = tmp_path / "bundle"
    manifest = EXPORTER.export_bundle(
        database, index_base, bundle, "nomic-embed-text", "", ["main"], offline=True
    )
    assert manifest["format"] == "dr-transition-offline-seed-v1"
    with sqlite3.connect(bundle / "seed.db") as clean:
        assert clean.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 1
        assert clean.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 0
        assert clean.execute("SELECT COUNT(*) FROM llm_exchange_logs").fetchone()[0] == 0
    assert not list(bundle.glob("seed.db-*"))
    assert b"private@example.com" not in (bundle / "seed.db").read_bytes()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    client_db = tmp_path / "client.db"
    destination = tmp_path / "client" / "knowledge.faiss"
    settings = _settings(client_db, destination)
    settings.sync_enabled = False
    assert install_offline_seed_bundle(settings, bundle) == "installed offline main"
    assert client_db.is_file()
    assert faiss.read_index(str(destination.with_name("knowledge.main.faiss"))).ntotal == 1
    assert install_offline_seed_bundle(settings, bundle) == "client database or index already exists"


def test_offline_export_rejects_user_policy(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    _source_database(database, synced=False)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO policies VALUES ('policy-1', 'user', 'user-1')")
    index_base = tmp_path / "knowledge.faiss"
    _source_index(tmp_path / "knowledge.main.faiss")
    with pytest.raises(ValueError, match="user-created policy"):
        EXPORTER.export_bundle(
            database, index_base, tmp_path / "bundle", "nomic-embed-text", "", ["main"], offline=True
        )


def test_offline_export_includes_locally_seeded_policy_vectors(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    _source_database(database, synced=False)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO policies VALUES ('policy-1', 'xlsx', NULL)")
        db.execute(
            "INSERT INTO knowledge_documents VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, 1, NULL, NULL)",
            ("policy-document-1", "policy_document", "url", "https://example.org/policy.pdf", "policy-1"),
        )
        db.execute(
            "INSERT INTO knowledge_chunks VALUES (?, ?, NULL, 1, NULL, NULL)",
            ("policy-chunk-1", "policy-document-1"),
        )
    index_base = tmp_path / "knowledge.faiss"
    _source_index(tmp_path / "knowledge.main.faiss")
    _source_index(tmp_path / "knowledge.policy_reference.faiss", chunk_id="policy-chunk-1")
    manifest = EXPORTER.export_bundle(
        database, index_base, tmp_path / "bundle", "nomic-embed-text", "",
        ["main", "policy_document"], offline=True,
    )
    assert {entry["scope"] for entry in manifest["indexes"]} == {"main", "policy_document"}
    with sqlite3.connect(tmp_path / "bundle" / "seed.db") as clean:
        assert clean.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE scope = 'policy_document'"
        ).fetchone()[0] == 1
