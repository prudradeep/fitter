# Dr Transition

Dr Transition is a local browser-based FastAPI application for guided Twin-Transition policy analysis. It uses a single chat endpoint to walk users through country, region, and sector selection, with MySQL-backed reference data and optional local Ollama support.

## Current Conversation Flow

The main chat flow is:

```text
Country -> Region -> Sector -> Hazard listing
```

From the hazard listing step, users can:

```text
Start Mitigation Planning -> select a hazard -> review affected profiles -> create or adopt a mitigation measure
Add a new Hazard -> describe a custom hazard -> add reason/evidence -> validate -> review affected groups -> save
Refresh hazards and DGs -> regenerate the sector hazard/profile suggestions
Dive deeper into statistical findings -> ask follow-up questions about the sector statistics
```

During any in-scope workflow step, users may also ask project questions such as
what an action means, whether they can add their own hazard, or what happens
next. The app answers the question using workflow context, keeps the active
step, and then returns the user to the same flow position.

At the hazard-selection step, **Other Options** includes **Go back to list of
hazards** so users can return from mitigation planning hazard selection to the
hazard overview without starting hazard creation.

## Prerequisites

- Python 3.12+
- UV
- MySQL 8+
- FAISS for local knowledge-base vector search
- Ollama, with the chat and embedding models available locally

## Install UV

On macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Configure Environment

Copy the example file and adjust credentials for your local MySQL instance:

```bash
cp .env.example .env
```

Example database URL:

```env
DATABASE_URL="mysql+pymysql://drtransition:<choose-a-strong-local-password>@localhost:3306/drtransition"
```

The password placeholder is local setup guidance only. Choose a unique password
for each installation; do not reuse old sample passwords from earlier docs.

For production, set at least:

```env
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="<strong unique random secret>"
AUTH_COOKIE_SECURE=true
CSRF_PROTECTION_ENABLED=true
DATABASE_AUTO_MIGRATE=false
STRUCTURED_LOGS=true
LLM_LOG_ENABLED=false
LLM_LOG_TO_FILE=false
LLM_LOG_TO_DB=false
LLM_LOG_INCLUDE_PAYLOADS=false
CORS_ORIGINS="https://your-production-host.example"
```

`DATABASE_AUTO_MIGRATE` must stay false outside development. Apply schema
changes deliberately through the migration/seed commands during deployment,
after taking a database backup.

Apply production migrations explicitly:

```bash
uv run python scripts/apply_migrations.py
```

For a brand-new database, apply the base schema first:

```bash
uv run python scripts/apply_migrations.py --apply-base-schema
```

The legacy schema repair path is intentionally separate from production
migrations. Use it only for controlled local or installer recovery after a
database backup:

```bash
uv run python scripts/repair_legacy_schema.py --seed-reference-data
```

## MySQL Setup

Create the database using the SQL file:

```bash
mysql -u root -p < schema.sql
```

Alternatively, create a database and user manually, then apply the SQL file:

```sql
CREATE DATABASE drtransition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'drtransition'@'localhost' IDENTIFIED BY '<choose-a-strong-local-password>';
GRANT ALL PRIVILEGES ON drtransition.* TO 'drtransition'@'localhost';
FLUSH PRIVILEGES;
```

```bash
mysql -u drtransition -p drtransition < schema.sql
```

## Seed Reference Data

The application no longer applies `schema.sql` or reloads CSV/XLSX reference data
on every startup. Run migrations first, then run the seed command only when
setting up the database or after changing the source files such as `mm.csv`,
`additionalHazards.csv`, `additionalHazardProfiles.csv`,
`MM Target group.xlsx`, `sectoral_challenges.xlsx`, `hazards.xlsx`, or the
sector prompt files under `app/prompts/*_truth.txt`.

Reference seeding also extracts the authoritative hazard names from each sector
prompt into `system_hazards`. This runs before `hazards.xlsx` is mapped into
`mitigation_measure_policy_system_hazards`, so policy-hazard mappings have the
sector hazards they need.

PowerShell:

```powershell
.\scripts\seed_database.ps1
```

Cross-platform:

```bash
uv run python -m app.seed_data
```

