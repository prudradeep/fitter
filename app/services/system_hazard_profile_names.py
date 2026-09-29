"""Canonical display names for system hazard socio-demographic profiles.

The source of truth is ``profiles.xlsx``.  Predictor prompts often contain the
technical variable name (for example ``utility_arrears``); this module keeps
that implementation detail from leaking into the UI.
"""

import re


PROFILE_NAME_BY_VARIABLE: dict[str, str] = {
    "activity_status": "Activity status: Unemployed",
    "age_group": "Age group: 45+",
    "aware_gov_programmes": "Aware gov programmes: No",
    "ber_rating": "Ber rating: E-G",
    "bill_confidence": "Bill confidence",
    "can_cover_solar_cost": "Can cover solar cost: Not sure",
    "macro_asthma_death_rate": "Asthma death rate",
    "macro_cold_home_pct": "Cold homes",
    "macro_electricity_consumption": "Households with higher Electricity consumption",
    "macro_heating_degree_days": "Households with higher Heating degree days",
    "macro_modal_split_cars": "Travellers with Modal split",
    "disability": "Disabled",
    "dwelling_year": "Old Dwellings",
    "education": "Low level of education",
    "energy_improvements": "Unsure about energy improvements",
    "english_proficiency": "English speakers",
    "ethnic_minority": "Ethnic minorities",
    "ev_likelihood": "Unlikely to buy EV",
    "gender": "Gender: Woman or Non-binary",
    "heat_fuel_bottle_gas": "Consumers of bottle gas as heat fuel",
    "heat_fuel_wood": "Consumers with wood as Heat fuel",
    "age": "Higher age population",
    "ev_perception": "Positive EV perceivers",
    "home_problems_count": "Households with Higher Home problems count",
    "household_size": "Higher Household size",
    "income_band": "Higher Income band",
    "home_type": "Home type: Semi-detached house",
    "household_type": "Household type: Multi-generational / extended",
    "housing_cost_band": "Housing cost band: 1_Low",
    "housing_situation": "Housing situation: Owns outright - no mortgage",
    "issue_high_energy_bills": "Issue high energy bills",
    "newest_car_year_band": "Newest car year band: 2010-2019",
    "political_group": "Politically affiliated",
    "religious_minority": "Religious minority",
    "solar_install_likelihood": "Solar install likelihood",
    "speaks_national_language": "Non Native Speakers",
    "travel_frequency": "People with low traveling frequency",
    "utility_arrears": "Households with unpaid utility bills",
}


def canonical_profile_variable_name(value: object) -> str:
    """Extract a usable variable token from a predictor or profile payload."""
    raw_value = str(value or "").strip()
    prefixed_match = re.match(
        r"^(?:PREDICTOR\s+)?[0-9]+[A-Z]\s*:\s*(.+)$",
        raw_value,
        flags=re.IGNORECASE,
    )
    if prefixed_match:
        raw_value = prefixed_match.group(1)
    token = re.search(r"[A-Za-z_][A-Za-z0-9_]*", raw_value)
    return token.group(0).casefold() if token else ""


def profile_name_for_variable(variable_name: object, fallback: object = "") -> str:
    """Return the spreadsheet display name, retaining an unknown fallback."""
    return PROFILE_NAME_BY_VARIABLE.get(
        canonical_profile_variable_name(variable_name),
        str(fallback or "").strip(),
    )


def profile_name_for_legacy_label(label: object) -> str:
    """Translate pre-spreadsheet predictor labels retained in chat history."""
    original = str(label or "").strip()
    suffix = "." if original.endswith(".") else ""
    candidate = original.rstrip(". ")
    normalized = candidate.casefold()
    for variable_name, profile_name in PROFILE_NAME_BY_VARIABLE.items():
        old_label = variable_name.replace("_", " ")
        if old_label.startswith("macro "):
            old_label = old_label[6:]
        old_label = old_label[:1].upper() + old_label[1:]
        old_label_key = old_label.casefold()
        if (
            normalized == old_label_key
            or normalized.startswith(old_label_key + ":")
            or normalized == f"higher {old_label_key}"
            or normalized == f"countries with higher {old_label_key}"
        ):
            return profile_name + suffix
    return original
