import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.legacy_schema_repair import run_legacy_schema_repair
from app.db.session import validate_database_connection


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the legacy Dr Transition schema repair path. "
            "Use only for controlled local or installer recovery after a database backup."
        )
    )
    parser.add_argument(
        "--seed-reference-data",
        action="store_true",
        help="Reload reference data after applying legacy repair operations.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    validate_database_connection()
    run_legacy_schema_repair(seed_reference_data=args.seed_reference_data)
    print("Legacy schema repair completed.")


if __name__ == "__main__":
    main()
