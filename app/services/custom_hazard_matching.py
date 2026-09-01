import re
from difflib import SequenceMatcher
from typing import Any

from app.services.chat_options import compact_for_match, normalize_for_match
from app.services.enums import ConfidenceLevel


DUPLICATE_SEQUENCE_THRESHOLD = 0.86
DUPLICATE_TOKEN_THRESHOLD = 0.72
DUPLICATE_SEMANTIC_THRESHOLD = 0.70

ANSWER_ONLY_VALUES = {
    "yes",
    "no",
    "yes once",
    "yes twice or more",
    "twice or more",
    "once",
    "higher",
    "lower",
}

GENERIC_GROUPS = {
    "people",
    "communities",
    "citizens",
    "residents",
    "households",
    "consumers",
    "public",
    "society",
    "stakeholders",
}

POLICY_GROUPS = {
    "women",
    "energy communities",
    "renewable energy communities",
    "older adults",
    "disabled people",
    "tenants",
    "taxi drivers",
    "households in energy poverty",
    "low-income households",
    "middle-income households",
    "high-income households",
    "rural communities",
    "urban residents",
    "suburban residents",
    "tenant households",
    "homeowner households",
    "older homeowners",
    "young renters",
    "disabled commuters",
    "families relying on buses",
    "residents of apartment buildings",
    "people with poor digital skills",
    "energy-sector workers",
    "workers in fossil-fuel-dependent regions",
    "workers in fossil fuel dependent regions",
}


