from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.csv_utils import first_existing_csvs, normalized_key, optional_float


HIGH_CONCERN_THRESHOLD = 12.0


@dataclass(frozen=True)
class HazardSalienceRow:
    sector: str
    country: str
    hazard: str
    mean_concern: float
    pct_high_concern: float
    salience: float
    n: int

    def as_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "country": self.country,
            "hazard": self.hazard,
            "mean_concern": round(self.mean_concern, 3),
            "pct_high_concern": round(self.pct_high_concern, 1),
            "salience": round(self.salience, 3),
            "n": self.n,
        }


def hazard_salience_rows() -> list[dict[str, object]]:
    return [row.as_dict() for row in _hazard_salience()]


def country_hazard_salience(country: str | None = None, sector: str | None = None) -> list[dict[str, object]]:
    country_key = normalized_key(country)
    sector_key = normalized_key(sector)
    rows = [
        row
        for row in _hazard_salience()
        if (not country_key or normalized_key(row.country) == country_key)
        and (not sector_key or normalized_key(row.sector) == sector_key)
    ]
    return [row.as_dict() for row in rows]


def top_hazard_salience_by_country(limit: int = 3) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[HazardSalienceRow]] = {}
    for row in _hazard_salience():
        grouped.setdefault(row.country, []).append(row)
    return {
        country: [row.as_dict() for row in sorted(rows, key=_salience_sort_key)[:limit]]
        for country, rows in grouped.items()
    }


@lru_cache(maxsize=1)
def _hazard_salience() -> tuple[HazardSalienceRow, ...]:
    rows: list[HazardSalienceRow] = []
    for path in _df_csv_paths():
        sector = _sector_from_filename(path)
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            hazard_columns = [
                column for column in (reader.fieldnames or []) if column.startswith("hazard_")
            ]
            totals: dict[tuple[str, str], list[float]] = {}
            for record in reader:
                country = str(record.get("country") or "").strip()
                if not country:
                    continue
                for hazard in hazard_columns:
                    value = optional_float(record.get(hazard))
                    if value is None:
                        continue
                    totals.setdefault((country, hazard), []).append(value)
        for (country, hazard), values in totals.items():
            if not values:
                continue
            mean_concern = sum(values) / len(values)
            pct_high_concern = (
                sum(1 for value in values if value > HIGH_CONCERN_THRESHOLD) / len(values) * 100
            )
            rows.append(
                HazardSalienceRow(
                    sector=sector,
                    country=country,
                    hazard=hazard,
                    mean_concern=mean_concern,
                    pct_high_concern=pct_high_concern,
                    salience=mean_concern * pct_high_concern / 100,
                    n=len(values),
                )
            )
    return tuple(sorted(rows, key=_salience_sort_key))


def _df_csv_paths() -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    return first_existing_csvs(root, ("outputs/dfs", "app/outputs/dfs"), "*.csv")


def _sector_from_filename(path: Path) -> str:
    stem = path.stem
    return stem[:-3] if stem.endswith("_df") else stem


def _salience_sort_key(row: HazardSalienceRow) -> tuple[str, float, str, str]:
    return (row.country, -row.salience, row.sector, row.hazard)
