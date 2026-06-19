from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Sector,
    EurostatPopulationCache,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
)
from app.services.eurostat_service import EurostatService, predictor_eurostat_mapping
from app.services.hazard_effect_size import hazard_predictor_effect_rows


class ReachService:
    def __init__(
        self,
        eurostat_service: EurostatService | None = None,
        db: Session | None = None,
    ) -> None:
        self.eurostat = eurostat_service or EurostatService()
        self.db = db or getattr(self.eurostat, "db", None)

    async def hazard_reach(
        self,
        *,
        sector: str,
        hazard: str,
        hazard_name: str | None = None,
        country: str,
        region: str,
    ) -> dict[str, object]:
        predictors = hazard_predictor_effect_rows(sector=sector, hazard=hazard, min_or=1.0)
        total_predictors = len(predictors)
        mapped_predictors = 0
        used_predictors = 0
        reach = 0.0
        profiles: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []

        for predictor in predictors:
            predictor_name = str(predictor.get("predictor") or "")
            profile_name = predictor_profile_name(predictor_name)
            log_effect = float(predictor.get("log_effect") or 0.0)
            if predictor_eurostat_mapping(predictor_name) is None:
                skipped.append(
                    {
                        "predictor": predictor_name,
                        "reason": "No Eurostat mapping configured",
                        "bucket": 3,
                    }
                )
                continue
            mapped_predictors += 1
            prevalence = await self.eurostat.get_prevalence(
                predictor_name,
                country_code=country,
                nuts_code=region,
                sector=sector,
                hazard=hazard_name or hazard,
                confirmed_predictor_category=profile_name,
            )
            if prevalence is None:
                skipped.append(
                    {
                        "predictor": predictor_name,
                        "reason": "Eurostat value unavailable",
                        "bucket": 2,
                    }
                )
                continue
            used_predictors += 1
            population_share = float(prevalence["prevalence"])
            reach += population_share * log_effect
            profiles.append(
                {
                    "name": profile_name,
                    "predictor": predictor_name,
                    "eurostat_population_cache_id": prevalence.get("eurostat_population_cache_id"),
                    "population_pct": round(float(prevalence["population_pct"]), 1),
                    "national_population_pct": round(float(prevalence["national_population_pct"]), 1),
                    "source": prevalence["source"],
                    "dataset": prevalence["dataset"],
                    "geo": prevalence["geo"],
                    "weighted_effect": round(population_share * log_effect, 4),
                }
            )

        coverage_pct = (used_predictors / total_predictors * 100) if total_predictors else 0.0
        mapping_coverage_pct = (
            mapped_predictors / total_predictors * 100 if total_predictors else 0.0
        )
        matched_population = self._matched_population_percentages(
            sector=sector,
            hazard_name=hazard_name or hazard,
            country=country,
            region=region,
        )
        matched_reach = (
            float(matched_population["regional_population_pct"]) / 100
            if matched_population is not None
            else reach
        )
        return {
            "reach": matched_reach,
            "coverage_pct": coverage_pct,
            "mapping_coverage_pct": mapping_coverage_pct,
            "used_predictors": used_predictors,
            "mapped_predictors": mapped_predictors,
            "total_predictors": total_predictors,
            "profiles": profiles,
            "matched_population_profiles": (
                matched_population["profiles"] if matched_population is not None else []
            ),
            "regional_population_pct": (
                matched_population["regional_population_pct"]
                if matched_population is not None
                else None
            ),
            "national_population_pct": (
                matched_population["national_population_pct"]
                if matched_population is not None
                else None
            ),
            "skipped": skipped,
        }

    def _matched_population_percentages(
        self,
        *,
        sector: str,
        hazard_name: str,
        country: str,
        region: str,
    ) -> dict[str, object] | None:
        if self.db is None:
            return None
        cache_rows = self.db.scalars(
            select(EurostatPopulationCache)
            .join(
                SystemHazardSocioDemographicPopulationMatch,
                SystemHazardSocioDemographicPopulationMatch.eurostat_population_cache_id
                == EurostatPopulationCache.id,
            )
            .join(
                SystemHazardSocioDemographic,
                SystemHazardSocioDemographic.id
                == SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id,
            )
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .join(Sector, Sector.id == SystemHazard.sector_id)
            .where(
                func.lower(Sector.name) == sector.casefold(),
                func.lower(SystemHazard.name) == hazard_name.casefold(),
                func.lower(EurostatPopulationCache.country) == country.casefold(),
                func.lower(EurostatPopulationCache.region) == region.casefold(),
                EurostatPopulationCache.sector_id == Sector.id,
                EurostatPopulationCache.system_hazard_id == SystemHazard.id,
                SystemHazardSocioDemographicPopulationMatch.match_status == 1,
            )
        ).all()
        profiles: list[dict[str, object]] = []
        seen_cache_ids: set[int] = set()
        for cache_row in cache_rows:
            if cache_row.id in seen_cache_ids:
                continue
            seen_cache_ids.add(cache_row.id)
            try:
                response = json.loads(cache_row.response_json)
                population = (
                    response.get("data_set", {})
                    .get("eurostat", {})
                    .get("population", {})
                )
            except (AttributeError, TypeError, json.JSONDecodeError):
                continue
            profiles.append(
                {
                    "eurostat_population_cache_id": cache_row.id,
                    "country_id": cache_row.country_id,
                    "region_id": cache_row.region_id,
                    "sector_id": cache_row.sector_id,
                    "system_hazard_id": cache_row.system_hazard_id,
                    "profile": cache_row.profile,
                    "regional": {
                        "affected": (population.get("affected") or {}).get("regional"),
                        "total": (population.get("total") or {}).get("regional"),
                    },
                    "national": {
                        "affected": (population.get("affected") or {}).get("national"),
                        "total": (population.get("total") or {}).get("national"),
                    },
                }
            )
        if not profiles:
            return None
        regional_pct = self._average_population_percentage(profiles, "regional")
        national_pct = self._average_population_percentage(profiles, "national")
        if regional_pct is None or national_pct is None:
            return None
        return {
            "profiles": profiles,
            "regional_population_pct": regional_pct,
            "national_population_pct": national_pct,
        }

    @staticmethod
    def _average_population_percentage(
        profiles: list[dict[str, object]],
        scope: str,
    ) -> float | None:
        percentages: list[float] = []
        for profile in profiles:
            population = profile.get(scope)
            if not isinstance(population, dict):
                continue
            try:
                affected = float(population.get("affected"))
                total = float(population.get("total"))
            except (TypeError, ValueError):
                continue
            if total > 0:
                percentages.append(affected / total * 100)
        if not percentages:
            return None
        return round(sum(percentages) / len(percentages), 1)


def predictor_profile_name(predictor_name: str) -> str:
    value = predictor_name.strip()
    if "__" in value:
        variable, category = value.split("__", 1)
        variable_label = _humanize_variable(variable)
        category_label = category.strip()
        return f"{variable_label}: {category_label}" if variable_label else category_label
    return _humanize_variable(value)


def _humanize_variable(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("macro_"):
        cleaned = cleaned[len("macro_") :]
    return " ".join(part for part in cleaned.replace("_", " ").split()).title()
