import json
import re
from typing import Any

from app.llm import ask_llm_chat
from app.config import get_settings
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
    CustomHazardDimension.POLICY_OBJECTIVE_FIT.value: 0.15,
    CustomHazardDimension.MECHANISM_FIT.value: 0.20,
    # Hazard definition is the foundation. A policy/sector match is not enough
    # if the input is actually a benefit, mitigation, neutral fact, or question.
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value: 0.25,
    CustomHazardDimension.SELECTED_SECTOR_FIT.value: 0.15,
    CustomHazardDimension.COUNTRY_REGION_FIT.value: 0.10,
    CustomHazardDimension.AFFECTED_GROUPS_FIT.value: 0.15,
}

DIMENSION_SEQUENCE = tuple(DIMENSION_WEIGHTS)

DIMENSION_STAGES = (
    (CustomHazardDimension.POLICY_OBJECTIVE_FIT.value,),
    (CustomHazardDimension.MECHANISM_FIT.value,),
    (
        CustomHazardDimension.HAZARD_DEFINITION_FIT.value,
        CustomHazardDimension.SELECTED_SECTOR_FIT.value,
        CustomHazardDimension.COUNTRY_REGION_FIT.value,
    ),
    (CustomHazardDimension.AFFECTED_GROUPS_FIT.value,),
)

CRITICAL_DIMENSIONS = (
    CustomHazardDimension.POLICY_OBJECTIVE_FIT.value,
    CustomHazardDimension.MECHANISM_FIT.value,
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value,
    CustomHazardDimension.SELECTED_SECTOR_FIT.value,
    CustomHazardDimension.COUNTRY_REGION_FIT.value,
)

DIMENSION_TITLES = {
    CustomHazardDimension.POLICY_OBJECTIVE_FIT.value: "Policy Objective Fit",
    CustomHazardDimension.MECHANISM_FIT.value: "Mechanism Fit",
    CustomHazardDimension.HAZARD_DEFINITION_FIT.value: "Hazard definition",
    CustomHazardDimension.SELECTED_SECTOR_FIT.value: "Sector fit",
    CustomHazardDimension.COUNTRY_REGION_FIT.value: "Country / region fit",
    CustomHazardDimension.AFFECTED_GROUPS_FIT.value: "Affected population groups",
}

SECTOR_POLICY_OBJECTIVES = {
    "energy": "Transition towards renewable energy",
    "housing": "Adaptation of housing to climate change",
    "transport": "Shift to Sustainable Mobility",
}

CLARIFICATION_IMPROVEMENT_THRESHOLD = 3

SCORE_STRONG = 8
SCORE_PARTIAL = 6
SCORE_WEAK = 4
SCORE_POOR = 3


