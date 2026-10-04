# Windows Desktop Installer

This project now includes the first production packaging layer for a Windows desktop build of Dr Transition.

The target runtime is:

- `DrTransition.exe`: native Tauri/WebView2 desktop launcher
- `drtransition-backend.exe`: FastAPI application on `127.0.0.1:8000`
- `drtransition-grounding.exe --service reranker`: grounding reranker service on `127.0.0.1:8081`
- `drtransition-grounding.exe --service nli`: grounding NLI service on `127.0.0.1:8082`

The normal desktop runtime is a sync client. It uses SQLite for local/offline
application data, FAISS for local semantic search indexes, and authenticated
HTTPS calls to a central FastAPI sync server when synchronization is enabled.
It must not install MySQL, start MySQL, or store central MySQL credentials.

The desktop launcher starts the backend services as hidden child processes, waits for their health endpoints, then opens the UI in a native desktop window. The user's external browser is not launched.

If required local dependencies are missing, the launcher opens a setup diagnostics
window instead of failing silently. That window reports:

- Whether a runtime `.env` file was found
- Whether the configured SQLite database path is writable/initializable
- Whether Ollama is reachable
- Whether the configured chat and embedding models are downloaded in Ollama
- The local log directory for bundled service errors

## Layout

```text
desktop/tauri/
  package.json
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/main.rs

packaging/
  python/
    drtransition_app_server.py
    drtransition_grounding_server.py
  windows/
    DrTransition.iss
    config/default.config.json
    pyinstaller/*.spec
    scripts/*.ps1
```

## Build Prerequisites

Build machines need:

- Python 3.12
- `uv`
- Rust and Cargo
- Node.js and npm
- Tauri prerequisites for Windows, including WebView2
- Inno Setup 6, with `ISCC.exe` on `PATH`

End users should not need these tools after the installer is produced.

## Verify Before Building

From the repository root, run the backend checks before creating a release
installer:

```powershell
uv sync --extra test
uv run pytest
uv run ruff check .
```

For recent conversation-flow changes, run:

```powershell
uv run pytest tests/test_open_conversation_flow_actions.py tests/test_chat_selection_engine.py
```

If the release depends on workbook behavior, run the current workbook against a
single local Qwen model while debugging:

```powershell
uv run python tests/run_open_conversation_selection_cases.py --input .\new_test_cases.xlsx --models qwen3.5:2b
uv run python tests/run_open_conversation_selection_cases.py --input .\new_test_cases.xlsx --models qwen3.5:4b
```

Run the supported local Qwen models together before release:

```powershell
uv run python tests/run_open_conversation_selection_cases.py `
  --input .\new_test_cases.xlsx `
  --models qwen3.5:2b qwen3.5:4b
```

## Build Python Services

From the repository root:

```powershell
.\packaging\windows\scripts\build-python-services.ps1
```

This creates PyInstaller one-folder builds in `dist/`:

```text
dist/drtransition-backend/
dist/drtransition-grounding/
```

The shared `drtransition-grounding` build contains one executable that can run
either grounding service via `--service reranker` or `--service nli`, so PyTorch
and the grounding Python dependencies are bundled once instead of once per
service. Hugging Face model weights are still expected to be downloaded and
cached on the target machine unless a future offline model bundle is added.

If a bundled service shows an error like `Unable to configure formatter 'default'` or
`'NoneType' object has no attribute 'isatty'`, rebuild the services. The packaged
entrypoints explicitly disable Uvicorn's console formatter so the services can run
as hidden Windows processes.

## Build Desktop Launcher

```powershell
.\packaging\windows\scripts\build-desktop-launcher.ps1
```

The packaging script expects:

```text
desktop/tauri/src-tauri/target/release/drtransition.exe
```

The launcher opens the FastAPI backend URL in a native window, but Tauri still
requires a small local frontend directory at build time. That placeholder lives
at:

```text
desktop/tauri/ui/index.html
```

Tauri also requires a Windows icon at build time:

```text
desktop/tauri/src-tauri/icons/icon.ico
```

`build-desktop-launcher.ps1` creates it from `app/static/img/logo.png` when it is
missing.

