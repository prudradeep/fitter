# Backend Sync Server Deployment

This document describes how to deploy a central Dr Transition backend for
syncing user data and shared knowledge-base data from local installations.

For a complete Ubuntu server and local client setup walkthrough, see:

```text
docs/UBUNTU_SYNC_SERVER_AND_CLIENT_GUIDE.md
```

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

The sync service also does not sync internal control tables:

- `sync_clients`
- `sync_state`
- `schema_migrations`

`sync_clients` is server-owned credential state. Create or rotate those rows on
the central server only; clients receive only their raw token in local
configuration.

## Knowledge Base Sync

Knowledge-base rows are stored in two DB tables:

- `knowledge_documents`
- `knowledge_chunks`

Server-to-client KB scopes:

- `main`
- `validated_evidence`
- `sector_prompt`

Client-to-server KB scopes:

- `validated_evidence`

Admin client-to-server KB scopes:

- `main`
- `validated_evidence`
- `sector_prompt`

Excluded KB scopes:

- `temporary`

Main knowledge is centrally managed, but a client with a server-issued sync
credential can push Main KB changes during a manual client sync. The central
server never trusts `app_users.role` from a client database for admin sync
authorization. Admin KB sync is accepted only when the bearer token matches a
server-owned `sync_clients` row with the relevant permission flags. Validated
evidence is bidirectional for clients. Sector-prompt knowledge is normally
server-managed and distributed from the server to clients; only credentials with
`can_sync_sector_prompts` may push sector-prompt rows to the server.
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

- `id`: UUID primary key shared across clients and server
- `sync_id`: sync metadata identity; for UUID rows this normally matches `id`
- `origin_device_id`: installation/device that first assigned the sync identity
- `sync_revision`: row revision for future conflict checks
- `sync_updated_at`: sync-layer update timestamp
- `sync_deleted_at`: tombstone field for future delete sync

Reference rows with natural unique keys receive deterministic `sync_id` values.
User-created rows receive random UUIDs when no stable natural key is available.

Sync payloads include foreign-key references as `__fk_sync_ids`. On import, the
receiver first resolves those sync IDs and then falls back to the raw UUID FK
when the referenced UUID row already exists locally.

`app_users` rows are encrypted inside sync bundles with AES-GCM using a key
derived from the client sync token. Passwords are never synced in plaintext; the
stored `password_hash` value is encrypted in transit, decrypted by the receiver,
and written back as the same hash.

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

With `SYNC_MODE=server` and `SYNC_SERVER_EXPOSE_APP_APIS=false`, normal app APIs
are hidden and LLM-dependent startup work is skipped. Do not install, start, or
configure Ollama, reranker, or NLI services for a sync-only central backend.

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
SYNC_INCLUDE_LOGS=false
SYNC_SERVER_EXPOSE_APP_APIS=false
```

Do not set a server-wide legacy `SYNC_API_TOKEN` on the central server. Sync
tokens are created as server-owned `sync_clients` rows. The server stores only
the SHA-256 hash; the raw token is printed once and goes into the matching
client's environment.

Create server-owned sync credentials after migrations. If `--token` is omitted,
the script generates a strong token with Python `secrets.token_urlsafe(32)`.
Give normal clients no Main/Sector permissions:

```bash
uv run python scripts/create_sync_client.py \
  --name "Client 01"
```

Give an admin workstation explicit KB permissions:

```bash
uv run python scripts/create_sync_client.py \
  --name "Admin workstation" \
  --user-email "admin@example.com" \
  --main-kb \
  --sector-prompts \
  --reindex-sector-prompts \
  --manage-prompts
```

Use high-entropy per-client tokens, store them in a secret manager where
possible, and rotate them if exposed.

`--reindex-sector-prompts` controls whether the admin user named by
`--user-email` may run the sector prompt reindex action when sync is enabled.
In client mode, the local app checks the configured raw `SYNC_API_TOKEN`
against the central server's `/api/sync/status` response and uses that
server-owned permission. Without that flag, the Reindex button is hidden and
the API returns `403`.

Main KB management uses the same pattern. When sync is enabled, Main KB upload,
URL ingest, and delete actions require `can_sync_main_kb=true` for the
server-owned sync credential. In client mode this is checked against the central
server using the configured raw client token.

Validated-evidence uploads during sync require `can_sync_validated_kb=true`.
User data and app-user rows require `can_sync_user_data=true` for both inbound
server apply and server response bundles. Prompt create/update requires
`can_manage_prompts=true`. In client mode, prompt create/update is proxied to
the central server through `/api/sync/prompts`, then cached locally from the
server response.

## Client Environment

On each local installation that should sync to the central backend:

```env
SYNC_ENABLED=true
SYNC_MODE=client
SYNC_SERVER_URL="https://your-sync-host.example"
SYNC_API_TOKEN="<raw token printed by scripts/create_sync_client.py>"
SYNC_DEVICE_ID="<stable UUID for this installation>"
SYNC_INCLUDE_LOGS=false
SYNC_AUTO_ON_STARTUP=true
SYNC_INTERVAL_SECONDS=3600
```

If `SYNC_DEVICE_ID` is omitted, the app derives a stable value from local
settings. For managed fleets, explicitly set a UUID per installation so device
identity remains clear during troubleshooting.

Local clients can run sync manually from **Settings -> Sync Now**. Manual sync
may request admin KB upload when the signed-in local user is an admin, but the
central server accepts Main/Sector KB only if the sync token also has those
server-side `sync_clients` permissions. Automatic startup and interval sync has
no signed-in admin context, so it uploads only normal client scopes. Configure
automatic sync through `SYNC_AUTO_ON_STARTUP` and `SYNC_INTERVAL_SECONDS`.
Client user-data sync is enabled by default. Users can disable it from the
client sync settings; disabling writes a local `sync_state` opt-out.

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

Seed or refresh the database-backed prompt library after changing packaged
prompt files under `app/prompts` or `app/templates/chat`:

```bash
uv run python -c "from app.db.migrations_runtime import run_runtime_migrations; from app.services.prompt_store import seed_prompts_from_files; run_runtime_migrations(seed_reference_data=False); print(f'Seeded/updated {seed_prompts_from_files(overwrite=True)} prompt rows')"
```

Use `overwrite=False` to add only missing prompts and keep prompt content that
admins edited in the database.

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
- Keep `SYNC_SERVER_EXPOSE_APP_APIS=false` for sync-only central servers so chat, LLM, auth, and normal app APIs are not exposed.
- Do not run Ollama, reranker, or NLI on sync-only central servers.
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
  "knowledge_index_dirty_scopes": ["main", "validated_evidence"]
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
5. Seed the prompt library when packaged prompts changed.
6. Start the FastAPI service behind HTTPS.
7. Verify `/health/ready`.
8. Verify `/api/sync/status` with the sync token.
9. Configure clients with `SYNC_SERVER_URL`, `SYNC_API_TOKEN`, and stable `SYNC_DEVICE_ID`.
10. Run an initial sync from one client.
11. Check dirty KB scopes and rebuild affected indexes.
12. Schedule backups and retention cleanup.

## Limitations In Current First Pass

- Sync currently exchanges full bundles rather than cursor-based deltas.
- Conflict handling stores revision metadata but does not yet present a manual conflict UI.
- Delete/tombstone fields are present for future delete sync, but current behavior focuses on insert/update.
- KB dirty scopes are recorded, but automatic FAISS rebuild scheduling is not yet implemented.
