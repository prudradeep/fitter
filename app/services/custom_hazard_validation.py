import json
import re
from difflib import SequenceMatcher
from typing import Any

from numpy.strings import lower

from app.llm import ask_llm_chat
from app.services.chat_options import compact_for_match, normalize_for_match
from app.services.chat_parsers import is_llm_unavailable_response


DIMENSION_WEIGHTS = {
    "hazard_definition_fit": 0.20,
    "twin_transition_policy_fit": 0.25,
    "selected_sector_fit": 0.20,
    "country_region_fit": 0.15,
    "affected_groups_fit": 0.20,
}

CRITICAL_DIMENSIONS = (
    "hazard_definition_fit",
    "twin_transition_policy_fit",
    "selected_sector_fit",
    "country_region_fit",
)

DIMENSION_TITLES = {
    "hazard_definition_fit": "Hazard definition",
    "twin_transition_policy_fit": "Twin transition policy fit",
    "selected_sector_fit": "Sector fit",
    "country_region_fit": "Country / region fit",
    "affected_groups_fit": "Affected population groups",
}

CLARIFICATION_IMPROVEMENT_THRESHOLD = 3
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
    "energy communities",
    "renewable energy communities",
    "older adults",
    "disabled people",
    "tenants",
    "taxi drivers",
    "households in energy poverty",
    "low-income households",
    "rural communities",
}


def default_custom_hazard_state() -> dict[str, Any]:
    return {
        "raw_text": "",
        "normalized_text": "",
        "selected_country": "",
        "selected_region": "",
        "selected_sector": "",
        "validation_round": 0,
        "scores": [],
        "dimension_scores": {},
        "clarifications": [],
        "affected_groups": [],
        "confirmed_affected_groups": [],
        "removed_affected_groups": [],
        "added_affected_groups": [],
        "duplicate_candidates": [],
        "duplicate_override_confirmed": False,
        "status": "draft",
    }


