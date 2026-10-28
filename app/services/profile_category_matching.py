from __future__ import annotations

import re
from functools import lru_cache


PROFILE_CATEGORIES: tuple[str, ...] = (
    "Low income households",
    "Old age people",
    "Energy poverty / inability to keep home warm",
    "Unemployed people",
    "Children / young people",
    "People at risk of poverty or social exclusion",
    "Severe material and social deprivation",
    "Housing cost overburden",
    "Very low work intensity households",
    "People living alone",
    "Single-parent households",
    "People with disabilities",
    "Limited access to healthcare / unmet medical needs",
    "Arrears on housing, utilities or hire purchase",
    "Inability to face unexpected financial expenses",
    "Inability to afford a protein meal",
    "People at risk of poverty before social transfers",
    "Working-age people",
    "Oldest old people",
    "NEET young people",
    "Early leavers from education and training",
    "Youth unemployment",
    "Low educational attainment",
    "Employed people",
    "Income inequality S80/S20",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "Low income households": ("low income household", "low income families", "low income group", "income group low income"),
    "Old age people": ("older people", "elderly people", "older adults", "aged people"),
    "Energy poverty / inability to keep home warm": (
        "energy poverty", "fuel poverty", "unable to keep home warm", "unable to keep their home warm",
        "inability to keep home warm", "cannot afford to keep home warm",
    ),
    "Unemployed people": ("unemployed", "unemployed persons", "unemployment"),
    "Children / young people": ("children", "young people", "youth", "young persons"),
    "People at risk of poverty or social exclusion": (
        "at risk of poverty or social exclusion", "risk of poverty or social exclusion", "arope",
    ),
    "Severe material and social deprivation": (
        "severe material deprivation", "severe material and social deprivation rate",
    ),
    "Housing cost overburden": ("housing cost overburden rate", "overburden by housing costs"),
    "Very low work intensity households": (
        "very low work intensity", "very low work intensity household", "quasi jobless households",
    ),
    "People living alone": ("living alone", "one person households", "single person households"),
    "Single-parent households": ("single parent households", "lone parent households", "single parents"),
    "People with disabilities": ("people with a disability", "persons with disabilities", "disabled people"),
    "Limited access to healthcare / unmet medical needs": (
        "limited access to healthcare", "unmet medical needs", "unmet healthcare needs",
    ),
    "Arrears on housing, utilities or hire purchase": (
        "arrears on housing", "utility arrears", "arrears on utilities", "arrears on hire purchase",
        "housing arrears",
    ),
    "Inability to face unexpected financial expenses": (
        "unable to face unexpected financial expenses", "cannot face unexpected expenses",
        "inability to face unexpected expenses",
    ),
    "Inability to afford a protein meal": (
        "unable to afford a protein meal", "cannot afford a protein meal", "cannot afford protein",
    ),
    "People at risk of poverty before social transfers": (
        "at risk of poverty before social transfers", "poverty before social transfers",
        "pre transfer poverty risk",
    ),
    "Working-age people": ("working age people", "working age population", "working-age population"),
    "Oldest old people": ("oldest old", "oldest-old people", "people aged 80 and over", "people aged 85 and over"),
    "NEET young people": (
        "neet", "not in employment education or training", "not in education employment or training",
    ),
    "Early leavers from education and training": (
        "early leavers from education and training", "early school leavers", "early leavers from education",
    ),
    "Youth unemployment": ("unemployed young people", "youth unemployment rate", "young people unemployment"),
    "Low educational attainment": (
        "low education level", "low education attainment", "low educational level",
    ),
    "Employed people": ("employed persons", "employed population", "employment"),
    "Income inequality S80/S20": (
        "s80 s20", "s80/s20", "income quintile share ratio", "income inequality ratio",
    ),
}


def _normalize(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", value))


@lru_cache(maxsize=2048)
def _category_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for category in PROFILE_CATEGORIES:
        for phrase in (category, *_ALIASES.get(category, ())):
            normalized = _normalize(phrase)
            if normalized:
                lookup[normalized] = category
    return lookup


def match_profile_category(*labels: object) -> str | None:
    """Resolve profile labels to one approved API category, or return None."""
    lookup = _category_lookup()
    candidates: set[str] = set()
    for raw_label in labels:
        label = str(raw_label or "").strip()
        if not label:
            continue
        candidates.add(label)
        # Predictor encodings commonly append the category after a double underscore.
        candidates.update(part.strip() for part in label.split("__") if part.strip())
        candidates.update(part.strip() for part in re.split(r"[|;\n]", label) if part.strip())

    exact_matches = {
        lookup[normalized]
        for candidate in candidates
        if (normalized := _normalize(candidate)) in lookup
    }
    if len(exact_matches) == 1:
        return next(iter(exact_matches))
    if len(exact_matches) > 1:
        return None

    # Allow a unique, curated category phrase within a longer descriptive label.
    phrase_matches: list[tuple[int, str]] = []
    for candidate in candidates:
        normalized_candidate = f" {_normalize(candidate)} "
        for phrase, category in lookup.items():
            if f" {phrase} " in normalized_candidate:
                phrase_matches.append((len(phrase.split()), category))
    if not phrase_matches:
        return None
    longest = max(length for length, _ in phrase_matches)
    best_matches = {category for length, category in phrase_matches if length == longest}
    return next(iter(best_matches)) if len(best_matches) == 1 else None


def match_profile_data(profile: object) -> str | None:
    if not isinstance(profile, dict):
        return match_profile_category(profile)
    labels: list[object] = [
        profile.get("name"),
        profile.get("profile"),
        profile.get("variable_name"),
        profile.get("variable"),
        profile.get("predictor"),
        profile.get("explanation"),
        profile.get("statistical_basis"),
        profile.get("basis"),
    ]
    for key in ("target_population_labels", "population_lookup_labels"):
        values = profile.get(key)
        if isinstance(values, list):
            labels.extend(values)
    metadata = profile.get("metadata")
    if isinstance(metadata, dict):
        for key in (
            "profile",
            "category",
            "confirmed_predictor_category",
            "target_population_labels",
            "population_lookup_labels",
        ):
            value = metadata.get(key)
            labels.extend(value if isinstance(value, list) else [value])
        indicator_mapping = metadata.get("indicator_mapping")
        if isinstance(indicator_mapping, dict):
            labels.extend(
                indicator_mapping.get(key)
                for key in ("predictor_category", "proposed_indicator_label")
            )
    return match_profile_category(*labels)
