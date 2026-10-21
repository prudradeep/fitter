"""Import a complete OfflineAdmin seed snapshot and its FAISS indexes into MySQL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE_SCOPES = ("main", "sector_prompt", "policy_document")
INSERT_BATCH_SIZE = 500
SCOPE_SUFFIXES = {
    "main": "main",
    "sector_prompt": "sector_prompts",
    "policy_document": "policy_reference",
}


def _normalize(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _policy_key(
    excel_row_number: object,
    policy: object,
    policy_url: object,
    country_name: object,
    sector_name: object,
) -> tuple[object, ...]:
    return (
        int(excel_row_number) if excel_row_number is not None else None,
        _normalize(policy),
        str(policy_url or "").strip(),
        _normalize(country_name),
        _normalize(sector_name),
    )


def _policy_content_key(key: tuple[object, ...]) -> tuple[object, ...]:
    """Ignore the physical worksheet row, which may change between releases."""
    return key[1:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        connection.close()
        raise ValueError(f"SQLite source failed quick_check: {path}")
    return connection


def _validate_bundle(source: Path) -> None:
    manifest_path = source.parent / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Offline seed bundle manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "dr-transition-offline-seed-v1":
        raise ValueError("Source is not a Dr Transition OfflineAdmin seed bundle")
    if manifest.get("database_file") != source.name:
        raise ValueError("Bundle manifest database filename does not match --source")
    if _sha256(source) != manifest.get("database_sha256"):
        raise ValueError("Offline SQLite database checksum does not match its manifest")


def _bundle_manifest(source: Path) -> dict[str, object]:
    manifest = json.loads((source.parent / "manifest.json").read_text(encoding="utf-8"))
    indexes = manifest.get("indexes")
    if not isinstance(indexes, list) or not indexes:
        raise ValueError("Offline seed bundle has no FAISS indexes")
    return manifest


def _replace_all_database_rows(source: sqlite3.Connection, engine, *, dry_run: bool) -> dict[str, int]:
    """Replace server application rows with every table snapshot in seed.db."""
    import json as json_module
    from datetime import date, datetime, time

    from sqlalchemy import MetaData
    from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, JSON, Time

    metadata = MetaData()
    metadata.reflect(bind=engine)
    target_tables = metadata.tables
    source_names = {
        str(row[0])
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    control_tables = {"schema_migrations", "alembic_version"}
    missing = sorted(source_names - control_tables - set(target_tables))
    if missing:
        raise ValueError(
            "Server schema is missing tables from seed.db: " + ", ".join(missing)
            + ". Deploy and apply the matching application migrations first."
        )

    counts: dict[str, int] = {}
    for name in sorted(source_names - control_tables):
        source_columns = {str(row[1]) for row in source.execute(f'PRAGMA table_info("{name}")')}
        target = target_tables[name]
        target_columns = {column.name for column in target.columns}
        missing_columns = sorted(source_columns - target_columns)
        if missing_columns:
            raise ValueError(
                f"Server table {name} is missing seed.db columns: {', '.join(missing_columns)}"
            )
        for column in target.columns:
            if (
                column.name not in source_columns
                and not column.nullable
                and column.default is None
                and column.server_default is None
                and column.autoincrement is not True
                and column.computed is None
            ):
                raise ValueError(
                    f"Server table {name} requires column {column.name}, which is absent from seed.db"
                )
        counts[name] = int(source.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])

    if dry_run:
        return counts

    def convert_row(name: str, row: sqlite3.Row) -> dict[str, object]:
        converted: dict[str, object] = {}
        for column in target_tables[name].columns:
            if column.name not in row.keys():
                continue
            value = row[column.name]
            if value is None:
                converted[column.name] = None
                continue
            kind = column.type
            if isinstance(kind, DateTime) and isinstance(value, str):
                converted[column.name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif isinstance(kind, Date) and not isinstance(kind, DateTime) and isinstance(value, str):
                converted[column.name] = date.fromisoformat(value[:10])
            elif isinstance(kind, Time) and isinstance(value, str):
                converted[column.name] = time.fromisoformat(value)
            elif isinstance(kind, JSON) and isinstance(value, str):
                converted[column.name] = json_module.loads(value)
            elif isinstance(kind, Boolean):
                converted[column.name] = bool(value)
            else:
                converted[column.name] = value
        return converted

    with engine.connect() as target_connection:
        target_connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        target_connection.commit()
        try:
            with target_connection.begin():
                # Replace every application table, including MySQL-only tables,
                # so the resulting server reflects this clean seed snapshot.
                for name in reversed(sorted(target_tables)):
                    if name not in control_tables:
                        target_connection.execute(target_tables[name].delete())

                for name in sorted(source_names - control_tables):
                    table = target_tables[name]
                    cursor = source.execute(f'SELECT * FROM "{name}"')
                    while rows := cursor.fetchmany(250):
                        target_connection.execute(
                            table.insert(), [convert_row(name, row) for row in rows]
                        )
        finally:
            target_connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
            target_connection.commit()
    return counts


def _install_bundle_indexes(
    source: Path, manifest: dict[str, object], settings, *, validate_only: bool = False
) -> None:
    """Install the bundled vectors whose IDs match the imported SQLite rows."""
    import faiss

    from app.services.seed_index_bundle import _copy_atomically, _write_ollama_index_metadata

    model = str(manifest.get("embedding_model") or "").strip()
    if getattr(settings, "embedding_provider", "ollama") != "ollama":
        raise ValueError("Bundled indexes require the Ollama embedding provider")
    if model != str(settings.ollama_embedding_model or "").strip():
        raise ValueError(
            f"Bundle embedding model {model!r} does not match server model "
            f"{settings.ollama_embedding_model!r}"
        )
    entries = manifest["indexes"]
    scopes = [str(entry.get("scope") or "") for entry in entries if isinstance(entry, dict)]
    if len(scopes) != len(entries) or len(set(scopes)) != len(scopes):
        raise ValueError("Offline seed bundle contains invalid or duplicate FAISS scopes")
    index_base = Path(settings.faiss_index_path)
    destinations: list[tuple[Path, Path, int]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid FAISS index entry in bundle manifest")
        filename = str(entry.get("file") or "")
        scope = str(entry.get("scope") or "")
        expected_filename = f"{index_base.stem}.{SCOPE_SUFFIXES.get(scope, '')}{index_base.suffix}"
        if scope not in SCOPE_SUFFIXES or filename != expected_filename:
            raise ValueError(f"Unexpected FAISS scope or filename in bundle: {scope!r}, {filename!r}")
        if Path(filename).name != filename or not filename.endswith(".faiss"):
            raise ValueError(f"Invalid FAISS bundle filename: {filename!r}")
        bundled = source.parent / filename
        if not bundled.is_file() or _sha256(bundled) != entry.get("sha256"):
            raise ValueError(f"FAISS index checksum does not match manifest: {filename}")
        index = faiss.read_index(str(bundled))
        if index.ntotal != entry.get("vectors") or index.d != entry.get("dimensions"):
            raise ValueError(f"FAISS index metadata does not match manifest: {filename}")
        destination = index_base.with_name(filename)
        if destination.exists() and _sha256(destination) != entry.get("sha256"):
            raise ValueError(f"Server FAISS index already exists: {destination}")
        destinations.append((bundled, destination, index.d))
    if validate_only:
        return
    installed: list[Path] = []
    try:
        for bundled, destination, dimensions in destinations:
            if not destination.exists():
                _copy_atomically(bundled, destination)
                installed.append(destination)
            metadata = destination.with_suffix(f"{destination.suffix}.metadata.json")
            if not metadata.exists():
                installed.append(metadata)
            _write_ollama_index_metadata(destination, model, dimensions)
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        raise


def _source_policies(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row["id"]): row
        for row in connection.execute(
            """
            SELECT p.id, p.excel_row_number, p.policy, p.policy_url,
                   p.country_id, p.sector_id, c.name AS country_name,
                   s.name AS sector_name
            FROM policies p
            LEFT JOIN countries c ON c.id = p.country_id
            LEFT JOIN sectors s ON s.id = p.sector_id
            """
        )
    }


def _import_rows(source: sqlite3.Connection, session_factory, *, dry_run: bool) -> dict[str, int]:
    from sqlalchemy import select

    from app.models import Country, KnowledgeChunk, KnowledgeDocument, Policy, Region, Sector
    from app.services.knowledge_base import POLICY_DOCUMENT_SCOPE

    source_tables = {
        str(row[0])
        for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"knowledge_documents", "knowledge_chunks", "policies", "countries", "sectors"} <= source_tables:
        raise ValueError("SQLite source does not contain the required KB/reference tables")

    source_policies = _source_policies(source)
    source_country_names = {
        str(row["id"]): str(row["name"])
        for row in source.execute("SELECT id, name FROM countries")
    }
    source_sector_names = {
        str(row["id"]): str(row["name"])
        for row in source.execute("SELECT id, name FROM sectors")
    }
    source_regions = {
        str(row["id"]): row
        for row in source.execute("SELECT id, name, country_id FROM regions")
    }
    source_documents = source.execute(
        """
        SELECT id, title, source_type, source_uri, scope, scope_level,
               user_id, session_key, custom_hazard_id, policy_id,
               country_id, region_id, sector_id
        FROM knowledge_documents
        WHERE scope IN (?, ?, ?)
        ORDER BY scope, source_uri, id
        """,
        SOURCE_SCOPES,
    ).fetchall()

    result = {
        "imported_documents": 0,
        "imported_chunks": 0,
        "skipped_existing": 0,
        "skipped_empty": 0,
        "would_import_documents": 0,
        "would_import_chunks": 0,
    }

    with session_factory() as db:
        country_rows = db.scalars(select(Country)).all()
        sector_rows = db.scalars(select(Sector)).all()
        region_rows = db.scalars(select(Region)).all()
        target_country_by_name = {_normalize(row.name): row.id for row in country_rows}
        target_sector_by_name = {_normalize(row.name): row.id for row in sector_rows}
        target_country_names = {row.id: row.name for row in country_rows}
        target_sector_names = {row.id: row.name for row in sector_rows}
        target_regions = {
            (row.country_id, _normalize(row.name)): row.id for row in region_rows
        }
        target_policy_keys: dict[tuple[object, ...], list[Policy]] = defaultdict(list)
        target_policy_content_keys: dict[tuple[object, ...], list[Policy]] = defaultdict(list)
        for policy in db.scalars(select(Policy)).all():
            key = _policy_key(
                policy.excel_row_number,
                policy.policy,
                policy.policy_url,
                target_country_names.get(policy.country_id),
                target_sector_names.get(policy.sector_id),
            )
            target_policy_keys[key].append(policy)
            target_policy_content_keys[_policy_content_key(key)].append(policy)

    def map_country(source_id: object, *, context: str) -> str | None:
        if source_id is None:
            return None
        name = source_country_names.get(str(source_id))
        mapped = target_country_by_name.get(_normalize(name)) if name else None
        if mapped is None:
            raise ValueError(f"No matching target country for {context}: {source_id}")
        return mapped

    def map_sector(source_id: object, *, context: str) -> str | None:
        if source_id is None:
            return None
        name = source_sector_names.get(str(source_id))
        mapped = target_sector_by_name.get(_normalize(name)) if name else None
        if mapped is None:
            raise ValueError(f"No matching target sector for {context}: {source_id}")
        return mapped

    def map_region(source_id: object, country_id: str | None, *, context: str) -> str | None:
        if source_id is None:
            return None
        row = source_regions.get(str(source_id))
        if row is None:
            raise ValueError(f"Source region is missing for {context}: {source_id}")
        mapped_country_id = map_country(row["country_id"], context=context)
        if country_id is not None and mapped_country_id != country_id:
            raise ValueError(f"Region country does not match its KB row for {context}")
        mapped = target_regions.get((mapped_country_id, _normalize(row["name"])))
        if mapped is None:
            raise ValueError(f"No matching target region for {context}: {row['name']}")
        return mapped

    def map_policy(source_id: object, *, context: str) -> Policy | None:
        if source_id is None:
            return None
        source_policy = source_policies.get(str(source_id))
        if source_policy is None:
            raise ValueError(f"Source policy is missing for {context}: {source_id}")
        key = _policy_key(
            source_policy["excel_row_number"],
            source_policy["policy"],
            source_policy["policy_url"],
            source_policy["country_name"],
            source_policy["sector_name"],
        )
        matches = target_policy_keys.get(key, [])
        if not matches:
            matches = target_policy_content_keys.get(_policy_content_key(key), [])
        if len(matches) != 1:
            match_description = (
                f"row={source_policy['excel_row_number']}, "
                f"policy={source_policy['policy']!r}, "
                f"country={source_policy['country_name']!r}, "
                f"sector={source_policy['sector_name']!r}, "
                f"url={source_policy['policy_url']!r}"
            )
            raise ValueError(
                f"Expected one matching seeded MySQL policy for {context} "
                f"({match_description}); found {len(matches)}. "
                "Seed matching Policies.xlsx reference data on MySQL first."
            )
        return matches[0]

    for source_doc in source_documents:
        source_doc_id = str(source_doc["id"])
        context = f"{source_doc['scope']} document {source_doc_id}"
        if (
            source_doc["user_id"] is not None
            or source_doc["session_key"] is not None
            or source_doc["custom_hazard_id"] is not None
        ):
            raise ValueError(f"Refusing to import private or session KB data: {context}")

        source_policy_id = source_doc["policy_id"]
        target_policy = map_policy(source_policy_id, context=context)
        if source_doc["scope"] == POLICY_DOCUMENT_SCOPE and target_policy is None:
            raise ValueError(f"Policy KB document has no policy reference: {context}")

        target_country_id = map_country(source_doc["country_id"], context=context)
        target_sector_id = map_sector(source_doc["sector_id"], context=context)
        target_region_id = map_region(
            source_doc["region_id"], target_country_id, context=context
        )
        chunks = source.execute(
            """
            SELECT id, chunk_index, content, source_type, source_uri, page_number,
                   user_id, country_id, region_id, sector_id, scope_level
            FROM knowledge_chunks WHERE document_id = ?
            ORDER BY chunk_index, id
            """,
            (source_doc_id,),
        ).fetchall()
        if not chunks:
            result["skipped_empty"] += 1
            continue
        if any(chunk["user_id"] is not None for chunk in chunks):
            raise ValueError(f"Refusing to import user-owned chunks: {context}")
        prepared_chunks = [
            (
                chunk,
                map_country(chunk["country_id"], context=context),
                map_region(
                    chunk["region_id"],
                    map_country(chunk["country_id"], context=context),
                    context=context,
                ),
                map_sector(chunk["sector_id"], context=context),
            )
            for chunk in chunks
        ]

        with session_factory() as db:
            if target_policy is not None:
                existing = db.scalar(
                    select(KnowledgeDocument.id).where(
                        KnowledgeDocument.scope == source_doc["scope"],
                        KnowledgeDocument.policy_id == target_policy.id,
                    ).limit(1)
                )
            else:
                existing = db.scalar(
                    select(KnowledgeDocument.id).where(
                        KnowledgeDocument.scope == source_doc["scope"],
                        KnowledgeDocument.source_type == source_doc["source_type"],
                        KnowledgeDocument.source_uri == source_doc["source_uri"],
                    ).limit(1)
                )
            if existing is not None:
                if str(existing) != source_doc_id:
                    raise ValueError(
                        f"Existing {context} has ID {existing}, but bundle vectors require "
                        f"ID {source_doc_id}. Import into an empty KB or remove the conflicting row."
                    )
                existing_chunk_ids = set(
                    db.scalars(
                        select(KnowledgeChunk.id).where(
                            KnowledgeChunk.document_id == source_doc_id
                        )
                    ).all()
                )
                source_chunk_ids = {str(chunk["id"]) for chunk, *_ in prepared_chunks}
                if existing_chunk_ids != source_chunk_ids:
                    raise ValueError(f"Existing {context} has different chunk IDs from the bundle")
                result["skipped_existing"] += 1
                continue

            if dry_run:
                result["would_import_documents"] += 1
                result["would_import_chunks"] += len(prepared_chunks)
                continue

            try:
                document = KnowledgeDocument(
                    id=source_doc_id,
                    title=str(source_doc["title"] or "Knowledge document")[:255],
                    source_type=str(source_doc["source_type"] or "document")[:40],
                    source_uri=source_doc["source_uri"],
                    scope=str(source_doc["scope"]),
                    scope_level=str(source_doc["scope_level"] or "global"),
                    faiss_indexed=0,
                    policy_id=target_policy.id if target_policy else None,
                    country_id=target_country_id,
                    region_id=target_region_id,
                    sector_id=target_sector_id,
                )
                db.add(document)
                db.flush()
                imported_chunks = 0
                for offset in range(0, len(prepared_chunks), INSERT_BATCH_SIZE):
                    batch = prepared_chunks[offset : offset + INSERT_BATCH_SIZE]
                    db.add_all(
                        KnowledgeChunk(
                            id=str(chunk["id"]),
                            document_id=document.id,
                            chunk_index=int(chunk["chunk_index"]),
                            content=str(chunk["content"] or ""),
                            source_type=str(chunk["source_type"] or document.source_type)[:40],
                            source_uri=chunk["source_uri"] or document.source_uri,
                            page_number=chunk["page_number"],
                            scope_level=str(chunk["scope_level"] or document.scope_level),
                            faiss_indexed=0,
                            country_id=country_id,
                            region_id=region_id,
                            sector_id=sector_id,
                        )
                        for chunk, country_id, region_id, sector_id in batch
                    )
                    db.flush()
                    imported_chunks += len(batch)
                db.commit()
                result["imported_documents"] += 1
                result["imported_chunks"] += imported_chunks
                print(f"Imported {context}: {imported_chunks} chunks", flush=True)
            except Exception:
                db.rollback()
                raise

    return result


async def _index_scopes(session_factory, *, rebuild: bool = False) -> int:
    from app.services.knowledge_base import (
        MAIN_KB_SCOPE,
        POLICY_DOCUMENT_SCOPE,
        SECTOR_PROMPT_SCOPE,
        KnowledgeBaseService,
    )

    failures = 0
    for scope in (MAIN_KB_SCOPE, SECTOR_PROMPT_SCOPE, POLICY_DOCUMENT_SCOPE):
        with session_factory() as db:
            service = KnowledgeBaseService(db, None, scope=scope)
            started_at = time.monotonic()
            last_report_at = started_at
            last_report_count = 0
            action = "Rebuilding" if rebuild else "Checking for unindexed chunks in"
            print(f"{action} FAISS index for {scope}...", flush=True)

            def report_progress(completed: int, total: int) -> None:
                nonlocal last_report_at, last_report_count
                now = time.monotonic()
                if (
                    completed == 1
                    or completed == total
                    or completed - last_report_count >= 100
                    or now - last_report_at >= 10
                ):
                    elapsed = int(now - started_at)
                    hours, remainder = divmod(elapsed, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    percent = completed * 100 / total if total else 100.0
                    print(
                        f"Progress {scope}: {completed:,}/{total:,} chunks "
                        f"({percent:.1f}%) elapsed {hours:02}:{minutes:02}:{seconds:02}",
                        flush=True,
                    )
                    last_report_at = now
                    last_report_count = completed

            if rebuild:
                result = await service.rebuild_index(progress_callback=report_progress)
                if result.get("error"):
                    failures += 1
                    print(f"Index failed for {scope}: {result}", flush=True)
                    continue
                reconciled = await service.ensure_indexed_from_database()
                indexed = int(result.get("indexed_chunks", 0))
                failed = int(reconciled.get("failed", 0))
                if reconciled.get("error"):
                    failed += 1
            else:
                # This checks vector IDs in the existing FAISS file and only
                # embeds chunks whose IDs are absent. It also reconciles the
                # per-row faiss_indexed flags with the actual index contents.
                reconciled = await service.ensure_indexed_from_database(
                    progress_callback=report_progress,
                )
                indexed = int(reconciled.get("chunks", 0))
                skipped = int(reconciled.get("skipped_existing", 0))
                failed = int(reconciled.get("failed", 0))
                if reconciled.get("error"):
                    failed += 1
            if failed:
                failures += 1
            if rebuild:
                print(
                    f"Index {scope}: {indexed} chunks rebuilt; failed={failed}",
                    flush=True,
                )
            else:
                print(
                    f"Index {scope}: added={indexed}, already indexed and skipped={skipped}, "
                    f"failed={failed}",
                    flush=True,
                )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "build" / "seed-indexes" / "seed.db",
        help="Offline bundle SQLite snapshot (default: build/seed-indexes/seed.db)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Server environment file, e.g. /etc/dr-transition.env",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate mappings without importing")
    parser.add_argument("--no-index", action="store_true", help="Import rows without indexing; a later normal run indexes only missing chunks")
    parser.add_argument("--index-only", action="store_true", help="Force-rebuild indexes without importing")
    parser.add_argument(
        "--replace-all-data",
        action="store_true",
        help=(
            "Destructively replace every server application table with all table data "
            "from seed.db, then install the matching bundled FAISS indexes."
        ),
    )
    args = parser.parse_args()
    if args.dry_run and (args.no_index or args.index_only):
        parser.error("--dry-run cannot be combined with --no-index or --index-only")
    if args.no_index and args.index_only:
        parser.error("--no-index and --index-only cannot be combined")
    if args.replace_all_data and (args.no_index or args.index_only):
        parser.error("--replace-all-data cannot be combined with --no-index or --index-only")

    source_path = args.source.resolve()
    if not args.index_only:
        if not source_path.is_file():
            parser.error(f"SQLite source was not found: {source_path}")
        try:
            _validate_bundle(source_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.env_file:
        env_file = args.env_file.resolve()
        if not env_file.is_file():
            parser.error(f"Environment file was not found: {env_file}")
        import os

        os.environ["ENV_FILE"] = str(env_file)

    from app.config import get_settings
    from app.db.session import SessionLocal, engine, validate_database_connection

    settings = get_settings()
    if not settings.is_server_mode or engine.dialect.name != "mysql":
        parser.error("Target must be APP_MODE=server with a MySQL DATABASE_URL")
    try:
        validate_database_connection()
        bundle_manifest = None
        if not args.index_only:
            bundle_manifest = _bundle_manifest(source_path)
            if args.replace_all_data:
                _install_bundle_indexes(source_path, bundle_manifest, settings, validate_only=True)
            with closing(_open_source(source_path)) as source:
                if args.replace_all_data:
                    result = _replace_all_database_rows(source, engine, dry_run=args.dry_run)
                else:
                    result = _import_rows(source, SessionLocal, dry_run=args.dry_run)
            print("Import result:", json.dumps(result, sort_keys=True), flush=True)
        if args.dry_run or args.no_index:
            return 0
        if bundle_manifest is not None:
            _install_bundle_indexes(source_path, bundle_manifest, settings)
            from sqlalchemy import update

            from app.models import KnowledgeChunk, KnowledgeDocument

            scopes = [str(entry["scope"]) for entry in bundle_manifest["indexes"]]
            with closing(_open_source(source_path)) as source:
                document_ids = [
                    str(row[0])
                    for row in source.execute(
                        f"SELECT id FROM knowledge_documents WHERE scope IN ({','.join('?' for _ in scopes)})",
                        tuple(scopes),
                    )
                ]
                chunk_ids = [
                    str(row[0])
                    for row in source.execute(
                        f"SELECT id FROM knowledge_chunks WHERE document_id IN ({','.join('?' for _ in document_ids)})",
                        tuple(document_ids),
                    )
                ] if document_ids else []
            with SessionLocal() as db:
                if document_ids:
                    db.execute(
                        update(KnowledgeDocument)
                        .where(KnowledgeDocument.id.in_(document_ids))
                        .values(faiss_indexed=1)
                    )
                if chunk_ids:
                    db.execute(
                        update(KnowledgeChunk)
                        .where(KnowledgeChunk.id.in_(chunk_ids))
                        .values(faiss_indexed=1)
                    )
                db.commit()
            print("Installed matching bundled FAISS indexes", flush=True)
            failures = 0
        else:
            failures = asyncio.run(_index_scopes(SessionLocal, rebuild=args.index_only))
        return 1 if failures else 0
    except Exception as exc:
        print(f"Offline KB import failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