async def validate_custom_hazard_dimensions(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    known_hazards: list[str],
    previous_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = _merged_state(previous_state)
    llm_result = await _llm_dimension_validation(
        hazard_text,
        selected_sector,
        country,
        region,
        state,
    )
    result = _coerce_validation_result(llm_result) if llm_result else None
    if result is None:
        result = _heuristic_dimension_validation(
            hazard_text,
            selected_sector,
            country,
            region,
            state,
        )

    result["duplicate_candidates"] = _duplicate_candidates(
        hazard_text,
        known_hazards,
        result.get("duplicate_candidates", []),
    )
    result["affected_groups"] = _dedupe_groups(
        [
            *_extract_affected_groups(hazard_text),
            *[
                group
                for clarification in state.get("clarifications", [])
                for group in _extract_affected_groups(str(clarification.get("answer") or ""))
            ],
            *[group for group in result.get("affected_groups", []) if isinstance(group, dict)],
        ]
    )
    result["overall_score"] = _overall_score(result.get("dimension_scores", {}))
    result["next_action"] = _recommended_action(result, state)
    result["status"] = _status_for_action(result["next_action"])
    return result


def build_custom_hazard_grounding_status(custom_hazard: dict[str, Any] | None) -> list[dict[str, Any]]:
    state = _merged_state(custom_hazard)
    dimension_scores = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
    cards = [
        _dimension_card("hazard_definition_fit", dimension_scores),
        _dimension_card("twin_transition_policy_fit", dimension_scores),
        _dimension_card("selected_sector_fit", dimension_scores),
        _dimension_card("country_region_fit", dimension_scores),
        _duplicate_card(state),
        _affected_groups_card(state),
        _profile_reason_card(state),
        _clarification_progress_card(state),
        _validation_readiness_card(state),
    ]
    return cards


def custom_hazard_validation_details(custom_hazard: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "title": "Custom hazard grounding status",
        "phase": str((custom_hazard or {}).get("phase") or "custom_hazard_status"),
        "custom_hazard_grounding_status": build_custom_hazard_grounding_status(custom_hazard),
        "reason": str((custom_hazard or {}).get("message") or "").strip(),
    }


def frontend_custom_hazard_payload(custom_hazard: dict[str, Any] | None) -> dict[str, Any]:
    state = _merged_state(custom_hazard)
    return {
        "text": state.get("raw_text") or "",
        "overall_score": state.get("overall_score") or 0,
        "dimension_scores": state.get("dimension_scores") or {},
        "affected_groups": state.get("affected_groups") or [],
        "duplicate_candidates": state.get("duplicate_candidates") or [],
        "validation_round": state.get("validation_round") or 0,
        "status": state.get("status") or "draft",
    }


def normalize_custom_group(group: str, reason: str = "", source: str = "user_added") -> dict[str, Any]:
    label = re.sub(r"\s+", " ", group).strip(" `*_#.-")
    return {
        "group": label[:120],
        "source_text": label[:120],
        "reason": reason.strip(),
        "confidence": "high" if reason.strip() else "medium",
        "needs_review": False,
        "source": source,
        "confirmed": True,
    }


async def _llm_dimension_validation(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    hazard_text = (hazard_text or "").strip()
    selected_sector = (selected_sector or "").strip()
    country = (country or "").strip()
    region = (region or "").strip()

    if not hazard_text:
        return None

    system = """
You are a strict validation assistant for user-created twin-transition hazards.

Return JSON only.

Rules:
- Do not invent evidence, policies, risks, locations, or affected groups.
- Do not infer unsupported target groups.
- A valid hazard must describe a possible negative impact, risk, harm, burden, exclusion, vulnerability, or disruption.
- A fact, statistic, trend, or observation is NOT a hazard unless it clearly explains a negative impact.
- Judge hazard definition separately from twin-transition policy fit.
- Judge sector fit separately from twin-transition policy fit.
- The selected sector must be one of: Energy, Housing, Transport.
- Validate only against the selected sector.
- Do not assume Energy when the sector is Housing or Transport.
- Judge country/region fit separately from sector fit.
- Generic groups alone are invalid: people, communities, citizens, residents, households, consumers, public, society, stakeholders.
- Qualified or policy-specific groups are valid: low-income households, rural communities, energy communities, renewable energy communities, tenants, taxi drivers, older adults, disabled people.
- Ask at most 2 clarification questions.
""".strip()

    payload = {
        "selected_country": country,
        "selected_region": region,
        "selected_sector": selected_sector,
        "custom_hazard_text": hazard_text,
        "previous_clarifications": state.get("clarifications", []),
        "current_affected_groups": state.get("affected_groups", []),
        "dimensions": list(DIMENSION_WEIGHTS.keys()),
        "scoring": {
            "score_range": "0-10 per dimension",
            "weights": DIMENSION_WEIGHTS,
            "overall_score": "weighted score converted to 0-100",
        },
        "required_output_schema": {
            "overall_score": 0,
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 0,
                    "reason": "",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 0,
                    "reason": "",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 0,
                    "reason": "",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 0,
                    "reason": "",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 0,
                    "reason": "",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [
                {
                    "group": "",
                    "source_text": "",
                    "reason": "",
                    "confidence": "low | medium | high",
                    "needs_review": True,
                }
            ],
            "duplicate_candidates": [
                {
                    "existing_hazard": "",
                    "similarity_score": 0,
                    "reason": "",
                }
            ],
            "recommended_next_action": "ask_clarification | ask_duplicate_confirmation | review_groups | validate | reject",
            "clarification_questions": [],
        },
    }

    user = (
        "Validate this custom hazard using the supplied context and schema.\n"
        "Return JSON only.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        response = await ask_llm_chat(
            context=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=1800,
        )
    except Exception:
        return None

    if is_llm_unavailable_response(response):
        return None

    return _parse_json_object(response)


def _heuristic_dimension_validation(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    combined = " ".join(
        [hazard_text, *[str(item.get("answer") or "") for item in state.get("clarifications", [])]]
    )
    lower = normalize_for_match(combined)
    sector_terms = _sector_terms(selected_sector)
    sector_score = 8 if any(term in lower for term in sector_terms) else 4
    transition_terms = {
        "green",
        "digital",
        "transition",
        "renewable",
        "energy",
        "carbon",
        "emission",
        "electric",
        "retrofit",
        "automation",
        "smart",
    }
    policy_score = 8 if any(term in lower for term in transition_terms) else 4
    location_terms = {
        token
        for token in normalize_for_match(f"{country} {region}").split()
        if len(token) > 2
    }
    location_score = 8 if not location_terms or location_terms & set(lower.split()) else 5
    groups = _extract_affected_groups(combined)
    group_score = 8 if groups else 4

    hazard_terms = {
        "risk", "hazard", "harm", "burden", "unaffordable", "exclusion",
        "vulnerable", "shortage", "loss", "disruption", "power cut",
        "arrears", "fines", "penalty", "job loss", "cost increase"
    }
    hazard_definition_score = 8 if any(term in lower for term in hazard_terms) else 3
    return {
        "dimension_scores": {
            "hazard_definition_fit": _score_payload(
                hazard_definition_score,
                "The input describes a negative impact, risk, or burden."
                if hazard_definition_score >= 5
                else "The input appears to describe a fact or observation rather than a hazard.",
                "Can you describe the negative impact, risk, or harm caused by this issue?",
            ),
            "twin_transition_policy_fit": _score_payload(
                policy_score,
                "The hazard is linked to green, digital, or twin-transition policy changes."
                if policy_score >= 5
                else "The hazard does not clearly name a green, digital, or twin-transition policy mechanism.",
                "Can you explain how this hazard is linked to green, digital, or twin-transition policy changes?",
            ),
            "selected_sector_fit": _score_payload(
                sector_score,
                "The hazard appears connected to the selected sector."
                if sector_score >= 5
                else "The hazard is not clearly connected to the selected sector.",
                f"Can you explain how this hazard is connected to the selected sector: {selected_sector}?",
            ),
            "country_region_fit": _score_payload(
                location_score,
                "The hazard has enough country or regional context."
                if location_score >= 5
                else "The hazard does not clearly explain why this place is relevant.",
                f"Can you explain why this hazard is relevant in {region}, {country}?",
            ),
            "affected_groups_fit": _score_payload(
                group_score,
                "The hazard explicitly names affected population groups."
                if group_score >= 5
                else "The hazard does not explicitly name qualified affected population groups.",
                "Which population groups are affected by this hazard, and why?",
            ),
        },
        "affected_groups": groups,
        "duplicate_candidates": [],
    }


def _merged_state(previous_state: dict[str, Any] | None) -> dict[str, Any]:
    state = default_custom_hazard_state()
    if isinstance(previous_state, dict):
        state.update(previous_state)
    return state


def _coerce_validation_result(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dimensions = value.get("dimension_scores")
    if not isinstance(dimensions, dict):
        return None
    coerced = {"dimension_scores": {}, "affected_groups": [], "duplicate_candidates": []}
    for key in DIMENSION_WEIGHTS:
        item = dimensions.get(key)
        if not isinstance(item, dict):
            return None
        score = _clamp_score(item.get("score"))
        question = str(item.get("clarification_question") or "").strip()
        needs_clarification = score < 5
        coerced["dimension_scores"][key] = {
            "score": score,
            "reason": str(item.get("reason") or "").strip(),
            "needs_clarification": needs_clarification,
            "clarification_question": question if needs_clarification else "",
        }
    coerced["affected_groups"] = [
        _coerce_group(group)
        for group in value.get("affected_groups", [])
        if isinstance(group, dict) and _group_is_allowed(str(group.get("group") or ""))
    ]
    coerced["duplicate_candidates"] = [
        item for item in value.get("duplicate_candidates", []) if isinstance(item, dict)
    ]
    return coerced


def _score_payload(score: int, reason: str, question: str) -> dict[str, Any]:
    return {
        "score": score,
        "reason": reason,
        "needs_clarification": score < 5,
        "clarification_question": question if score < 5 else "",
    }


def _overall_score(dimensions: dict[str, Any]) -> int:
    weighted = 0.0
    for key, weight in DIMENSION_WEIGHTS.items():
        item = dimensions.get(key) if isinstance(dimensions, dict) else {}
        score = _clamp_score(item.get("score") if isinstance(item, dict) else 0)
        weighted += score * weight
    return round(weighted * 10)


def _recommended_action(result: dict[str, Any], state: dict[str, Any]) -> str:
    score = int(result.get("overall_score") or 0)
    scores = [*state.get("scores", []), score]
    round_number = int(state.get("validation_round") or 0)
    improvement = scores[-1] - scores[-2] if len(scores) >= 2 else None
    flattened = (
        improvement is not None
        and round_number >= 2
        and abs(improvement) < CLARIFICATION_IMPROVEMENT_THRESHOLD
    )
    dimensions = result.get("dimension_scores", {})
    critical_low = any(
        _clamp_score(dimensions.get(key, {}).get("score") if isinstance(dimensions.get(key), dict) else 0) < 5
        for key in CRITICAL_DIMENSIONS
    )
    groups_low = _clamp_score(
        dimensions.get("affected_groups_fit", {}).get("score")
        if isinstance(dimensions.get("affected_groups_fit"), dict)
        else 0
    ) < 5
    unresolved_required_gap = critical_low or groups_low

    if result.get("duplicate_candidates") and not state.get("duplicate_override_confirmed"):
        return "ask_duplicate_confirmation"

    if unresolved_required_gap:
        if flattened:
            return "reject"
        return "ask_clarification"

    if result.get("affected_groups") and not state.get("confirmed_affected_groups"):
        return "review_groups"

    if score >= 75:
        return "validate"
    if flattened:
        return "validate"
    if critical_low or groups_low:
        return "ask_clarification"
    return "validate"


def _status_for_action(action: str) -> str:
    return {
        "ask_clarification": "needs_clarification",
        "ask_duplicate_confirmation": "needs_duplicate_confirmation",
        "review_groups": "needs_group_review",
        "validate": "ready",
        "reject": "rejected",
    }.get(action, "needs_clarification")


def _duplicate_candidates(
    hazard_text: str,
    known_hazards: list[str],
    llm_candidates: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in llm_candidates:
        if not isinstance(item, dict):
            continue
        existing = str(item.get("existing_hazard") or item.get("match") or "").strip()
        if existing:
            candidates.append(
                {
                    "existing_hazard": existing,
                    "similarity_score": _clamp_percent(item.get("similarity_score")),
                    "reason": str(item.get("reason") or "The hazards appear similar.").strip(),
                }
            )
    for existing in known_hazards:
        score = _similarity(hazard_text, existing)
        if score >= 0.82:
            candidates.append(
                {
                    "existing_hazard": existing,
                    "similarity_score": round(score * 100),
                    "reason": "The proposed hazard is the same as, or very similar to, an existing hazard.",
                }
            )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item.get("similarity_score") or 0, reverse=True):
        key = normalize_for_match(str(candidate.get("existing_hazard") or ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique[:3]


def _extract_affected_groups(text: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    value = re.sub(r"\s+", " ", text).strip()
    patterns = [
        r"\b(?:low-income|rural|urban|older|disabled|renewable energy|energy|tenant|taxi|households in energy poverty|residents of [A-Z][A-Za-z -]+|communities affected by [^,.]+)\s+(?:households|communities|adults|people|drivers|residents|tenants)?",
        r"\bhouseholds in energy poverty\b",
        r"\bcommunities affected by [^,.]+",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            label = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;:")
            if _group_is_allowed(label):
                groups.append(
                    {
                        "group": label[:120],
                        "source_text": match.group(0).strip(),
                        "reason": "Explicitly named in the hazard or clarification text.",
                        "confidence": "high" if label.casefold() in POLICY_GROUPS else "medium",
                        "needs_review": True,
                    }
                )
    return _dedupe_groups(groups)


def _dedupe_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for group in groups:
        item = _coerce_group(group)
        key = normalize_for_match(str(item.get("group") or ""))
        if key and key not in seen and _group_is_allowed(str(item.get("group") or "")):
            seen.add(key)
            deduped.append(item)
    return deduped


def _coerce_group(group: dict[str, Any]) -> dict[str, Any]:
    label = _clean_group_label(str(group.get("group") or group.get("name") or group.get("profile") or ""))
    return {
        "group": label[:120],
        "source_text": str(group.get("source_text") or label).strip()[:240],
        "reason": str(group.get("reason") or group.get("explanation") or "").strip(),
        "confidence": str(group.get("confidence") or "medium").strip().lower(),
        "needs_review": bool(group.get("needs_review", True)),
        **({"source": group.get("source")} if group.get("source") else {}),
        **({"confirmed": group.get("confirmed")} if "confirmed" in group else {}),
    }


def _clean_group_label(value: str) -> str:
    label = re.sub(r"\s+", " ", value).strip(" `*_#.-")
    match = re.match(r"^(.+?)\s*:\s*Add\s+\1\s*$", label, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.match(r"^(.+?)\s*:\s*Add\s+(.+)$", label, flags=re.IGNORECASE)
    if match and normalize_for_match(match.group(1)) == normalize_for_match(match.group(2)):
        return match.group(1).strip()
    return label


def _group_is_allowed(group: str) -> bool:
    key = normalize_for_match(group)
    if not key or key in GENERIC_GROUPS:
        return False
    if key in POLICY_GROUPS:
        return True
    words = key.split()
    return len(words) > 1 and not all(word in GENERIC_GROUPS for word in words)


def _dimension_card(key: str, dimensions: dict[str, Any]) -> dict[str, Any]:
    item = dimensions.get(key) if isinstance(dimensions, dict) else {}
    raw_score = _clamp_score(item.get("score") if isinstance(item, dict) else 0)
    score = raw_score * 10
    needs = bool(item.get("needs_clarification")) if isinstance(item, dict) else True
    explicit_status = str(item.get("status") or "").strip().upper() if isinstance(item, dict) else ""
    status = explicit_status or ("NEEDS CLARIFICATION" if needs else "SUPPORTED")
    if raw_score == 0:
        status = explicit_status or "INSUFFICIENT INFO"
    return {
        "title": DIMENSION_TITLES[key],
        "status": status,
        "score": score,
        "reason": str(item.get("reason") or "Not checked yet.").strip() if isinstance(item, dict) else "Not checked yet.",
        "clarification_question": str(item.get("clarification_question") or "").strip() or None if isinstance(item, dict) else None,
    }


def _duplicate_card(state: dict[str, Any]) -> dict[str, Any]:
    candidates = state.get("duplicate_candidates") if isinstance(state.get("duplicate_candidates"), list) else []
    confirmed = bool(state.get("duplicate_override_confirmed"))
    if candidates and not confirmed:
        status = "WARNING"
        reason = f"This hazard appears similar to an existing hazard: '{candidates[0].get('existing_hazard')}'."
    elif candidates and confirmed:
        status = "CONFIRMED"
        reason = "The user chose to continue with the custom hazard despite a possible duplicate."
    else:
        status = "SUPPORTED"
        reason = "No duplicate hazard was detected in the selected sector."
    return {"title": "Duplicate check", "status": status, "score": None, "reason": reason, "clarification_question": None}


def _affected_groups_card(state: dict[str, Any]) -> dict[str, Any]:
    groups = state.get("confirmed_affected_groups") or state.get("affected_groups") or []
    if groups:
        names = [str(group.get("group") or group.get("name") or "").strip() for group in groups if isinstance(group, dict)]
        reason = "Identified groups: " + ", ".join([name for name in names if name])
        status = "CONFIRMED" if state.get("confirmed_affected_groups") else "NEEDS CLARIFICATION"
    else:
        reason = "No qualified affected population groups have been confirmed."
        status = "INSUFFICIENT INFO"
    return {"title": "Affected population groups", "status": status, "score": None, "reason": reason, "clarification_question": None}


def _profile_reason_card(state: dict[str, Any]) -> dict[str, Any]:
    added = state.get("added_affected_groups") if isinstance(state.get("added_affected_groups"), list) else []
    missing = [
        group for group in added
        if isinstance(group, dict) and not str(group.get("reason") or "").strip()
    ]
    if missing:
        status = "NEEDS CLARIFICATION"
        reason = "A user-added affected group needs an impact reason."
        question = f"How does this hazard affect '{missing[0].get('group')}'?"
    elif added:
        status = "CONFIRMED"
        reason = "Every user-added affected group has an impact reason."
        question = None
    else:
        status = "INSUFFICIENT INFO"
        reason = "No user-added affected group impact reason has been provided."
        question = None
    return {
        "title": "Custom profile impact reason",
        "status": status,
        "score": None,
        "reason": reason,
        "clarification_question": question,
    }


def _clarification_progress_card(state: dict[str, Any]) -> dict[str, Any]:
    round_number = int(state.get("validation_round") or 0)
    scores = state.get("scores") if isinstance(state.get("scores"), list) else []
    improvement = float(scores[-1]) - float(scores[-2]) if len(scores) >= 2 else None
    flattened = (
        improvement is not None
        and round_number >= 2
        and abs(improvement) < CLARIFICATION_IMPROVEMENT_THRESHOLD
    )
    if round_number == 0:
        status = "INSUFFICIENT INFO"
        reason = "No clarification rounds have been run yet."
    elif flattened:
        status = "READY"
        reason = (
            f"Validation round {round_number}. Score improvement is "
            f"{improvement:.0f} points, below the "
            f"{CLARIFICATION_IMPROVEMENT_THRESHOLD}-point threshold, so clarification has flattened."
        )
    else:
        status = "CONFIRMED"
        if improvement is None:
            reason = f"Validation round {round_number}. Waiting to compare score improvement after the next round."
        else:
            reason = f"Validation round {round_number}. Score improved by {improvement:.0f} points; clarification can continue if needed."
    return {
        "title": "Clarification progress",
        "status": status,
        "score": None,
        "reason": reason,
        "clarification_question": None,
    }


def _validation_readiness_card(state: dict[str, Any]) -> dict[str, Any]:
    score = int(state.get("overall_score") or 0)
    status = "READY" if score >= 75 or state.get("status") == "ready" else ("REJECTED" if state.get("status") == "rejected" else "WARNING")
    return {
        "title": "Validation readiness",
        "status": status,
        "score": score,
        "reason": "Ready to move to validation." if status == "READY" else "More support may be needed before validation.",
        "clarification_question": None,
    }


def _sector_terms(sector: str) -> set[str]:
    lower = normalize_for_match(sector)
    mapping = {
        "energy": {
            "energy", "electricity", "power", "grid", "smart grid",
            "renewable", "solar", "wind", "battery", "storage",
            "heating", "heat", "heat pump", "electrification",
            "energy efficiency", "utility"
        },
        "housing": {
            "housing", "house", "home", "homes", "building",
            "buildings", "dwelling", "retrofit", "renovation",
            "insulation", "heat pump", "smart home",
            "tenant", "homeowner", "residential"
        },
        "transport": {
            "transport", "mobility", "vehicle", "vehicles",
            "electric vehicle", "ev", "charging", "charging infrastructure",
            "public transport", "bus", "rail", "train",
            "taxi", "traffic", "logistics"
        },
    }
    for key, values in mapping.items():
        if key in lower:
            return values
    return set(lower.split())


def _similarity(left: str, right: str) -> float:
    left_key = compact_for_match(left)
    right_key = compact_for_match(right)
    if not left_key or not right_key:
        return 0.0
    if left_key in right_key or right_key in left_key:
        return 0.95
    return SequenceMatcher(None, left_key, right_key).ratio()


def _clamp_score(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, number))


def _clamp_percent(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if number <= 10:
        number *= 10
    return max(0, min(100, number))


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None