## Build Installer

### Prebuilt knowledge indexes for sync clients

The normal sync-client installer can include FAISS files for the Main KB, sector
prompts, and policy documents. Build them on a dedicated client that has fully
synced from the **same server** the release will use. The server must own and
sync the matching knowledge documents and chunks, including policy documents.
Complete its indexing with the release's Ollama embedding model before export.

```powershell
.\.venv\Scripts\python.exe .\packaging\windows\scripts\create-seed-index-bundle.py `
  --database .\data\release-client.db `
  --index-base .\data\knowledge.faiss `
  --embedding-model nomic-embed-text `
  --sync-server-url https://your-sync-host.example `
  --output .\build\seed-indexes

.\packaging\windows\scripts\build-installer.ps1 `
  -SeedIndexBundlePath .\build\seed-indexes
```

`build-release.ps1` accepts the same `-SeedIndexBundlePath` parameter. Omit it
to build an installer without prebuilt indexes. The exporter checks that every
FAISS vector matches a public, indexed, server-synced knowledge chunk and
rejects user-owned documents or inconsistent files. It copies only FAISS files
and a manifest; the source SQLite database is never packaged. Review the
source KB and policy documents before distributing their vectors.

At the first backend startup, the client verifies the bundle checksum,
embedding model, and sync server URL, then places the indexes under
`%ProgramData%\DrTransition\data`. It leaves existing client knowledge and
indexes untouched. The first sync supplies the matching document/chunk rows;
its index reconciliation then finds those vectors without embedding them again.
New or changed server chunks are indexed normally. A bundle built from a
different server or embedding model is ignored.

Policy documents seeded only on an individual client have no server sync IDs
and cannot be exported for this workflow. Seed and index those documents on
the release server, then sync the dedicated export client first. The offline
admin installer can instead use the offline bundle below.

### Prebuilt indexes for offline admin installers

An offline admin installer needs the SQLite knowledge rows that match its FAISS
vector IDs because it has no server sync. Prepare a SQLite database with the
bundled Main KB, sector prompts, and policy documents already indexed using the
release embedding model. Then export a sanitized database snapshot and indexes:

```powershell
.\.venv\Scripts\python.exe .\packaging\windows\scripts\create-seed-index-bundle.py `
  --offline `
  --database .\data\prepared-offline.db `
  --index-base .\data\knowledge.faiss `
  --embedding-model nomic-embed-text `
  --output .\build\offline-seed-indexes

.\packaging\windows\scripts\build-installer.ps1 `
  -OfflineAdmin -PrepackageDependencies `
  -SeedIndexBundlePath .\build\offline-seed-indexes
```

`build-release.ps1` accepts the same flags. The exporter checks FAISS IDs
against the knowledge chunks, rejects user-owned knowledge and user-created
policies, and removes users, chat, logs, sync state, and other client data from
the SQLite snapshot. Review the KB and policy content before distributing it.
The installer verifies the database and index checksums and installs them only
when no local database or index exists. It then creates the local admin account
and skips indexing for the Main KB and sector-prompt scopes included in the
bundle. With no bundle, or when the
embedding model differs, the existing local indexing flow runs. Ollama model
weights still require a separate download or preinstalled model store.

### Standard installer build

For release builds, prefer the full release script. It increments the patch
version by default, keeps all Windows packaging version files in sync, then
builds the bundled services, desktop launcher, and installer:

```powershell
.\packaging\windows\scripts\build-release.ps1
```

Use `-VersionPart Minor` or `-VersionPart Major` when needed. Use `-Version
1.2.3` to set an exact version, or `-NoVersionBump` when rerunning the same
release build after a packaging failure.

To package the current already-built payload without changing the version:

```powershell
.\packaging\windows\scripts\build-installer.ps1
```

To build only the local database/model preparation installer, without the
desktop launcher or full app installer:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1
```

This produces:

```text
build/windows-dependencies-installer/DrTransitionDatabaseModelOnlineSetup-<version>.exe
```

Despite its legacy script name, that standalone preparation installer uses
SQLite and Ollama. It does not install or bundle MySQL.

