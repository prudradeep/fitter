from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from app.auth import hash_password
from app.database import seed_reference_data, validate_database_connection
from app.database import SessionLocal
from app.models import AppUser

DEFAULT_APP_USER_EMAIL = "admin@drtransition.local"
DEFAULT_APP_USER_PASSWORD = "DrTransition@123"
DEFAULT_APP_USER_NAME = "Dr Transition Admin"
DEFAULT_APP_USER_DESIGNATION = "Administrator"
DEFAULT_APP_USER_ORGANISATION_TYPE = "Local"
DEFAULT_APP_USER_ORGANISATION_NAME = "Dr Transition"


def ensure_default_app_user(
    *,
    email: str,
    password: str,
    name: str,
    designation: str,
    organisation_type: str,
    organisation_name: str,
) -> bool:
    normalized_email = email.strip().casefold()
    if not normalized_email or not password:
        logging.getLogger(__name__).info("Default app user creation skipped")
        return False

    with SessionLocal() as db:
        existing = db.scalar(select(AppUser).where(AppUser.email == normalized_email))
        if existing is not None:
            logging.getLogger(__name__).info(
                "Default app user already exists: %s", normalized_email
            )
            return False

        db.add(
            AppUser(
                email=normalized_email,
                name=name,
                password_hash=hash_password(password),
                designation=designation,
                organisation_type=organisation_type,
                organisation_name=organisation_name,
            )
        )
        db.commit()

    logging.getLogger(__name__).info("Default app user created: %s", normalized_email)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the database schema and reload reference data from CSV/XLSX files."
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Only reload CSV/XLSX reference data; do not apply schema.sql first.",
    )
    parser.add_argument("--skip-default-user", action="store_true")
    parser.add_argument("--default-user-email", default=DEFAULT_APP_USER_EMAIL)
    parser.add_argument("--default-user-password", default=DEFAULT_APP_USER_PASSWORD)
    parser.add_argument("--default-user-name", default=DEFAULT_APP_USER_NAME)
    parser.add_argument("--default-user-designation", default=DEFAULT_APP_USER_DESIGNATION)
    parser.add_argument(
        "--default-user-organisation-type",
        default=DEFAULT_APP_USER_ORGANISATION_TYPE,
    )
    parser.add_argument(
        "--default-user-organisation-name",
        default=DEFAULT_APP_USER_ORGANISATION_NAME,
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    validate_database_connection()
    seed_reference_data(apply_schema=not args.skip_schema)
    if not args.skip_default_user:
        created = ensure_default_app_user(
            email=args.default_user_email,
            password=args.default_user_password,
            name=args.default_user_name,
            designation=args.default_user_designation,
            organisation_type=args.default_user_organisation_type,
            organisation_name=args.default_user_organisation_name,
        )
        if created:
            print(f"Default app user created: {args.default_user_email.strip().casefold()}")
        else:
            print(f"Default app user ready: {args.default_user_email.strip().casefold()}")
    print("Database reference data seeded successfully.")


if __name__ == "__main__":
    main()
