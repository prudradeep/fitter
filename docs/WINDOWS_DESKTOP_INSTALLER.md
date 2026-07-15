# Windows Desktop Installer

The Windows installer now targets the split architecture defined in
[`ARCHITECTURE_BOUNDARIES.md`](ARCHITECTURE_BOUNDARIES.md).
Backend deployment and frontend bundle release steps are in
[`DEPLOYMENT.md`](DEPLOYMENT.md).

The installer bundles the desktop client and local runtime configuration only.
It does not bundle backend APIs, MySQL, schema files, migrations, seeds, or
database assets. Authentication, persistence, knowledge sync, and validated
evidence storage are provided by the hosted backend.

## Target Runtime

- `DrTransition.exe`: native Tauri/WebView2 desktop launcher
- Hosted backend URL: configured during install or through local override config
- Local Ollama API: used for client-side chat and embeddings
- Local frontend assets: owned by the desktop app

The launcher opens the hosted backend in a native desktop window after runtime
diagnostics pass. The user's external browser is not launched.

## Diagnostics

If required runtime checks fail, the launcher opens a setup diagnostics window
instead of failing silently. That window reports:

- Hosted backend reachable
- Hosted auth/session endpoint reachable or ready for sign-in
- Local Ollama reachable
- Configured chat model installed
- Configured embedding model installed
- Local diagnostics log directory

The launcher does not check for MySQL. MySQL belongs to the hosted backend.

## Layout

```text
desktop/tauri/
  package.json
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/main.rs

packaging/
  windows/
    DrTransition.iss
    config/default.config.json
    scripts/*.ps1
```

## Build Prerequisites

Build machines need:

- Rust and Cargo
- Node.js and npm
- Tauri prerequisites for Windows, including WebView2
- Inno Setup 6, with `ISCC.exe` on `PATH`

End users should not need these tools after the installer is produced.

## Build Desktop Launcher

```powershell
.\packaging\windows\scripts\build-desktop-launcher.ps1
```

The packaging script expects:

```text
desktop/tauri/src-tauri/target/release/drtransition.exe
```

Tauri still requires a small local frontend directory at build time:

```text
desktop/tauri/ui/index.html
```

Tauri also requires a Windows icon:

```text
desktop/tauri/src-tauri/icons/icon.ico
```

`build-desktop-launcher.ps1` creates it from `app/static/img/logo.png` when it is
missing.

## Release Build

For a full release build, run:

```powershell
.\packaging\windows\scripts\build-release.ps1
```

By default this bumps the patch version, builds the Tauri desktop launcher, then
builds the Windows installer.

Useful options:

```powershell
.\packaging\windows\scripts\build-release.ps1 -VersionPart Minor
.\packaging\windows\scripts\build-release.ps1 -Version 1.2.0
.\packaging\windows\scripts\build-release.ps1 -NoVersionBump
```

## Build Installer Only

To package the current already-built desktop payload:

```powershell
.\packaging\windows\scripts\build-installer.ps1
```

This assembles:

```text
build/windows-installer/payload/
```

Then compiles:

```text
build/windows-installer/DrTransitionSetup-0.1.3.exe
```

The payload contains only client/runtime assets:

```text
DrTransition.exe
config/default.config.json
frontend/static/
frontend/templates/
reference/prompts/
services/drtransition-reranker/
services/drtransition-nli/
models/huggingface/
scripts/Get-ModelRecommendation.ps1
scripts/Test-SystemCompatibility.ps1
```

The payload assembly script starts from a clean payload directory and fails if
backend/database artifacts are detected. It does not include Python backend
service folders, PyInstaller outputs, `schema.sql`, migrations, seeds, MySQL
assets, or database scripts.

The reranker and NLI/entailment bundles are local desktop companion services.
They do not expose backend APIs or connect to MySQL. The launcher starts them on
localhost and checks:

- Reranker: `http://127.0.0.1:8081/health`
- NLI/entailment: `http://127.0.0.1:8082/health`

Build them before assembling the installer payload:

```powershell
.\packaging\windows\scripts\build-grounding-services.ps1
```

That command also pre-caches the reranker and NLI model weights into:

```text
build/windows-installer/model-cache/huggingface/
```

The assembled installer copies that cache to `models/huggingface/`, and the
desktop launcher sets `HF_HOME`/`TRANSFORMERS_CACHE` for the companion services
so they work on the client machine without relying on a first-run model
download.

The historical backend PyInstaller spec has been archived at:

```text
packaging/windows/pyinstaller/archive/drtransition-backend.spec.deprecated
```

Do not use it for new Windows builds. The active PyInstaller specs are only the
local reranker and NLI companion services.

