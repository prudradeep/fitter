# Ubuntu Central Sync Server And Client Configuration Guide

This guide explains how to host the Dr Transition central sync service and
database on an Ubuntu server, then configure local client installations to sync
with it.

The central service is the same FastAPI app running in server mode. Local client
installations keep their local database IDs, and sync with the server through
global `sync_id` values.

## Architecture

```text
Local Dr Transition client
  -> HTTPS
  -> Nginx reverse proxy on Ubuntu
  -> FastAPI app on 127.0.0.1:8000
  -> MySQL database on Ubuntu
```

Server-to-client knowledge-base scopes:

- `main`
- `validated_evidence`
- `sector_prompt`

Client-to-server knowledge-base scopes:

- `validated_evidence`

Admin client-to-server knowledge-base scopes:

- `main`
- `validated_evidence`
- `sector_prompt`

Not synced:

- `temporary`

Main knowledge is centrally managed but can be pushed to the server by a client
that has a server-issued sync credential with Main KB permission. The central
server never trusts `app_users.role` from a client database for admin sync
authorization. Sector-prompt knowledge is server-managed and flows from the
server to clients; only credentials with sector-prompt permission may push
sector-prompt rows to the server. Temporary KB data is session-local evidence
and remains on the client.

`app_users` rows are encrypted inside sync bundles with AES-GCM using a key
derived from the configured sync client token. The stored `password_hash` is
synced as an encrypted value; plaintext passwords are never included in sync
payloads.

## Assumptions

Replace these placeholders throughout:

```text
your-domain.example
strong-db-password
long-random-secret-key
client-raw-sync-token
deploy-user
```

Recommended server:

- Ubuntu 22.04 LTS or 24.04 LTS
- 2+ CPU cores
- 4+ GB RAM for sync-only use
- More RAM/storage if hosting full chat, KB search, Ollama, or grounding services

## 1. Prepare DNS

Create an `A` record pointing your domain to the Ubuntu server public IP:

```text
your-domain.example -> SERVER_PUBLIC_IP
```

Wait until DNS resolves:

```bash
dig your-domain.example
```

## 2. Create Deploy User

Log in as a sudo-capable user:

```bash
ssh ubuntu@SERVER_PUBLIC_IP
```

Create a dedicated app user:

```bash
sudo adduser deploy-user
sudo usermod -aG sudo deploy-user
```

Switch to that user:

```bash
sudo su - deploy-user
```

## 3. Install System Packages

```bash
sudo apt update
sudo apt install -y \
  git \
  curl \
  build-essential \
  python3 \
  python3-venv \
  mysql-server \
  nginx \
  certbot \
  python3-certbot-nginx
```

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

Confirm:

```bash
uv --version
python3 --version
```

## 4. Secure MySQL

Run:

```bash
sudo mysql_secure_installation
```

Use your organization’s password policy. Then create the app database:

```bash
sudo mysql
```

