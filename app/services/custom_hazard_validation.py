import json
import re
from difflib import SequenceMatcher
from typing import Any

from app.llm import ask_llm_chat
from app.services.chat_options import compact_for_match, normalize_for_match
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.enums import (
    ConfidenceLevel,
    CustomHazardAction,
    CustomHazardDimension,
    CustomHazardStatus,
    GroundingStatus,
)
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template


DIMENSION_WEIGHTS = {
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value: 0.20,
    CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value: 0.25,
    CustomHazardDimension.SELECTED_SECTOR_FIT.value: 0.20,
    CustomHazardDimension.COUNTRY_REGION_FIT.value: 0.15,
    CustomHazardDimension.AFFECTED_GROUPS_FIT.value: 0.20,
}

CRITICAL_DIMENSIONS = (
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value,
    CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value,
    CustomHazardDimension.SELECTED_SECTOR_FIT.value,
    CustomHazardDimension.COUNTRY_REGION_FIT.value,
)

VALIDATION_THRESHOLDS = {
    "strict": {
        "ready_score": 75,
        "dimension_floor": 5,
    },
    "easy": {
        "ready_score": 60,
        "dimension_floor": 4,
    },
}

DIMENSION_TITLES = {
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value: "Hazard definition",
    CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value: "Twin transition policy fit",
    CustomHazardDimension.SELECTED_SECTOR_FIT.value: "Sector fit",
    CustomHazardDimension.COUNTRY_REGION_FIT.value: "Country / region fit",
    CustomHazardDimension.AFFECTED_GROUPS_FIT.value: "Affected population groups",
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
        "confidence": ConfidenceLevel.LOW.value,
        "status": CustomHazardStatus.DRAFT.value,
    }


async def validate_custom_hazard_dimensions(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    known_hazards: list[str],
    previous_state: dict[str, Any] | None,
    validation_mode: str = "strict",
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
    result["confidence"] = _confidence_for_percent(result["overall_score"]).value
    result["next_action"] = _recommended_action(result, state, validation_mode).value
    result["status"] = _status_for_action(result["next_action"]).value
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
        "confidence": state.get("confidence") or ConfidenceLevel.LOW.value,
        "status": state.get("status") or CustomHazardStatus.DRAFT.value,
    }


def normalize_custom_group(group: str, reason: str = "", source: str = "user_added") -> dict[str, Any]:
    label = re.sub(r"\s+", " ", group).strip(" `*_#.-")
    return {
        "group": label[:120],
        "source_text": label[:120],
        "reason": reason.strip(),
        "confidence": (
            ConfidenceLevel.HIGH if reason.strip() else ConfidenceLevel.MEDIUM
        ).value,
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

    system = load_nested_prompt_file("llm/custom_hazard_dimension_validation.txt")

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
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 0,
                    "reason": "",
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 0,
                    "reason": "",
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 0,
                    "reason": "",
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 0,
                    "reason": "",
                    "confidence": "low | medium | high",
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
                    "confidence": "low | medium | high",
                    "reason": "",
                }
            ],
            "recommended_next_action": " | ".join(CustomHazardAction.values()),
            "clarification_questions": [],
        },
    }

    user = render_prompt_template(
        "llm/custom_hazard_dimension_validation_user.txt",
        payload=json.dumps(payload, ensure_ascii=False, indent=2),
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
        "risk", "hazard", "harm", "burden", "cost", "costs", "higher",
        "unaffordable", "exclusion", "vulnerable", "shortage", "loss",
        "disruption", "power cut", "arrears", "fines", "penalty",
        "job loss", "cost increase"
    }
    softer_hazard_terms = {"uncertainty", "pressure", "barrier", "delay"}
    if any(term in lower for term in hazard_terms):
        hazard_definition_score = 8
    elif any(term in lower for term in softer_hazard_terms):
        hazard_definition_score = 6
    else:
        hazard_definition_score = 3
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
    state["confidence"] = ConfidenceLevel.coerce(
        state.get("confidence"),
        ConfidenceLevel.LOW,
    ).value
    state["status"] = CustomHazardStatus.coerce(
        state.get("status"),
        CustomHazardStatus.DRAFT,
    ).value
    if state.get("next_action"):
        state["next_action"] = CustomHazardAction.coerce(
            state.get("next_action"),
            CustomHazardAction.ASK_CLARIFICATION,
        ).value
    return state