To include the Ollama installer in the payload, place it under:

```text
packaging/windows/offline/ollama/OllamaSetup.exe
```

Then pass:

```powershell
.\packaging\windows\scripts\build-installer.ps1 -PrepackageDependencies
```

For the standalone database/model preparation installer, use:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1 -PrepackageDependencies
```

Use `-PrepackageDependencies` together with `-OfflineAdmin` to bundle Ollama
setup for the local SQLite admin installer:

```powershell
.\packaging\windows\scripts\build-installer.ps1 -OfflineAdmin -PrepackageDependencies
```

This assembles:

```text
build/windows-installer/payload/
```

Then compiles:

```text
build/windows-installer/DrTransitionOnlineSetup-0.1.11.exe
```

If you only run `build-python-services.ps1`, you will get the service executables
under `dist/`, but you will not get an installer. The installer is produced only
after the Tauri launcher and Inno Setup steps complete.

## Runtime Configuration

The default installed config is:

```text
config/default.config.json
```

It defines the backend, reranker, NLI, Ollama, and data/log paths. The launcher reads this file from the installed app directory.

The packaged Python backend reads environment variables from these locations, in
order:

```text
.env
%ProgramData%\DrTransition\.env
%LOCALAPPDATA%\DrTransition\.env
```

When a build-time `.env.client.dev` exists in the repository root, the installer
packages it as the client runtime `.env` template and copies it to:

```text
%ProgramData%\DrTransition\.env
```

The copy is conditional and does not overwrite an existing runtime `.env`. During
dependency setup, the installer preserves an existing `SYNC_DEVICE_ID`; if it is
missing or still has a sample value, setup generates a new GUID and writes it to
`SYNC_DEVICE_ID`.

For installed desktop use, prefer one of:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\DrTransition"
Copy-Item .\.env "$env:LOCALAPPDATA\DrTransition\.env"
```

or, for a machine-wide admin-managed config:

```powershell
New-Item -ItemType Directory -Force "$env:ProgramData\DrTransition"
Copy-Item .\.env "$env:ProgramData\DrTransition\.env"
```

Default runtime ports:

- Main app: `8000`
- Reranker: `8081`
- NLI: `8082`
- Ollama: `11434`

## Grounding Services

The reranker and NLI services are started as separate processes from the shared
`drtransition-grounding.exe` executable when `grounding.enabled` is `true`.

If either service is already healthy on its configured port, the launcher reuses it instead of starting another process.

## Dependency and Model Checks

The normal sync-client installer should perform this dependency setup pass:

- Creates or verifies the local SQLite data directory and database file
- Checks for Ollama before installing it; if `ollama.exe` already exists, the installer skips installation and only starts/checks the API
- Writes a client `.env` with `APP_MODE=client`, `SYNC_MODE=client`, a SQLite `DATABASE_URL`, and optional sync server settings
- Updates `%ProgramData%\DrTransition\.env`
- Applies client SQLite schema/migrations only; reference, user, policy, and prompt-library data are pulled from the central sync server on app startup
- Does not ingest bundled `kb/*.pdf` files locally; Main KB is pulled from the central server
- Pulls the required Ollama chat and embedding models

Example normal client runtime values:

```env
APP_MODE=client
SYNC_MODE=client
DATABASE_URL="sqlite:///data/dr_transition.db"
SQLITE_DATABASE_PATH="data/dr_transition.db"
SYNC_SERVER_URL="https://your-sync-host.example"
SYNC_API_TOKEN="<server-issued-client-token>"
SYNC_DEVICE_ID="<stable-client-uuid>"
FAISS_INDEX_PATH="data/knowledge.faiss"
```

