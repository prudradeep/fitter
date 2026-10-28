from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import func, or_, select
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Country, EurostatPopulationCache, Region, Sector, SystemHazard
from app.services.profile_category_matching import match_profile_category

logger = logging.getLogger(__name__)


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
        context_ids = self._context_ids(country, region, sector, hazard)
        cached = self._cached_profile_population(
            country=country,
            region=region,
            sector=sector,
            hazard=hazard,
            profile=profile,
            context_ids=context_ids,
        )
        if cached is not None:
            return cached
        api_key = self.settings.population_analysis_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("POPULATION_ANALYSIS_API_KEY is not configured.")
        request_body = {
            "country": country,
            "region": region,
            "sector": sector,
            "hazard": hazard,
            "confirmed_predictor_category": confirmed_predictor_category,
            "run_mode": "full_analysis",
        }
        logger.info(
            "Population analysis API request body: %s",
            json.dumps(request_body, ensure_ascii=False, sort_keys=True),
        )
        async with httpx.AsyncClient(timeout=self.settings.eurostat_timeout_seconds) as client:
            api_response = await client.post(
                self.settings.population_analysis_api_url,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=request_body,
            )
            logger.info(
                "Population analysis API response body (status=%s): %s",
                api_response.status_code,
                api_response.text,
            )
            api_response.raise_for_status()
            response = api_response.json()
        if not isinstance(response, dict):
            raise ValueError("Population analysis API returned a non-object JSON response.")
        self._store_profile_population(
            country=country,
            region=region,
            sector=sector,
            hazard=hazard,
            profile=profile,
            response=response,
            context_ids=context_ids,
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
        category = match_profile_category(confirmed_predictor_category)
        if category is None:
            category = match_profile_category(predictor_name)
        if category is None:
            return None
        mapping = predictor_eurostat_mapping(category)
        if mapping is None:
            return None
        region = nuts_code or country_code
        response = await self.get_profile_population(
            country=country_code,
            region=region,
            sector=sector or "",
            hazard=hazard or "",
            confirmed_predictor_category=category,
        )
        cache_row = self._profile_population_cache_row(
            country=country_code,
            region=region,
            sector=sector or "",
            hazard=hazard or "",
            profile=category,
        )
        percentages = population_percentages(response)
        if percentages["regional_pct"] is None:
            return None
        eurostat_data = _eurostat_response_data(response)
        dataset_code = _first_text(
            eurostat_data.get("dataset_code"),
            response.get("dataset_code"),
        )
        indicator_label = _first_text(
            eurostat_data.get("indicator_label"),
            eurostat_data.get("proposed_indicator_label"),
            eurostat_data.get("indicator_name"),
            response.get("indicator_label"),
            response.get("proposed_indicator_label"),
            response.get("indicator_name"),
        )
        return {
            "eurostat_population_cache_id": cache_row.id if cache_row is not None else None,
            "prevalence": percentages["regional_pct"] / 100,
            "population_pct": percentages["regional_pct"],
            "national_population_pct": percentages["national_pct"],
            "source": "Population analysis API cache",
            "dataset": dataset_code or "NA",
            "proposed_eurostat_dataset": dataset_code or "NA",
            "proposed_indicator_label": indicator_label or category,
            "geo": region,
            "bucket": mapping.bucket,
        }

    def _cached_profile_population(
        self,
        *,
        country: str,
        region: str,
        sector: str,
        hazard: str,
        profile: str,
        context_ids: dict[str, int | None] | None = None,
    ) -> dict[str, object] | None:
        row = self._profile_population_cache_row(
            country=country,
            region=region,
            sector=sector,
            hazard=hazard,
            profile=profile,
            context_ids=context_ids,
        )
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
        sector: str,
        hazard: str,
        profile: str,
        context_ids: dict[str, int | None] | None = None,
    ) -> EurostatPopulationCache | None:
        if self.db is None:
            return None
        ids = context_ids or self._context_ids(country, region, sector, hazard)
        return self.db.scalar(
            select(EurostatPopulationCache).where(
                EurostatPopulationCache.country == country,
                EurostatPopulationCache.region == region,
                EurostatPopulationCache.country_id == ids["country_id"],
                EurostatPopulationCache.region_id == ids["region_id"],
                EurostatPopulationCache.sector_id == ids["sector_id"],
                EurostatPopulationCache.system_hazard_id == ids["system_hazard_id"],
                EurostatPopulationCache.profile == profile,
                EurostatPopulationCache.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )

    def _store_profile_population(
        self,
        *,
        country: str,
        region: str,
        sector: str,
        hazard: str,
        profile: str,
        response: dict[str, object],
        context_ids: dict[str, int | None] | None = None,
    ) -> None:
        if self.db is None:
            return
        timestamp = _parse_timestamp(str(response.get("Timestamp") or "")) or datetime.now(
            timezone.utc
        ).replace(tzinfo=None)
        expires_at = add_months(timestamp, self.settings.eurostat_cache_expiry_months)
        payload = json.dumps(response, ensure_ascii=False)
        ids = context_ids or self._context_ids(country, region, sector, hazard)
        row = self.db.scalar(
            select(EurostatPopulationCache).where(
                EurostatPopulationCache.country_id == ids["country_id"],
                EurostatPopulationCache.region_id == ids["region_id"],
                EurostatPopulationCache.sector_id == ids["sector_id"],
                EurostatPopulationCache.system_hazard_id == ids["system_hazard_id"],
                EurostatPopulationCache.profile == profile,
            )
        )
        if row is None:
            row = EurostatPopulationCache(
                country=country,
                region=region,
                country_id=ids["country_id"],
                region_id=ids["region_id"],
                sector_id=ids["sector_id"],
                system_hazard_id=ids["system_hazard_id"],
                profile=profile,
                response_json=payload,
                expires_at=expires_at,
            )
            self.db.add(row)
        else:
            row.response_json = payload
            row.expires_at = expires_at
            row.country = country
            row.region = region
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _context_ids(
        self,
        country: str,
        region: str,
        sector: str,
        hazard: str,
    ) -> dict[str, int | None]:
        ids: dict[str, int | None] = {
            "country_id": None,
            "region_id": None,
            "sector_id": None,
            "system_hazard_id": None,
        }
        if self.db is None:
            return ids
        country_row = self.db.scalar(
            select(Country).where(
                or_(
                    func.lower(Country.name) == country.casefold(),
                    func.lower(Country.map_code) == country.casefold(),
                )
            )
        )
        sector_row = self.db.scalar(
            select(Sector).where(func.lower(Sector.name) == sector.casefold())
        )
        region_row = None
        if country_row is not None:
            region_row = self.db.scalar(
                select(Region).where(
                    Region.country_id == country_row.id,
                    func.lower(Region.name) == region.casefold(),
                )
            )
        hazard_row = None
        if sector_row is not None:
            hazard_row = self.db.scalar(
                select(SystemHazard).where(
                    SystemHazard.sector_id == sector_row.id,
                    func.lower(SystemHazard.name) == hazard.casefold(),
                )
            )
        ids.update(
            country_id=country_row.id if country_row is not None else None,
            region_id=region_row.id if region_row is not None else None,
            sector_id=sector_row.id if sector_row is not None else None,
            system_hazard_id=hazard_row.id if hazard_row is not None else None,
        )
        return ids

def population_percentages(response: dict[str, object]) -> dict[str, float | None]:
    population = _eurostat_response_data(response).get("population", {})
    total = population.get("total", {}) if isinstance(population, dict) else {}
    affected = population.get("affected", {}) if isinstance(population, dict) else {}
    regional_pct = _safe_pct(affected.get("regional"), total.get("regional"))
    national_pct = _safe_pct(affected.get("national"), total.get("national"))
    return {"regional_pct": regional_pct, "national_pct": national_pct}


def _eurostat_response_data(response: dict[str, object]) -> dict[str, object]:
    data_set = response.get("data_set")
    if not isinstance(data_set, dict):
        return {}
    eurostat_data = data_set.get("eurostat")
    return eurostat_data if isinstance(eurostat_data, dict) else {}


def _first_text(*values: object) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return ""


@lru_cache(maxsize=512)
def predictor_eurostat_mapping(predictor_name: str) -> EurostatMapping | None:
    category = match_profile_category(predictor_name)
    if category is None:
        return None
    normalized = predictor_name.casefold().split("__", 1)[0].strip() or "profile"
    return EurostatMapping(
        predictor_prefix=normalized,
        dataset="population_analysis",
        dimensions={"profile": category},
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


def _safe_pct(numerator: object, denominator: object) -> float | None:
    try:
        top = float(numerator)
        bottom = float(denominator)
    except (TypeError, ValueError):
        return None
    return (top / bottom * 100) if bottom else None


def _leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
