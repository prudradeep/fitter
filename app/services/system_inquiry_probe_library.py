from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PROBE_LIBRARY_PATH = Path(__file__).resolve().parents[1] / "system_inquiry_probe_library.json"


@lru_cache(maxsize=1)
def system_inquiry_probe_library() -> dict[str, Any]:
    with PROBE_LIBRARY_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    probes = data.get("probes")
    if not isinstance(probes, list) or len(probes) < 30:
        raise ValueError("System inquiry probe library must contain at least 30 probes.")
    records: dict[str, dict[str, Any]] = {}
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        probe_id = str(probe.get("probe_id") or "").strip()
        if probe_id:
            records[probe_id] = probe
    data["records"] = records
    return data


def system_inquiry_library_version() -> str:
    return str(system_inquiry_probe_library().get("library_version") or "1.0")


def system_inquiry_probe_record(probe_id: str) -> dict[str, Any] | None:
    records = system_inquiry_probe_library().get("records")
    if not isinstance(records, dict):
        return None
    record = records.get(str(probe_id or "").strip())
    return dict(record) if isinstance(record, dict) else None