def _coerce_validation_result(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dimensions = value.get("dimension_scores")
    if not isinstance(dimensions, dict):
        return None
    coerced = {"dimension_scores": {}, "affected_groups": [], "duplicate_candidates": []}
    fallback_score = _fallback_dimension_score(dimensions)
    for key in DIMENSION_WEIGHTS:
        item = dimensions.get(key)
        if not isinstance(item, dict):
            item = {
                "score": fallback_score,
                "reason": "Inferred from the available validation dimensions.",
            }
        score = _clamp_score(item.get("score"))
        question = str(item.get("clarification_question") or "").strip()
        needs_clarification = score < 5
        coerced["dimension_scores"][key] = {
            "score": score,
            "reason": str(item.get("reason") or "").strip(),
            "confidence": _coerce_confidence(item.get("confidence"), score).value,
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
        "confidence": _confidence_for_score(score).value,
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


def _recommended_action(
    result: dict[str, Any],
    state: dict[str, Any],
    validation_mode: str = "strict",
) -> CustomHazardAction:
    thresholds = _validation_thresholds(validation_mode)
    dimension_floor = thresholds["dimension_floor"]
    ready_score = thresholds["ready_score"]
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
        _clamp_score(dimensions.get(key, {}).get("score") if isinstance(dimensions.get(key), dict) else 0)
        < dimension_floor
        for key in CRITICAL_DIMENSIONS
    )
    groups_low = _clamp_score(
        dimensions.get("affected_groups_fit", {}).get("score")
        if isinstance(dimensions.get("affected_groups_fit"), dict)
        else 0
    ) < dimension_floor
    unresolved_required_gap = critical_low or groups_low

    if result.get("duplicate_candidates") and not state.get("duplicate_override_confirmed"):
        return CustomHazardAction.ASK_DUPLICATE_CONFIRMATION

    if unresolved_required_gap:
        if flattened:
            return CustomHazardAction.REJECT
        return CustomHazardAction.ASK_CLARIFICATION

    if result.get("affected_groups") and not state.get("confirmed_affected_groups"):
        return CustomHazardAction.REVIEW_GROUPS

    if score >= ready_score:
        return CustomHazardAction.VALIDATE
    if flattened:
        return CustomHazardAction.VALIDATE
    if critical_low or groups_low:
        return CustomHazardAction.ASK_CLARIFICATION
    return CustomHazardAction.VALIDATE


def _validation_thresholds(validation_mode: str) -> dict[str, int]:
    return VALIDATION_THRESHOLDS.get(
        str(validation_mode or "").strip().casefold(),
        VALIDATION_THRESHOLDS["strict"],
    )


def _status_for_action(action: str | CustomHazardAction) -> CustomHazardStatus:
    action_value = CustomHazardAction.coerce(
        action,
        CustomHazardAction.ASK_CLARIFICATION,
    ).value
    return {
        CustomHazardAction.ASK_CLARIFICATION.value: CustomHazardStatus.NEEDS_CLARIFICATION,
        CustomHazardAction.ASK_DUPLICATE_CONFIRMATION.value: (
            CustomHazardStatus.NEEDS_DUPLICATE_CONFIRMATION
        ),
        CustomHazardAction.REVIEW_GROUPS.value: CustomHazardStatus.NEEDS_GROUP_REVIEW,
        CustomHazardAction.VALIDATE.value: CustomHazardStatus.READY,
        CustomHazardAction.REJECT.value: CustomHazardStatus.REJECTED,
    }.get(action_value, CustomHazardStatus.NEEDS_CLARIFICATION)


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
                    "confidence": _confidence_for_percent(
                        _clamp_percent(item.get("similarity_score"))
                    ).value,
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
                    "confidence": _confidence_for_percent(round(score * 100)).value,
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
                        "confidence": (
                            ConfidenceLevel.HIGH
                            if label.casefold() in POLICY_GROUPS
                            else ConfidenceLevel.MEDIUM
                        ).value,
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
        "confidence": _coerce_confidence(group.get("confidence")).value,
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
        "confidence": _coerce_confidence(
            item.get("confidence") if isinstance(item, dict) else None,
            raw_score,
        ).value,
        "reason": (
            str(item.get("reason") or "Not checked yet.").strip()
            if isinstance(item, dict)
            else "Not checked yet."
        ),
        "clarification_question": (
            str(item.get("clarification_question") or "").strip() or None
            if isinstance(item, dict)
            else None
        ),
    }


def _duplicate_card(state: dict[str, Any]) -> dict[str, Any]:
    candidates = state.get("duplicate_candidates") if isinstance(state.get("duplicate_candidates"), list) else []
    confirmed = bool(state.get("duplicate_override_confirmed"))
    if candidates and not confirmed:
        status = GroundingStatus.WARNING.value
        reason = f"This hazard appears similar to an existing hazard: '{candidates[0].get('existing_hazard')}'."
    elif candidates and confirmed:
        status = GroundingStatus.CONFIRMED.value
        reason = "The user chose to continue with the custom hazard despite a possible duplicate."
    else:
        status = GroundingStatus.SUPPORTED.value
        reason = "No duplicate hazard was detected in the selected sector."
    return {
        "title": "Duplicate check",
        "status": status,
        "score": None,
        "confidence": (
            ConfidenceLevel.HIGH.value
            if status != GroundingStatus.WARNING.value
            else ConfidenceLevel.MEDIUM.value
        ),
        "reason": reason,
        "clarification_question": None,
    }


