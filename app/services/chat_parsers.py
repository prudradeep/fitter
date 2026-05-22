import json


def parse_additional_dgs(message: str) -> list[str]:
    return [profile.strip() for profile in message.split(",") if profile.strip()]


def parse_reason_evidence(message: str) -> tuple[str | None, str | None]:
    reason = None
    evidence = None
    current = None
    buffers = {"reason": [], "evidence": []}

    for line in message.splitlines():
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith("reason:"):
            current = "reason"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if lowered.startswith(("evidence:", "evidence url:", "evidence file:")):
            current = "evidence"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if current and stripped:
            buffers[current].append(stripped)

    reason_text = " ".join(part for part in buffers["reason"] if part).strip()
    evidence_text = " ".join(part for part in buffers["evidence"] if part).strip()
    if reason_text:
        reason = reason_text
    if evidence_text:
        evidence = evidence_text

    return reason, evidence


def parse_mitigation_reason(message: str) -> tuple[str | None, str | None]:
    mitigation_measure = None
    reason = None
    current = None
    buffers = {"mitigation_measure": [], "reason": []}

    for line in message.splitlines():
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith("mitigation measure:") or lowered.startswith("mitigation:"):
            current = "mitigation_measure"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if lowered.startswith("reason:"):
            current = "reason"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if current and stripped:
            buffers[current].append(stripped)

    mitigation_text = " ".join(part for part in buffers["mitigation_measure"] if part).strip()
    reason_text = " ".join(part for part in buffers["reason"] if part).strip()
    if mitigation_text:
        mitigation_measure = mitigation_text
    if reason_text:
        reason = reason_text

    return mitigation_measure, reason


def parse_evaluation_answer(message: str) -> tuple[int | None, str | None, str | None]:
    score = None
    reason = None
    evidence = None
    current = None
    buffers = {"reason": [], "evidence": []}

    for line in message.splitlines():
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith("score:"):
            raw_score = stripped.split(":", 1)[1].strip()
            try:
                parsed_score = int(raw_score)
            except ValueError:
                parsed_score = None
            if parsed_score is not None and 1 <= parsed_score <= 10:
                score = parsed_score
            current = None
            continue
        if lowered.startswith("reason:"):
            current = "reason"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if lowered.startswith(("evidence:", "evidence url:", "evidence file:")):
            current = "evidence"
            buffers[current].append(stripped.split(":", 1)[1].strip())
            continue
        if current and stripped:
            buffers[current].append(stripped)

    reason_text = " ".join(part for part in buffers["reason"] if part).strip()
    evidence_text = " ".join(part for part in buffers["evidence"] if part).strip()
    if reason_text:
        reason = reason_text
    if evidence_text:
        evidence = evidence_text

    return score, reason, evidence


def parse_validation_response(response: str) -> dict[str, str | bool]:
    response = extract_json_object(response)
    try:
        parsed = json.loads(response.strip())
    except json.JSONDecodeError:
        return {
            "valid": False,
            "reason": "The validation response was not valid JSON. Please clarify the reason and evidence.",
        }

    if not isinstance(parsed, dict):
        return {
            "valid": False,
            "reason": "The validation response was not an object. Please clarify the reason and evidence.",
        }

    valid = parsed.get("valid")
    reason = parsed.get("reason")
    if not isinstance(valid, bool) or not isinstance(reason, str):
        return {
            "valid": False,
            "reason": "The validation result was incomplete. Please clarify the reason and evidence.",
        }

    return {"valid": valid, "reason": reason.strip() or "No validation reason was provided."}


def parse_duplicate_check_response(response: str) -> dict[str, object]:
    response = extract_json_object(response)
    try:
        parsed = json.loads(response.strip())
    except json.JSONDecodeError:
        return {
            "duplicate": False,
            "match": "",
            "reason": "The duplicate-check response was not valid JSON.",
            "duplicates": [],
            "error": True,
        }

    if not isinstance(parsed, dict):
        return {
            "duplicate": False,
            "match": "",
            "reason": "The duplicate-check response was not an object.",
            "duplicates": [],
            "error": True,
        }

    duplicate = parsed.get("duplicate")
    match = parsed.get("match")
    reason = parsed.get("reason")
    duplicates = parsed.get("duplicates")

    cleaned_duplicates: list[dict[str, str]] = []
    if isinstance(duplicates, list):
        for item in duplicates:
            if not isinstance(item, dict):
                continue
            profile = item.get("profile")
            item_match = item.get("match")
            item_reason = item.get("reason")
            if isinstance(profile, str) and isinstance(item_match, str):
                cleaned_duplicates.append(
                    {
                        "profile": profile.strip(),
                        "match": item_match.strip(),
                        "reason": item_reason.strip() if isinstance(item_reason, str) else "",
                    }
                )

    if not isinstance(duplicate, bool):
        duplicate = bool(cleaned_duplicates)
    if not isinstance(match, str):
        match = cleaned_duplicates[0]["match"] if cleaned_duplicates else ""
    if not isinstance(reason, str):
        reason = cleaned_duplicates[0]["reason"] if cleaned_duplicates else ""

    return {
        "duplicate": duplicate,
        "match": match.strip(),
        "reason": reason.strip(),
        "duplicates": cleaned_duplicates,
        "error": False,
    }


def extract_json_object(response: str) -> str:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.casefold().startswith("json"):
            cleaned = cleaned[4:].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]

    return cleaned


def parse_llm_hazard_list(response: str) -> list[str]:
    if is_llm_unavailable_response(response):
        return []

    try:
        parsed = json.loads(response.strip())
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return clean_hazard_items(parsed)

    hazards: list[str] = []
    seen: set[str] = set()

    for line in response.splitlines():
        cleaned = line.strip()
        cleaned = cleaned.lstrip("-*•").strip()
        cleaned = cleaned.split(".", 1)[1].strip() if cleaned[:2].replace(".", "").isdigit() else cleaned
        cleaned = cleaned.strip("`*_ ")

        if not cleaned or len(cleaned) > 140:
            continue
        if cleaned.lower().startswith(("here", "these", "the hazards", "hazards")):
            continue

        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            hazards.append(cleaned)

    return hazards


def is_llm_unavailable_response(response: str) -> bool:
    lowered = response.casefold()
    return any(
        phrase in lowered
        for phrase in [
            "cannot reach ollama",
            "taking longer than",
            "ollama returned http",
            "ollama returned an invalid response",
            "returned an empty response",
            "cannot reach the local",
        ]
    )


def clean_hazard_items(items: list[object]) -> list[str]:
    hazards: list[str] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().strip("`*_ ")
        if not cleaned or len(cleaned) > 140:
            continue
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            hazards.append(cleaned)

    return hazards
