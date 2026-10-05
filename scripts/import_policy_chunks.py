"""Copy previously extracted policy chunks from an older SQLite database.

Run from the repository root with the application stopped. The source is opened
read-only. The target is selected explicitly; the app's policy FAISS index is
selected by its normal FAISS_INDEX_PATH setting (or --index-base).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _policy_key(row: sqlite3.Row) -> tuple[object, ...]:
    return (
        row["excel_row_number"],
        _key(row["policy"]),
        str(row["policy_url"] or "").strip(),
        _key(row["country_name"]),
        _key(row["sector_name"]),
    )


def _source_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_source_policies(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT d.id AS document_id, d.title, d.source_uri, p.id,
               p.excel_row_number, p.policy, p.policy_url, p.source,
               c.name AS country_name, s.name AS sector_name
        FROM knowledge_documents AS d
        JOIN policies AS p ON p.id = d.policy_id
        JOIN countries AS c ON c.id = p.country_id
        JOIN sectors AS s ON s.id = p.sector_id
        WHERE d.scope = 'main' AND d.source_type = 'policy_url'
          AND d.user_id IS NULL AND p.source = 'xlsx'
          AND p.excel_row_number IS NOT NULL
        ORDER BY p.excel_row_number, d.id
        """
    ).fetchall()


def _import_chunks(source: sqlite3.Connection, session_factory, *, dry_run: bool = False) -> dict[str, int]:
    from sqlalchemy import select

    from app.models import Country, KnowledgeChunk, KnowledgeDocument, Policy, Sector
    from app.services.knowledge_base import POLICY_DOCUMENT_SCOPE

    source_documents = _load_source_policies(source)
    source_counts = {}
    for row in source_documents:
        source_counts[row["excel_row_number"]] = source_counts.get(row["excel_row_number"], 0) + 1

    with session_factory() as db:
        policies = db.scalars(select(Policy).where(Policy.source == "xlsx")).all()
        countries = {row.id: row.name for row in db.scalars(select(Country))}
        sectors = {row.id: row.name for row in db.scalars(select(Sector))}
        target_by_row = {}
        for policy in policies:
            if policy.excel_row_number is None:
                continue
            key = (
                policy.excel_row_number,
                _key(policy.policy),
                str(policy.policy_url or "").strip(),
                _key(countries.get(policy.country_id)),
                _key(sectors.get(policy.sector_id)),
            )
            target_by_row.setdefault(policy.excel_row_number, []).append((key, policy))

    result = {
        "imported": 0, "chunks": 0, "would_import": 0, "would_copy_chunks": 0,
        "existing": 0, "unmatched": 0, "ambiguous": 0,
    }
    for source_doc in source_documents:
        row_number = source_doc["excel_row_number"]
        if str(source_doc["source_uri"] or "").strip() != str(source_doc["policy_url"] or "").strip():
            result["unmatched"] += 1
            print(f"Skipped source row {row_number}: document URL differs from policy URL", flush=True)
            continue
        matches = [p for key, p in target_by_row.get(row_number, []) if key == _policy_key(source_doc)]
        if source_counts[row_number] != 1 or len(matches) != 1:
            category = "ambiguous" if source_counts[row_number] != 1 or len(matches) > 1 else "unmatched"
            result[category] += 1
            print(f"Skipped source row {row_number}: {category}", flush=True)
            continue
        policy = matches[0]
        with session_factory() as db:
            existing = db.scalar(select(KnowledgeDocument.id).where(
                KnowledgeDocument.policy_id == policy.id,
                KnowledgeDocument.scope == POLICY_DOCUMENT_SCOPE,
            ).limit(1))
            if existing is not None:
                result["existing"] += 1
                continue

            chunks = source.execute(
                """SELECT chunk_index, content, page_number FROM knowledge_chunks
                   WHERE document_id = ? ORDER BY chunk_index, id""",
                (source_doc["document_id"],),
            ).fetchall()
            if not chunks or any(not str(chunk["content"] or "").strip() for chunk in chunks):
                result["unmatched"] += 1
                print(f"Skipped source row {row_number}: missing or empty chunks", flush=True)
                continue
            if dry_run:
                result["would_import"] += 1
                result["would_copy_chunks"] += len(chunks)
                continue

            document = KnowledgeDocument(
                id=str(uuid.uuid4()), title=str(source_doc["title"] or policy.policy)[:255],
                source_type="policy_url", source_uri=policy.policy_url,
                scope=POLICY_DOCUMENT_SCOPE, scope_level="global", faiss_indexed=0,
                policy_id=policy.id, country_id=policy.country_id, sector_id=policy.sector_id,
            )
            db.add(document)
            db.flush()
            for chunk in chunks:
                db.add(KnowledgeChunk(
                    id=str(uuid.uuid4()), document_id=document.id,
                    chunk_index=chunk["chunk_index"], content=chunk["content"],
                    source_type="policy_url", source_uri=policy.policy_url,
                    page_number=chunk["page_number"], scope_level="global", faiss_indexed=0,
                    country_id=policy.country_id, sector_id=policy.sector_id,
                ))
            db.commit()
            result["imported"] += 1
            result["chunks"] += len(chunks)
            print(f"Imported row {row_number}: {len(chunks)} chunks", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "dta_c - Copy.db")
    parser.add_argument("--target", type=Path, required=True, help="Current SQLite database")
    parser.add_argument("--index-base", type=Path, help="Base FAISS path, e.g. data/knowledge.faiss")
    parser.add_argument("--import-only", action="store_true", help="Copy chunks without embedding")
    parser.add_argument("--index-only", action="store_true", help="Resume embedding existing chunks")
    parser.add_argument("--dry-run", action="store_true", help="Check matches without writing")
    args = parser.parse_args()
    if args.import_only and args.index_only:
        parser.error("--import-only and --index-only cannot be combined")
    if args.dry_run and args.index_only:
        parser.error("--dry-run and --index-only cannot be combined")
    source = args.source.resolve()
    target = args.target.resolve()
    index_base = args.index_base.resolve() if args.index_base else None
    if not target.is_file() or (not args.index_only and not source.is_file()):
        parser.error("Source and target databases must already exist")
    if source == target:
        parser.error("Source and target must be different databases")

    os.chdir(ROOT)
    os.environ["DATABASE_URL"] = "sqlite:///" + target.as_posix()
    if index_base:
        os.environ["FAISS_INDEX_PATH"] = str(index_base)

    from app.db.session import SessionLocal, validate_database_connection
    from app.services.knowledge_base import KnowledgeBaseService, POLICY_DOCUMENT_SCOPE

    validate_database_connection()
    if not args.index_only:
        with closing(_source_connection(source)) as connection:
            print("Import result:", _import_chunks(connection, SessionLocal, dry_run=args.dry_run), flush=True)
    if not args.import_only and not args.dry_run:
        with SessionLocal() as db:
            result = asyncio.run(
                KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)
                .ensure_indexed_from_database()
            )
            print("Index result:", result, flush=True)
            return 1 if result.get("failed") or result.get("error") else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
