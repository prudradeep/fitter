from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EurostatPopulationCache


@dataclass(frozen=True)
class EurostatMapping:
    predictor_prefix: str
    dataset: str
    dimensions: dict[str, str]
    unit: str = "PC"
    bucket: int = 1


class EurostatService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db
        self.settings = get_settings()

    async def get_profile_population(
        self,
        *,
        country: str,
        region: str,
        sector: str,
        hazard: str,
        confirmed_predictor_category: str,
    ) -> dict[str, object]:
        profile = confirmed_predictor_category
        cached = self._cached_profile_population(country=country, region=region, profile=profile)
        if cached is not None:
            return cached
        request_body = {
            "country": country,
            "region": region,
            "sector": sector,
            "hazard": hazard,
            "confirmed_predictor_category": confirmed_predictor_category,
        }
        response = self._mock_profile_population(request_body)
        self._store_profile_population(
            country=country,
            region=region,
            profile=profile,
            response=response,
        )
        return response

    async def get_prevalence(
        self,
        predictor_name: str,
        country_code: str,
        nuts_code: str | None,
        *,
        sector: str | None = None,
        hazard: str | None = None,
        confirmed_predictor_category: str | None = None,
    ) -> dict[str, object] | None:
        mapping = predictor_eurostat_mapping(predictor_name)
        if mapping is None:
            return None
        region = nuts_code or country_code
        response = await self.get_profile_population(
            country=country_code,
            region=region,
            sector=sector or "",
            hazard=hazard or "",
            confirmed_predictor_category=confirmed_predictor_category or predictor_name,
        )
        cache_row = self._profile_population_cache_row(
            country=country_code,
            region=region,
            profile=confirmed_predictor_category or predictor_name,
        )
        percentages = population_percentages(response)
        return {
            "eurostat_population_cache_id": cache_row.id if cache_row is not None else None,
            "prevalence": percentages["regional_pct"] / 100,
            "population_pct": percentages["regional_pct"],
            "national_population_pct": percentages["national_pct"],
            "source": "Mock Eurostat cache",
            "dataset": mapping.dataset,
            "geo": region,
            "bucket": mapping.bucket,
        }

    def _cached_profile_population(
        self,
        *,
        country: str,
        region: str,
        profile: str,
    ) -> dict[str, object] | None:
        row = self._profile_population_cache_row(country=country, region=region, profile=profile)
        if row is None:
            return None
        try:
            return json.loads(row.response_json)
        except json.JSONDecodeError:
            return None

    def _profile_population_cache_row(
        self,
        *,
        country: str,
        region: str,
        profile: str,
    ) -> EurostatPopulationCache | None:
        if self.db is None:
            return None
        return self.db.scalar(
            select(EurostatPopulationCache).where(
                EurostatPopulationCache.country == country,
                EurostatPopulationCache.region == region,
                EurostatPopulationCache.profile == profile,
                EurostatPopulationCache.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )

    def _store_profile_population(
        self,
        *,
        country: str,
        region: str,
        profile: str,
        response: dict[str, object],
    ) -> None:
        if self.db is None:
            return
        timestamp = _parse_timestamp(str(response.get("Timestamp") or "")) or datetime.now(
            timezone.utc
        ).replace(tzinfo=None)
        expires_at = add_months(timestamp, self.settings.eurostat_cache_expiry_months)
        payload = json.dumps(response, ensure_ascii=False)
        row = self.db.scalar(
            select(EurostatPopulationCache).where(
                EurostatPopulationCache.country == country,
                EurostatPopulationCache.region == region,
                EurostatPopulationCache.profile == profile,
            )
        )
        if row is None:
            row = EurostatPopulationCache(
                country=country,
                region=region,
                profile=profile,
                response_json=payload,
                expires_at=expires_at,
            )
            self.db.add(row)
        else:
            row.response_json = payload
            row.expires_at = expires_at
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _mock_profile_population(request_body: dict[str, str]) -> dict[str, object]:
        country = request_body["country"]
        region = request_body["region"]
        profile = request_body["confirmed_predictor_category"]
        seed = int(
            hashlib.sha256(json.dumps(request_body, sort_keys=True).encode("utf-8")).hexdigest()[:8],
            16,
        )
        regional_total = 300_000 + (seed % 2_400_000)
        national_total = max(regional_total * 4, 4_000_000 + ((seed >> 4) % 78_000_000))
        regional_pct = 8 + ((seed >> 8) % 38)
        national_pct = 7 + ((seed >> 16) % 36)
        regional_affected = round(regional_total * regional_pct / 100)
        national_affected = round(national_total * national_pct / 100)
        return {
            "country": country,
            "region": region,
            "profile": profile,
            "data_set": {
                "eurostat": {
                    "year": str(datetime.now(timezone.utc).year),
                    "population": {
                        "total": {
                            "regional": str(regional_total),
                            "national": str(national_total),
                        },
                        "affected": {
                            "regional": str(regional_affected),
                            "national": str(national_affected),
                        },
                    },
                }
            },
            "Timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }


def population_percentages(response: dict[str, object]) -> dict[str, float]:
    population = (
        response.get("data_set", {})
        .get("eurostat", {})
        .get("population", {})
        if isinstance(response.get("data_set"), dict)
        else {}
    )
    total = population.get("total", {}) if isinstance(population, dict) else {}
    affected = population.get("affected", {}) if isinstance(population, dict) else {}
    regional_pct = _safe_pct(affected.get("regional"), total.get("regional"))
    national_pct = _safe_pct(affected.get("national"), total.get("national"))
    return {"regional_pct": regional_pct, "national_pct": national_pct}


@lru_cache(maxsize=512)
def predictor_eurostat_mapping(predictor_name: str) -> EurostatMapping | None:
    normalized = predictor_name.casefold().split("__", 1)[0].strip() or "profile"
    return EurostatMapping(
        predictor_prefix=normalized,
        dataset="mock_profile_population",
        dimensions={"profile": predictor_name},
        bucket=1,
    )


def add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + max(0, months)
    year = value.year + month // 12
    month = month % 12 + 1
    days = [31, 29 if _leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return value.replace(year=year, month=month, day=min(value.day, days[month - 1]))


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _safe_pct(numerator: object, denominator: object) -> float:
    try:
        top = float(numerator)
        bottom = float(denominator)
    except (TypeError, ValueError):
        return 0.0
    return (top / bottom * 100) if bottom else 0.0


def _leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
