from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class HazardPredictorEffect:
    sector: str
    hazard: str
    predictor: str
    odds_ratio: float
    log_effect: float

    def as_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "hazard": self.hazard,
            "predictor": self.predictor,
            "odds_ratio": round(self.odds_ratio, 6),
            "log_effect": round(self.log_effect, 4),
        }


@dataclass(frozen=True)
class HazardEffectSizeRow:
    sector: str
    hazard: str
    effect_size: float
    predictor_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "hazard": self.hazard,
            "effect_size": round(self.effect_size, 4),
            "predictor_count": self.predictor_count,
        }


def hazard_effect_size_rows(
    sector: str | None = None,
    hazard: str | None = None,
    min_or: float = 1.0,
) -> list[dict[str, object]]:
    sector_key = _norm(sector)
    hazard_key = _norm(hazard)
    rows = [
        row
        for row in _hazard_effect_sizes(min_or)
        if (not sector_key or _norm(row.sector) == sector_key)
        and (not hazard_key or _norm(row.hazard) == hazard_key)
    ]
    return [row.as_dict() for row in rows]


def hazard_predictor_effect_rows(
    sector: str | None = None,
    hazard: str | None = None,
    min_or: float = 1.0,
) -> list[dict[str, object]]:
    sector_key = _norm(sector)
    hazard_key = _norm(hazard)
    rows = [
        row
        for row in _hazard_predictor_effects(min_or)
        if (not sector_key or _norm(row.sector) == sector_key)
        and (not hazard_key or _norm(row.hazard) == hazard_key)
    ]
    return [row.as_dict() for row in rows]


@lru_cache(maxsize=16)
def _hazard_effect_sizes(min_or: float = 1.0) -> tuple[HazardEffectSizeRow, ...]:
    grouped: dict[tuple[str, str], list[HazardPredictorEffect]] = {}
    for row in _hazard_predictor_effects(min_or):
        grouped.setdefault((row.sector, row.hazard), []).append(row)
    rows: list[HazardEffectSizeRow] = []
    for (sector, hazard), effects in grouped.items():
        if not effects:
            continue
        rows.append(
            HazardEffectSizeRow(
                sector=sector,
                hazard=hazard,
                effect_size=sum(effect.log_effect for effect in effects) / len(effects),
                predictor_count=len(effects),
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.sector, row.hazard)))


@lru_cache(maxsize=16)
def _hazard_predictor_effects(min_or: float = 1.0) -> tuple[HazardPredictorEffect, ...]:
    rows: list[HazardPredictorEffect] = []
    for path in _step6_csv_paths():
        sector, hazard = _sector_hazard_from_filename(path)
        if not sector or not hazard:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for record in reader:
                if not _confirmed_predictor(record):
                    continue
                odds_ratio = _to_float(record.get("OR"))
                if odds_ratio is None or odds_ratio <= min_or:
                    continue
                rows.append(
                    HazardPredictorEffect(
                        sector=sector,
                        hazard=hazard,
                        predictor=str(record.get("predictor") or "").strip(),
                        odds_ratio=odds_ratio,
                        log_effect=abs(math.log(odds_ratio)),
                    )
                )
    return tuple(sorted(rows, key=lambda row: (row.sector, row.hazard, row.predictor)))


def _step6_csv_paths() -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "outputs" / "step6",
        root / "app" / "outputs" / "step6",
    ]
    paths: list[Path] = []
    for directory in candidates:
        if directory.exists():
            paths.extend(sorted(directory.glob("*_A_step6_*.csv")))
    return paths


def _sector_hazard_from_filename(path: Path) -> tuple[str, str]:
    stem = path.stem
    marker = "_A_step6_"
    if marker not in stem:
        return "", ""
    sector, hazard = stem.split(marker, 1)
    if hazard.endswith("_effects"):
        hazard = hazard[: -len("_effects")]
    return sector, hazard


def _confirmed_predictor(record: dict[str, str]) -> bool:
    value = record.get("confirmed_via_LRT")
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def _to_float(value: object) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _norm(value: str | None) -> str:
    return str(value or "").strip().casefold()
