from __future__ import annotations

from app.services.eurostat_service import EurostatService, predictor_eurostat_mapping
from app.services.hazard_effect_size import hazard_predictor_effect_rows


class ReachService:
    def __init__(self, eurostat_service: EurostatService | None = None) -> None:
        self.eurostat = eurostat_service or EurostatService()

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
        return {
            "reach": reach,
            "coverage_pct": coverage_pct,
            "mapping_coverage_pct": mapping_coverage_pct,
            "used_predictors": used_predictors,
            "mapped_predictors": mapped_predictors,
            "total_predictors": total_predictors,
            "profiles": profiles,
            "skipped": skipped,
        }


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
