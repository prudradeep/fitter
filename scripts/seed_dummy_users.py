"""Create 15 idempotent dummy application users for local testing."""

from __future__ import annotations

import argparse
import secrets

from sqlalchemy import select

from app.auth import hash_password, password_rule_errors
from app.db.session import SessionLocal
from app.models import AppUser


USER_COUNT = 15
EMAIL_DOMAIN = "local.test"
PASSWORD_WORDS = (
    "Amber", "Birch", "Cedar", "Cloud", "Coral", "Daisy", "Forest", "Harbor",
    "Maple", "Meadow", "Mellow", "Olive", "Orbit", "Pine", "River", "Silver",
    "Sunny", "Willow", "Winter", "Zephyr",
)


def generate_password() -> str:
    """Generate a memorable password that satisfies the application's rules."""
    first = secrets.choice(PASSWORD_WORDS)
    second = secrets.choice(PASSWORD_WORDS)
    number = secrets.randbelow(90) + 10
    return f"{first}{second}{number}!"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    users = []
    issued_passwords = set()
    for index in range(1, USER_COUNT + 1):
        password = generate_password()
        while password in issued_passwords:
            password = generate_password()
        issued_passwords.add(password)
        users.append({
            "email": f"dummy.user{index:02d}@{EMAIL_DOMAIN}",
            "name": f"Dummy User {index:02d}",
            "designation": "Test participant",
            "organisation_type": "Research",
            "organisation_name": f"Dummy Test Organisation {index:02d}",
            "password": password,
        })
    for user in users:
        errors = password_rule_errors(user["password"])
        if errors:
            parser.error(
                f"Password for {user['email']} does not meet the app's rules: "
                + "; ".join(errors)
            )
    emails = [user["email"] for user in users]

    with SessionLocal() as db:
        existing_users = {
            user.email: user
            for user in db.scalars(select(AppUser).where(AppUser.email.in_(emails))).all()
        }
        created_count = 0
        for user in users:
            password = user["password"]
            user_data = {key: value for key, value in user.items() if key != "password"}
            existing_user = existing_users.get(user["email"])
            if existing_user is None:
                db.add(AppUser(**user_data, password_hash=hash_password(password), role="user"))
                created_count += 1
            else:
                existing_user.password_hash = hash_password(password)
        db.commit()

    print(f"Dummy accounts created: {created_count}; existing accounts updated: {USER_COUNT - created_count}.")
    print("Credentials:")
    for user in users:
        print(f"  {user['email']}  {user['password']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