```sql
CREATE DATABASE drtransition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'drtransition'@'localhost' IDENTIFIED BY 'strong-db-password';
GRANT ALL PRIVILEGES ON drtransition.* TO 'drtransition'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Verify login:

```bash
mysql -u drtransition -p drtransition -e "SELECT 1;"
```

## 5. Deploy Application Code

Create the app directory:

```bash
sudo mkdir -p /opt/dr-transition
sudo chown deploy-user:deploy-user /opt/dr-transition
cd /opt/dr-transition
```

Clone your repository:

```bash
git clone <your-repo-url> .
```

Install dependencies:

```bash
uv sync
```

If your deployment needs optional grounding/browser/test extras, install those
separately. Sync-only central service does not require Ollama, FAISS, reranker,
or NLI services unless users will also use chat/search directly on the server.

For a sync-only central server, do not install, start, or configure Ollama,
reranker, or NLI services. The server-mode guard blocks chat/LLM APIs and skips
startup work that would call embedding models. Bundled `kb/*.pdf` files are not
auto-ingested on startup in either server or client mode.

## 6. Configure Server Environment

Create a protected environment file:

```bash
sudo nano /etc/dr-transition.env
```

Example:

```env
APP_NAME="Dr Transition"
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="long-random-secret-key"

DATABASE_URL="mysql+pymysql://drtransition:strong-db-password@localhost:3306/drtransition"
DATABASE_AUTO_MIGRATE=false
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=3600

AUTH_COOKIE_SECURE=true
CSRF_PROTECTION_ENABLED=true
CORS_ORIGINS="https://your-domain.example"

STRUCTURED_LOGS=true
ACCESS_LOG_ENABLED=true
ACCESS_LOG_SUPPRESSED_PATHS="/health,/health/live,/health/ready"
ACCESS_LOG_SAMPLE_RATE=1.0

SYNC_ENABLED=true
SYNC_MODE=server
SYNC_INCLUDE_LOGS=false
SYNC_SERVER_EXPOSE_APP_APIS=false

FAISS_INDEX_PATH="/var/lib/dr-transition/knowledge.faiss"

LLM_LOG_ENABLED=false
LLM_LOG_TO_FILE=false
LLM_LOG_TO_DB=false
LLM_LOG_INCLUDE_PAYLOADS=false
```

Do not set `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL`,
`RERANKER_URL`, or `NLI_URL` for a sync-only central server unless you
deliberately enable the full app APIs with `SYNC_SERVER_EXPOSE_APP_APIS=true`.

Protect it:

```bash
sudo chown root:deploy-user /etc/dr-transition.env
sudo chmod 640 /etc/dr-transition.env
```

Create runtime data directories:

```bash
sudo mkdir -p /var/lib/dr-transition
sudo chown deploy-user:deploy-user /var/lib/dr-transition
```

Generate strong secrets if needed:

```bash
openssl rand -hex 32
```

Use separate random values for `SECRET_KEY` and any sync client tokens.

After migrations, create server-owned sync credentials:

```bash
uv run python scripts/create_sync_client.py \
  --name "Client 01"

uv run python scripts/create_sync_client.py \
  --name "Admin workstation" \
  --user-email "admin@example.com" \
  --main-kb \
  --sector-prompts \
  --reindex-sector-prompts \
  --manage-prompts
```

If `--token` is omitted, the script generates a strong token with Python
`secrets.token_urlsafe(32)`. The central server stores only the SHA-256 hash in
`sync_clients`; use the printed raw `Token:` value as `SYNC_API_TOKEN` on that
specific client. Do not use the printed `Token SHA256:` value on the client.

`sync_clients` is server-owned credential state and is not synced to clients or
back from clients. Create, revoke, or rotate sync-client credentials on the
central server only.

`--reindex-sector-prompts` controls whether the admin user named by
`--user-email` may run sector prompt reindex when sync is enabled. In client
mode, the local app checks the configured raw `SYNC_API_TOKEN` against the
central server's `/api/sync/status` response and uses that server-owned
permission. Without that flag, the Reindex button is hidden and the API returns
`403`.

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

## 7. Apply Database Schema And Seed Data

From the app directory:

```bash
cd /opt/dr-transition
set -a
source /etc/dr-transition.env
set +a
```

For a brand-new database:

```bash
uv run python scripts/apply_migrations.py --apply-base-schema
uv run python scripts/apply_migrations.py
```

Seed reference data:

```bash
uv run python -m app.seed_data
```

For future upgrades, take a database backup first, then run:

```bash
uv run python scripts/apply_migrations.py
```

## 8. Create systemd Service

Find the `uv` path:

```bash
which uv
```

Create service:

```bash
sudo nano /etc/systemd/system/dr-transition.service
```

Example:

```ini
[Unit]
Description=Dr Transition central sync backend
After=network.target mysql.service
Wants=mysql.service

[Service]
Type=simple
User=deploy-user
Group=deploy-user
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/etc/dr-transition.env
ExecStart=/home/deploy-user/.local/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

If `which uv` returned a different path, use that path in `ExecStart`.

Start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dr-transition
sudo systemctl status dr-transition
```

View logs:

```bash
journalctl -u dr-transition -f
```

Local health check on the server:

```bash
curl http://127.0.0.1:8000/health/ready
```

## 9. Configure Nginx Reverse Proxy

Create site:

```bash
sudo nano /etc/nginx/sites-available/dr-transition
```

Config:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/dr-transition /etc/nginx/sites-enabled/dr-transition
sudo nginx -t
sudo systemctl reload nginx
```

## 10. Enable HTTPS

Use Certbot:

```bash
sudo certbot --nginx -d your-domain.example
```

Confirm auto-renewal:

```bash
sudo certbot renew --dry-run
```

Check public health:

```bash
curl https://your-domain.example/health/ready
```

## 11. Verify Sync Server

Check sync status:

```bash
curl -H "Authorization: Bearer client-raw-sync-token" \
  https://your-domain.example/api/sync/status
```

Expected response includes:

```json
{
  "enabled": true,
  "mode": "server",
  "server_to_client_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
  "client_to_server_knowledge_scopes": ["validated_evidence"],
  "admin_client_to_server_knowledge_scopes": ["main", "validated_evidence", "sector_prompt"],
  "excluded_knowledge_scopes": ["temporary"]
}
```

If token is missing or wrong, the endpoint should return `401`.

Confirm normal app and chat endpoints are not exposed:

```bash
curl -i https://your-domain.example/login
curl -i -X POST https://your-domain.example/api/chat
```

With `SYNC_MODE=server` and `SYNC_SERVER_EXPOSE_APP_APIS=false`, these should
return `404`. A central sync-only server should expose health endpoints and
`/api/sync/*`, not the normal UI, auth, chat, LLM, or knowledge-management APIs.

Confirm no LLM-dependent startup work is required by watching logs after restart:

```bash
journalctl -u dr-transition -n 100 --no-pager
```

You should see sync-only mode skip LLM-dependent startup work. You should not
see Ollama, embedding, reranker, or NLI requests from the central sync server.

## 12. Configure Local Clients

On each local Dr Transition installation, add these values to the runtime `.env`.

Windows desktop installed app commonly reads from:

```text
%ProgramData%\DrTransition\.env
%LOCALAPPDATA%\DrTransition\.env
```

Client example:

```env
SYNC_ENABLED=true
SYNC_MODE=client
SYNC_SERVER_URL="https://your-domain.example"
SYNC_API_TOKEN="client-raw-sync-token"
SYNC_DEVICE_ID="client-specific-stable-uuid"
SYNC_INCLUDE_LOGS=false
SYNC_AUTO_ON_STARTUP=true
SYNC_INTERVAL_SECONDS=3600
```

Generate a client device UUID:

PowerShell:

```powershell
[guid]::NewGuid().ToString()
```

Linux/macOS:

```bash
uuidgen
```

Use a different `SYNC_DEVICE_ID` per installation. Keep it stable; do not change
it after first sync unless you intentionally want the server to see a new device.

## 13. Run Client Sync

From a local client with the backend running:

```bash
curl -X POST \
  -H "Authorization: Bearer client-raw-sync-token" \
  http://127.0.0.1:8000/api/sync/run
```

The local client pushes its bundle to:

```text
https://your-domain.example/api/sync/exchange
```

Then it imports the server bundle back into the local DB.

Users can also run sync from the app UI:

1. Open Dr Transition on the local client.
2. Open **Settings**.
3. Click **Sync Now**.

The browser does not receive `SYNC_API_TOKEN`. The local backend reads the token
from `.env`, performs the server exchange, and returns a summary to the UI.

Automatic client sync:

- `SYNC_AUTO_ON_STARTUP=true` runs one sync shortly after local app startup.
- `SYNC_INTERVAL_SECONDS=3600` repeats sync every hour.
- Set `SYNC_INTERVAL_SECONDS=0` to disable interval sync.
- Auto-sync only runs in `SYNC_MODE=client` when `SYNC_SERVER_URL` and
  `SYNC_API_TOKEN` are configured.
- Client user-data sync is enabled by default. Users can disable it from the
  client sync settings; disabling writes a local `sync_state` opt-out.

## 14. Knowledge Base After Sync

The sync service copies KB rows, not FAISS files.

Why:

- FAISS IDs are based on local `knowledge_chunks.id`.
- Local IDs can differ between server and clients.
- Copying FAISS files between machines would produce stale or incorrect vector IDs.

After sync, inspect dirty KB scopes:

```bash
curl -H "Authorization: Bearer client-raw-sync-token" \
  https://your-domain.example/api/sync/status
```

Look for:

```json
{
  "knowledge_index_dirty_scopes": ["main", "validated_evidence"]
}
```

Rebuild affected FAISS indexes before relying on vector search for those scopes.
The current service records dirty scopes; automatic reindex scheduling is a
future hardening step.

## 15. Backups

Create backup directory:

```bash
sudo mkdir -p /var/backups/dr-transition
sudo chown deploy-user:deploy-user /var/backups/dr-transition
```

Manual database backup:

```bash
mysqldump --single-transaction --routines --triggers \
  -u drtransition -p drtransition \
  > /var/backups/dr-transition/drtransition-$(date +%F-%H%M).sql
```

Restore example:

```bash
mysql -u drtransition -p drtransition < /var/backups/dr-transition/backup.sql
```

Schedule backups with cron:

```bash
crontab -e
```

Example daily backup at 02:00:

```cron
0 2 * * * mysqldump --single-transaction --routines --triggers -u drtransition -p'STRONG_DB_PASSWORD' drtransition > /var/backups/dr-transition/drtransition-$(date +\%F-\%H\%M).sql
```

Prefer a secrets-managed backup script over putting passwords directly in cron
where possible.

## 16. Retention Cleanup

Run manually:

```bash
cd /opt/dr-transition
set -a
source /etc/dr-transition.env
set +a
uv run python scripts/cleanup_retained_data.py
```

systemd service:

```bash
sudo nano /etc/systemd/system/dr-transition-cleanup.service
```

```ini
[Unit]
Description=Dr Transition retention cleanup

[Service]
Type=oneshot
User=deploy-user
Group=deploy-user
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/etc/dr-transition.env
ExecStart=/home/deploy-user/.local/bin/uv run python scripts/cleanup_retained_data.py
```

Timer:

```bash
sudo nano /etc/systemd/system/dr-transition-cleanup.timer
```

```ini
[Unit]
Description=Run Dr Transition retention cleanup daily

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dr-transition-cleanup.timer
```

## 17. Upgrade Procedure

On the server:

```bash
cd /opt/dr-transition
```

Back up database:

```bash
mysqldump --single-transaction --routines --triggers \
  -u drtransition -p drtransition \
  > /var/backups/dr-transition/pre-upgrade-$(date +%F-%H%M).sql
```

Pull new code:

```bash
git pull
uv sync
```

Apply migrations:

```bash
set -a
source /etc/dr-transition.env
set +a
uv run python scripts/apply_migrations.py
```

Restart:

```bash
sudo systemctl restart dr-transition
sudo systemctl status dr-transition
curl https://your-domain.example/health/ready
```

If reference CSV/XLSX files changed:

```bash
uv run python -m app.seed_data
```

## 18. Troubleshooting

Check service logs:

```bash
journalctl -u dr-transition -n 200 --no-pager
```

Check Nginx:

```bash
sudo nginx -t
sudo journalctl -u nginx -n 100 --no-pager
```

Check MySQL:

```bash
sudo systemctl status mysql
mysql -u drtransition -p drtransition -e "SHOW TABLES;"
```

Check app readiness:

```bash
curl http://127.0.0.1:8000/health/ready
curl https://your-domain.example/health/ready
```

Check sync token:

```bash
curl -i https://your-domain.example/api/sync/status
curl -i -H "Authorization: Bearer client-raw-sync-token" \
  https://your-domain.example/api/sync/status
```

Common issues:

- `404` from `/api/sync/status`: `SYNC_ENABLED` is false or the service was not restarted after config changes.
- `404` from `/login` or `/api/chat` on the central server: expected when `SYNC_MODE=server` and `SYNC_SERVER_EXPOSE_APP_APIS=false`.
- LLM/Ollama requests appearing in central server logs: check that `SYNC_MODE=server` and `SYNC_SERVER_EXPOSE_APP_APIS=false`, then restart `dr-transition`.
- `401`: wrong or missing sync token.
- `/health/ready` fails: database URL, credentials, MySQL service, or schema is wrong.
- Client sync fails with HTTPS error: DNS, certificate, proxy, or firewall issue.

## 19. Security Checklist

- Use HTTPS for all central sync traffic.
- Keep `/etc/dr-transition.env` readable only by root and the deploy user.
- Use different strong values for `SECRET_KEY`, DB password, and every client `SYNC_API_TOKEN`.
- Keep `DATABASE_AUTO_MIGRATE=false` in production.
- Keep `SYNC_SERVER_EXPOSE_APP_APIS=false` on central sync-only servers.
- Do not install or start Ollama, reranker, or NLI on sync-only central servers.
- Keep `SYNC_INCLUDE_LOGS=false` unless central log sync is explicitly required.
- Restrict inbound firewall access to SSH, HTTP, and HTTPS.
- Use SSH keys instead of password login where possible.
- Back up MySQL before upgrades and before first large syncs.
- Treat backups as sensitive user data.

Basic firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow "Nginx Full"
sudo ufw enable
sudo ufw status
```