def duplicate_candidates(
    hazard_text: str,
    known_hazards: list[str],
    llm_candidates: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    hazard_title_key = normalize_for_match(_hazard_title_from_grounding_text(hazard_text))
    for item in llm_candidates:
        if not isinstance(item, dict):
            continue
        existing = str(item.get("existing_hazard") or item.get("match") or "").strip()
        if normalize_for_match(existing) == hazard_title_key:
            continue
        if existing:
            score = _clamp_percent(item.get("similarity_score"))
            candidates.append(
                {
                    "existing_hazard": existing,
                    "similarity_score": score,
                    "confidence": _confidence_for_percent(score).value,
                    "reason": str(item.get("reason") or "The hazards appear similar.").strip(),
                }
            )

    for existing in known_hazards:
        scores = duplicate_similarity_scores(hazard_text, existing)
        score = max(scores.values())
        if (
            scores["sequence"] >= DUPLICATE_SEQUENCE_THRESHOLD
            or scores["token"] >= DUPLICATE_TOKEN_THRESHOLD
            or scores["semantic"] >= DUPLICATE_SEMANTIC_THRESHOLD
        ):
            candidates.append(
                {
                    "existing_hazard": existing,
                    "similarity_score": round(score * 100),
                    "confidence": _confidence_for_percent(round(score * 100)).value,
                    "reason": duplicate_reason(scores),
                }
            )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item.get("similarity_score") or 0,
        reverse=True,
    ):
        key = normalize_for_match(str(candidate.get("existing_hazard") or ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique[:3]


def _hazard_title_from_grounding_text(hazard_text: str) -> str:
    for line in str(hazard_text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return str(hazard_text or "").strip()


def extract_affected_groups(text: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return []

    alias_map: tuple[tuple[tuple[str, ...], str], ...] = (
        (("low-income", "low income", "income poor"), "Low-income households"),
        (("middle-income", "medium income", "middle income"), "Middle-income households"),
        (("high-income", "high income"), "High-income households"),
        (("tenant", "tenants", "renters", "renting"), "Tenant households"),
        (("homeowner", "homeowners", "owner occupiers", "owner-occupiers"), "Homeowner households"),
        (("older adults", "elderly", "pensioners", "retirees"), "Older adults"),
        (("disabled", "disability", "long-term condition", "long term condition"), "People with disabilities or long-term conditions"),
        (("poor digital skills", "low digital skills", "digital exclusion"), "People with poor digital skills"),
        (("taxi drivers", "cab drivers"), "Taxi drivers"),
        (("rural communities", "rural residents", "rural households"), "Rural residents"),
        (("urban communities", "urban residents", "urban households"), "Urban residents"),
        (("families relying on buses", "bus-dependent families"), "Families relying on buses"),
        (("apartment dwellers", "residents of apartment buildings", "flat residents"), "Residents of apartment buildings"),
        (("energy poverty", "fuel poverty", "utility arrears", "energy affordability"), "Households experiencing energy affordability challenges"),
        (("renewable energy communities", "energy communities"), "Renewable energy communities"),
        (("energy-sector workers", "energy sector workers", "energy workers"), "Energy-sector workers"),
        (("fossil-fuel-dependent regions", "fossil fuel dependent regions", "coal regions", "oil and gas regions"), "Workers in fossil-fuel-dependent regions"),
    )
    value_key = normalize_for_match(value)
    for aliases, canonical in alias_map:
        if contains_any_term(value_key, aliases):
            groups.append(group_payload(canonical, canonical, "Matched a known affected-group expression."))

    patterns = [
        r"\b(?:low[- ]income|middle[- ]income|high[- ]income|rural|urban|suburban|older|young|disabled|tenant|homeowner|taxi|bus[- ]dependent|car[- ]dependent)\s+(?:households|communities|adults|people|drivers|residents|tenants|families|commuters)\b",
        r"\bhouseholds\s+(?:in|with|experiencing)\s+(?:energy poverty|fuel poverty|utility arrears|energy affordability challenges)\b",
        r"\bpeople\s+with\s+[^,.]{3,80}",
        r"\bresidents\s+of\s+[^,.]{3,80}",
        r"\bcommunities\s+affected\s+by\s+[^,.]{3,80}",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            label = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;:")
            if group_is_allowed(label):
                groups.append(group_payload(label[:120], match.group(0), "Explicitly named in the hazard or clarification text."))
    return dedupe_groups(groups)


def group_payload(label: str, source_text: str, reason: str) -> dict[str, Any]:
    return {
        "group": label[:120],
        "source_text": str(source_text or label).strip()[:240],
        "reason": reason,
        "confidence": (
            ConfidenceLevel.HIGH
            if normalize_for_match(label) in POLICY_GROUPS
            else ConfidenceLevel.MEDIUM
        ).value,
        "needs_review": True,
    }


def dedupe_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for group in groups:
        for item in split_group_entry(group):
            key = normalize_for_match(str(item.get("group") or ""))
            if key and key not in seen and group_is_allowed(str(item.get("group") or "")):
                seen.add(key)
                deduped.append(item)
    return deduped


def split_group_entry(group: dict[str, Any]) -> list[dict[str, Any]]:
    """Return separate records when one extracted label contains multiple groups."""
    item = coerce_group(group)
    label = str(item.get("group") or "").strip()
    parts = [
        part.strip(" ,;:")
        for part in re.split(r"\s+(?:and|&)\s+", label, flags=re.IGNORECASE)
        if part.strip(" ,;:")
    ]
    if len(parts) <= 1:
        return [item]

    return [
        {
            **item,
            "group": part[:120],
            "source_text": str(item.get("source_text") or label).strip()[:240],
        }
        for part in parts
    ]


def coerce_group(group: dict[str, Any]) -> dict[str, Any]:
    label = clean_group_label(str(group.get("group") or group.get("name") or group.get("profile") or ""))
    return {
        "group": label[:120],
        "source_text": str(group.get("source_text") or label).strip()[:240],
        "reason": str(group.get("reason") or group.get("explanation") or "").strip(),
        "confidence": _coerce_confidence(group.get("confidence")).value,
        "needs_review": bool(group.get("needs_review", True)),
        **({"source": group.get("source")} if group.get("source") else {}),
        **({"confirmed": group.get("confirmed")} if "confirmed" in group else {}),
    }


def clean_group_label(value: str) -> str:
    label = re.sub(r"\s+", " ", value).strip(" `*_#.-")
    match = re.match(r"^(.+?)\s*:\s*Add\s+\1\s*$", label, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.match(r"^(.+?)\s*:\s*Add\s+(.+)$", label, flags=re.IGNORECASE)
    if match and normalize_for_match(match.group(1)) == normalize_for_match(match.group(2)):
        return match.group(1).strip()
    return label


def group_is_allowed(group: str) -> bool:
    key = normalize_for_match(group)
    if not key or key in GENERIC_GROUPS or key in ANSWER_ONLY_VALUES:
        return False
    if any(key == value or key.endswith(f" {value}") for value in ANSWER_ONLY_VALUES):
        return False
    if key in POLICY_GROUPS:
        return True
    words = key.split()
    return len(words) > 1 and not all(word in GENERIC_GROUPS for word in words)


def similarity(left: str, right: str) -> float:
    scores = duplicate_similarity_scores(left, right)
    return max(scores.values())


def duplicate_similarity_scores(left: str, right: str) -> dict[str, float]:
    left_key = compact_for_match(left)
    right_key = compact_for_match(right)
    if not left_key or not right_key:
        return {"sequence": 0.0, "token": 0.0, "semantic": 0.0}
    if left_key in right_key or right_key in left_key:
        return {"sequence": 0.95, "token": 0.95, "semantic": 0.95}

    return {
        "sequence": SequenceMatcher(None, left_key, right_key).ratio(),
        "token": token_similarity(left, right),
        "semantic": semantic_duplicate_similarity(left, right),
    }


def token_similarity(left: str, right: str) -> float:
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(intersection) / len(union) if union else 0.0


def semantic_duplicate_similarity(left: str, right: str) -> float:
    left_terms = canonical_duplicate_terms(left)
    right_terms = canonical_duplicate_terms(right)
    if not left_terms or not right_terms:
        return 0.0

    intersection = left_terms & right_terms
    overlap = len(intersection) / min(len(left_terms), len(right_terms))
    jaccard = len(intersection) / len(left_terms | right_terms)
    return max(jaccard, overlap * 0.92)


def content_tokens(value: str) -> set[str]:
    stop_words = {
        "the", "and", "for", "with", "from", "during", "driven",
        "caused", "because", "into", "onto", "that", "this", "those",
        "these", "are", "may", "can", "due", "resulting", "results",
    }
    return {
        token
        for token in normalize_for_match(value).split()
        if len(token) > 2 and token not in stop_words
    }


def canonical_duplicate_terms(value: str) -> set[str]:
    key = normalize_for_match(value)
    tokens = content_tokens(value)
    terms: set[str] = set()

    phrase_aliases: tuple[tuple[tuple[str, ...], str], ...] = (
        (("employment shock", "employment shocks", "job losses", "job loss", "employment decline", "workforce decline", "labour market shock", "labor market shock"), "employment_loss"),
        (("fossil fuel", "fossil fuels", "fossil-fuel", "coal", "oil", "gas", "coal oil gas"), "fossil_fuel"),
        (("energy region", "energy regions", "regional", "regions", "region"), "region"),
        (("energy industry", "energy industries", "energy sector", "power generation", "conventional power"), "energy_industry"),
        (("green energy transition", "green transition", "renewable energy transition", "transition to renewable energy", "decarbonisation", "decarbonization", "clean energy transition"), "green_transition"),
        (("renewable", "renewables", "renewable energy"), "renewable_energy"),
        (("economic disruption", "economic decline", "left behind", "left-behind"), "regional_economic_disruption"),
    )

    for aliases, canonical in phrase_aliases:
        if contains_any_term(key, aliases):
            terms.add(canonical)

    token_aliases = {
        "jobs": "employment",
        "job": "employment",
        "employment": "employment",
        "workers": "workers",
        "worker": "workers",
        "decline": "loss",
        "declining": "loss",
        "losses": "loss",
        "loss": "loss",
        "shock": "shock",
        "shocks": "shock",
        "fossil": "fossil_fuel",
        "coal": "fossil_fuel",
        "oil": "fossil_fuel",
        "gas": "fossil_fuel",
        "energy": "energy",
        "renewable": "renewable_energy",
        "renewables": "renewable_energy",
        "transition": "transition",
        "green": "green_transition",
        "regional": "region",
        "region": "region",
        "regions": "region",
        "industries": "industry",
        "industry": "industry",
        "sector": "industry",
    }
    for token in tokens:
        terms.add(token_aliases.get(token, token))

    return terms


def duplicate_reason(scores: dict[str, float]) -> str:
    if scores.get("semantic", 0.0) >= DUPLICATE_SEMANTIC_THRESHOLD:
        return "The proposed hazard appears to describe the same underlying hazard using different wording."
    if scores.get("token", 0.0) >= DUPLICATE_TOKEN_THRESHOLD:
        return "The proposed hazard substantially overlaps with an existing hazard."
    return "The proposed hazard is the same as, or very similar to, an existing hazard."


def contains_any_term(value: str, terms: set[str] | tuple[str, ...]) -> bool:
    key = normalize_for_match(value)
    for term in terms:
        term_key = normalize_for_match(term)
        if not term_key:
            continue
        if " " in term_key:
            if term_key in key:
                return True
        elif re.search(rf"\b{re.escape(term_key)}\b", key):
            return True
    return False


def _clamp_percent(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if number <= 10:
        number *= 10
    return max(0, min(100, number))


def _confidence_for_percent(score: int) -> ConfidenceLevel:
    if score >= 75:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _coerce_confidence(value: Any) -> ConfidenceLevel:
    return ConfidenceLevel.coerce(value, ConfidenceLevel.MEDIUM)
