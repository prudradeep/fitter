from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import AppUser
from app.services.sync_service import SyncService


async def sync_client_permission_enabled(
    db: Session,
    user: AppUser,
    permission: str,
    *,
    settings: Any,
) -> bool:
    if not bool(settings.sync_enabled):
        return True

    sync_mode = str(settings.sync_mode or "").strip().casefold()
    if sync_mode == "server":
        client = SyncService(db).sync_client_for_user_email(user.email)
        return bool(client and client.get(permission))

    if sync_mode != "client":
        return False

    server_url = str(settings.sync_server_url or "").strip().rstrip("/")
    token = str(settings.sync_api_token or "").strip()
    if not server_url or not token:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{server_url}/api/sync/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return False

    sync_client = payload.get("sync_client") if isinstance(payload, dict) else None
    return bool(isinstance(sync_client, dict) and sync_client.get(permission))