If you are targeting a non-default env file such as `.env.server.dev`, set
`ENV_FILE` for that command:

```powershell
$env:ENV_FILE=".env.server.dev"
uv run python -m app.seed_data --skip-schema
```

If the schema is already up to date and you only want to reload CSV/XLSX data:

```powershell
.\scripts\seed_database.ps1 -SkipSchema
```

The old runtime schema-repair path is intentionally not part of normal
startup/deployment anymore. If you are recovering a local installer database
that predates the current migration files, use the explicit recovery flag only
after backing up the database:

```powershell
.\scripts\seed_database.ps1 -LegacySchemaRepair
```

Production deployments should keep this flag off and rely on `schema.sql` for
fresh installs plus versioned files under `app/db/migrations/` for changes.

## Seed Prompt Library

Database-backed prompts are loaded from packaged prompt files under
`app/prompts/**/*.txt` and chat response templates under
`app/templates/chat/**/*.md`. On a sync server, startup seeds these rows
automatically. To refresh all prompt rows manually after changing packaged LLM
prompts, run:

```bash
uv run python -c "from app.db.migrations_runtime import run_runtime_migrations; from app.services.prompt_store import seed_prompts_from_files; run_runtime_migrations(seed_reference_data=False); print(f'Seeded/updated {seed_prompts_from_files(overwrite=True)} prompt rows')"
```

Use `overwrite=False` instead when you only want to add missing prompt rows and
preserve prompt edits already stored in the database.

## Production Operations

Health endpoints:

```http
GET /health/live
GET /health/ready
```

`/health/ready` validates the database connection and should be used by process
managers or load balancers before routing traffic to an instance.

Admin-only metrics are available at:

```http
GET /metrics
```

Security headers are applied by middleware on every response. Override
`CONTENT_SECURITY_POLICY`, `REFERRER_POLICY`, `PERMISSIONS_POLICY`, or
`STRICT_TRANSPORT_SECURITY` only when a deployment has a known proxy/CDN policy
that should take precedence. HSTS is only sent for HTTPS requests or when
`X-Forwarded-Proto: https` is present.

Request access logs can be reduced for noisy deployments:

```env
ACCESS_LOG_ENABLED=true
ACCESS_LOG_SUPPRESSED_PATHS="/health,/health/live,/health/ready"
ACCESS_LOG_SAMPLE_RATE=1.0
```

Run retention cleanup from a scheduled task or cron:

```bash
uv run python scripts/cleanup_retained_data.py
```

This removes expired rate-limit rows, old temporary knowledge documents, and old
LLM exchange logs, plus anonymised System Inquiry telemetry events, according to
`RATE_LIMIT_RETENTION_DAYS`, `TEMPORARY_KNOWLEDGE_RETENTION_HOURS`,
`LLM_LOG_RETENTION_DAYS`, and `SYSTEM_INQUIRY_PROFILE_RETENTION_DAYS`.

Windows Task Scheduler example:

```powershell
schtasks /Create /TN "Dr Transition Retention Cleanup" /SC DAILY /ST 02:30 /TR "cmd /c cd /d F:\Dr Transition && uv run python scripts\cleanup_retained_data.py"
```

systemd timer example:

```ini
# /etc/systemd/system/dr-transition-cleanup.service
[Unit]
Description=Dr Transition retention cleanup

[Service]
Type=oneshot
WorkingDirectory=/opt/dr-transition
ExecStart=/usr/bin/uv run python scripts/cleanup_retained_data.py
```

```ini
# /etc/systemd/system/dr-transition-cleanup.timer
[Unit]
Description=Run Dr Transition retention cleanup daily

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dr-transition-cleanup.timer
```

cron example:

```cron
30 2 * * * cd /opt/dr-transition && uv run python scripts/cleanup_retained_data.py >> /var/log/dr-transition-cleanup.log 2>&1
```

Tune database connections for the deployment size:

```env
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=3600
DATABASE_CONNECT_TIMEOUT_SECONDS=10
```

Recommended production flow:

1. Back up the database.
2. Deploy the new application files.
3. Run `uv run python scripts/apply_migrations.py`.
4. Restart the app processes.
5. Check `/health/ready`.
6. Run `.\scripts\seed_database.ps1 -SkipSchema` only when reference CSV/XLSX
   source files changed.
