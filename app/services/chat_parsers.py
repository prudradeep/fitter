import re

from app.services.chat_json import parse_json_array, parse_json_object


def extract_first_url(message: str) -> str:
    text = str(message or "")
    match = re.search(r"https?://[^\s<>)\"']+", text)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:]")


def normalize_evidence_message(message: str) -> str:
    _, parsed_evidence = parse_reason_evidence(message)
    evidence = (parsed_evidence or message or "").strip()
    if not evidence:
        return ""
    if extract_first_url(evidence) and "evidence url:" not in evidence.casefold():
        return f"Evidence URL: {extract_first_url(evidence)}"
    return evidence


def open_evidence_decision_action(message: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(message or "").strip().casefold())
    compact = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    if not normalized:
        return None
    if extract_first_url(message):
        return "evidence"
    yes_phrases = {
        "yes",
        "yes i have evidence",
        "yes, i have evidence",
        "i have evidence",
        "i want to add evidence",
        "add evidence",
        "provide evidence",
        "upload evidence",
        "paste evidence",
        "i can provide evidence",
    }
    no_phrases = {
        "no",
        "no evidence",
        "i do not have evidence",
        "i don't have evidence",
        "i dont have evidence",
        "i don't have it",
        "i dont have it",
        "i don't have",
        "i dont have",
        "no, i don't have",
        "no i don't have",
        "no, i dont have",
        "no i dont have",
        "no, i don't know",
        "no i don't know",
        "no, i dont know",
        "no i dont know",
        "skip evidence",
        "continue without evidence",
        "proceed without evidence",
        "without evidence",
        "no, continue",
        "no continue",
    }
    if normalized in yes_phrases:
        return "yes"
    if normalized in no_phrases:
        return "no"
    if compact.startswith("no ") and any(
        phrase in compact
        for phrase in (
            "dont have",
            "do not have",
            "not have",
            "dont know",
            "do not know",
            "not know",
        )
    ):
        return "no"
    if compact.startswith("i ") and any(
        phrase in compact
        for phrase in (
            "dont have",
            "do not have",
            "dont know",
            "do not know",
        )
    ):
        return "no"
    if "evidence" in normalized and any(
        phrase in normalized for phrase in ("without", "skip", "do not", "don't", "no")
    ):
        return "no"
    if "evidence" in normalized and any(
        token in normalized for token in ("add", "provide", "upload", "paste", "have")
    ):
        return "yes"
    return None


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
        if lowered.startswith(("evidence url:", "evidence file:", "evidence content:")):
            current = "evidence"
            buffers[current].append(stripped)
            continue
        if lowered.startswith("evidence:"):
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
        if lowered.startswith(("evidence url:", "evidence file:", "evidence content:")):
            current = "evidence"
            buffers[current].append(stripped)
            continue
        if lowered.startswith("evidence:"):
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
    parsed = parse_json_object(response)
    if parsed is None:
        return {
            "valid": False,
            "reason": "The validation response was not valid JSON. Please clarify the reason and evidence.",
        }

    valid = parsed.get("valid")
    reason = parsed.get("reason")
    if not isinstance(valid, bool) or not isinstance(reason, str):
        return {
            "valid": False,
            "reason": "The validation result was incomplete. Please clarify the reason and evidence.",
        }

    return {"valid": valid, "reason": reason.strip() or "No validation reason was provided."}


def parse_grounded_validation_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {
            "dimensions": {},
            "reason": "The grounded validation response was not valid JSON.",
            "error": True,
        }

    dimensions: dict[str, dict[str, object]] = {}
    raw_dimensions = parsed.get("dimensions")
    if isinstance(raw_dimensions, dict):
        for name, raw_dimension in raw_dimensions.items():
            if not isinstance(name, str) or not isinstance(raw_dimension, dict):
                continue
            status = str(raw_dimension.get("status") or "").strip().upper()
            if status not in {"SUPPORTED", "CONTRADICTED", "INSUFFICIENT_INFO"}:
                status = "INSUFFICIENT_INFO"
            citation_ids = raw_dimension.get("citation_ids")
            cleaned_citations = [
                citation.strip().upper()
                for citation in citation_ids
                if isinstance(citation, str) and citation.strip()
            ] if isinstance(citation_ids, list) else []
            explanation = raw_dimension.get("explanation")
            dimensions[name.strip()] = {
                "status": status,
                "citation_ids": cleaned_citations,
                "explanation": explanation.strip() if isinstance(explanation, str) else "",
            }

    reason = parsed.get("reason")
    return {
        "dimensions": dimensions,
        "reason": reason.strip() if isinstance(reason, str) else "",
        "error": False,
    }


