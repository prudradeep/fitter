from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.csv_utils import first_existing_csvs, normalized_key, optional_float


HIGH_CONCERN_THRESHOLD = 12.0

# The region selector uses administrative names while some survey exports use
# translated names or broader statistical regions. Map those UI labels to the
# corresponding values in the CSV `region` column.
SURVEY_REGIONS_BY_SELECTED_REGION: dict[tuple[str, str], tuple[str, ...]] = {
    ("germany", "baden-württemberg"): ("Freiburg", "Karlsruhe", "Stuttgart", "Tübingen"),
    ("germany", "bavaria"): (
        "Lower Bavaria",
        "Lower Franconia",
        "Middle Franconia",
        "Upper Bavaria",
        "Upper Franconia",
        "Upper Palatinate",
        "Swabia",
    ),
    ("germany", "hesse"): ("Darmstadt", "Gießen", "Kassel"),
    ("germany", "lower saxony"): ("Hannover", "Lüneburg", "Weser-Ems"),
    ("germany", "north rhine-westphalia"): (
        "Arnsberg",
        "Cologne",
        "Detmold",
        "Düsseldorf",
        "Münster",
    ),
    ("germany", "rhineland-palatinate"): ("Koblenz", "Rheinhessen-Palatinate", "Trier"),
    ("germany", "saxony"): ("Chemnitz", "Dresden", "Leipzig"),
    ("ireland", "clare"): ("Southern",),
    ("ireland", "connacht"): ("Northern and Western",),
    ("ireland", "cork"): ("Southern",),
    ("ireland", "dublin"): ("Eastern and Midland",),
    ("ireland", "galway"): ("Northern and Western",),
    ("ireland", "kerry"): ("Southern",),
    ("ireland", "kilkenny"): ("Southern",),
    ("ireland", "leinster"): ("Eastern and Midland",),
    ("ireland", "limerick"): ("Southern",),
    ("ireland", "mayo"): ("Northern and Western",),
    ("ireland", "munster"): ("Southern",),
    ("ireland", "sligo"): ("Northern and Western",),
    ("ireland", "tipperary"): ("Southern",),
    ("ireland", "ulster (roi)"): ("Northern and Western",),
    ("ireland", "waterford"): ("Southern",),
    ("ireland", "wicklow"): ("Eastern and Midland",),
    ("italy", "puglia"): ("Apulia",),
    ("italy", "trentino-alto adige"): ("Trentino-South Tyrol",),
    ("portugal", "centro"): ("Centre",),
    ("spain", "madrid"): ("Community of Madrid",),
    ("spain", "murcia"): ("Region of Murcia",),
    ("spain", "valencia"): ("Valencian Community",),
}


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


def country_hazard_salience(
    country: str | None = None,
    sector: str | None = None,
    region: str | None = None,
) -> list[dict[str, object]]:
    country_key = normalized_key(country)
    sector_key = normalized_key(sector)
    rows = [
        row
        for row in _hazard_salience()
        if (not country_key or normalized_key(row.country) == country_key)
        and (not sector_key or normalized_key(row.sector) == sector_key)
    ]
    return [row.as_dict() for row in rows]


def hazard_concern_values(
    *,
    country: str,
    sector: str,
    hazard_column: str,
    region: str | None = None,
) -> list[float]:
    """Return the raw survey values used in a hazard's salience calculation."""
    return [
        float(row["concern_score"])
        for row in hazard_concern_rows(
            country=country,
            sector=sector,
            hazard_column=hazard_column,
            region=region,
        )
    ]


def hazard_concern_rows(
    *,
    country: str,
    sector: str,
    hazard_column: str,
    region: str | None = None,
) -> list[dict[str, object]]:
    """Return each salience source value with its survey region."""
    country_key = normalized_key(country)
    sector_key = normalized_key(sector)
    hazard_key = normalized_key(hazard_column)
    if not country_key or not sector_key or not hazard_key:
        return []

    rows: list[dict[str, object]] = []
    for path in _df_csv_paths():
        if normalized_key(_sector_from_filename(path)) != sector_key:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            matched_column = next(
                (
                    column
                    for column in (reader.fieldnames or [])
                    if normalized_key(column) == hazard_key
                ),
                "",
            )
            if not matched_column:
                continue
            for record in reader:
                if normalized_key(record.get("country")) != country_key:
                    continue
                value = optional_float(record.get(matched_column))
                if value is not None:
                    rows.append(
                        {
                            "region": str(record.get("region") or "").strip(),
                            "concern_score": value,
                        }
                    )
    return rows


@lru_cache(maxsize=64)
def survey_respondent_count(*, sector: str, country: str | None = None) -> int:
    """Count survey rows for a sector, optionally limited to one country."""
    sector_key = normalized_key(sector)
    country_key = normalized_key(country)
    if not sector_key:
        return 0

    count = 0
    for path in _df_csv_paths():
        if normalized_key(_sector_from_filename(path)) != sector_key:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for record in csv.DictReader(handle):
                if country_key and normalized_key(record.get("country")) != country_key:
                    continue
                count += 1
    return count


def top_hazard_salience_by_country(limit: int = 3) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[HazardSalienceRow]] = {}
    for row in _hazard_salience():
        grouped.setdefault(row.country, []).append(row)
    return {
        country: [row.as_dict() for row in sorted(rows, key=_salience_sort_key)[:limit]]
        for country, rows in grouped.items()
    }


@lru_cache(maxsize=32)
def _hazard_salience(region_keys: tuple[str, ...] = ()) -> tuple[HazardSalienceRow, ...]:
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
                if region_keys and normalized_key(record.get("region")) not in region_keys:
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


def _region_filter_keys(country: str | None, region: str | None) -> tuple[str, ...]:
    region_key = normalized_key(region)
    if not region_key or region_key == normalized_key("National scope"):
        return ()
    aliases = SURVEY_REGIONS_BY_SELECTED_REGION.get((normalized_key(country), region_key))
    return tuple(normalized_key(alias) for alias in aliases) if aliases else (region_key,)