def _affected_groups_card(state: dict[str, Any]) -> dict[str, Any]:
    groups = state.get("confirmed_affected_groups") or state.get("affected_groups") or []
    if groups:
        names = [str(group.get("group") or group.get("name") or "").strip() for group in groups if isinstance(group, dict)]
        reason = "Identified groups: " + ", ".join([name for name in names if name])
        status = (
            GroundingStatus.CONFIRMED.value
            if state.get("confirmed_affected_groups")
            else GroundingStatus.NEEDS_CLARIFICATION.value
        )
    else:
        reason = "No qualified affected population groups have been confirmed."
        status = GroundingStatus.INSUFFICIENT_INFO.value
    return {
        "title": "Affected population groups",
        "status": status,
        "score": None,
        "confidence": ConfidenceLevel.HIGH.value if groups else ConfidenceLevel.LOW.value,
        "reason": reason,
        "clarification_question": None,
    }


def _profile_reason_card(state: dict[str, Any]) -> dict[str, Any]:
    added = state.get("added_affected_groups") if isinstance(state.get("added_affected_groups"), list) else []
    missing = [
        group for group in added
        if isinstance(group, dict) and not str(group.get("reason") or "").strip()
    ]
    if missing:
        status = GroundingStatus.NEEDS_CLARIFICATION.value
        reason = "A user-added affected group needs an impact reason."
        question = f"How does this hazard affect '{missing[0].get('group')}'?"
    elif added:
        status = GroundingStatus.CONFIRMED.value
        reason = "Every user-added affected group has an impact reason."
        question = None
    else:
        status = GroundingStatus.INSUFFICIENT_INFO.value
        reason = "No user-added affected group impact reason has been provided."
        question = None
    return {
        "title": "Custom profile impact reason",
        "status": status,
        "score": None,
        "confidence": (
            ConfidenceLevel.HIGH.value
            if status == GroundingStatus.CONFIRMED.value
            else ConfidenceLevel.LOW.value
        ),
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
        status = GroundingStatus.INSUFFICIENT_INFO.value
        reason = "No clarification rounds have been run yet."
    elif flattened:
        status = GroundingStatus.READY.value
        reason = (
            f"Validation round {round_number}. Score improvement is "
            f"{improvement:.0f} points, below the "
            f"{CLARIFICATION_IMPROVEMENT_THRESHOLD}-point threshold, so clarification has flattened."
        )
    else:
        status = GroundingStatus.CONFIRMED.value
        if improvement is None:
            reason = f"Validation round {round_number}. Waiting to compare score improvement after the next round."
        else:
            reason = f"Validation round {round_number}. Score improved by {improvement:.0f} points; clarification can continue if needed."
    return {
        "title": "Clarification progress",
        "status": status,
        "score": None,
        "confidence": (
            ConfidenceLevel.MEDIUM.value
            if status in {GroundingStatus.READY.value, GroundingStatus.CONFIRMED.value}
            else ConfidenceLevel.LOW.value
        ),
        "reason": reason,
        "clarification_question": None,
    }


def _validation_readiness_card(state: dict[str, Any]) -> dict[str, Any]:
    score = int(state.get("overall_score") or 0)
    if score >= 75 or state.get("status") == CustomHazardStatus.READY.value:
        status = GroundingStatus.READY.value
    elif state.get("status") == CustomHazardStatus.REJECTED.value:
        status = GroundingStatus.REJECTED.value
    else:
        status = GroundingStatus.WARNING.value
    return {
        "title": "Validation readiness",
        "status": status,
        "score": score,
        "confidence": _confidence_for_percent(score).value,
        "reason": (
            "Ready to move to validation."
            if status == GroundingStatus.READY.value
            else "More support may be needed before validation."
        ),
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


def _fallback_dimension_score(dimensions: dict[str, Any]) -> int:
    scores = [
        _clamp_score(item.get("score"))
        for item in dimensions.values()
        if isinstance(item, dict)
    ]
    if not scores:
        return 0
    return round(sum(scores) / len(scores))


def _confidence_for_score(score: int) -> ConfidenceLevel:
    if score >= 8:
        return ConfidenceLevel.HIGH
    if score >= 5:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _confidence_for_percent(score: int) -> ConfidenceLevel:
    if score >= 75:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _coerce_confidence(value: Any, score: int | None = None) -> ConfidenceLevel:
    if score is not None:
        return ConfidenceLevel.coerce(value, _confidence_for_score(score))
    return ConfidenceLevel.coerce(value, ConfidenceLevel.MEDIUM)


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
