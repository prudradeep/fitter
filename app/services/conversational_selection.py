from __future__ import annotations

import json
import logging
from typing import Any

from app.llm import ask_llm_chat
from app.services.chat_json import parse_json_object
from app.services.chat_options import normalize
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.prompt_loader import load_nested_prompt_file

logger = logging.getLogger(__name__)

SelectionResult = dict[str, str | bool | None]


async def resolve_selection(
    user_text: str,
    available_countries: list[str] | None,
    available_regions: list[str] | None,
    available_sectors: list[str] | None,
    current_phase: str,
) -> SelectionResult:
    countries = _clean_options(available_countries)
    regions = _clean_options(available_regions)
    sectors = _clean_options(available_sectors)
    context = load_nested_prompt_file("llm/conversational_selection_resolver.txt")
    user_prompt = json.dumps(
        {
            "current_phase": current_phase,
            "available_countries": countries,
            "available_regions": regions,
            "available_sectors": sectors,
            "user_text": user_text,
        },
        ensure_ascii=False,
    )
    response = await ask_llm_chat(
        context=context,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.0,
        max_tokens=350,
    )
    if is_llm_unavailable_response(response):
        return _no_match("LLM unavailable.")

    parsed = parse_json_object(response)
    if not isinstance(parsed, dict):
        return _no_match("Resolver did not return valid JSON.")

    country = _validated_option(parsed.get("country"), countries)
    region = _validated_option(parsed.get("region"), regions)
    sector = _validated_option(parsed.get("sector"), sectors)
    if _has_invalid_returned_value(parsed, "country", country):
        return _no_match("Resolver returned an invalid country.")
    if _has_invalid_returned_value(parsed, "region", region):
        return _no_match("Resolver returned an invalid region.")
    if _has_invalid_returned_value(parsed, "sector", sector):
        return _no_match("Resolver returned an invalid sector.")

    matched = bool(country or region or sector)
    confidence = str(parsed.get("confidence") or "low").strip().casefold()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "matched": matched,
        "country": country,
        "region": region,
        "sector": sector,
        "confidence": confidence,
        "reason": str(parsed.get("reason") or "").strip(),
    }


def _clean_options(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    options: list[str] = []
    for value in values or []:
        label = str(value or "").strip()
        key = normalize(label)
        if label and key not in seen:
            seen.add(key)
            options.append(label)
    return options


def _validated_option(value: Any, options: list[str]) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    normalized = normalize(candidate)
    for option in options:
        if normalize(option) == normalized:
            return option
    return None


def _has_invalid_returned_value(parsed: dict[str, Any], key: str, validated: str | None) -> bool:
    raw_value = parsed.get(key)
    return raw_value is not None and bool(str(raw_value).strip()) and validated is None


def _no_match(reason: str) -> SelectionResult:
    return {
        "matched": False,
        "country": None,
        "region": None,
        "sector": None,
        "confidence": "low",
        "reason": reason,
    }