7. Refresh database-backed prompts only when packaged prompt files changed.
8. Confirm the retention cleanup schedule is enabled.

Central sync backend deployment is documented in:

```text
docs/BACKEND_SYNC_DEPLOYMENT.md
docs/UBUNTU_SYNC_SERVER_AND_CLIENT_GUIDE.md
```

## Ollama Setup

Install Ollama from https://ollama.com, start the Ollama service, then pull the chat and embedding models:

```bash
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
```

Supported local chat models:

```text
qwen3.5:2b
qwen3.5:4b
ministral-3:8b
qwen3.5:9b
ministral-3:14b
mistral-small3.2:24b
qwen3.5:27b
```

Each supported chat model has its own prompt directory under
`app/prompts/llm/<model-directory>/`. The configured `OLLAMA_MODEL` selects the
matching prompt files automatically, with fallback to the shared
`app/prompts/llm/*.txt` prompts if a model-specific file is missing.

Example:

```env
OLLAMA_MODEL="qwen3.5:4b"
```

The app calls Ollama at:

```text
http://localhost:11434
```

The guided wizard works even if Ollama is not running; `app/llm.py` returns a graceful fallback message when the local model is unavailable.

LLM request/response logging is enabled by default in development and disabled
by default outside development. When enabled, chat and embedding calls are
appended as JSON Lines to:

```env
LLM_LOG_ENABLED=true
LLM_LOG_TO_FILE=true
LLM_LOG_TO_DB=true
LLM_LOG_PATH="data/service-runtime/logs/llm_requests.jsonl"
```

The same audit payload is also stored in the `llm_exchange_logs` database table
when `LLM_LOG_TO_DB` is enabled. Production logs redact prompt/response payloads
unless `LLM_LOG_INCLUDE_PAYLOADS=true` and
`LLM_LOG_ALLOW_PRODUCTION_PAYLOADS=true` are both set deliberately. Protect the
log file path and database table with the same access controls as user data, and
run retention cleanup with `LLM_LOG_RETENTION_DAYS`.

## FAISS Knowledge Base

The app stores knowledge-base document metadata and chunk text/source/page records in MySQL. It stores vector embeddings in a local FAISS index file. Configure the index path and embedding model in `.env` when needed:

```env
FAISS_INDEX_PATH="data/knowledge.faiss"
OLLAMA_EMBEDDING_MODEL="nomic-embed-text"
```

## Grounding Model Services

Mitigation validation includes two dedicated local model services:

```env
RERANKER_URL="http://localhost:8081/rerank"
NLI_URL="http://localhost:8082/entail"
MITIGATION_VERDICT_SAMPLES=3
MITIGATION_VERDICT_TEMPERATURE=0.25
MITIGATION_SUPPORT_SCORE_FLOOR=0.15
```

Install the model dependencies and start the main app, reranker, and NLI services:

```powershell
uv sync --extra grounding
.\scripts\start_grounding_services.ps1
```

The first inference downloads the configured Hugging Face models. Defaults:

```env
RERANKER_MODEL="cross-encoder/ms-marco-MiniLM-L-6-v2"
NLI_MODEL="cross-encoder/nli-deberta-v3-small"
```

Verify the services:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8081/health
Invoke-RestMethod http://localhost:8082/health
```

Stop all processes started by the launcher:

```powershell
.\scripts\stop_all_services.ps1
```

Logs and PID files are stored under `data/service-runtime/`.

The reranker receives `{"query": "...", "documents": ["...", "..."]}` and returns
`{"scores": [0.91, 0.42]}`. The NLI service receives
`{"pairs": [{"premise": "...", "hypothesis": "..."}]}` and returns
`{"results": [{"label": "entailment", "score": 0.94}]}`.

If a service is unavailable, mitigation validation falls back to the existing combined retrieval
score or strict LLM entailment verification.

## Install Dependencies

```bash
uv sync
```

## Run Locally

To start only the main application:

```bash
uv run uvicorn app.main:app --reload --reload-dir app
```

Open:

```text
http://localhost:8000
```

Health check:

```text
http://localhost:8000/health
```

## Test Commands

Install the test tools:

```bash
uv sync --extra test
```

Run the full Python test suite:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check .
```