def parse_grounded_claims_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {"claims": [], "error": True}

    claims: list[dict[str, object]] = []
    raw_claims = parsed.get("claims")
    if isinstance(raw_claims, list):
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            text = raw_claim.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            citation_ids = raw_claim.get("citation_ids")
            user_fields = raw_claim.get("user_fields")
            claims.append(
                {
                    "text": text.strip(),
                    "citation_ids": [
                        item.strip().upper()
                        for item in citation_ids
                        if isinstance(item, str) and item.strip()
                    ] if isinstance(citation_ids, list) else [],
                    "user_fields": [
                        item.strip()
                        for item in user_fields
                        if isinstance(item, str) and item.strip()
                    ] if isinstance(user_fields, list) else [],
                }
            )
    return {"claims": claims, "error": False}


def parse_entailment_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {"verdicts": [], "error": True}

    verdicts: list[dict[str, object]] = []
    raw_verdicts = parsed.get("verdicts")
    if isinstance(raw_verdicts, list):
        for raw_verdict in raw_verdicts:
            if not isinstance(raw_verdict, dict):
                continue
            claim_index = raw_verdict.get("claim_index")
            entailed = raw_verdict.get("entailed")
            if not isinstance(claim_index, int) or not isinstance(entailed, bool):
                continue
            reason = raw_verdict.get("reason")
            verdicts.append(
                {
                    "claim_index": claim_index,
                    "entailed": entailed,
                    "reason": reason.strip() if isinstance(reason, str) else "",
                }
            )
    return {"verdicts": verdicts, "error": False}


def parse_mitigation_clarity_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {
            "clear": False,
            "dimensions": {},
            "follow_up_question": "Please clarify the mitigation measure or reason.",
            "follow_up_questions": ["Please clarify the mitigation measure or reason."],
            "frozen_inputs": {},
            "reason": "The clarity response was not valid JSON.",
            "error": True,
        }

    dimensions = _normalized_clarity_dimensions(parsed.get("dimensions"))
    clear = parsed.get("clear")
    if not isinstance(clear, bool):
        clear = bool(dimensions) and all(value == "CLEAR" for value in dimensions.values())
    required_dimensions = {
        "specificity",
        "justification_clarity",
        "evidence_identifiability",
    }
    if set(dimensions) != required_dimensions or any(
        value != "CLEAR" for value in dimensions.values()
    ):
        clear = False

    follow_up_question = parsed.get("follow_up_question")
    if not isinstance(follow_up_question, str):
        follow_up_question = ""
    follow_up_questions = parsed.get("follow_up_questions")
    cleaned_follow_up_questions = [
        question.strip()
        for question in follow_up_questions
        if isinstance(question, str) and question.strip()
    ] if isinstance(follow_up_questions, list) else []
    if not cleaned_follow_up_questions and follow_up_question.strip():
        cleaned_follow_up_questions = [follow_up_question.strip()]
    reason = parsed.get("reason")
    if not isinstance(reason, str):
        reason = ""

    frozen_inputs = parsed.get("frozen_inputs")
    cleaned_frozen_inputs: dict[str, str] = {}
    if isinstance(frozen_inputs, dict):
        for key in ("measure_description", "justification", "evidence"):
            value = frozen_inputs.get(key)
            cleaned_frozen_inputs[key] = value.strip() if isinstance(value, str) else ""

    return {
        "clear": clear,
        "dimensions": dimensions,
        "follow_up_question": follow_up_question.strip(),
        "follow_up_questions": cleaned_follow_up_questions,
        "frozen_inputs": cleaned_frozen_inputs,
        "reason": reason.strip(),
        "error": False,
    }


