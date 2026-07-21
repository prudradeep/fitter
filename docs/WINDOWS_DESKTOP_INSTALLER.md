# Windows Desktop Installer

This project now includes the first production packaging layer for a Windows desktop build of Dr Transition.

The target runtime is:

- `DrTransition.exe`: native Tauri/WebView2 desktop launcher
- `drtransition-backend.exe`: FastAPI application on `127.0.0.1:8000`
- `drtransition-grounding.exe --service reranker`: grounding reranker service on `127.0.0.1:8081`
- `drtransition-grounding.exe --service nli`: grounding NLI service on `127.0.0.1:8082`

The desktop launcher starts the backend services as hidden child processes, waits for their health endpoints, then opens the UI in a native desktop window. The user's external browser is not launched.

If required local dependencies are missing, the launcher opens a setup diagnostics
window instead of failing silently. That window reports:

- Whether a runtime `.env` file was found
- Whether MySQL is reachable on `127.0.0.1:3306`
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

This assembles:

```text
build/windows-installer/payload/
```

Then compiles:

```text
build/windows-installer/DrTransitionSetup-0.1.5.exe
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

The installer now performs the first dependency setup pass:

- Checks for MySQL before installing it; if `mysql.exe` already exists, the installer skips installation and only starts/checks the service
- Checks for Ollama before installing it; if `ollama.exe` already exists, the installer skips installation and only starts/checks the API
- Asks for the database name and MySQL credentials
- Creates the application database/user
- Updates `%ProgramData%\DrTransition\.env`
- Applies schema/migrations only; reference, user, and policy data are pulled from the central sync server on app startup
- Does not ingest bundled `kb/*.pdf` files locally; Main KB is pulled from the central server
- Pulls the required Ollama chat and embedding models

If a custom Ollama model directory is already configured through `OLLAMA_MODELS`
or a common Ollama `server.json` location, the installer preserves that path
before starting Ollama or pulling models. It does not reset the model directory
to Ollama's default path.

Missing dependencies are installed with `winget`:

```text
Oracle.MySQL
Ollama.Ollama
```

The setup log is written to:

```text
%LOCALAPPDATA%\DrTransition\logs\installer-setup.log
```

The desktop launcher still checks the external dependencies before starting the
bundled services. If setup did not complete, it opens the diagnostics window
instead of starting the backend.

The client installer does not create a default app user or seed reference data.
When sync is configured, users and reference data are pulled from the central
server on app startup.

Manual checks:

```powershell
Test-NetConnection 127.0.0.1 -Port 3306
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
- First-run diagnostics for `.env`, MySQL, Ollama, and Ollama models
- Installer-driven MySQL/Ollama detection, install, DB creation, seeding, and model pull

Next production hardening layer:

- Optional offline Hugging Face model bundle
- Code signing
- Upgrade migration rules
- Rich installer progress UI for long MySQL/Ollama/model operations
- Diagnostics export command
