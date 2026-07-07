# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parents[2]

datas = [
    (str(root / "app" / "static"), "app/static"),
    (str(root / "app" / "templates"), "app/templates"),
    (str(root / "app" / "prompts"), "app/prompts"),
    (str(root / "app" / "outputs"), "app/outputs"),
    (str(root / "schema.sql"), "."),
    (str(root / "mm.csv"), "."),
    (str(root / "MM Target group.xlsx"), "."),
    (str(root / "sectoral_challenges.xlsx"), "."),
    (str(root / "hazards.xlsx"), "."),
    (str(root / "additionalHazards.csv"), "."),
    (str(root / "additionalHazardProfiles.csv"), "."),
    (str(root / ".env.example"), "."),
]

a = Analysis(
    [str(root / "packaging" / "python" / "drtransition_app_server.py")],
    pathex=[str(root), str(root / "packaging" / "python")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        *collect_submodules("app"),
        "pymysql",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "runtime_stdio",
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
    name="drtransition-backend",
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
    name="drtransition-backend",
)
