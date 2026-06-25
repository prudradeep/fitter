from __future__ import annotations

import argparse
import logging

from app.database import seed_reference_data, validate_database_connection


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the database schema and reload reference data from CSV/XLSX files."
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Only reload CSV/XLSX reference data; do not apply schema.sql first.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    validate_database_connection()
    seed_reference_data(apply_schema=not args.skip_schema)
    print("Database reference data seeded successfully.")


if __name__ == "__main__":
    main()
