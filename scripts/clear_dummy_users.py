"""Remove the 15 dummy accounts created by seed_dummy_users.py."""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AppUser


USER_COUNT = 15
EMAIL_DOMAIN = "local.test"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Delete the matching dummy accounts. Without this flag, only list them.",
    )
    args = parser.parse_args()
    emails = [
        f"dummy.user{index:02d}@{EMAIL_DOMAIN}"
        for index in range(1, USER_COUNT + 1)
    ]

    with SessionLocal() as db:
        users = db.scalars(select(AppUser).where(AppUser.email.in_(emails))).all()
        if not users:
            print("No seeded dummy accounts found.")
            return 0

        print(f"Matching dummy accounts: {len(users)}")
        for user in users:
            print(f"  {user.email}")

        if not args.confirm:
            print("Nothing deleted. Rerun with --confirm to remove these accounts.")
            return 0

        for user in users:
            db.delete(user)
        db.commit()

    print(f"Dummy accounts deleted: {len(users)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
