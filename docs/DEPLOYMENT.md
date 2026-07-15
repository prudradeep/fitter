# Deployment Guide

This guide follows the split architecture in
[`ARCHITECTURE_BOUNDARIES.md`](ARCHITECTURE_BOUNDARIES.md):

- Hosted backend owns auth, MySQL persistence, sessions, authoritative knowledge
  scopes, validated evidence, sync APIs, and audit logs.
- Windows desktop app owns the frontend shell, local Ollama chat/embeddings,
  local RAG, local indexes, and temporary evidence.
- The Windows installer bundles frontend/runtime assets only. It does not bundle
  backend APIs, MySQL, schema, migrations, seeds, or secrets.

## Hosted Backend Deployment

Deploy the backend on the server where MySQL is available.

For a concrete Ubuntu + MySQL + Nginx + systemd runbook, see
[`UBUNTU_BACKEND_DEPLOYMENT.md`](UBUNTU_BACKEND_DEPLOYMENT.md).

### 1. Server Prerequisites

- Python 3.12+
- `uv`
- MySQL 8+
- Reverse proxy or TLS terminator such as Nginx, Caddy, IIS, or a cloud load
  balancer
- A production domain, for example `https://api.example.org`

The hosted backend does not require Ollama for request paths after the client
LLM/RAG migration.

### 2. Environment

Create a production `.env` on the hosted server:

```env
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="<strong unique random secret>"
DATABASE_URL="mysql+pymysql://drtransition:<password>@127.0.0.1:3306/drtransition"
DATABASE_AUTO_MIGRATE=false

AUTH_COOKIE_SECURE=true
CSRF_PROTECTION_ENABLED=true
CORS_ORIGINS="https://api.example.org,tauri://localhost,http://tauri.localhost"

STRUCTURED_LOGS=true
ACCESS_LOG_ENABLED=true
ACCESS_LOG_SUPPRESSED_PATHS="/health,/health/live,/health/ready"

LLM_LOG_ENABLED=false
LLM_LOG_TO_FILE=false
LLM_LOG_TO_DB=false
LLM_LOG_INCLUDE_PAYLOADS=false
```

Notes:

- Keep production secrets only on the hosted server.
- Set `AUTH_COOKIE_SECURE=true` behind HTTPS.
- Include the deployed web origin and desktop WebView origin in `CORS_ORIGINS`
  if the desktop client reaches the backend cross-origin.
- Keep `DATABASE_AUTO_MIGRATE=false`; run migrations explicitly.

### 3. Database

Create the database and user:

```sql
CREATE DATABASE drtransition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'drtransition'@'%' IDENTIFIED BY '<strong password>';
GRANT ALL PRIVILEGES ON drtransition.* TO 'drtransition'@'%';
FLUSH PRIVILEGES;
```

Apply schema and migrations from the backend release:

```bash
uv sync
uv run python scripts/apply_migrations.py --apply-base-schema
uv run python scripts/apply_migrations.py
```

Seed reference data only during initial setup or when reference source files
change:

```bash
uv run python -m app.seed_data
```

Do not run seed or migration commands from the Windows installer.

### 4. Run Backend

Example direct process:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Example systemd service:

```ini
[Unit]
Description=Dr Transition hosted backend
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/opt/dr-transition/.env
ExecStart=/usr/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Expose the backend through HTTPS. Configure the proxy to forward:

- `X-Forwarded-Proto`
- `X-Forwarded-For`
- `Host`

### 5. Health and Smoke Checks

```bash
curl -fsS https://api.example.org/health/live
curl -fsS https://api.example.org/health/ready
```

Authenticated checks from a browser or API client:

- Login succeeds and sets `dr_transition_auth` and `dr_transition_csrf`.
- `GET /api/sessions` returns sessions for the signed-in user.
- `GET /api/knowledge/sync/manifest?scope=main` returns an authorized manifest.
- `GET /api/knowledge/sync/manifest?scope=temporary` is rejected.
- `POST /api/validated-evidence/promote` stores `validated_evidence` and writes
  a `validated_evidence.promote` audit event.

### 6. Operations

Run retention cleanup on a schedule:

```bash
uv run python scripts/cleanup_retained_data.py
```

Back up before every release:

- MySQL database
- production `.env`
- uploaded/imported authoritative knowledge files, if stored outside MySQL

After deployment, run:

```bash
uv run python -m unittest tests.test_api_routes
```

## Frontend/Desktop Bundle Release

The Windows bundle is built from the desktop/Tauri app and frontend/runtime
assets. It connects to the hosted backend configured at install time.

### 1. Build Prerequisites

On the Windows build machine:

- Rust and Cargo
- Node.js and npm
- Tauri Windows prerequisites and WebView2
- Inno Setup 6 with `ISCC.exe` on `PATH`
- Built or available local Ollama on target machines, not bundled in the app

### 2. Build Tauri App

```powershell
.\packaging\windows\scripts\build-desktop-launcher.ps1
```

Expected output:

```text
desktop/tauri/src-tauri/target/release/drtransition.exe
```

### 3. Assemble Installer Payload

```powershell
.\packaging\windows\scripts\assemble-installer-payload.ps1
```

Expected payload:

```text
build/windows-installer/payload/
  DrTransition.exe
  config/default.config.json
  frontend/static/
  frontend/templates/
  reference/prompts/
  scripts/Get-ModelRecommendation.ps1
  scripts/Test-SystemCompatibility.ps1
```

The assembler starts from a clean payload directory and fails if it detects
backend/database artifacts such as:

- `drtransition-backend.exe`
- `schema.sql`
- migrations or seeds
- MySQL/database scripts
- production `.env`

### 4. Build Installer

```powershell
.\packaging\windows\scripts\build-installer.ps1
```

Expected output:

```text
build/windows-installer/DrTransitionSetup-<version>.exe
```

### 5. Installer Runtime Configuration

During setup, enter:

- Hosted backend URL, for example `https://api.example.org/`
- Chat model, or `auto`
- Embedding model, usually `nomic-embed-text`

The installer writes no-secret runtime config to:

```text
%ProgramData%\DrTransition\.env
```

Per-user overrides can be placed at:

```text
%LOCALAPPDATA%\DrTransition\.env
```

Supported keys:

```text
DR_TRANSITION_BACKEND_URL
DR_TRANSITION_BACKEND_HEALTH_URL
DR_TRANSITION_BACKEND_AUTH_CHECK_URL
OLLAMA_BASE_URL
OLLAMA_MODEL
OLLAMA_EMBEDDING_MODEL
```

### 6. Desktop Smoke Test

On a clean Windows machine:

1. Install the generated setup executable.
2. Launch Dr Transition.
3. Confirm diagnostics pass for hosted backend, auth/session endpoint, Ollama,
   chat model, and embedding model.
4. Login to the hosted backend.
5. Confirm `main`, `sector_prompt`, and `validated_evidence` sync starts.
6. Upload temporary evidence in a validation step.
7. Confirm temporary evidence stays local until validation is accepted.
8. Accept validation and confirm promoted evidence appears through
   `validated_evidence` sync.
9. Confirm the hosted server has no `temporary` knowledge documents.

## Automated Test Commands

Server:

```bash
uv run python -m unittest tests.test_api_routes
```

Client module tests:

```bash
node tests/client_phase12.test.js
```

Packaging guard:

```powershell
.\packaging\windows\scripts\assemble-installer-payload.ps1
rg -n "drtransition-backend|schema\.sql|mysql|migrations?|seeds?|DATABASE_URL" build/windows-installer/payload
```

The `rg` command should return no matches.

## Gaps and Improvements

Recommended follow-ups before production rollout:

- Add a CI job that runs `node tests/client_phase12.test.js` and
  `uv run python -m unittest tests.test_api_routes`.
- Add a CI packaging job that runs `assemble-installer-payload.ps1` and the
  forbidden-artifact scan.
- Remove or archive old PyInstaller specs once nobody needs the historical local
  service bundle.
- Add a true Playwright desktop smoke test when a hosted test backend is
  available.
- Decide the final desktop origin/CORS policy for Tauri WebView and document it
  beside production proxy config.
- Add release signing for `DrTransitionSetup-<version>.exe`.
