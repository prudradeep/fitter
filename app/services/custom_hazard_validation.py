import json
import re
from typing import Any

from app.llm import ask_llm_chat
from app.services.chat_json import parse_json_object
from app.services.chat_options import normalize_for_match
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.custom_hazard_matching import (
    coerce_group as _coerce_group,
    dedupe_groups as _dedupe_groups,
    duplicate_candidates as _duplicate_candidates,
    extract_affected_groups as _extract_affected_groups,
    group_is_allowed as _group_is_allowed,
)
from app.services.enums import (
    ConfidenceLevel,
    CustomHazardAction,
    CustomHazardDimension,
    CustomHazardStatus,
    GroundingStatus,
)
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template


DIMENSION_WEIGHTS = {
    # Hazard definition is the foundation. A policy/sector match is not enough
    # if the input is actually a benefit, mitigation, neutral fact, or question.
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value: 0.30,
    CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value: 0.25,
    CustomHazardDimension.SELECTED_SECTOR_FIT.value: 0.20,
    CustomHazardDimension.COUNTRY_REGION_FIT.value: 0.10,
    CustomHazardDimension.AFFECTED_GROUPS_FIT.value: 0.15,
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
        "ready_score": 45,
        "dimension_floor": 3,
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

SCORE_STRONG = 8
SCORE_PARTIAL = 6
SCORE_WEAK = 4
SCORE_POOR = 3

def default_custom_hazard_state() -> dict[str, Any]:
    return {
        "raw_text": "",
        "normalized_text": "",
        "resolved_hazard_text": "",
        "selected_country": "",
        "selected_region": "",
        "selected_sector": "",
        "title_validation_status": None,
        "title_validation_code": None,
        "title_validation_reason": None,
        "title_validation_confidence": None,
        "title_clarification_round": 0,
        "title_clarification_questions": [],
        "title_clarification_answers": [],
        "review_title_generated": False,
        "transition_link": None,
        "detected_sector": None,
        "negative_consequence": None,
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
    raw_hazard_text = str(hazard_text or "").strip()

    # If the user edits the hazard after overriding a duplicate warning, the
    # override should not silently carry over to the new text.
    state = _reset_duplicate_override_if_hazard_changed(state, raw_hazard_text)
    hazard_text = re.sub(r"\s+", " ", raw_hazard_text).strip()

    # Cheap deterministic signal first. It is not the final decision; it gives
    # stable guardrails and preserves offline behavior if the LLM is unavailable.
    heuristic_result = _heuristic_dimension_validation(
        hazard_text,
        selected_sector,
        country,
        region,
        state,
    )

    llm_result = await _llm_dimension_validation(
        hazard_text,
        selected_sector,
        country,
        region,
        state,
    )
    result = _coerce_validation_result(llm_result) if llm_result else None
    if result is None:
        result = heuristic_result
    else:
        result = _merge_llm_with_heuristic_guardrails(result, heuristic_result)

    result["duplicate_candidates"] = _duplicate_candidates(
        hazard_text,
        known_hazards,
        result.get("duplicate_candidates", []),
    )
    explicitly_identified_groups = _dedupe_groups(
        [
            *_extract_affected_groups(hazard_text),
            *[
                group
                for clarification in state.get("clarifications", [])
                if isinstance(clarification, dict)
                for group in _extract_affected_groups(str(clarification.get("answer") or ""))
            ],
        ]
    )
    result["affected_groups"] = _dedupe_groups(
        [
            *explicitly_identified_groups,
            *[group for group in result.get("affected_groups", []) if isinstance(group, dict)],
        ]
    )

    # Keep the affected-groups dimension consistent with the groups actually
    # extracted/coerced after LLM output is sanitized.
    if result["affected_groups"]:
        group_dimension = result.setdefault("dimension_scores", {}).get("affected_groups_fit")
        if isinstance(group_dimension, dict):
            group_dimension.update(
                _score_payload(
                    max(SCORE_PARTIAL, _clamp_score(group_dimension.get("score"))),
                    "Qualified affected population groups were identified after normalization.",
                    "",
                )
            )
        if explicitly_identified_groups:
            result["confirmed_affected_groups"] = result["affected_groups"]

    result["overall_score"] = _overall_score(result.get("dimension_scores", {}))
    result["confidence"] = _overall_confidence(result).value
    result["next_action"] = _recommended_action(result, state, validation_mode).value
    result["status"] = _status_for_action(result["next_action"]).value
    result["raw_text"] = hazard_text
    result["normalized_text"] = normalize_for_match(hazard_text)
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

    return parse_json_object(response)


def _heuristic_dimension_validation(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    combined = " ".join(
        [
            str(hazard_text or ""),
            *[
                str(item.get("answer") or "")
                for item in state.get("clarifications", [])
                if isinstance(item, dict)
            ],
        ]
    )
    lower = normalize_for_match(combined)
    tokens = set(lower.split())

    sector_terms = _sector_terms(selected_sector)
    sector_score = SCORE_STRONG if _contains_any_term(lower, sector_terms) else SCORE_WEAK

    transition_terms = {
        "green", "digital", "transition", "twin transition", "renewable",
        "decarbonisation", "decarbonization", "carbon", "emission",
        "electrification", "electric", "retrofit", "renovation",
        "building renovation", "energy efficiency", "automation", "smart",
        "smart meter", "smart grid", "heat pump", "ev", "electric vehicle",
        "charging infrastructure", "grid modernization", "grid modernisation",
        "clean heating", "energy community", "renewable energy community",
    }
    policy_score = SCORE_STRONG if _contains_any_term(lower, transition_terms) else SCORE_WEAK

    location_terms = {
        token
        for token in normalize_for_match(f"{country} {region}").split()
        if len(token) > 2
    }
    location_score = SCORE_STRONG if not location_terms or location_terms & tokens else 5

    groups = _extract_affected_groups(combined)
    group_score = SCORE_STRONG if groups else SCORE_WEAK

    hazard_terms = {
        "risk", "hazard", "harm", "burden", "cost", "costs", "higher",
        "increase", "increases", "unaffordable", "exclusion", "excluded",
        "vulnerable", "shortage", "loss", "disruption", "power cut",
        "outage", "arrears", "fines", "fine", "penalty", "job loss",
        "cost increase", "forced to", "relocate", "displacement", "delays",
        "barrier", "lack of access", "limited access", "cannot afford",
    }
    softer_hazard_terms = {"uncertainty", "pressure", "delay", "difficulty", "challenge"}
    benefit_terms = {
        "benefit", "benefits", "improve", "improves", "reduce", "reduces",
        "support", "subsidy", "grant", "mitigation", "solution", "measure",
    }

    if _contains_any_term(lower, hazard_terms):
        hazard_definition_score = SCORE_STRONG
    elif _contains_any_term(lower, softer_hazard_terms):
        hazard_definition_score = SCORE_PARTIAL
    else:
        hazard_definition_score = SCORE_POOR

    # Guardrail: a pure benefit/mitigation statement is not a hazard unless it
    # also states a negative impact or risk mechanism.
    if _contains_any_term(lower, benefit_terms) and not _contains_any_term(lower, hazard_terms):
        hazard_definition_score = min(hazard_definition_score, SCORE_POOR)

    return {
        "dimension_scores": {
            "hazard_definition_fit": _score_payload(
                hazard_definition_score,
                "The input describes a negative impact, risk, or burden."
                if hazard_definition_score >= 5
                else "The input appears to describe a benefit, mitigation, fact, or observation rather than a hazard.",
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
        needs_clarification = score < 5 or (
            score < SCORE_STRONG
            and bool(item.get("needs_clarification"))
            and bool(question)
        )
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
        _dimension_score(dimensions, key) < dimension_floor
        for key in CRITICAL_DIMENSIONS
    )
    critical_needs_clarification = any(
        _dimension_needs_clarification(dimensions, key)
        for key in CRITICAL_DIMENSIONS
    )
    if result.get("duplicate_candidates") and not state.get("duplicate_override_confirmed"):
        return CustomHazardAction.ASK_DUPLICATE_CONFIRMATION

    # The core hazard, transition, sector, and location dimensions must resolve
    # before the flow asks for reason/evidence or affected-group review.
    if critical_low or critical_needs_clarification:
        return CustomHazardAction.REJECT if flattened else CustomHazardAction.ASK_CLARIFICATION

    groups_low = _dimension_score(dimensions, "affected_groups_fit") < dimension_floor

    # Never mark ready while required dimensions are below the floor. Flattening
    # means the conversation stopped improving, not that the hazard became valid.
    if groups_low and not state.get("reason"):
        return CustomHazardAction.REJECT if flattened else CustomHazardAction.ASK_CLARIFICATION

    if result.get("affected_groups") and not state.get("confirmed_affected_groups"):
        return CustomHazardAction.REVIEW_GROUPS

    if score >= ready_score or flattened or str(validation_mode or "").strip().casefold() == "easy":
        return CustomHazardAction.VALIDATE

    return CustomHazardAction.ASK_CLARIFICATION


def _validation_thresholds(validation_mode: str) -> dict[str, int]:
    return VALIDATION_THRESHOLDS.get(
        str(validation_mode or "").strip().casefold(),
        VALIDATION_THRESHOLDS["strict"],
    )


def _dimension_needs_clarification(dimensions: dict[str, Any], key: str) -> bool:
    item = dimensions.get(key) if isinstance(dimensions, dict) else {}
    if not isinstance(item, dict):
        return False
    return bool(item.get("needs_clarification")) and bool(
        str(item.get("clarification_question") or "").strip()
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
            "energy efficiency", "utility", "transmission", "distribution",
            "flexibility", "balancing", "demand response", "prosumer",
            "grid congestion", "energy community", "clean heating",
        },
        "housing": {
            "housing", "house", "home", "homes", "building",
            "buildings", "dwelling", "apartment", "flat", "retrofit",
            "renovation", "insulation", "heat pump", "smart home",
            "tenant", "homeowner", "residential", "landlord", "rent",
            "building renovation", "energy performance", "epc",
        },
        "transport": {
            "transport", "mobility", "vehicle", "vehicles",
            "electric vehicle", "ev", "charging", "charging infrastructure",
            "home charging", "public transport", "bus", "rail", "train",
            "taxi", "traffic", "logistics", "commuter", "commuters",
            "low emission zone", "clean vehicle", "active travel",
        },
    }
    for key, values in mapping.items():
        if key in lower:
            return values
    return set(lower.split())


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


def _contains_any_term(value: str, terms: set[str] | tuple[str, ...]) -> bool:
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


def _dimension_score(dimensions: dict[str, Any], key: str) -> int:
    item = dimensions.get(key) if isinstance(dimensions, dict) else {}
    return _clamp_score(item.get("score") if isinstance(item, dict) else 0)


def _overall_confidence(result: dict[str, Any]) -> ConfidenceLevel:
    score_confidence = _confidence_for_percent(int(result.get("overall_score") or 0))
    dimensions = result.get("dimension_scores", {})
    dimension_confidences = [
        _coerce_confidence(item.get("confidence"))
        for item in dimensions.values()
        if isinstance(item, dict)
    ]
    if any(confidence == ConfidenceLevel.LOW for confidence in dimension_confidences):
        return ConfidenceLevel.LOW if score_confidence != ConfidenceLevel.HIGH else ConfidenceLevel.MEDIUM
    if any(confidence == ConfidenceLevel.MEDIUM for confidence in dimension_confidences):
        return ConfidenceLevel.MEDIUM
    return score_confidence


def _merge_llm_with_heuristic_guardrails(
    result: dict[str, Any],
    heuristic: dict[str, Any],
) -> dict[str, Any]:
    dimensions = result.setdefault("dimension_scores", {})
    heuristic_dimensions = heuristic.get("dimension_scores", {})

    # If deterministic checks strongly indicate benefit/mitigation/not-a-hazard,
    # do not let an over-helpful LLM mark hazard definition as ready.
    heuristic_hazard_score = _dimension_score(heuristic_dimensions, "hazard_definition_fit")
    if heuristic_hazard_score < 5:
        item = dimensions.get("hazard_definition_fit")
        if isinstance(item, dict) and _clamp_score(item.get("score")) > SCORE_PARTIAL:
            item["score"] = SCORE_PARTIAL
            item["needs_clarification"] = True
            item["clarification_question"] = (
                item.get("clarification_question")
                or "Can you describe the negative impact, risk, or harm caused by this issue?"
            )
            item["reason"] = (
                item.get("reason")
                or "The text may describe a benefit, mitigation, fact, or observation rather than a hazard."
            )

    for group in heuristic.get("affected_groups", []):
        if isinstance(group, dict):
            result.setdefault("affected_groups", []).append(group)
    return result


def _reset_duplicate_override_if_hazard_changed(
    state: dict[str, Any],
    hazard_text: str,
) -> dict[str, Any]:
    previous_text = str(state.get("raw_text") or "").strip()
    current_text = _hazard_title_from_grounding_text(hazard_text)
    if (
        previous_text
        and normalize_for_match(previous_text) != normalize_for_match(current_text)
        and state.get("duplicate_override_confirmed")
    ):
        state = dict(state)
        state["duplicate_override_confirmed"] = False
        state["duplicate_candidates"] = []
    return state


def _hazard_title_from_grounding_text(hazard_text: str) -> str:
    for line in str(hazard_text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return str(hazard_text or "").strip()
