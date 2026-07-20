# Backend Sync Server Deployment

This document describes how to deploy a central Dr Transition backend for
syncing user data and shared knowledge-base data from local installations.

The sync design keeps each installation's local integer primary keys intact.
Rows are matched across machines by `sync_id`, while foreign keys in sync
payloads are resolved through referenced rows' `sync_id` values.

## Scope

The backend sync service can sync:

- Users and authentication records
- Sessions, chat messages, hazards, demographics, mitigations, responses, and activities
- Reference and policy tables
- Knowledge-base document and chunk rows
- Optional audit and LLM logs when explicitly enabled

The sync service does not sync local FAISS files. FAISS indexes are derived from
`knowledge_documents` and `knowledge_chunks`, and should be rebuilt after synced
knowledge rows are imported.

## Knowledge Base Sync

Knowledge-base rows are stored in two DB tables:

- `knowledge_documents`
- `knowledge_chunks`

Synced KB scopes:

- `main`
- `validated_evidence`
- `sector_prompt`

Excluded KB scopes:

- `temporary`

Temporary KB data is session-local evidence and is intentionally not exported.
The receiver also skips temporary KB rows defensively if an older client sends
them.

After a sync import changes knowledge rows, the service marks affected KB index
scopes dirty in `sync_state`, for example:

```text
knowledge_index_dirty:main
knowledge_index_dirty:validated_evidence
knowledge_index_dirty:sector_prompt
```

Rebuild only the dirty FAISS index scopes after import. The current first pass
records the dirty scopes; an automated reindex worker can be added on top of
that marker.

## ID Strategy

Each synced row uses:

- `id`: local database primary key, never treated as global
- `sync_id`: global UUID identity used by sync
- `origin_device_id`: installation/device that first assigned the sync identity
- `sync_revision`: row revision for future conflict checks
- `sync_updated_at`: sync-layer update timestamp
- `sync_deleted_at`: tombstone field for future delete sync

Reference rows with natural unique keys receive deterministic `sync_id` values.
User-created rows receive random UUIDs when no stable natural key is available.

Sync payloads include foreign-key references as `__fk_sync_ids`. On import, the
receiver maps those global IDs back to its own local integer IDs.

## Server Topology

Deploy the same FastAPI application as the central sync server:

```text
Local desktop/client app  --->  Central FastAPI sync server  --->  Central MySQL DB
```

Recommended central services:

- FastAPI app process
- MySQL 8+
- Reverse proxy with HTTPS, such as Nginx, Caddy, IIS, or a managed load balancer
- Scheduled database backups
- Optional scheduled retention cleanup

The central server does not need to run Ollama or grounding services for sync
endpoints alone. It needs them only if users will also use the full chat and
knowledge search experience directly on the server.

## Server Environment

Set production-safe application values:

```env
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="<strong unique random secret>"
DATABASE_URL="mysql+pymysql://drtransition:<password>@<mysql-host>:3306/drtransition"
DATABASE_AUTO_MIGRATE=false
AUTH_COOKIE_SECURE=true
CSRF_PROTECTION_ENABLED=true
CORS_ORIGINS="https://your-sync-host.example"
STRUCTURED_LOGS=true
```

Enable sync on the central server:

```env
SYNC_ENABLED=true
SYNC_MODE=server
SYNC_API_TOKEN="<long random shared sync token>"
SYNC_INCLUDE_LOGS=false
```

Use a high-entropy token, store it in a secret manager where possible, and rotate
it if it is exposed.

## Client Environment

On each local installation that should sync to the central backend:

```env
SYNC_ENABLED=true
SYNC_MODE=client
SYNC_SERVER_URL="https://your-sync-host.example"
SYNC_API_TOKEN="<same shared sync token or per-client token>"
SYNC_DEVICE_ID="<stable UUID for this installation>"
SYNC_INCLUDE_LOGS=false
```

If `SYNC_DEVICE_ID` is omitted, the app derives a stable value from local
settings. For managed fleets, explicitly set a UUID per installation so device
identity remains clear during troubleshooting.

## Database Setup

Create and migrate the central database:

