from __future__ import annotations

import math
from pathlib import Path


def first_existing_csvs(
    root: Path,
    relative_directories: tuple[str, ...],
    pattern: str,
) -> list[Path]:
    paths: list[Path] = []
    for relative_directory in relative_directories:
        directory = root / relative_directory
        if directory.exists():
            paths.extend(sorted(directory.glob(pattern)))
    return paths


def optional_float(value: object, *, finite: bool = True) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if finite and not math.isfinite(number):
        return None
    return number


def normalized_key(value: object) -> str:
    return str(value or "").strip().casefold()