Run the local CI script on Windows:

```powershell
.\scripts\ci_check.ps1
```

Run focused conversation-flow checks:

```bash
uv run pytest tests/test_open_conversation_flow_actions.py tests/test_chat_selection_engine.py
```

Run the current `new_test_cases.xlsx` workbook against one Qwen model while
debugging:

```powershell
uv run python tests/run_open_conversation_selection_cases.py --input .\new_test_cases.xlsx --models qwen3.5:2b
uv run python tests/run_open_conversation_selection_cases.py --input .\new_test_cases.xlsx --models qwen3.5:4b
```

Run the same workbook against Qwen 3.5 2B and 4B together:

```powershell
uv run python tests/run_open_conversation_selection_cases.py `
  --input .\new_test_cases.xlsx `
  --models qwen3.5:2b qwen3.5:4b
```

Generate and run the standard open-conversation regression workbook:

```bash
uv run python tests/run_open_conversation_selection_regression.py
```

Run hazard-creation regression cases:

```bash
uv run python tests/run_hazard_creation_regression.py
```

Optional browser smoke tests require a running app:

```powershell
uv sync --extra browser
uv run playwright install chromium
$env:DR_TRANSITION_BROWSER_TESTS = "1"
$env:DR_TRANSITION_BASE_URL = "http://127.0.0.1:8000"
$env:DR_TRANSITION_TEST_EMAIL = "admin@example.com"
$env:DR_TRANSITION_TEST_PASSWORD = "local-admin-password"
.\scripts\ci_check.ps1
```

## Build Commands

The browser app has no separate frontend build step; static assets are served by
FastAPI from `app/static` and templates from `app/templates`.

Build the Windows desktop services:

```powershell
.\packaging\windows\scripts\build-python-services.ps1
```

Build the Tauri desktop launcher:

```powershell
.\packaging\windows\scripts\build-desktop-launcher.ps1
```

Build the installer from an existing payload:

```powershell
.\packaging\windows\scripts\build-installer.ps1
```

Build a full Windows release:

```powershell
.\packaging\windows\scripts\build-release.ps1
```

Build only the local database/model preparation installer:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1
```

This produces `build\windows-dependencies-installer\DrTransitionDatabaseModelSetup-<version>.exe`.
It installs/checks MySQL and Ollama, creates the app database/user, applies and
seeds the database schema/reference/prompt data, and pulls the selected Ollama
chat and embedding models without installing the desktop app.

Build an offline/admin installer when a fully local deployment is required:

```powershell
.\packaging\windows\scripts\build-release.ps1 -OfflineAdmin
```

To prepackage offline MySQL and Ollama installers, place the official vendor
installers under `packaging/windows/offline/mysql/` and
`packaging/windows/offline/ollama/`, then add `-PrepackageDependencies`:

```powershell
.\packaging\windows\scripts\build-release.ps1 -OfflineAdmin -PrepackageDependencies
```

For the standalone database/model preparation installer with bundled MySQL and
Ollama installers:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1 -PrepackageDependencies
```

## Deployment Commands

For a fresh server database:

```bash
uv sync
uv run python scripts/apply_migrations.py --apply-base-schema
uv run python scripts/apply_migrations.py
uv run python -m app.seed_data
```

For an existing deployment after code/schema changes:

```bash
uv sync
uv run python scripts/apply_migrations.py
uv run python -m app.seed_data --skip-schema
```

Refresh database-backed prompts after changing packaged prompts or chat
templates:

```bash
uv run python -c "from app.db.migrations_runtime import run_runtime_migrations; from app.services.prompt_store import seed_prompts_from_files; run_runtime_migrations(seed_reference_data=False); print(f'Seeded/updated {seed_prompts_from_files(overwrite=True)} prompt rows')"
```

Run the app behind a production process manager:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run retention cleanup:

```bash
uv run python scripts/cleanup_retained_data.py
```

## Windows Desktop Installer

The repository includes an initial Windows desktop packaging layer under
`desktop/tauri/` and `packaging/windows/`.