def _normalized_clarity_dimensions(value: object) -> dict[str, str]:
    dimensions: dict[str, str] = {}
    if not isinstance(value, dict):
        return dimensions
    for key in ("specificity", "justification_clarity", "evidence_identifiability"):
        raw_status = value.get(key)
        status = str(raw_status or "").strip().upper()
        if status not in {"CLEAR", "NEEDS_CLARIFICATION"}:
            status = "NEEDS_CLARIFICATION"
        dimensions[key] = status
    return dimensions


def parse_duplicate_check_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {
            "duplicate": False,
            "match": "",
            "reason": "The duplicate-check response was not valid JSON.",
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


def parse_hazard_input_review_response(response: str) -> dict[str, object]:
    parsed = parse_json_object(response)
    if parsed is None:
        return {
            "valid": False,
            "status": "Invalid",
            "reason": "The hazard review response was not valid JSON.",
            "suggestions": [],
            "error": True,
        }

    valid = parsed.get("valid")
    status = parsed.get("status")
    reason = parsed.get("reason")
    suggestions = parsed.get("suggestions")
    cleaned_suggestions: list[str] = []
    if isinstance(suggestions, list):
        for item in suggestions:
            if isinstance(item, str) and item.strip():
                cleaned_suggestions.append(item.strip())

    if isinstance(status, str):
        normalized_status = status.strip().strip("<>").casefold()
        if normalized_status == "valid":
            status = "Valid"
        elif normalized_status == "ambiguous":
            status = "Ambiguous"
        elif normalized_status == "invalid":
            status = "Invalid"
        else:
            status = None
    else:
        status = None

    if not isinstance(valid, bool):
        valid = status == "Valid"
    if status is None:
        status = "Valid" if valid else "Ambiguous"
    if not isinstance(reason, str):
        reason = "Please rewrite the hazard with a narrower, clearer context."

    return {
        "valid": valid,
        "status": status,
        "reason": reason.strip() or "Please rewrite the hazard with a narrower, clearer context.",
        "suggestions": cleaned_suggestions,
        "error": False,
    }


def parse_llm_hazard_list(response: str) -> list[str]:
    if is_llm_unavailable_response(response):
        return []

    parsed = parse_json_array(response)
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


def parse_llm_hazard_profiles(response: str) -> list[dict[str, object]]:
    if is_llm_unavailable_response(response):
        return []

    parsed = parse_json_array(response)
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    if isinstance(parsed, list):
        for item in parsed:
            hazard = ""
            profiles: list[object] = []
            if isinstance(item, dict):
                hazard_value = item.get("hazard") or item.get("name")
                profile_value = (
                    item.get("profiles")
                    or item.get("affected_profiles")
                    or item.get("socio_demographic_profiles")
                    or item.get("profile")
                    or item.get("hazard_profile")
                    or item.get("description")
                )
                if isinstance(hazard_value, str):
                    hazard = hazard_value.strip().strip("`*_ ")
                if isinstance(profile_value, list):
                    for profile_item in profile_value:
                        if isinstance(profile_item, str) and profile_item.strip():
                            profiles.append(profile_item.strip().strip("`*_ ")[:180])
                        elif isinstance(profile_item, dict):
                            name = profile_item.get("name") or profile_item.get("profile")
                            explanation = (
                                profile_item.get("explanation")
                                or profile_item.get("reason")
                                or profile_item.get("description")
                            )
                            if isinstance(name, str) and name.strip():
                                profiles.append(
                                    {
                                        "name": name.strip().strip("`*_ ")[:100],
                                        "explanation": (
                                            explanation.strip().strip("`*_ ")[:220]
                                            if isinstance(explanation, str)
                                            else ""
                                        ),
                                    }
                                )
                elif isinstance(profile_value, str) and profile_value.strip():
                    profiles.append(profile_value.strip().strip("`*_ ")[:180])
            elif isinstance(item, str):
                hazard = item.strip().strip("`*_ ")

            if not hazard or len(hazard) > 180:
                continue
            key = hazard.casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append({"hazard": hazard, "profiles": profiles[:12]})

    if items:
        return items

    return [{"hazard": hazard, "profiles": []} for hazard in parse_llm_hazard_list(response)]


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
