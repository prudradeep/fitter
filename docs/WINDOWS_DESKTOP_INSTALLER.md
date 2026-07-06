# Windows Desktop Installer

This project now includes the first production packaging layer for a Windows desktop build of Dr Transition.

The target runtime is:

- `DrTransition.exe`: native Tauri/WebView2 desktop launcher
- `drtransition-backend.exe`: FastAPI application on `127.0.0.1:8000`
- `drtransition-reranker.exe`: grounding reranker service on `127.0.0.1:8081`
- `drtransition-nli.exe`: grounding NLI service on `127.0.0.1:8082`

The desktop launcher starts the backend services as hidden child processes, waits for their health endpoints, then opens the UI in a native desktop window. The user's external browser is not launched.

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
    drtransition_reranker_server.py
    drtransition_nli_server.py
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
dist/drtransition-reranker/
dist/drtransition-nli/
```

The reranker and NLI builds include the grounding Python dependencies. Hugging Face model weights are still expected to be downloaded and cached on the target machine unless a future offline model bundle is added.

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

```powershell
.\packaging\windows\scripts\build-installer.ps1
```

This assembles:

```text
build/windows-installer/payload/
```

Then compiles:

```text
build/windows-installer/DrTransitionSetup-0.1.0.exe
```

To run all build steps in order:

```powershell
.\packaging\windows\scripts\build-release.ps1
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

Default runtime ports:

- Main app: `8000`
- Reranker: `8081`
- NLI: `8082`
- Ollama: `11434`

## Grounding Services

The reranker and NLI services are packaged as separate executables and started by `DrTransition.exe` when `grounding.enabled` is `true`.

If either service is already healthy on its configured port, the launcher reuses it instead of starting another process.

## Current Scope

Included now:

- Native launcher scaffold
- App, reranker, and NLI executable entrypoints
- PyInstaller specs
- Inno Setup installer skeleton
- Hardware/model helper scripts
- Installer payload assembly
- Config/log path conventions

Next production hardening layer:

- MySQL installer detection/install/reuse flow
- Ollama installer detection/install/reuse flow
- Automatic Ollama model pull UI
- Optional offline Hugging Face model bundle
- Code signing
- Upgrade migration rules
- Installer page wiring for hardware analysis and model choice
- Diagnostics export command