```bash
uv run python scripts/apply_migrations.py --apply-base-schema
uv run python scripts/apply_migrations.py
```

Seed reference data when setting up the central server or when reference source
files change:

```bash
uv run python -m app.seed_data
```

Start the app once with sync enabled, then call:

```http
GET /api/sync/status
Authorization: Bearer <sync token>
```

The first sync/status operation ensures sync metadata columns and the
`sync_state` table exist.

## Sync API

All sync routes require `SYNC_ENABLED=true` and a valid token.

Token options:

```http
Authorization: Bearer <sync token>
```

or:

```http
X-Sync-Token: <sync token>
```

Endpoints:

```http
GET /api/sync/status
POST /api/sync/pull
POST /api/sync/push
POST /api/sync/exchange
POST /api/sync/run
```

Use cases:

- `/api/sync/status`: check enabled state, device ID, syncable tables, and dirty KB index scopes
- `/api/sync/pull`: download a full sync bundle from the receiver
- `/api/sync/push`: upload and apply a sync bundle to the receiver
- `/api/sync/exchange`: upload local bundle and receive receiver bundle in one call
- `/api/sync/run`: client-side helper that exchanges with configured `SYNC_SERVER_URL`

Manual status check:

```bash
curl -H "Authorization: Bearer $SYNC_API_TOKEN" \
  https://your-sync-host.example/api/sync/status
```

Manual client sync:

```bash
curl -X POST -H "Authorization: Bearer $SYNC_API_TOKEN" \
  http://127.0.0.1:8000/api/sync/run
```

## Running The Server

Install dependencies:

```bash
uv sync
```

Run directly for a smoke test:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use a production process manager for long-running deployment. Example systemd
unit:

```ini
[Unit]
Description=Dr Transition central sync backend
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/etc/dr-transition.env
ExecStart=/usr/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Put HTTPS in front of the app. Example Nginx proxy block:

```nginx
server {
    listen 443 ssl http2;
    server_name your-sync-host.example;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## Security Checklist

- Use HTTPS only for central sync traffic.
- Keep `APP_ENV=production` on the central server.
- Use a strong `SECRET_KEY`.
- Use a long random `SYNC_API_TOKEN`.
- Restrict network access to `/api/sync/*` where possible.
- Keep `SYNC_INCLUDE_LOGS=false` unless logs are required centrally.
- Back up MySQL before migrations and before large first-time syncs.
- Protect database backups as user data.

## Operations

Health checks:

```http
GET /health/live
GET /health/ready
```

Sync status:

```http
GET /api/sync/status
```

Retention cleanup:

```bash
uv run python scripts/cleanup_retained_data.py
```

Database backup example:

```bash
mysqldump --single-transaction --routines --triggers \
  -u drtransition -p drtransition > drtransition-sync-backup.sql
```

After a sync run, inspect dirty KB scopes:

```json
{
  "knowledge_index_dirty_scopes": ["main", "sector_prompt"]
}
```

Rebuild the affected FAISS indexes before relying on vector search for those KB
scopes. Until rebuilt, the DB rows are present and lexical retrieval can still
operate where supported, but vector search may be stale.

## Deployment Flow

1. Provision MySQL and create the database/user.
2. Deploy the application files.
3. Configure production `.env`.
4. Run migrations and seed reference data.
5. Start the FastAPI service behind HTTPS.
6. Verify `/health/ready`.
7. Verify `/api/sync/status` with the sync token.
8. Configure clients with `SYNC_SERVER_URL`, `SYNC_API_TOKEN`, and stable `SYNC_DEVICE_ID`.
9. Run an initial sync from one client.
10. Check dirty KB scopes and rebuild affected indexes.
11. Schedule backups and retention cleanup.

## Limitations In Current First Pass

- Sync currently exchanges full bundles rather than cursor-based deltas.
- Conflict handling stores revision metadata but does not yet present a manual conflict UI.
- Delete/tombstone fields are present for future delete sync, but current behavior focuses on insert/update.
- KB dirty scopes are recorded, but automatic FAISS rebuild scheduling is not yet implemented.
