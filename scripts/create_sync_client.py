import argparse
import secrets

from app.db.session import SessionLocal
from app.services.sync_service import SyncService


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update a server-owned sync client credential.")
    parser.add_argument("--name", required=True, help="Display name for the sync client.")
    parser.add_argument("--token", default="", help="Client token. Generated when omitted.")
    parser.add_argument("--user-email", default="", help="Optional user email this credential represents.")
    parser.add_argument("--main-kb", action="store_true", help="Allow syncing Main Knowledge Base to server.")
    parser.add_argument("--sector-prompts", action="store_true", help="Allow syncing Sector Prompt KB to server.")
    parser.add_argument("--reindex-sector-prompts", action="store_true", help="Allow sector prompt reindex actions.")
    parser.add_argument(
        "--no-validated-kb",
        action="store_true",
        help="Disable validated evidence KB sync for this credential.",
    )
    parser.add_argument(
        "--no-user-data",
        action="store_true",
        help="Disable user data sync permission for this credential.",
    )
    parser.add_argument("--inactive", action="store_true", help="Create/update the credential as inactive.")
    args = parser.parse_args()

    token = args.token.strip() or secrets.token_urlsafe(32)
    with SessionLocal() as db:
        token_hash = SyncService(db).upsert_sync_client(
            token=token,
            client_name=args.name,
            user_email=args.user_email,
            can_sync_main_kb=args.main_kb,
            can_sync_sector_prompts=args.sector_prompts,
            can_reindex_sector_prompts=args.reindex_sector_prompts,
            can_sync_validated_kb=not args.no_validated_kb,
            can_sync_user_data=not args.no_user_data,
            active=not args.inactive,
        )
    print("Sync client saved.")
    print(f"Token: {token}")
    print(f"Token SHA256: {token_hash}")


if __name__ == "__main__":
    main()
