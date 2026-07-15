# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parents[2]

a = Analysis(
    [str(root / "packaging" / "python" / "drtransition_reranker_server.py")],
    pathex=[str(root), str(root / "packaging" / "python")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "app.config",
        "app.grounding_servers",
        "app.grounding_servers.model_runtime",
        "app.grounding_servers.reranker",
        "app.services",
        "app.services.llm_logging",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "runtime_stdio",
        *collect_submodules("sentence_transformers"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="drtransition-reranker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="drtransition-reranker",
)
