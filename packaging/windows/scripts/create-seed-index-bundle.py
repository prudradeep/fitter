"""Export matching FAISS files for a synced client or an offline admin installer.

Run this against a client fully synced from the release's target server, after
its Main KB, sector prompt, and policy document indexing has completed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import faiss

SCOPES = {"main": "main", "sector_prompt": "sector_prompts", "policy_document": "policy_reference"}
OFFLINE_REFERENCE_TABLES = {
    "additional_hazard_profile_target_populations", "additional_hazard_profiles",
    "additional_hazards", "countries", "country_sectors", "eurostat_population_cache",
    "evaluation_questions",
    "knowledge_chunks", "knowledge_documents", "mitigation_measure_examples",
    "mitigation_measure_policies", "mitigation_measure_policy_additional_hazards",
    "mitigation_measure_policy_system_hazards", "mitigation_measure_target_groups",
    "policies", "policy_hazard_links", "question_options", "regions",
    "schema_migrations", "sectors", "system_hazard_socio_demographic_population_matches",
    "system_hazard_socio_demographic_target_populations", "system_hazard_socio_demographics",
    "system_hazards",
}


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _write_offline_database(source: sqlite3.Connection, target: Path, scopes: set[str]) -> None:
    extra_scopes = {
        str(row[0]) for row in source.execute("SELECT DISTINCT scope FROM knowledge_documents")
    } - scopes
    if extra_scopes:
        raise ValueError(f"Offline source has knowledge scopes without matching indexes: {sorted(extra_scopes)}")
    unsafe_policy = source.execute(
        "SELECT 1 FROM policies WHERE source = 'user' OR created_by_user_id IS NOT NULL LIMIT 1"
    ).fetchone()
    if unsafe_policy:
        raise ValueError("Offline source contains a user-created policy")
    for name in OFFLINE_REFERENCE_TABLES:
        columns = {str(row[1]) for row in source.execute(f"PRAGMA table_info({_quote_identifier(name)})")}
        if "source" in columns and source.execute(
            f"SELECT 1 FROM {_quote_identifier(name)} WHERE lower(source) IN ('user', 'custom', 'upload') LIMIT 1"
        ).fetchone():
            raise ValueError(f"Offline source contains user-created rows in {name}")
        if "sync_deleted_at" in columns and source.execute(
            f"SELECT 1 FROM {_quote_identifier(name)} WHERE sync_deleted_at IS NOT NULL LIMIT 1"
        ).fetchone():
            raise ValueError(f"Offline source contains deleted rows in {name}")
        for column in {"user_id", "created_by_user_id", "session_key"} & columns:
            if source.execute(
                f"SELECT 1 FROM {_quote_identifier(name)} WHERE {_quote_identifier(column)} IS NOT NULL LIMIT 1"
            ).fetchone():
                raise ValueError(f"Offline source contains user-owned rows in {name}")
    with closing(sqlite3.connect(target)) as clean:
        source.backup(clean)
        clean.execute("PRAGMA journal_mode = DELETE")
        clean.execute("PRAGMA foreign_keys = OFF")
        clean.execute("PRAGMA secure_delete = ON")
        table_names = [
            str(row[0]) for row in clean.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in table_names:
            if name not in OFFLINE_REFERENCE_TABLES:
                clean.execute(f"DELETE FROM {_quote_identifier(name)}")
            else:
                columns = {str(row[1]) for row in clean.execute(f"PRAGMA table_info({_quote_identifier(name)})")}
                metadata = columns & {
                    "sync_id", "origin_device_id", "sync_revision", "sync_updated_at", "sync_deleted_at"
                }
                if metadata:
                    assignments = ", ".join(f"{_quote_identifier(column)} = NULL" for column in sorted(metadata))
                    clean.execute(f"UPDATE {_quote_identifier(name)} SET {assignments}")
        clean.commit()
        missing_reference = clean.execute("PRAGMA foreign_key_check").fetchone()
        if missing_reference:
            raise ValueError(
                "Offline snapshot has a missing referenced row after removing client data "
                f"({missing_reference[0]} -> {missing_reference[2]})"
            )
        clean.execute("VACUUM")
        if clean.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Offline snapshot failed SQLite integrity check")


def vector_id(chunk_id: str) -> int:
    return int.from_bytes(hashlib.sha256(chunk_id.encode("utf-8")).digest()[:8], "big") & ((1 << 63) - 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_bundle(
    database: Path,
    index_base: Path,
    output: Path,
    embedding_model: str,
    sync_server_url: str,
    scopes: list[str],
    *,
    offline: bool = False,
) -> dict[str, object]:
    if not database.is_file():
        raise ValueError(f"SQLite database does not exist: {database}")
    if not embedding_model.strip() or (not offline and not sync_server_url.strip()):
        raise ValueError("Embedding model and sync server URL are required for a sync bundle")
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("Select each index scope once")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    entries = []
    source_files = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        for scope in scopes:
            if scope not in SCOPES:
                raise ValueError(f"Unsupported scope: {scope}")
            filename = f"{index_base.stem}.{SCOPES[scope]}{index_base.suffix}"
            index_file = index_base.with_name(filename)
            if not index_file.is_file():
                raise ValueError(f"Missing FAISS file for {scope}: {index_file}")
            documents = db.execute(
                """SELECT d.id, d.source_type, d.source_uri, d.user_id, d.session_key,
                          d.custom_hazard_id, d.policy_id, d.faiss_indexed, d.sync_id,
                          d.sync_deleted_at, p.source AS policy_source
                   FROM knowledge_documents d
                   LEFT JOIN policies p ON p.id = d.policy_id
                   WHERE d.scope = ?""",
                (scope,),
            ).fetchall()
            chunks = db.execute(
                """SELECT c.id, c.user_id, c.faiss_indexed, c.sync_id, c.sync_deleted_at
                   FROM knowledge_chunks c
                   JOIN knowledge_documents d ON d.id = c.document_id
                   WHERE d.scope = ?""",
                (scope,),
            ).fetchall()
            if not documents or not chunks:
                raise ValueError(f"No indexed knowledge rows found for {scope}")
            for row in documents:
                uri = str(row["source_uri"] or "")
                if (row["user_id"] or row["session_key"] or row["custom_hazard_id"]
                    or (not offline and not row["sync_id"])
                    or row["sync_deleted_at"] or not row["faiss_indexed"]):
                    raise ValueError(f"{scope} contains private, unsynced, deleted, or unindexed documents")
                if scope == "main" and (not uri.startswith("kb/") or row["policy_id"]):
                    raise ValueError(f"Main KB includes a non-bundled source: {uri}")
                if scope == "sector_prompt" and not uri.startswith("sector-prompt://"):
                    raise ValueError(f"Sector prompt has an unexpected source: {uri}")
                if scope == "policy_document" and (
                    not uri.startswith(("http://", "https://")) or not row["policy_id"]
                    or row["policy_source"] in (None, "user")
                ):
                    raise ValueError(f"Policy document has an unexpected source or policy: {uri}")
            if any(row["user_id"] or (not offline and not row["sync_id"]) or row["sync_deleted_at"]
                   or not row["faiss_indexed"] for row in chunks):
                raise ValueError(f"{scope} contains private, unsynced, deleted, or unindexed chunks")
            expected = {vector_id(str(row["id"])) for row in chunks}
            index = faiss.read_index(str(index_file))
            actual = {int(value) for value in faiss.vector_to_array(index.id_map)}
            if len(expected) != len(chunks) or len(actual) != index.ntotal or actual != expected:
                raise ValueError(f"FAISS IDs do not exactly match {scope} knowledge chunks")
            entries.append({
                "scope": scope,
                "file": filename,
                "sha256": sha256(index_file),
                "vectors": index.ntotal,
                "dimensions": index.d,
            })
            source_files.append((index_file, filename))
        output.mkdir(parents=True, exist_ok=True)
        if offline:
            _write_offline_database(db, output / "seed.db", set(scopes))
    for source, filename in source_files:
        shutil.copy2(source, output / filename)
    for entry in entries:
        entry["sha256"] = sha256(output / str(entry["file"]))
    manifest = {
        "format": "dr-transition-offline-seed-v1" if offline else "dr-transition-seed-indexes-v1",
        "embedding_model": embedding_model.strip(),
        "indexes": entries,
    }
    if offline:
        manifest["database_file"] = "seed.db"
        manifest["database_sha256"] = sha256(output / "seed.db")
    else:
        manifest["sync_server_url"] = sync_server_url.strip().rstrip("/")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--index-base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--sync-server-url", default="")
    parser.add_argument("--offline", action="store_true", help="Include a sanitized seeded SQLite snapshot for OfflineAdmin")
    parser.add_argument("--scopes", nargs="+", choices=SCOPES, default=list(SCOPES))
    args = parser.parse_args()
    manifest = export_bundle(
        args.database, args.index_base, args.output, args.embedding_model,
        args.sync_server_url, args.scopes, offline=args.offline,
    )
    print(f"Exported {sum(item['vectors'] for item in manifest['indexes'])} vectors to {args.output}")


if __name__ == "__main__":
    main()