For machines that need only local database/model preparation, build the
standalone installer:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1
```

or bundle the Ollama installer:

```powershell
.\packaging\windows\scripts\build-mysql-ollama-installer.ps1 -PrepackageDependencies
```

Despite the legacy build-script name, this standalone installer configures a
local SQLite database, installs/checks Ollama, applies schema/migrations, seeds
bundled reference data and prompts, and pulls the selected chat and embedding
models. It does not install the desktop launcher, grounding services, or create
a default app user. MySQL is not required.

The default installer remains a sync-client build and does not create a local
default user or seed reference data. To build the optional fully local installer
for offline/admin deployments, pass the offline-admin build flag:

```powershell
.\packaging\windows\scripts\build-installer.ps1 -OfflineAdmin
```

or for a full release build:

```powershell
.\packaging\windows\scripts\build-release.ps1 -OfflineAdmin
```

To include the Ollama installer in a full release build:

```powershell
.\packaging\windows\scripts\build-release.ps1 -OfflineAdmin -PrepackageDependencies
```

This produces `DrTransitionOfflineAdminPrepackagedSetup-<version>.exe`, uses a runtime
template with sync disabled, seeds local reference data and prompts, and creates
or reuses `admin@drtransition.local` with the `admin` role. With an offline seed
bundle, setup installs its SQLite knowledge rows and FAISS files before seeding
and skips Main KB and sector-prompt indexing. Without a bundle, it indexes the
sector prompts and starts a background seed for bundled `kb/*.pdf` files. The
installer shows the seeded admin user details after dependency setup completes,
and the final setup screen includes a button to copy the admin credentials.

If a custom Ollama model directory is already configured through `OLLAMA_MODELS`
or a common Ollama `server.json` location, the installer preserves that path
before starting Ollama or pulling models. It does not reset the model directory
to Ollama's default path.

When `-PrepackageDependencies` is used, the installer payload includes:

```text
installers/ollama/<bundled Ollama .exe>
```

The offline admin installer uses SQLite. It does not read
`packaging/windows/offline/mysql/` or ship a MySQL installer. At setup time,
Ollama is installed from the bundled `OllamaSetup.exe` if needed. Without
`-PrepackageDependencies`, setup obtains Ollama online if it is missing.

Prepackaging Ollama installs the Ollama application offline. Ollama model pulls
still require network access unless the target machine already has the required
models in its Ollama model store or a separate model-bundle/import process is
added.

The setup log is written to:

```text
%LOCALAPPDATA%\DrTransition\logs\installer-setup.log
```

The desktop launcher still checks the external dependencies before starting the
bundled services. If setup did not complete, it opens the diagnostics window
instead of starting the backend.

The normal client installer does not create a default app user, seed reference
data, seed the prompt library, or provision MySQL. When sync is configured,
users, reference data, and database-backed prompts are pulled from the central
server on app startup.

Manual checks for a normal sync client:

```powershell
Test-Path "$env:ProgramData\DrTransition"
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama list
```

Default model downloads:

```powershell
ollama pull mistral-nemo
ollama pull nomic-embed-text
```

If a model is missing, the setup diagnostics window shows the exact `ollama pull`
command for the configured model.

The diagnostics launcher reads these values from the runtime `.env` when present:

```text
OLLAMA_BASE_URL
OLLAMA_MODEL
OLLAMA_EMBEDDING_MODEL
```

During installer setup, leaving the chat model as `auto` selects a model from
RAM/GPU conditions:

- `< 8 GB RAM`: `llama3.2:3b`
- `8-15 GB RAM`: `llama3.2:3b`
- `16-31 GB RAM`: `mistral`
- `32+ GB RAM`: `mistral-nemo`
- `32+ GB RAM and 12+ GB GPU VRAM`: `qwen2.5:14b`

## Current Scope

Included now:

- Native launcher scaffold
- App, reranker, and NLI executable entrypoints
- PyInstaller specs
- Inno Setup installer skeleton
- Hardware/model helper scripts
- Installer payload assembly
- Config/log path conventions
- First-run diagnostics for `.env`, SQLite path, Ollama, and Ollama models
- Installer-driven SQLite/Ollama setup and model pull for normal clients
- Offline admin setup backed by local SQLite

Next production hardening layer:

- Remove unused MySQL helper code from legacy setup scripts
- Optional offline Hugging Face model bundle
- Code signing
- Upgrade migration rules
- Rich installer progress UI for long Ollama/model operations
- Diagnostics export command
