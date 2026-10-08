"""Install release-built FAISS indexes for sync or clean offline clients.

The matching knowledge rows arrive from the sync server with stable chunk IDs.
Existing client knowledge is never replaced by an installer bundle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import Settings

logger = logging.getLogger(__name__)
SCOPES = {"main": "main", "sector_prompt": "sector_prompts", "policy_document": "policy_reference"}


def _index_metadata_path(index_path: Path) -> Path:
    return index_path.with_suffix(f"{index_path.suffix}.metadata.json")


def _write_ollama_index_metadata(index_path: Path, model: str, dimensions: int) -> None:
    metadata = {"version": 1, "provider": "ollama", "model": model, "dimensions": dimensions}
    _index_metadata_path(index_path).write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def _same_server(left: str, right: str) -> bool:
    return left.strip().rstrip("/").casefold() == right.strip().rstrip("/").casefold()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _has_existing_knowledge(db_path: Path, scopes: set[str]) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "knowledge_documents" not in tables:
            return False
        placeholders = ",".join("?" for _ in scopes)
        return db.execute(
            f"SELECT 1 FROM knowledge_documents WHERE scope IN ({placeholders}) LIMIT 1",
            tuple(sorted(scopes)),
        ).fetchone() is not None


def _copy_atomically(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def install_offline_seed_bundle(settings: Settings, bundle_dir: Path | None = None) -> str:
    """Install a clean SQLite seed and its indexes before installer database seeding."""
    if not getattr(sys, "frozen", False) or not settings.is_client_mode or settings.sync_enabled:
        return "unavailable"
    if getattr(settings, "embedding_provider", "ollama") != "ollama":
        return "embedding provider differs"
    if bundle_dir is None:
        bundle_dir = Path(sys.executable).resolve().parents[2] / "seed-indexes"
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return "unavailable"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "dr-transition-offline-seed-v1":
            return "unavailable"
        if manifest.get("embedding_model") != settings.ollama_embedding_model:
            return "embedding model differs"
        url = make_url(settings.database_url)
        if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
            return "SQLite database is not configured"
        db_target = Path(url.database)
        db_source = bundle_dir / "seed.db"
        if manifest.get("database_file") != "seed.db" or not db_source.is_file():
            raise ValueError("offline SQLite seed is missing")
        if _file_hash(db_source) != manifest.get("database_sha256"):
            raise ValueError("offline SQLite checksum mismatch")
        with closing(sqlite3.connect(db_source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("offline SQLite seed is corrupt")
        entries = manifest.get("indexes")
        if not isinstance(entries, list) or not entries:
            raise ValueError("offline manifest has no indexes")
        import faiss

        scopes: set[str] = set()
        copies: list[tuple[Path, Path, int]] = []
        index_base = Path(settings.faiss_index_path)
        for entry in entries:
            scope = entry.get("scope") if isinstance(entry, dict) else None
            if scope not in SCOPES or scope in scopes:
                raise ValueError("invalid or duplicate offline index scope")
            scopes.add(scope)
            filename = f"{index_base.stem}.{SCOPES[scope]}{index_base.suffix}"
            if entry.get("file") != filename:
                raise ValueError("offline index filename does not match configured path")
            source = bundle_dir / filename
            if not source.is_file() or _file_hash(source) != entry.get("sha256"):
                raise ValueError(f"offline index checksum mismatch: {filename}")
            index = faiss.read_index(str(source))
            if index.ntotal != entry.get("vectors") or index.d != entry.get("dimensions"):
                raise ValueError(f"offline index metadata mismatch: {filename}")
            copies.append((source, index_base.with_name(filename), index.d))
        if db_target.exists() or any(destination.exists() for _, destination, _ in copies):
            return "client database or index already exists"
        installed: list[Path] = []
        try:
            for source, destination, dimensions in copies:
                _copy_atomically(source, destination)
                installed.append(destination)
                installed.append(_index_metadata_path(destination))
                _write_ollama_index_metadata(destination, settings.ollama_embedding_model, dimensions)
            _copy_atomically(db_source, db_target)
            installed.append(db_target)
        except OSError:
            for destination in installed:
                destination.unlink(missing_ok=True)
            raise
        return f"installed offline {', '.join(sorted(scopes))}"
    except (ImportError, OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("Offline seed bundle could not be installed: %s", exc)
        return "invalid bundle"


def install_seed_indexes(settings: Settings, bundle_dir: Path | None = None) -> str:
    """Return a short status; invalid or unsuitable bundles fall back to normal indexing."""
    if (
        not getattr(sys, "frozen", False)
        or not settings.is_client_mode
        or not settings.sync_enabled
        or str(settings.sync_mode or "").strip().casefold() != "client"
        or not str(settings.sync_server_url or "").strip()
    ):
        return "unavailable"
    if getattr(settings, "embedding_provider", "ollama") != "ollama":
        return "embedding provider differs"
    if bundle_dir is None:
        bundle_dir = Path(sys.executable).resolve().parents[2] / "seed-indexes"
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return "unavailable"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "dr-transition-seed-indexes-v1":
            raise ValueError("unsupported manifest format")
        if manifest.get("embedding_model") != settings.ollama_embedding_model:
            return "embedding model differs"
        if not _same_server(str(manifest.get("sync_server_url") or ""), settings.sync_server_url):
            return "sync server differs"
        url = make_url(settings.database_url)
        if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
            return "SQLite database is not configured"
        db_path = Path(url.database)
        index_base = Path(settings.faiss_index_path)
        entries = manifest.get("indexes")
        if not isinstance(entries, list) or not entries:
            raise ValueError("manifest has no indexes")
        scopes: set[str] = set()
        copies: list[tuple[Path, Path, int]] = []
        import faiss

        for entry in entries:
            scope = entry.get("scope") if isinstance(entry, dict) else None
            if scope not in SCOPES or scope in scopes:
                raise ValueError("invalid or duplicate index scope")
            scopes.add(scope)
            filename = f"{index_base.stem}.{SCOPES[scope]}{index_base.suffix}"
            if entry.get("file") != filename:
                raise ValueError("index file name does not match configured path")
            source = bundle_dir / filename
            if not source.is_file() or _file_hash(source) != entry.get("sha256"):
                raise ValueError(f"index checksum mismatch: {filename}")
            index = faiss.read_index(str(source))
            if index.ntotal != entry.get("vectors") or index.d != entry.get("dimensions"):
                raise ValueError(f"index metadata mismatch: {filename}")
            copies.append((source, index_base.with_name(filename), index.d))
        existing_scopes = scopes | ({"policy_reference"} if "policy_document" in scopes else set())
        if _has_existing_knowledge(db_path, existing_scopes):
            return "client knowledge already exists"
        if any(destination.exists() for _, destination, _ in copies):
            return "client index already exists"
        installed: list[Path] = []
        try:
            for source, destination, dimensions in copies:
                _copy_atomically(source, destination)
                installed.append(destination)
                installed.append(_index_metadata_path(destination))
                _write_ollama_index_metadata(destination, settings.ollama_embedding_model, dimensions)
        except OSError:
            for destination in installed:
                destination.unlink(missing_ok=True)
            raise
        return f"installed {', '.join(sorted(scopes))}"
    except (ImportError, OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("Seed FAISS bundle could not be installed: %s", exc)
        return "invalid bundle"
