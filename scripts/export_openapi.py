"""Export the three FastAPI OpenAPI documents.

Run from the repository root: uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.grounding_servers.nli import app as nli_app
from app.grounding_servers.reranker import app as reranker_app
from app.main import app as main_app
from app.openapi_documentation import METHODS

OUT = ROOT / "docs" / "openapi"


def write(name: str, app, expected: int) -> None:
    spec = app.openapi()
    count = sum(1 for methods in spec["paths"].values() for method in methods if method in METHODS)
    if count != expected:
        raise RuntimeError(f"{name}: expected {expected} operations, found {count}")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / name
    target.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{target.relative_to(ROOT)}: {count} operations")


if __name__ == "__main__":
    write("main.openapi.json", main_app, 53)
    write("nli.openapi.json", nli_app, 2)
    write("reranker.openapi.json", reranker_app, 2)
