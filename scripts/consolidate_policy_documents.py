"""Back up a SQLite database and consolidate duplicate policy URL documents."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.policy_knowledge_schema import ensure_policy_knowledge_document_schema
from app.services.knowledge_base import KnowledgeBaseService, POLICY_DOCUMENT_SCOPE
from app.services.policy_knowledge_seed import consolidate_policy_url_documents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()

    database = args.database.resolve(strict=True)
    backup_dir = database.parent / "backups" / (
        "policy_url_dedup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(backup_dir / database.name) as target:
            source.backup(target)

    engine = create_engine(URL.create("sqlite", database=str(database)))
    factory = sessionmaker(bind=engine)
    with factory() as db:
        index_path = KnowledgeBaseService(db, None, scope=POLICY_DOCUMENT_SCOPE)._index_path
        if index_path.is_file():
            shutil.copy2(index_path, backup_dir / index_path.name)
        metadata_path = index_path.with_suffix(index_path.suffix + ".metadata.json")
        if metadata_path.is_file():
            shutil.copy2(metadata_path, backup_dir / metadata_path.name)
        ensure_policy_knowledge_document_schema(db.connection())
        db.commit()
        removed = consolidate_policy_url_documents(db)

    print(json.dumps({
        "database": str(database),
        "backup": str(backup_dir),
        "removed_duplicate_documents": removed,
    }))


if __name__ == "__main__":
    main()
