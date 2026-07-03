from __future__ import annotations

import json
from typing import Any


def clean_json_text(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.casefold().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def extract_json_object(value: str) -> str:
    cleaned = clean_json_text(value)
    extracted = _extract_first_json_value(cleaned, dict)
    if extracted:
        return extracted

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def extract_json_array(value: str) -> str:
    cleaned = clean_json_text(value)
    extracted = _extract_first_json_value(cleaned, list)
    if extracted:
        return extracted

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def parse_json_object(value: str) -> dict[str, Any] | None:
    parsed = _loads_json(clean_json_text(value))
    if isinstance(parsed, dict):
        return parsed

    parsed = _loads_json(extract_json_object(value))
    return parsed if isinstance(parsed, dict) else None


def parse_json_array(value: str) -> list[Any] | None:
    parsed = _loads_json(clean_json_text(value))
    if isinstance(parsed, list):
        return parsed

    parsed = _loads_json(extract_json_array(value))
    return parsed if isinstance(parsed, list) else None


def _loads_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _extract_first_json_value(cleaned: str, expected_type: type) -> str:
    decoder = json.JSONDecoder()
    opening = "{" if expected_type is dict else "["
    for index, char in enumerate(cleaned):
        if char != opening:
            continue
        try:
            parsed, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, expected_type):
            return cleaned[index : index + end]
    return ""