The packaged desktop target is:

- `DrTransition.exe`, a native Tauri/WebView2 launcher
- `drtransition-backend.exe`, the main FastAPI backend
- `drtransition-reranker.exe`, the grounding reranker service
- `drtransition-nli.exe`, the grounding NLI service

On launch, `DrTransition.exe` starts the backend, reranker, and NLI services as
hidden local processes, waits for their health checks, then opens the app in its
own desktop window instead of the user's browser.

Build documentation is in:

```text
docs/WINDOWS_DESKTOP_INSTALLER.md
```

## Open Conversation Selection Regression

Generate the Country -> Region -> Sector test-case workbook and the Pass/Fail
results workbook with one command:

```bash
uv run python tests/run_open_conversation_selection_regression.py
```

This creates these files in the current working directory:

```text
open_conversation_selection_test_cases.xlsx
open_conversation_selection_test_results.xlsx
```

To run only the workbook/unit tests:

```bash
uv run python -m unittest tests.test_open_conversation_selection_test_cases tests.test_chat_selection_engine tests.test_conversational_selection tests.test_question_intent
```

The lower-level scripts are also available when needed:

```bash
uv run python tests/generate_open_conversation_selection_test_cases.py
uv run python tests/run_open_conversation_selection_cases.py
```

To run the open conversation selection cases across all supported Ollama chat
models, use:

```powershell
uv run python tests/run_open_conversation_selection_cases.py `
  --models `
  qwen3.5:2b `
  qwen3.5:4b `
  ministral-3:8b `
  qwen3.5:9b `
  ministral-3:14b `
  mistral-small3.2:24b `
  qwen3.5:27b
```

Add `--limit <count>` to run only a fixed number of generated cases per model:

```powershell
uv run python tests/run_open_conversation_selection_cases.py `
  --models `
  qwen3.5:2b `
  qwen3.5:4b `
  ministral-3:8b `
  qwen3.5:9b `
  ministral-3:14b `
  mistral-small3.2:24b `
  qwen3.5:27b `
  --limit 20
```

## Hazard Creation Regression

Generate the hazard-creation test-case workbook and run the validation flow:

```bash
uv run python tests/run_hazard_creation_regression.py
```

This creates:

```text
hazard_creation_test_cases.xlsx
hazard_creation_test_results_<model>.xlsx
```

The generated cases cover valid hazards for Energy, Housing, and Transport;
invalid or non-hazard inputs; blank/back actions; and every wrong-sector
combination between the three sectors. The runner calls the real app hazard
creation path, including the local LLM hazard classifier.

Run the same cases across all supported chat model settings:

```powershell
uv run python tests/run_hazard_creation_cases.py `
  --models `
  qwen3.5:2b `
  qwen3.5:4b `
  ministral-3:8b `
  qwen3.5:9b `
  ministral-3:14b `
  mistral-nemo
```

Add `--limit <count>` to run only the first generated cases per model.

## CI Checks

Install test/dev tools when needed:

```bash
uv sync --extra test
```

Run the local CI check script:

```powershell
.\scripts\ci_check.ps1
```

The script runs Python compilation, `unittest`, `ruff`, and JavaScript syntax
checks for the frontend entry files. Use `-SkipRuff` only when the lint extra
has not been installed yet.

Optional browser smoke tests cover login, chat, option selection, knowledge
dialog/search, and session restore/export/import. Start the app first, install
the browser extra, then opt in:

```powershell
uv sync --extra browser
uv run playwright install chromium
$env:DR_TRANSITION_BROWSER_TESTS = "1"
$env:DR_TRANSITION_BASE_URL = "http://127.0.0.1:8000"
$env:DR_TRANSITION_TEST_EMAIL = "admin@example.com"
$env:DR_TRANSITION_TEST_PASSWORD = "local-admin-password"
.\scripts\ci_check.ps1
```

## API

The full chat flow uses one endpoint:

```http
POST /api/chat
Content-Type: application/json
```

Request:

```json
{
  "message": "Spain",
  "session_id": "optional-session-id"
}
```

Response:

```json
{
  "session_id": "uuid",
  "step": "region",
  "bot_message": "Great. Select your region in Spain.",
  "options": [{ "id": 1, "label": "Andalusia" }],
  "session": {
    "country": "Spain",
    "region": null,
    "sector": null
  },
  "error": false
}
```

Reset a session:

```json
{
  "message": "/reset",
  "session_id": "existing-session-id"
}
```

## Project Structure

```text
app/
  main.py                 FastAPI app, middleware, health, metrics
  config.py               Pydantic settings and production safety checks
  auth.py                 Login/session cookie helpers
  security.py             CSRF and response security headers
  observability.py        Request IDs, structured logging, in-process metrics
  models.py               SQLAlchemy ORM models
  llm.py                  Ollama chat/embedding client helpers
  schemas.py              API request/response models
  db/
    session.py            SQLAlchemy engine/session setup
    migrations_runtime.py Legacy/local schema repair helpers
    versioned_migrations.py
    migrations/           Versioned SQL migrations
    reference_schema.py   Reference-data schema checks
  seed/
    reference_data.py     CSV/XLSX reference-data loading
    xlsx_readers.py       Spreadsheet readers
  grounding_servers/
    reranker.py           Local reranker service
    nli.py                Local NLI service
    model_runtime.py      Shared model runtime helpers
  routes/
    api.py                Chat, session, knowledge, admin APIs
    auth.py               Login/signup/logout routes
    request_limits.py     Upload/body size guardrails
  services/
    chat_service.py       Conversation orchestration
    chat_*                Flow-specific chat modules
    custom_hazard_*       Custom hazard validation/matching/rules
    mitigation_*          Mitigation validation and formatting helpers
    knowledge_base.py     Knowledge document ingestion/search/delete
    sector_prompt_rag.py  Sector prompt retrieval index
    maintenance.py        Retention cleanup logic
    audit_log.py          Admin/sensitive action audit records
    rate_limit.py         Login/signup/password rate limits
    llm_logging.py        LLM exchange logging
  prompts/
    llm/                  LLM task prompts
    workflow/             Workflow help context for in-flow user questions
    *_truth.txt           Sector statistical context prompts
  templates/
    index.html            Main chat shell
    login.html
    signup.html
    chat/                 Markdown response templates
  static/
    css/
      base.css
      shell-layout.css
      chat.css
      dialogs.css
      knowledge.css
      stage-visual.css
      auth.css
      responsive.css
      style.css           Aggregator/legacy shared styles
    js/
      app.js              Main UI controller
      app-utils.js        Shared fetch/CSRF/util helpers
      app-settings.js     Frontend settings helpers
      app-session-export.js
      app-dom-icons.js
      auth.js
      auth-map.js
    img/
scripts/
  apply_migrations.py     Explicit production migration runner
  seed_database.ps1       Reference-data seeding entry point
  cleanup_retained_data.py
  ci_check.ps1
  start_grounding_services.ps1
  stop_all_services.ps1
packaging/
  python/                 PyInstaller service entry points
  windows/                Inno Setup, Tauri, and release scripts
tests/
  browser/                Optional Playwright smoke tests
  test_*.py               Unit and integration tests
schema.sql
.env.example
pyproject.toml
README.md
```

## Notes

- Persistent session records, chat messages, reference data, knowledge metadata,
  audit logs, rate limits, and LLM exchange logs are stored in MySQL.
- `app.services.chat_session.session_store` still keeps live in-memory flow state
  for active conversations; persisted sessions can be restored through the UI.
- Schema changes should go through `schema.sql` for fresh installs and
  `app/db/migrations/` plus `scripts/apply_migrations.py` for production updates.
- Reference-data loading is explicit. Use `scripts/seed_database.ps1` during setup
  or after changing source CSV/XLSX files, not as a normal app startup step.
- Prompt-library loading is explicit outside sync-server startup. It includes
  LLM prompts, workflow help context, sector truth prompts, and chat response
  templates. Use `app.services.prompt_store.seed_prompts_from_files(overwrite=True)`
  after changing packaged prompt files, or `overwrite=False` to preserve DB edits.
- Retention cleanup is handled by `scripts/cleanup_retained_data.py`; schedule it
  with Task Scheduler, systemd timers, or cron in production.
