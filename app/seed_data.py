from __future__ import annotations

import argparse
import logging
import os
import secrets

from sqlalchemy import select

from app.auth import hash_password
from app.db.legacy_schema_repair import run_legacy_schema_repair
from app.db.migrations_runtime import run_runtime_migrations
from app.db.session import SessionLocal, validate_database_connection
from app.models import AppUser

DEFAULT_APP_USER_EMAIL = "admin@drtransition.local"
DEFAULT_APP_USER_PASSWORD = os.getenv("DEFAULT_APP_USER_PASSWORD", "")
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
            if existing.role != "admin":
                existing.role = "admin"
                db.commit()
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
                role="admin",
            )
        )
        db.commit()

    logging.getLogger(__name__).info("Default app user created: %s", normalized_email)
    return True


def _default_user_password(value: str) -> tuple[str, bool]:
    password = value.strip()
    if password:
        return password, False
    return f"DrTransition-{secrets.token_urlsafe(18)}!1aA", True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the database schema and reload reference data from CSV/XLSX files."
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Only reload CSV/XLSX reference data; do not apply schema.sql first.",
    )
    parser.add_argument(
        "--legacy-schema-repair",
        action="store_true",
        help=(
            "Run the legacy CREATE/ALTER repair path after versioned migrations. "
            "Use only for local/installer recovery, never routine production deploys."
        ),
    )
    parser.add_argument("--skip-default-user", action="store_true")
    parser.add_argument("--default-user-email", default=DEFAULT_APP_USER_EMAIL)
    parser.add_argument(
        "--default-user-password",
        default=DEFAULT_APP_USER_PASSWORD,
        help=(
            "Password for the initial admin account. If omitted, a one-time "
            "random password is generated and printed when the account is created."
        ),
    )
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
    run_runtime_migrations(
        apply_base_schema=not args.skip_schema,
        seed_reference_data=not args.legacy_schema_repair,
    )
    if args.legacy_schema_repair:
        run_legacy_schema_repair(seed_reference_data=True)
    if not args.skip_default_user:
        default_password, generated_password = _default_user_password(args.default_user_password)
        created = ensure_default_app_user(
            email=args.default_user_email,
            password=default_password,
            name=args.default_user_name,
            designation=args.default_user_designation,
            organisation_type=args.default_user_organisation_type,
            organisation_name=args.default_user_organisation_name,
        )
        if created:
            print(f"Default app user created: {args.default_user_email.strip().casefold()}")
            if generated_password:
                print(f"Generated default app user password: {default_password}")
        else:
            print(f"Default app user ready: {args.default_user_email.strip().casefold()}")
    print("Database reference data seeded successfully.")


if __name__ == "__main__":
    main()
