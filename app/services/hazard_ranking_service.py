from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Country, Region, Sector
from app.services.eurostat_service import EurostatService
from app.services.hazard_effect_size import hazard_effect_size_rows, hazard_predictor_effect_rows
from app.services.hazard_salience import country_hazard_salience
from app.services.reach_service import ReachService


HAZARD_COLUMN_BY_SLUG = {
    "heating_and_cooling_costs_increase": "hazard_heat_cool",
    "higher_electricity_bills": "hazard_elec_bill",
    "struggling_to_pay_bills_each_month": "hazard_bill_struggle",
    "house_value_decrease_no_solar": "hazard_house_value",
    "missing_out_on_solar_savings": "hazard_miss_savings",
    "new_taxes_or_fines_for_inefficiency": "hazard_tax_fines",
    "more_frequent_power_cuts": "hazard_power_cuts",
    "home_insurance_more_expensive": "hazard_insurance_cost",
    "laws_forbid_selling_renting_non_renovated": "hazard_rent_ban",
    "home_more_damp_or_mould": "hazard_damp_mould",
    "insurers_classify_home_as_high_risk": "hazard_insurer_risk",
    "increased_severe_weather_impacts": "hazard_severe_weather",
    "cold_damp_leads_to_health_problems": "hazard_cold_damp_health",
    "higher_fuel_repair_costs_ice": "hazard_fuel_repair_cost",
    "car_loses_resale_value": "hazard_car_resale",
    "restricted_from_town_city_centres": "hazard_zez_restriction",
    "longer_or_more_complex_journeys": "hazard_journey_complex",
    "more_pollution_exposure": "hazard_pollution_exposure",
}


@dataclass(frozen=True)
class RankedHazard:
    hazard: str
    hazard_slug: str
    salience_score: float
    effect_size_score: float
    reach_score: float
    relevance_score: float
    used_predictors: int
    total_predictors: int
    profiles: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        return {
            "hazard": self.hazard,
            "hazard_slug": self.hazard_slug,
            "salience_score": round(self.salience_score, 4),
            "effect_size_score": round(self.effect_size_score, 4),
            "reach_score": round(self.reach_score, 4),
            "relevance_score": round(self.relevance_score, 4),
            "used_predictors": self.used_predictors,
            "total_predictors": self.total_predictors,
            "profiles": self.profiles,
        }


class HazardRankingService:
    def __init__(self, db: Session | None = None, reach_service: ReachService | None = None) -> None:
        self.reach_service = reach_service or ReachService(EurostatService(db))

    async def rank_hazards(
        self,
        *,
        country: Country,
        region: Region | None,
        sector: Sector,
        hazards: list[str] | None = None,
    ) -> list[dict[str, object]]:
        sector_name = sector.name
        country_name = country.name
        region_name = region.name if region else country.name
        positive_effect_rows = {
            str(row["hazard"]): row
            for row in hazard_effect_size_rows(sector=sector_name, min_or=1.0)
        }
        confirmed_effect_rows = {
            str(row["hazard"]): row
            for row in hazard_effect_size_rows(sector=sector_name, min_or=0.0)
        }
        salience_rows = {
            str(row["hazard"]): row
            for row in country_hazard_salience(country=country_name, sector=sector_name)
        }
        hazard_names = list(hazards or [])
        seen_slugs = {
            self._match_hazard_slug(hazard_name, confirmed_effect_rows)
            for hazard_name in hazard_names
        }
        hazard_names.extend(
            humanize_hazard_slug(slug)
            for slug in confirmed_effect_rows
            if slug not in seen_slugs
        )
        drafts: list[dict[str, object]] = []

        for hazard_name in hazard_names:
            slug = self._match_hazard_slug(hazard_name, confirmed_effect_rows)
            if not slug:
                continue
            confirmed_predictor_count = len(
                hazard_predictor_effect_rows(sector=sector_name, hazard=slug, min_or=0.0)
            )
            positive_predictor_count = len(
                hazard_predictor_effect_rows(sector=sector_name, hazard=slug, min_or=1.0)
            )
            if confirmed_predictor_count == 0:
                continue
            salience_column = HAZARD_COLUMN_BY_SLUG.get(slug, "")
            salience_score = float(salience_rows.get(salience_column, {}).get("salience") or 0.0)
            effect_size_score = float(positive_effect_rows.get(slug, {}).get("effect_size") or 0.0)
            reach = await self.reach_service.hazard_reach(
                sector=sector_name,
                hazard=slug,
                hazard_name=hazard_name,
                country=country_name,
                region=region_name,
            )
            drafts.append(
                {
                    "hazard": hazard_name,
                    "hazard_slug": slug,
                    "salience_score": salience_score,
                    "effect_size_score": effect_size_score,
                    "reach_score": float(reach["reach"]),
                    "used_predictors": int(reach["used_predictors"]),
                    "total_predictors": confirmed_predictor_count,
                    "positive_predictors": positive_predictor_count,
                    "profiles": list(reach["profiles"]),
                }
            )

        ranked: list[RankedHazard] = []
        for row in drafts:
            slug = str(row["hazard_slug"])
            relevance = (
                float(row["salience_score"])
                + float(row["effect_size_score"])
                + float(row["reach_score"])
            )
            ranked.append(
                RankedHazard(
                    hazard=str(row["hazard"]),
                    hazard_slug=slug,
                    salience_score=float(row["salience_score"]),
                    effect_size_score=float(row["effect_size_score"]),
                    reach_score=float(row["reach_score"]),
                    relevance_score=relevance,
                    used_predictors=int(row["used_predictors"]),
                    total_predictors=int(row["total_predictors"]),
                    profiles=list(row["profiles"]),
                )
            )
        return [
            row.as_dict()
            for row in sorted(
                ranked,
                key=lambda item: (
                    -item.relevance_score,
                    -item.salience_score,
                    -item.effect_size_score,
                    item.hazard,
                ),
            )
        ]

    @staticmethod
    def _match_hazard_slug(hazard_name: str, effect_rows: dict[str, dict[str, object]]) -> str:
        normalized_name = slugify_hazard(hazard_name)
        if normalized_name in effect_rows:
            return normalized_name
        for slug in effect_rows:
            normalized_slug = slugify_hazard(slug)
            if (
                normalized_name == normalized_slug
                or normalized_name in normalized_slug
                or normalized_slug in normalized_name
            ):
                return slug
        return ""


def slugify_hazard(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    replacements = {
        "and": "",
        "or": "",
        "for": "",
        "the": "",
    }
    tokens = [token for token in normalized.split("_") if token and token not in replacements]
    return "_".join(tokens)


def humanize_hazard_slug(value: str) -> str:
    return value.replace("_", " ").capitalize()
