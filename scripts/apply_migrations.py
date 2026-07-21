import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.migrations_runtime import run_runtime_migrations
from app.db.session import validate_database_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Dr Transition database migrations.")
    parser.add_argument(
        "--apply-base-schema",
        action="store_true",
        help="Apply schema.sql first. Use for fresh databases before versioned migrations.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    validate_database_connection()
    run_runtime_migrations(apply_base_schema=args.apply_base_schema)
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