## Installer Ollama Check

During setup, the model page now verifies local runtime readiness before
installation continues:

- Ollama must be reachable at `http://127.0.0.1:11434`
- The selected chat model must already be installed
- The embedding model must already be installed

For the default configuration, prepare the machine with:

```powershell
ollama pull mistral-nemo
ollama pull nomic-embed-text
```

If the chat model is left as `auto`, the installer first selects the recommended
model for the machine, then checks whether that model is installed.

## Runtime Configuration

The source template lives at:

```text
packaging/windows/config/default.config.json
```

After `assemble-installer-payload.ps1` runs, it is copied into the payload as:

```text
build/windows-installer/payload/config/default.config.json
```

After installation, the default installed config is under the application
install directory:

```text
config/default.config.json
```

It contains no secrets. It defines:

- Hosted backend base URL
- Hosted backend health URL
- Hosted auth/session check URL
- Local Ollama base URL
- Chat model name
- Embedding model name

The installer asks for the hosted backend URL and writes a no-secret override to:

```text
%ProgramData%\DrTransition\.env
```

The launcher also accepts per-user overrides from:

```text
%LOCALAPPDATA%\DrTransition\.env
```

Supported override keys:

```text
DR_TRANSITION_BACKEND_URL
DR_TRANSITION_BACKEND_HEALTH_URL
DR_TRANSITION_BACKEND_AUTH_CHECK_URL
OLLAMA_BASE_URL
OLLAMA_MODEL
OLLAMA_EMBEDDING_MODEL
DR_TRANSITION_GROUNDING_ENABLED
DR_TRANSITION_RERANKER_URL
DR_TRANSITION_RERANKER_HEALTH_URL
DR_TRANSITION_NLI_URL
DR_TRANSITION_NLI_HEALTH_URL
```

When only `DR_TRANSITION_BACKEND_URL` is set, the launcher derives:

- `{backend}/health/ready`
- `{backend}/api/sessions`

## Ollama Checks

Default model downloads:

```powershell
ollama pull mistral-nemo
ollama pull nomic-embed-text
```

If a model is missing, the setup diagnostics window shows the exact `ollama pull`
command for the configured model.

## Local Knowledge Store

The desktop runtime initializes the client knowledge store at:

```text
%LOCALAPPDATA%\DrTransition\knowledge
```

It keeps synced `main`, `sector_prompt`, and `validated_evidence` scopes
separate from session-local `temporary` evidence. Each scope tracks sync cursors,
checksums, document checksums, and deleted-document tombstones.

The authenticated frontend starts `window.DrTransitionKBSync` on app load. It
fetches hosted manifests, downloads changed documents/chunks, removes stale
local documents, generates embeddings through local Ollama, and rebuilds local
indexes per knowledge scope. It repeats incremental sync periodically while the
app remains open.

Temporary evidence is handled by `window.DrTransitionEvidence`. Files and URLs
are parsed locally before chat state is persisted. Extracted text is chunked,
embedded through local Ollama, and stored only in the session-local `temporary`
scope. Reset and logout clear that temporary scope. The hosted backend never
receives temporary evidence before validation.

Supported local temporary file formats are PDF, DOCX, TXT, Markdown, CSV, JSON,
and HTML. Evidence URLs are fetched locally when browser CORS policy permits it.
Image-only scans or unusually encoded PDFs may fail local text extraction, but
temporary evidence is still never uploaded to the server before validation.

Local chat and retrieval are handled by `window.DrTransitionLocalRAG`. It calls
Ollama on the user's machine, retrieves from selected local KB scopes, constructs
cited prompts, parses responses, and reports clear errors when Ollama or the
configured models are unavailable.

Workflow turns are routed through `window.DrTransitionWorkflows` after the
hosted backend persists state. Intro/help text, open-selection assistance,
grounded questions, validation turns, stats deep-dives, and auto-user testing
use the local LLM/RAG layer in the desktop app.

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
- Hosted backend URL configuration
- No-secret local override config
- Bundled frontend static/template assets for the desktop client package
- Bundled local prompt/reference assets
- Client knowledge store for persistent and temporary scopes
- Client knowledge sync manager and local embedding/index refresh
- Client-only temporary evidence parsing, chunking, embedding, and cleanup
- Client local Ollama chat/RAG layer with citations and response parsing
- Client workflow controller for local LLM-backed workflow turns
- Validated evidence promotion to hosted `validated_evidence`
- Frontend API rewrite around hosted auth/session/sync/promotion APIs
- Installer payload assembly without backend/database assets
- First-run diagnostics for hosted backend, auth/session, Ollama, and models
- Hardware/model helper scripts