async def suggest_custom_hazard_mechanisms(
    hazard: str,
    sector: str,
    objective: str,
) -> list[str]:
    """Return concise candidate causal mechanisms without claiming they are proven."""
    payload = {
        "hazard": str(hazard or "").strip(),
        "selected_sector": str(sector or "").strip(),
        "policy_objective": str(objective or "").strip(),
        "required_output_schema": {"mechanisms": ["concise causal mechanism"]},
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_mechanism_suggestion.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=350,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    values = result.get("mechanisms") if isinstance(result, dict) else []
    mechanisms: list[str] = []
    for value in values if isinstance(values, list) else []:
        item = re.sub(r"\s+", " ", str(value or "")).strip(" .-•")[:220]
        if item and normalize_for_match(item) not in {
            normalize_for_match(existing) for existing in mechanisms
        }:
            mechanisms.append(item)
    return mechanisms[:3]


async def validate_hazard_evidence_relevance(
    hazard: str,
    evidence_context: str,
) -> dict[str, Any]:
    content = str(evidence_context or "").strip()
    if not content:
        return {"relevant": False, "reason": "No readable evidence content was supplied."}
    payload = {
        "hazard": str(hazard or "").strip(),
        "evidence_content": content[:24000],
        "required_output_schema": {"relevant": False, "reason": "", "causal_linkage": ""},
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_evidence_relevance.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=350,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if isinstance(result, dict) and isinstance(result.get("relevant"), bool):
        return {
            "relevant": result["relevant"],
            "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:600],
            "causal_linkage": re.sub(
                r"\s+", " ", str(result.get("causal_linkage") or "")
            ).strip()[:1200],
        }
    hazard_terms = set(normalize_for_match(hazard).split()) - {
        "the", "and", "for", "with", "from"
    }
    evidence_terms = set(normalize_for_match(content).split())
    overlap = hazard_terms & evidence_terms
    return {
        "relevant": len(overlap) >= 2,
        "reason": (
            "The evidence shares material hazard concepts."
            if len(overlap) >= 2
            else "The evidence does not clearly address the stated hazard."
        ),
        "causal_linkage": "",
    }


async def reflect_on_custom_hazard_kb_evidence(
    hazard: str,
    reason: str,
    evidence_context: str,
) -> dict[str, Any]:
    """Summarize only KB evidence that materially supports the proposed hazard."""
    content = str(evidence_context or "").strip()
    if not content:
        return {"supported": False, "reflection": "", "relationship": "", "reason": ""}
    payload = {
        "hazard": str(hazard or "").strip(),
        "hazard_reason": str(reason or "").strip(),
        "knowledge_base_evidence": content[:24000],
        "required_output_schema": {
            "supported": False,
            "reflection": "",
            "relationship": "",
            "reason": "",
        },
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_evidence_reflection.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=500,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("supported"), bool):
        return {"supported": False, "reflection": "", "relationship": "", "reason": ""}
    return {
        "supported": result["supported"],
        "reflection": re.sub(r"\s+", " ", str(result.get("reflection") or "")).strip()[:1200],
        "relationship": re.sub(r"\s+", " ", str(result.get("relationship") or "")).strip()[:1200],
        "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:600],
    }


async def validate_custom_hazard_evidence_reflection(
    hazard: str,
    reflection: str,
    evidence_context: str,
) -> dict[str, Any]:
    """Check whether a user's alternative reflection is supported by retrieved KB evidence."""
    content = str(evidence_context or "").strip()
    if not content:
        return {
            "supported": False,
            "acknowledgement": "",
            "relationship": "",
            "reason": "No supporting knowledge-base evidence was found for that reflection.",
        }
    payload = {
        "hazard": str(hazard or "").strip(),
        "user_reflection": str(reflection or "").strip(),
        "knowledge_base_evidence": content[:24000],
        "required_output_schema": {
            "supported": False,
            "acknowledgement": "",
            "relationship": "",
            "reason": "",
        },
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file(
                "llm/custom_hazard_evidence_reflection_validation.txt"
            ),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=450,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("supported"), bool):
        return {
            "supported": False,
            "acknowledgement": "",
            "relationship": "",
            "reason": "The reflection could not be validated against the available evidence.",
        }
    return {
        "supported": result["supported"],
        "acknowledgement": re.sub(
            r"\s+", " ", str(result.get("acknowledgement") or "")
        ).strip()[:800],
        "relationship": re.sub(
            r"\s+", " ", str(result.get("relationship") or "")
        ).strip()[:1200],
        "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:600],
    }


async def validate_custom_hazard_mechanism_linkage(
    hazard: str,
    mechanism: str,
    source_context: str,
    source_label: str,
) -> dict[str, Any]:
    content = str(source_context or "").strip()
    if not content:
        return {
            "supported": False,
            "reason": f"No relevant {source_label} text was found.",
            "causal_linkage": "",
        }
    payload = {
        "hazard": str(hazard or "").strip(),
        "mechanism": str(mechanism or "").strip(),
        "source_label": source_label,
        "source_content": content[:24000],
        "required_output_schema": {"supported": False, "reason": "", "causal_linkage": ""},
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_mechanism_linkage.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=450,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if isinstance(result, dict) and isinstance(result.get("supported"), bool):
        return {
            "supported": result["supported"],
            "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:600],
            "causal_linkage": re.sub(
                r"\s+", " ", str(result.get("causal_linkage") or "")
            ).strip()[:1200],
        }
    mechanism_terms = set(normalize_for_match(mechanism).split()) - {
        "the", "and", "for", "with", "from"
    }
    source_terms = set(normalize_for_match(content).split())
    supported = len(mechanism_terms & source_terms) >= 2
    return {
        "supported": supported,
        "reason": (
            "The source contains the proposed mechanism."
            if supported
            else f"The {source_label} text does not support the proposed mechanism."
        ),
        "causal_linkage": (
            f"{source_label} finding -> {mechanism} -> {hazard}" if supported else ""
        ),
    }


async def summarize_custom_hazard_supporting_policy(
    hazard: str,
    mechanism: str,
    policy_context: str,
    *,
    relevance_clarification: str = "",
) -> dict[str, Any]:
    """Validate and summarize policy support for a mechanism-to-hazard pathway."""
    content = str(policy_context or "").strip()
    if not content:
        return {
            "supported": False,
            "summary": "",
            "policy_details": "",
            "reason": "No readable supporting policy details were found.",
            "causal_linkage": "",
        }
    payload = {
        "hazard": str(hazard or "").strip(),
        "mechanism": str(mechanism or "").strip(),
        "policy_content": content[:24000],
        "user_relevance_clarification": str(relevance_clarification or "").strip(),
        "required_output_schema": {
            "supported": False,
            "summary": "",
            "policy_details": "",
            "reason": "",
            "causal_linkage": "",
        },
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_policy_support_summary.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=600,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("supported"), bool):
        linkage = await validate_custom_hazard_mechanism_linkage(
            hazard, mechanism, content, "policy"
        )
        return {
            "supported": bool(linkage.get("supported")),
            "summary": str(linkage.get("reason") or "").strip(),
            "policy_details": "",
            "reason": str(linkage.get("reason") or "").strip(),
            "causal_linkage": str(linkage.get("causal_linkage") or "").strip(),
        }
    return {
        "supported": result["supported"],
        "summary": re.sub(r"\s+", " ", str(result.get("summary") or "")).strip()[:1200],
        "policy_details": re.sub(
            r"\s+", " ", str(result.get("policy_details") or "")
        ).strip()[:1600],
        "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:700],
        "causal_linkage": re.sub(
            r"\s+", " ", str(result.get("causal_linkage") or "")
        ).strip()[:1400],
    }


async def assess_custom_hazard_mechanism_clarity(
    hazard: str,
    mechanism: str,
) -> dict[str, Any]:
    value = re.sub(r"\s+", " ", str(mechanism or "")).strip()
    if len(value.split()) < 3:
        return {
            "clear": False,
            "reason": (
                "Please describe the process or change that produces the hazard, "
                "not only a broad topic."
            ),
        }
    payload = {
        "hazard": str(hazard or "").strip(),
        "mechanism": value,
        "required_output_schema": {"clear": False, "reason": ""},
    }
    try:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/custom_hazard_mechanism_clarity.txt"),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=220,
        )
        result = parse_json_object(response)
    except Exception:
        result = None
    if isinstance(result, dict) and isinstance(result.get("clear"), bool):
        return {
            "clear": result["clear"],
            "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:500],
        }
    return {"clear": True, "reason": ""}


async def validate_policy_reference_twin_transition(
    policy_reference_context: str,
) -> dict[str, Any] | None:
    """Classify the document itself before using it as a policy reference."""
    content = str(policy_reference_context or "").strip()
    if not content:
        return {
            "related": False,
            "reason": "No readable policy content was supplied.",
        }

    system = load_nested_prompt_file(
        "llm/policy_reference_twin_transition_validation.txt"
    )
    user = json.dumps(
        {
            "policy_reference_content": content[:24000],
            "required_output_schema": {
                "related": False,
                "reason": "",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        response = await ask_llm_chat(
            context=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=300,
        )
    except Exception:
        return _heuristic_policy_reference_twin_transition(content)

    if is_llm_unavailable_response(response):
        return _heuristic_policy_reference_twin_transition(content)
    result = parse_json_object(response)
    if not isinstance(result, dict) or not isinstance(result.get("related"), bool):
        return _heuristic_policy_reference_twin_transition(content)
    return {
        "related": result["related"],
        "reason": re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()[:500],
    }


def _heuristic_policy_reference_twin_transition(content: str) -> dict[str, Any]:
    normalized = normalize_for_match(content)
    transition_terms = {
        "green transition",
        "digital transition",
        "twin transition",
        "climate change",
        "climate adaptation",
        "climate mitigation",
        "decarbonisation",
        "decarbonization",
        "renewable energy",
        "clean energy",
        "energy efficiency",
        "energy transition",
        "circular economy",
        "sustainable housing",
        "sustainable transport",
        "electric vehicle",
        "zero emission",
        "digitalisation",
        "digitalization",
        "digital transformation",
        "artificial intelligence",
        "data governance",
        "digital public service",
        "broadband",
        "automation",
        "smart grid",
    }
    policy_terms = {
        "policy",
        "regulation",
        "regulatory",
        "directive",
        "legislation",
        "law",
        "strategy",
        "programme",
        "program",
        "action plan",
        "implementation",
        "requirement",
        "mandate",
        "target",
        "subsidy",
        "governance",
    }
    related = _contains_any_term(normalized, transition_terms) and _contains_any_term(
        normalized,
        policy_terms,
    )
    return {
        "related": related,
        "reason": (
            "The readable content contains both transition and policy signals."
            if related
            else "The readable content does not establish a green, digital, or twin-transition policy context."
        ),
    }


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
        "generated_title": "",
        "generated_summary": "",
        "summary_confirmed": False,
        "summary_revision_history": [],
        "transition_link": None,
        "policy_reference": "",
        "policy_reference_document_ids": [],
        "pending_policy_reference": "",
        "pending_policy_reference_document_ids": [],
        "pending_policy_reference_context": "",
        "policy_reference_available": False,
        "replacing_policy_reference": False,
        "suggested_mechanisms": [],
        "selected_mechanism": "",
        "mechanism_source": "",
        "mechanism_confirmed": False,
        "mechanism_causal_linkage": "",
        "causal_linkage_confirmed": False,
        "mechanism_knowledge_context": "",
        "supporting_policy_summary": "",
        "supporting_policy_details": "",
        "supporting_policy_sources": [],
        "pending_policy_linkage": {},
        "policy_reference_context": "",
        "policy_reference_relevance_pending": False,
        "awaiting_policy_relevance_clarification": False,
        "policy_summary_notice": "",
        "evidence_kb_checked": False,
        "evidence_kb_context": "",
        "evidence_kb_sources": [],
        "evidence_reflection": "",
        "evidence_reflection_confirmed": False,
        "evidence_user_reflection": "",
        "evidence_relationship_notice": "",
        "evidence_relevance_checked": False,
        "evidence_relevant": False,
        "objective_fit_reason": "",
        "linkage_analysis": {},
        "detected_sector": None,
        "negative_consequence": None,
        "validation_round": 0,
        "active_validation_dimension": CustomHazardDimension.POLICY_OBJECTIVE_FIT.value,
        "active_validation_dimensions": [
            CustomHazardDimension.POLICY_OBJECTIVE_FIT.value
        ],
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
        "validation_mode": "strict",
    }


async def validate_custom_hazard_dimensions(
    hazard_text: str,
    selected_sector: str,
    country: str,
    region: str,
    known_hazards: list[str],
    previous_state: dict[str, Any] | None,
    validation_mode: str = "strict",
    policy_reference_context: str = "",
    evidence_context: str = "",
    dimensions_to_validate: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    state = _merged_state(previous_state)
    requested_dimensions = _requested_dimensions(dimensions_to_validate)
    staged_validation = dimensions_to_validate is not None
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
        policy_reference_context,
        evidence_context,
    )
    if staged_validation:
        _limit_validation_to_dimensions(heuristic_result, requested_dimensions)

    llm_result = await _llm_dimension_validation(
        hazard_text,
        selected_sector,
        country,
        region,
        state,
        policy_reference_context,
        evidence_context,
        requested_dimensions,
    )
    result = (
        _coerce_validation_result(llm_result, requested_dimensions)
        if llm_result
        else None
    )
    if result is None:
        result = heuristic_result
    else:
        result = _merge_llm_with_heuristic_guardrails(result, heuristic_result)
    if (
        CustomHazardDimension.POLICY_OBJECTIVE_FIT.value in requested_dimensions
        and _objective_result_requests_policy_reference(result)
    ):
        heuristic_objective = heuristic_result.get("dimension_scores", {}).get(
            CustomHazardDimension.POLICY_OBJECTIVE_FIT.value
        )
        if isinstance(heuristic_objective, dict):
            result.setdefault("dimension_scores", {})[
                CustomHazardDimension.POLICY_OBJECTIVE_FIT.value
            ] = heuristic_objective
    _enforce_linkage_content_guardrails(
        result,
        has_policy_reference=bool(policy_reference_context.strip()),
        has_evidence=bool(evidence_context.strip()),
    )

    if (
        CustomHazardDimension.MECHANISM_FIT.value
        in requested_dimensions
        and not policy_reference_context.strip()
        and not bool(state.get("causal_linkage_confirmed"))
    ):
        result.setdefault("dimension_scores", {})[
            CustomHazardDimension.MECHANISM_FIT.value
        ] = _score_payload(
            0,
            "A supported mechanism has not yet been confirmed.",
            "Confirm a suggested mechanism or provide the mechanism that causes or worsens the hazard.",
        )

    if staged_validation:
        current_dimensions = state.get("dimension_scores")
        merged_dimensions = (
            dict(current_dimensions) if isinstance(current_dimensions, dict) else {}
        )
        minimum_score = _validation_thresholds(validation_mode)["dimension_floor"]
        for key, item in (result.get("dimension_scores") or {}).items():
            current_item = merged_dimensions.get(key)
            # Staged follow-up passes (especially evidence-linkage analysis)
            # may inspect all context again, but a completed dimension is an
            # established workflow decision. Only an explicit state reset,
            # such as replacing the policy reference, may make it mutable.
            if _dimension_result_is_supported(current_item, minimum_score):
                continue
            merged_dimensions[key] = item
        for key in DIMENSION_SEQUENCE:
            merged_dimensions.setdefault(key, _deferred_dimension())
        result["dimension_scores"] = merged_dimensions

    _ensure_dimension_reasons(result)

    result["duplicate_candidates"] = _duplicate_candidates(
        hazard_text,
        known_hazards,
        result.get("duplicate_candidates", []),
    )
    dimensions = result.setdefault("dimension_scores", {})
    core_supported = _core_dimensions_supported(
        dimensions,
        _validation_thresholds(validation_mode)["dimension_floor"],
    )
    should_evaluate_groups = (
        not staged_validation
        or CustomHazardDimension.AFFECTED_GROUPS_FIT.value in requested_dimensions
    )
    if core_supported and should_evaluate_groups:
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
                *[
                    group
                    for group in state.get("affected_groups", [])
                    if isinstance(group, dict)
                ],
                *[
                    group
                    for group in result.get("affected_groups", [])
                    if isinstance(group, dict)
                ],
            ]
        )

        # Keep the affected-groups dimension consistent with the groups
        # actually extracted/coerced after LLM output is sanitized.
        group_dimension = dimensions.get("affected_groups_fit")
        if result["affected_groups"] and isinstance(group_dimension, dict):
            group_dimension.update(
                _score_payload(
                    max(SCORE_PARTIAL, _clamp_score(group_dimension.get("score"))),
                    "Qualified affected population groups were identified after normalization.",
                    "",
                )
            )
        elif not result["affected_groups"]:
            dimensions["affected_groups_fit"] = _score_payload(
                SCORE_WEAK,
                "No specific affected population group was identified in the submitted information.",
                "Which specific population groups are affected by this hazard, and why?",
            )
        # Extraction establishes candidate groups, not user approval. Only the
        # affected-groups review handler may populate confirmed_affected_groups.
    elif not core_supported:
        # Affected populations are deliberately evaluated only after the five
        # mandatory grounding dimensions have passed.
        result["affected_groups"] = []
        deferred_group_score = _dimension_score(dimensions, "affected_groups_fit")
        dimensions["affected_groups_fit"] = {
            **_score_payload(
                deferred_group_score,
                "Affected population groups will be checked after all mandatory dimensions are supported.",
                "",
            ),
            "needs_clarification": False,
            "clarification_question": "",
            "status": "DEFERRED",
        }
    else:
        result["affected_groups"] = list(state.get("affected_groups") or [])
        dimensions["affected_groups_fit"] = _deferred_dimension(
            "Affected population groups will be checked after the mandatory dimensions."
        )

    result["overall_score"] = _overall_score(result.get("dimension_scores", {}))
    result["confidence"] = _overall_confidence(result).value
    result["next_action"] = _recommended_action(result, state, validation_mode).value
    result["status"] = _status_for_action(result["next_action"]).value
    result["validation_mode"] = str(validation_mode or "strict").strip().casefold()
    result["raw_text"] = hazard_text
    result["normalized_text"] = normalize_for_match(hazard_text)
    return result


def build_custom_hazard_grounding_status(custom_hazard: dict[str, Any] | None) -> list[dict[str, Any]]:
    state = _merged_state(custom_hazard)
    dimension_scores = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
    dimension_floor = custom_hazard_dimension_floor(state.get("validation_mode"))
    cards = [
        _dimension_card("policy_objective_fit", dimension_scores, dimension_floor),
        _dimension_card("mechanism_fit", dimension_scores, dimension_floor),
        _dimension_card("hazard_definition_fit", dimension_scores, dimension_floor),
        _dimension_card("selected_sector_fit", dimension_scores, dimension_floor),
        _dimension_card("country_region_fit", dimension_scores, dimension_floor),
        _duplicate_card(state),
        _affected_groups_card(state, dimension_floor),
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
        "policy_reference": state.get("policy_reference") or "",
        "linkage_analysis": state.get("linkage_analysis") or {},
        "suggested_mechanisms": state.get("suggested_mechanisms") or [],
        "selected_mechanism": state.get("selected_mechanism") or "",
        "mechanism_source": state.get("mechanism_source") or "",
        "mechanism_causal_linkage": state.get("mechanism_causal_linkage") or "",
        "causal_linkage_confirmed": bool(state.get("causal_linkage_confirmed")),
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
    policy_reference_context: str = "",
    evidence_context: str = "",
    requested_dimensions: tuple[str, ...] = DIMENSION_SEQUENCE,
) -> dict[str, Any] | None:
    hazard_text = (hazard_text or "").strip()
    selected_sector = (selected_sector or "").strip()
    country = (country or "").strip()
    region = (region or "").strip()
    policy_objective = policy_objective_for_sector(selected_sector)

    if not hazard_text:
        return None

    system = load_nested_prompt_file("llm/custom_hazard_dimension_validation.txt")
    requested_labels = ", ".join(
        DIMENSION_TITLES[key] for key in requested_dimensions
    )
    objective_only = requested_dimensions == (
        CustomHazardDimension.POLICY_OBJECTIVE_FIT.value,
    )
    if objective_only:
        system += f"""

Policy-objective validation stage:
- Evaluate only this requested dimension: {requested_labels}.
- Compare the submitted hazard directly with the supplied predefined sector policy objective and the selected country, region, and sector context.
- Decide whether the hazard is a plausible adverse consequence of pursuing that objective. The hazard need not repeat the objective verbatim.
- The predefined sector objective is authoritative application context for this stage. No policy document, policy reference, evidence source, URL, upload, or file path is required.
- Never request or mention missing policy-reference content.
- Do not evaluate document-level causal linkage, twin-transition policy fit, hazard definition, sector fit, country fit, or affected groups in this call.
- If clarification is required, ask only how pursuing the predefined sector policy objective could cause or worsen the submitted hazard.
"""
    else:
        system += f"""

Validation order and application-context rules:
- Evaluate only these requested dimensions in this call: {requested_labels}. Do not evaluate, score, or comment on any other dimension.
- Across calls, the workflow evaluates policy objective fit, mechanism fit, hazard definition, selected sector fit, country fit, and affected groups in that order. Keep using the country_region_fit output key, but do not assess regional fit in that dimension.
- Policy objective fit is distinct from mechanism fit. Determine whether the hazard is a plausible adverse consequence of pursuing the supplied sector policy objective.
- For mechanism fit, assess the confirmed mechanism against knowledge-base excerpts or the supplied policy-reference content. Identify the causal linkage from the mechanism to the hazard and explicitly describe any mismatch.
- Populate mechanism_fit.causal_linkage only when the supplied source supports a defensible chain in the form "source finding or policy provision -> mechanism -> hazard impact"; otherwise leave it empty.
- The policy reference is context for this dimension only. Never treat it as evidence that the hazard occurred, is prevalent, or affects a population.
- When evidence content is supplied, separately assess whether it supports the hazard and whether the policy document has a defensible causal connection to that evidence.
- Populate evidence_hazard_linkage only for a chain grounded in the evidence content: "evidence finding -> supported impact -> hazard".
- Populate policy_evidence_linkage only for a chain grounded across both documents: "policy provision -> intermediate mechanism -> evidence finding".
- Do not infer support from filenames, URLs, topic similarity, or the user's assertion alone. If a chain is not supported, set supported to false, leave causal_linkage empty, and briefly explain the missing link.
- A policy document is required only for a user-provided mechanism when no supporting knowledge-base text is available.
- A hazard need not repeat the policy objective verbatim, but its causal mechanism must be compatible with that objective.
- The selected country and sector are application context. Do not ask the user to reconfirm them merely because their names are absent from the hazard text. The selected region may inform other regional context, but it must not affect country_region_fit.
- Use the hazard's meaning and the supplied application context to assess sector and location fit. Ask only when there is a substantive ambiguity or contradiction.
- Only after all five mandatory dimensions are supported, evaluate and extract affected population groups.
- If the mandatory dimensions are supported and no specific affected group can be extracted, ask the user for one.
"""

    payload = {
        "selected_country": country,
        "selected_region": region,
        "selected_sector": selected_sector,
        "selected_sector_policy_objective": policy_objective,
        "custom_hazard_text": hazard_text,
        "hazard_statement": _hazard_title_from_grounding_text(hazard_text),
        "previous_clarifications": state.get("clarifications", []),
        "current_affected_groups": state.get("affected_groups", []),
        "dimensions": list(requested_dimensions),
        "scoring": {
            "score_range": "0-10 per dimension",
            "weights": {
                key: DIMENSION_WEIGHTS[key] for key in requested_dimensions
            },
            "overall_score": "weighted score converted to 0-100",
        },
        "required_output_schema": {
            "overall_score": 0,
            "linkage_analysis": {
                "evidence_hazard_linkage": {
                    "supported": False,
                    "causal_linkage": "",
                    "reason": "",
                },
                "policy_evidence_linkage": {
                    "supported": False,
                    "causal_linkage": "",
                    "reason": "",
                },
            },
            "dimension_scores": {
                "policy_objective_fit": {
                    "score": 0,
                    "reason": "",
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "mechanism_fit": {
                    "score": 0,
                    "reason": "",
                    "causal_linkage": "",
                    "confidence": "low | medium | high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "hazard_definition_fit": {
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
    if not objective_only:
        payload["policy_reference_content"] = (
            policy_reference_context[:24000] or "Not provided"
        )
        payload["evidence_content"] = evidence_context[:24000] or "Not provided"
    schema_dimensions = payload["required_output_schema"]["dimension_scores"]
    payload["required_output_schema"]["dimension_scores"] = {
        key: schema_dimensions[key] for key in requested_dimensions
    }
    if (
        CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value
        not in requested_dimensions
        and not evidence_context.strip()
    ):
        payload["required_output_schema"].pop("linkage_analysis", None)

    user = render_prompt_template(
        "llm/custom_hazard_dimension_validation_user.txt",
        payload=json.dumps(payload, ensure_ascii=False, indent=2),
    )

    try:
        response = await ask_llm_chat(
            context=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=2200,
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
    policy_reference_context: str = "",
    evidence_context: str = "",
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
    policy_reference_lower = normalize_for_match(policy_reference_context)
    evidence_lower = normalize_for_match(evidence_context)
    policy_reference_has_transition = _contains_any_term(
        policy_reference_lower,
        transition_terms,
    )
    hazard_policy_terms = {
        term for term in transition_terms if _contains_any_term(lower, {term})
    }
    shared_policy_terms = {
        term
        for term in hazard_policy_terms
        if _contains_any_term(policy_reference_lower, {term})
    }
    confirmed_mechanism = str(state.get("selected_mechanism") or "").strip()
    confirmed_linkage = str(state.get("mechanism_causal_linkage") or "").strip()
    if state.get("causal_linkage_confirmed") and confirmed_mechanism and confirmed_linkage:
        policy_score = SCORE_STRONG
        policy_causal_linkage = confirmed_linkage
        policy_reason = "The source-supported mechanism and causal linkage were confirmed by the user."
    elif not policy_reference_lower:
        policy_score = 0
        policy_causal_linkage = ""
        policy_reason = (
            "A source-supported causal mechanism has not yet been confirmed."
        )
    elif shared_policy_terms:
        policy_score = SCORE_STRONG
        shared_mechanisms = ", ".join(sorted(shared_policy_terms)[:4])
        hazard_summary = str(hazard_text or "").splitlines()[0].strip()
        policy_causal_linkage = (
            f"Policy provisions concerning {shared_mechanisms} activate the transition "
            f"mechanism described in the hazard, which can cause or worsen: {hazard_summary}"
        )
        policy_reason = (
            "The supplied policy reference and hazard share a plausible transition mechanism "
            f"({shared_mechanisms}); no clear policy-hazard mismatch was detected by the fallback analysis."
        )
    elif policy_reference_has_transition and _contains_any_term(lower, transition_terms):
        policy_score = SCORE_PARTIAL
        policy_causal_linkage = ""
        policy_reason = (
            "Both texts concern the twin transition, but the fallback analysis found only an indirect causal linkage and a possible policy-hazard mismatch."
        )
    else:
        policy_score = SCORE_WEAK
        policy_causal_linkage = ""
        policy_reason = (
            "The supplied document does not provide a clear causal linkage between its policy provisions and this hazard; a policy-hazard mismatch is likely."
        )

    policy_objective = policy_objective_for_sector(selected_sector)
    objective_terms = _sector_policy_objective_terms(selected_sector)
    objective_score = SCORE_STRONG if _contains_any_term(lower, objective_terms) else SCORE_WEAK

    # Country is selected application context and need not be repeated in the
    # hazard text. Region is intentionally excluded from this fit dimension.
    country_score = SCORE_STRONG if country.strip() else SCORE_WEAK

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

    mechanism_fit = _score_payload(
        policy_score,
        policy_reason,
        "Confirm an AI-suggested mechanism or provide a specific mechanism for this hazard."
        if not policy_reference_lower
        else "How does the proposed mechanism connect the policy provisions to this hazard?",
    )
    mechanism_fit["causal_linkage"] = policy_causal_linkage

    linkage_analysis = _heuristic_linkage_analysis(
        hazard_text,
        policy_reference_lower,
        evidence_lower,
        transition_terms,
    )

    return {
        "linkage_analysis": linkage_analysis,
        "dimension_scores": {
            "policy_objective_fit": _score_payload(
                objective_score,
                f"The hazard is plausibly linked to the sector policy objective: {policy_objective}."
                if objective_score >= 5
                else f"The hazard is not clearly linked to the sector policy objective: {policy_objective}.",
                f"How could pursuing the policy objective '{policy_objective}' cause or worsen this hazard?",
            ),
            "mechanism_fit": mechanism_fit,
            "hazard_definition_fit": _score_payload(
                hazard_definition_score,
                "The input describes a negative impact, risk, or burden."
                if hazard_definition_score >= 5
                else "The input appears to describe a benefit, mitigation, fact, or observation rather than a hazard.",
                "Can you describe the negative impact, risk, or harm caused by this issue?",
            ),
            "selected_sector_fit": _score_payload(
                sector_score,
                "The hazard appears connected to the selected sector."
                if sector_score >= 5
                else "The hazard is not clearly connected to the selected sector.",
                f"Can you explain how this hazard is connected to the selected sector: {selected_sector}?",
            ),
            "country_region_fit": _score_payload(
                country_score,
                "The selected country provides the application context for this hazard."
                if country_score >= 5
                else "A selected country is required to assess where this hazard applies.",
                "Why is this hazard relevant to the selected country?",
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


def _heuristic_linkage_analysis(
    hazard_text: str,
    policy_reference: str,
    evidence: str,
    transition_terms: set[str],
) -> dict[str, dict[str, object]]:
    hazard_statement = _hazard_title_from_grounding_text(hazard_text)
    hazard_key = normalize_for_match(hazard_statement)
    stopwords = {
        "this", "that", "with", "from", "into", "have", "will", "would",
        "could", "should", "their", "there", "about", "selected", "hazard",
        "evidence", "policy", "reason", "because", "through", "which",
    }

    def meaningful_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", normalize_for_match(value))
            if len(token) >= 4 and token not in stopwords
        }

    hazard_tokens = meaningful_tokens(hazard_key)
    evidence_tokens = meaningful_tokens(evidence)
    shared_hazard_terms = sorted(hazard_tokens & evidence_tokens)
    impact_terms = {
        "increase", "increased", "higher", "cost", "costs", "loss", "risk",
        "harm", "burden", "exclusion", "shortage", "disruption", "delay",
        "unaffordable", "penalty", "reduced", "decline", "affected",
    }
    evidence_has_impact = _contains_any_term(evidence, impact_terms)
    evidence_hazard_supported = bool(
        evidence and len(shared_hazard_terms) >= 2 and evidence_has_impact
    )
    if evidence_hazard_supported:
        evidence_hazard_linkage = {
            "supported": True,
            "causal_linkage": (
                f"Evidence finding concerning {', '.join(shared_hazard_terms[:5])} -> "
                f"documented adverse impact -> {hazard_statement}"
            ),
            "reason": "The evidence and hazard share a specific adverse-impact mechanism.",
        }
    else:
        evidence_hazard_linkage = {
            "supported": False,
            "causal_linkage": "",
            "reason": (
                "The supplied evidence does not state a sufficiently specific finding "
                "that supports the hazard's adverse impact."
            ),
        }

    shared_policy_evidence_terms = sorted(
        term
        for term in transition_terms
        if _contains_any_term(policy_reference, {term})
        and _contains_any_term(evidence, {term})
    )
    causal_terms = {
        "because", "caused", "causes", "due to", "following", "increased",
        "increase", "led to", "resulted", "implementation", "requirement",
        "mandate", "effect", "impact",
    }
    policy_evidence_supported = bool(
        policy_reference
        and evidence
        and shared_policy_evidence_terms
        and _contains_any_term(evidence, causal_terms)
    )
    if policy_evidence_supported:
        policy_evidence_linkage = {
            "supported": True,
            "causal_linkage": (
                f"Policy provision concerning {', '.join(shared_policy_evidence_terms[:4])} -> "
                "implementation or market mechanism -> evidence finding supporting the hazard"
            ),
            "reason": "Both documents identify the same transition mechanism.",
        }
    else:
        policy_evidence_linkage = {
            "supported": False,
            "causal_linkage": "",
            "reason": (
                "The policy reference and evidence do not establish the same specific "
                "causal mechanism."
            ),
        }
    return {
        "evidence_hazard_linkage": evidence_hazard_linkage,
        "policy_evidence_linkage": policy_evidence_linkage,
    }


def _enforce_linkage_content_guardrails(
    result: dict[str, Any],
    *,
    has_policy_reference: bool,
    has_evidence: bool,
) -> None:
    analysis = result.setdefault("linkage_analysis", {})
    if not isinstance(analysis, dict):
        result["linkage_analysis"] = {}
        analysis = result["linkage_analysis"]
    if not has_evidence:
        analysis["evidence_hazard_linkage"] = {
            "supported": False,
            "causal_linkage": "",
            "reason": "No readable evidence content was supplied.",
        }
        analysis["policy_evidence_linkage"] = {
            "supported": False,
            "causal_linkage": "",
            "reason": "No readable evidence content was supplied.",
        }
    elif not has_policy_reference:
        analysis["policy_evidence_linkage"] = {
            "supported": False,
            "causal_linkage": "",
            "reason": "No readable policy-reference content was supplied.",
        }


def _merged_state(previous_state: dict[str, Any] | None) -> dict[str, Any]:
    state = default_custom_hazard_state()
    if isinstance(previous_state, dict):
        state.update(previous_state)
    dimensions = state.get("dimension_scores")
    if isinstance(dimensions, dict) and "twin_transition_policy_fit" in dimensions:
        dimensions.setdefault("mechanism_fit", dimensions.pop("twin_transition_policy_fit"))
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


def _coerce_validation_result(
    value: dict[str, Any] | None,
    requested_dimensions: tuple[str, ...] = DIMENSION_SEQUENCE,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dimensions = value.get("dimension_scores")
    if not isinstance(dimensions, dict):
        return None
    if "mechanism_fit" not in dimensions and "twin_transition_policy_fit" in dimensions:
        dimensions = {
            **dimensions,
            "mechanism_fit": dimensions["twin_transition_policy_fit"],
        }
    coerced = {
        "dimension_scores": {},
        "affected_groups": [],
        "duplicate_candidates": [],
        "linkage_analysis": {},
    }
    fallback_score = _fallback_dimension_score(dimensions)
    for key in requested_dimensions:
        item = dimensions.get(key)
        if not isinstance(item, dict):
            if key == CustomHazardDimension.POLICY_OBJECTIVE_FIT.value:
                item = {
                    "score": 0,
                    "reason": "The mandatory policy-objective dimension was not evaluated.",
                }
            else:
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
        coerced_item = {
            "score": score,
            "reason": str(item.get("reason") or "").strip(),
            "confidence": _coerce_confidence(item.get("confidence"), score).value,
            "needs_clarification": needs_clarification,
            "clarification_question": question if needs_clarification else "",
        }
        if key == CustomHazardDimension.TWIN_TRANSITION_POLICY_FIT.value:
            coerced_item["causal_linkage"] = re.sub(
                r"\s+",
                " ",
                str(item.get("causal_linkage") or ""),
            ).strip()[:1200]
        coerced["dimension_scores"][key] = coerced_item
    coerced["affected_groups"] = [
        _coerce_group(group)
        for group in value.get("affected_groups", [])
        if isinstance(group, dict) and _group_is_allowed(str(group.get("group") or ""))
    ]
    coerced["duplicate_candidates"] = [
        item for item in value.get("duplicate_candidates", []) if isinstance(item, dict)
    ]
    linkage_analysis = value.get("linkage_analysis")
    if isinstance(linkage_analysis, dict):
        for key in ("evidence_hazard_linkage", "policy_evidence_linkage"):
            item = linkage_analysis.get(key)
            if not isinstance(item, dict):
                continue
            causal_linkage = re.sub(
                r"\s+",
                " ",
                str(item.get("causal_linkage") or ""),
            ).strip()[:1600]
            supported = bool(item.get("supported")) and bool(causal_linkage)
            coerced["linkage_analysis"][key] = {
                "supported": supported,
                "causal_linkage": causal_linkage if supported else "",
                "reason": re.sub(
                    r"\s+",
                    " ",
                    str(item.get("reason") or ""),
                ).strip()[:800],
            }
    return coerced


def _requested_dimensions(
    dimensions: tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    if dimensions is None:
        return DIMENSION_SEQUENCE
    normalized = {
        "mechanism_fit" if str(key) == "twin_transition_policy_fit" else str(key)
        for key in dimensions
    }
    requested = tuple(key for key in DIMENSION_SEQUENCE if key in normalized)
    if not requested:
        raise ValueError("At least one recognized custom-hazard dimension is required.")
    return requested


def _limit_validation_to_dimensions(
    result: dict[str, Any],
    requested_dimensions: tuple[str, ...],
) -> None:
    dimensions = result.get("dimension_scores")
    if isinstance(dimensions, dict):
        result["dimension_scores"] = {
            key: dimensions[key]
            for key in requested_dimensions
            if key in dimensions
        }


def _deferred_dimension(
    reason: str = "This dimension has not been checked yet.",
) -> dict[str, Any]:
    return {
        "score": 0,
        "reason": reason,
        "confidence": ConfidenceLevel.LOW.value,
        "needs_clarification": False,
        "clarification_question": "",
        "status": "DEFERRED",
    }


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

    # The objective, transition, hazard, sector, and location dimensions must resolve
    # before the flow asks for reason/evidence or affected-group review.
    if critical_low or critical_needs_clarification:
        return CustomHazardAction.REJECT if flattened else CustomHazardAction.ASK_CLARIFICATION

    groups_low = _dimension_score(dimensions, "affected_groups_fit") < dimension_floor

    # Never mark ready while required dimensions are below the floor. Flattening
    # means the conversation stopped improving, not that the hazard became valid.
    if groups_low:
        return CustomHazardAction.REJECT if flattened else CustomHazardAction.ASK_CLARIFICATION

    if result.get("affected_groups") and not state.get("confirmed_affected_groups"):
        return CustomHazardAction.REVIEW_GROUPS

    if score >= ready_score or flattened or str(validation_mode or "").strip().casefold() == "easy":
        return CustomHazardAction.VALIDATE

    return CustomHazardAction.ASK_CLARIFICATION


def _validation_thresholds(validation_mode: str) -> dict[str, int]:
    thresholds = get_settings().custom_hazard_validation_thresholds
    return thresholds.get(
        str(validation_mode or "").strip().casefold(),
        thresholds["strict"],
    )


def custom_hazard_dimension_floor(validation_mode: object) -> int:
    return _validation_thresholds(str(validation_mode or "strict"))["dimension_floor"]


def _dimension_needs_clarification(dimensions: dict[str, Any], key: str) -> bool:
    item = dimensions.get(key) if isinstance(dimensions, dict) else {}
    if not isinstance(item, dict):
        return False
    return bool(item.get("needs_clarification")) and bool(
        str(item.get("clarification_question") or "").strip()
    )


def _core_dimensions_supported(dimensions: dict[str, Any], minimum_score: int) -> bool:
    return all(
        _dimension_result_is_supported(dimensions.get(key), minimum_score)
        for key in CRITICAL_DIMENSIONS
    )


def _dimension_result_is_supported(item: object, minimum_score: int) -> bool:
    if not isinstance(item, dict):
        return False
    status = str(item.get("status") or "").strip().upper()
    if status in {"REJECTED", "INSUFFICIENT INFO"}:
        return False
    return (
        _clamp_score(item.get("score")) >= minimum_score
        and not bool(item.get("needs_clarification"))
    )


def _ensure_dimension_reasons(result: dict[str, Any]) -> None:
    dimensions = result.get("dimension_scores")
    if not isinstance(dimensions, dict):
        return
    supported_reasons = {
        "hazard_definition_fit": "The submitted text describes a negative impact or risk.",
        "mechanism_fit": "The confirmed mechanism has a supported causal link to the hazard.",
        "policy_objective_fit": "The hazard is compatible with the selected sector's policy objective.",
        "selected_sector_fit": "The hazard is compatible with the selected sector.",
        "country_region_fit": "The selected country provides the application context for this hazard.",
        "affected_groups_fit": "A specific affected population group was identified.",
    }
    clarification_reasons = {
        "hazard_definition_fit": "The negative impact or risk is not yet explicit.",
        "mechanism_fit": "The causal mechanism producing or worsening the hazard is not yet clear.",
        "policy_objective_fit": "The link to the selected sector's policy objective is not yet clear.",
        "selected_sector_fit": "The relationship to the selected sector is not yet clear.",
        "country_region_fit": "The applicability to the selected country is not yet clear.",
        "affected_groups_fit": "No specific affected population group was identified.",
    }
    for key in DIMENSION_WEIGHTS:
        item = dimensions.get(key)
        if not isinstance(item, dict) or str(item.get("reason") or "").strip():
            continue
        item["reason"] = (
            supported_reasons[key]
            if _clamp_score(item.get("score")) >= 5 and not item.get("needs_clarification")
            else clarification_reasons[key]
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


def _dimension_card(
    key: str,
    dimensions: dict[str, Any],
    minimum_score: int,
) -> dict[str, Any]:
    item = dimensions.get(key) if isinstance(dimensions, dict) else {}
    raw_score = _clamp_score(item.get("score") if isinstance(item, dict) else 0)
    score = raw_score * 10
    needs = bool(item.get("needs_clarification")) if isinstance(item, dict) else True
    explicit_status = str(item.get("status") or "").strip().upper() if isinstance(item, dict) else ""
    below_floor = raw_score < minimum_score
    if explicit_status == "DEFERRED":
        status = "DEFERRED"
    elif explicit_status in {"REJECTED", "INSUFFICIENT INFO"}:
        status = explicit_status
    elif needs or below_floor:
        status = "NEEDS CLARIFICATION"
    else:
        status = explicit_status or "SUPPORTED"
    if raw_score == 0 and explicit_status != "DEFERRED":
        status = explicit_status or "INSUFFICIENT INFO"
    return {
        "title": DIMENSION_TITLES[key],
        "status": status,
        "score": None if explicit_status == "DEFERRED" else score,
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


def _affected_groups_card(state: dict[str, Any], minimum_score: int) -> dict[str, Any]:
    groups = state.get("confirmed_affected_groups") or state.get("affected_groups") or []
    dimensions = state.get("dimension_scores")
    core_supported = isinstance(dimensions, dict) and _core_dimensions_supported(
        dimensions,
        minimum_score,
    )
    group_dimension = (
        dimensions.get("affected_groups_fit") if isinstance(dimensions, dict) else {}
    )
    group_status = (
        str(group_dimension.get("status") or "").strip().upper()
        if isinstance(group_dimension, dict)
        else ""
    )
    group_supported = (
        isinstance(group_dimension, dict)
        and _dimension_score(dimensions, "affected_groups_fit") >= minimum_score
        and not group_dimension.get("needs_clarification")
        and group_status not in {"REJECTED", "INSUFFICIENT INFO", "DEFERRED"}
    )
    if not core_supported:
        reason = "This check will run after all mandatory dimensions are supported."
        status = GroundingStatus.WARNING.value
    elif not group_supported:
        reason = (
            str(group_dimension.get("reason") or "").strip()
            if isinstance(group_dimension, dict)
            else ""
        ) or "The affected-population dimension needs more support."
        status = GroundingStatus.NEEDS_CLARIFICATION.value
    elif groups:
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
        "confidence": (
            ConfidenceLevel.HIGH.value
            if core_supported and groups
            else ConfidenceLevel.LOW.value
        ),
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


def policy_objective_for_sector(sector: str) -> str:
    key = normalize_for_match(sector)
    for sector_key, objective in SECTOR_POLICY_OBJECTIVES.items():
        if sector_key in key:
            return objective
    return "the selected sector policy objective"


def _sector_policy_objective_terms(sector: str) -> set[str]:
    key = normalize_for_match(sector)
    mapping = {
        "energy": {
            "renewable", "renewable energy", "solar", "wind", "clean energy",
            "green transition", "energy transition", "grid modernization",
            "grid modernisation", "smart grid", "energy community",
        },
        "housing": {
            "adaptation", "climate adaptation", "climate change", "resilience",
            "climate resilience", "flood", "flooding", "heat", "overheating",
            "cooling", "retrofit", "renovation", "insulation", "climate proofing",
        },
        "transport": {
            "electric vehicle", "electric vehicles", "ev", "evs", "charging",
            "charging infrastructure", "vehicle electrification", "electrification",
            "clean vehicle", "low emission zone", "zero emission vehicle",
            "sustainable mobility", "public transport", "public transit", "transit",
            "active mobility", "walking", "cycling", "shared mobility", "rail",
            "bus", "buses", "modal shift", "mobility policy", "digital mobility",
        },
    }
    for sector_key, terms in mapping.items():
        if sector_key in key:
            return terms
    return set()


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
    linkage_analysis = result.setdefault("linkage_analysis", {})
    heuristic_linkages = heuristic.get("linkage_analysis", {})
    if isinstance(linkage_analysis, dict) and isinstance(heuristic_linkages, dict):
        for key in ("evidence_hazard_linkage", "policy_evidence_linkage"):
            if key not in linkage_analysis and isinstance(heuristic_linkages.get(key), dict):
                linkage_analysis[key] = heuristic_linkages[key]

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

    # Sector and place are supplied application context, and policy fit can be
    # established from explicit transition terminology. A small model sometimes
    # asks the user to reconfirm these values even when deterministic tool checks
    # already support them. Promote only generic/missing-detail responses; keep
    # substantive incompatibility findings from the LLM.
    for key in (
        "mechanism_fit",
        "policy_objective_fit",
        "selected_sector_fit",
        "country_region_fit",
    ):
        item = dimensions.get(key)
        heuristic_item = heuristic_dimensions.get(key)
        if (
            isinstance(item, dict)
            and isinstance(heuristic_item, dict)
            and _dimension_score(heuristic_dimensions, key) >= 5
            and _contextual_result_only_requests_reconfirmation(item)
        ):
            item.update(heuristic_item)

    for group in heuristic.get("affected_groups", []):
        if isinstance(group, dict):
            result.setdefault("affected_groups", []).append(group)
    return result


def _contextual_result_only_requests_reconfirmation(item: dict[str, Any]) -> bool:
    if _clamp_score(item.get("score")) >= 5 and not item.get("needs_clarification"):
        return False
    reason = normalize_for_match(str(item.get("reason") or ""))
    if not reason:
        return True
    conflict_terms = (
        "contradict",
        "incompatible",
        "incorrect sector",
        "wrong sector",
        "wrong country",
        "wrong region",
        "unrelated to",
        "outside the selected",
    )
    if any(term in reason for term in conflict_terms):
        return False
    missing_context_terms = (
        "not found",
        "not mentioned",
        "not explicitly",
        "missing",
        "does not name",
        "doesn't name",
        "cannot determine",
        "insufficient information",
        "required details",
    )
    return any(term in reason for term in missing_context_terms)


def _objective_result_requests_policy_reference(result: dict[str, Any]) -> bool:
    dimensions = result.get("dimension_scores")
    objective = (
        dimensions.get(CustomHazardDimension.POLICY_OBJECTIVE_FIT.value)
        if isinstance(dimensions, dict)
        else None
    )
    if not isinstance(objective, dict):
        return False
    response_text = normalize_for_match(
        " ".join(
            (
                str(objective.get("reason") or ""),
                str(objective.get("clarification_question") or ""),
            )
        )
    )
    forbidden_phrases = (
        "policy reference",
        "reference content",
        "policy document",
        "document url",
        "file path",
        "upload a file",
        "upload the file",
    )
    return any(phrase in response_text for phrase in forbidden_phrases)


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
