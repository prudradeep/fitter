"""Run original-policy document preparation, indexing, and cleanup manually."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "index", "cleanup"))
    parser.add_argument("--database", type=Path, help="SQLite database path; omit for the configured database")
    parser.add_argument("--index-base", type=Path, help="Base FAISS path (default: configured FAISS_INDEX_PATH)")
    args = parser.parse_args()
    if args.database is not None:
        database = args.database.resolve()
        if not database.is_file():
            parser.error(f"Database does not exist: {database}")
        os.environ["APP_MODE"] = "client"
        os.environ["DATABASE_URL"] = "sqlite:///" + database.as_posix()
    if args.index_base is not None:
        os.environ["FAISS_INDEX_PATH"] = str(args.index_base.resolve())

    from app.db.migrations_runtime import run_runtime_migrations
    from app.services.original_policy_knowledge import cleanup, index, plan, prepare

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.action != "plan":
        run_runtime_migrations()
    try:
        result = {
            "plan": plan,
            "prepare": prepare,
            "index": index,
            "cleanup": cleanup,
        }[args.action]()
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get("failed") else 0
    except Exception as exc:
        print(f"Original policy {args.action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
