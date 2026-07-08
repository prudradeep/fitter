# Central Server Deployment

The central server is the existing FastAPI backend running with `APP_MODE=cloud_server`.
It is the only component that connects to cloud MySQL.

Desktop clients should use `APP_MODE=cloud_client` and talk to the central server API.
They should never receive `DATABASE_URL` or cloud MySQL credentials.

## Server Environment

Start from:

```text
.env.server.example
```

Required production values:

```env
APP_MODE=cloud_server
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="long-random-secret"
DATABASE_URL="mysql+pymysql://user:password@cloud-host:3306/dr_transition"
CENTRAL_SYNC_TOKEN="long-random-sync-token"
CENTRAL_EVIDENCE_TOKEN="long-random-evidence-token"
```

Use different values for `CENTRAL_SYNC_TOKEN` and `CENTRAL_EVIDENCE_TOKEN`.
Rotate them independently if a client token is exposed.

## Client Environment

Cloud clients keep their local DB and local FAISS files, but sync central content:

```env
APP_MODE=cloud_client
CENTRAL_API_BASE_URL="https://your-central-domain.example"
CENTRAL_SYNC_TOKEN="server-sync-token"
CENTRAL_EVIDENCE_TOKEN="server-evidence-token"
CENTRAL_CLIENT_ID="site-or-device-id"
```

The client still uses a local `DATABASE_URL` because synced chunks and FAISS indexes
are stored locally for generation-time retrieval.

## Sync Endpoints

- `GET /api/sync/manifest`
- `GET /api/sync/knowledge/changes`
- `GET /api/sync/status`
- `POST /api/sync/pull`
- `POST /api/sync/evidence/submit`

Sync endpoints use bearer tokens. Main KB, Validated Evidences KB, and Sector Prompt
RAG sync as chunks; clients rebuild FAISS locally. Large sync responses are paged
with `limit`, `cursor_version`, and `cursor_id`; cloud clients follow `next_cursor`
until `has_more` is false, then rebuild the local FAISS index once.

Recommended production limits:

```env
SYNC_BATCH_SIZE=100
SYNC_RATE_LIMIT_PER_MINUTE=120
EVIDENCE_RATE_LIMIT_PER_MINUTE=30
```

Set a lower rate limit for small servers. Set a higher limit only when the reverse
proxy and database capacity are sized for concurrent desktop clients.

## Admin Review

Admin users review submitted evidence inside the Knowledge Base dialog.

Admin-only API endpoints:

- `GET /api/sync/evidence/submissions`
- `POST /api/sync/evidence/submissions/{id}/approve`
- `POST /api/sync/evidence/submissions/{id}/reject`

Approving a submission ingests it into `validated_evidence` with its country,
region, and sector scope. Other clients receive it on their next scoped sync.

## Operational Notes

- Serve the central API only over HTTPS.
- Keep `.env.server.example` as a template only; do not commit real `.env` files.
- Use `CENTRAL_SYNC_TOKEN` only for KB sync.
- Use `CENTRAL_EVIDENCE_TOKEN` only for evidence submission.
- Cloud clients do not query cloud MySQL directly.

## Token Rotation

1. Generate new strong random values for `CENTRAL_SYNC_TOKEN` and/or `CENTRAL_EVIDENCE_TOKEN`.
2. Schedule a maintenance window if many clients are deployed, because each token is single-value.
3. Update the central server `.env` and restart the server process.
4. Update the desktop/client installer or client `.env` with the matching new token.
5. Confirm clients can call `GET /api/sync/status` and then run `POST /api/sync/pull`.
6. Remove any old token from password managers, scripts, installer presets, and deployment notes.

Rotate `CENTRAL_EVIDENCE_TOKEN` immediately if a client machine is compromised.
Rotate `CENTRAL_SYNC_TOKEN` if a client can no longer be trusted to read central KB data.

## HTTPS And Proxy Checklist

- Terminate public traffic with HTTPS using a valid certificate.
- Forward requests to the FastAPI server over a private port, for example `127.0.0.1:8000`.
- Set `CORS_ORIGINS` to the exact public origin, not `*`.
- Preserve `Authorization` headers through the proxy.
- Set proxy body-size limits high enough for evidence submissions but not unlimited.
- Enable proxy access logs and keep application logs outside the web root.
- Restrict direct access to cloud MySQL by IP/security group so only the central server can connect.
- Keep `.env` readable only by the service account that runs the server.
- Add health monitoring for `/health` and alert on repeated `429`, `5xx`, or database errors.

## Server Runners

Use `docs/SERVER_RUNNERS.md` for deployment-specific run commands for development,
Linux `systemd`, Windows service runners, and reverse-proxy fronting.
