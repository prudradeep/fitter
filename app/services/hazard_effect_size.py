from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.csv_utils import first_existing_csvs, normalized_key, optional_float


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
    sector_key = normalized_key(sector)
    hazard_key = normalized_key(hazard)
    rows = [
        row
        for row in _hazard_effect_sizes(min_or)
        if (not sector_key or normalized_key(row.sector) == sector_key)
        and (not hazard_key or normalized_key(row.hazard) == hazard_key)
    ]
    return [row.as_dict() for row in rows]


def hazard_predictor_effect_rows(
    sector: str | None = None,
    hazard: str | None = None,
    min_or: float = 1.0,
) -> list[dict[str, object]]:
    sector_key = normalized_key(sector)
    hazard_key = normalized_key(hazard)
    rows = [
        row
        for row in _hazard_predictor_effects(min_or)
        if (not sector_key or normalized_key(row.sector) == sector_key)
        and (not hazard_key or normalized_key(row.hazard) == hazard_key)
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
                odds_ratio = optional_float(record.get("OR"))
                if odds_ratio is None or odds_ratio < min_or:
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
    return first_existing_csvs(
        root,
        ("outputs/step6", "app/outputs/step6"),
        "*_A_step6_*.csv",
    )


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

