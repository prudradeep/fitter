import re
from collections.abc import Callable

from app.services.chat_json import parse_json_array
from app.services.chat_options import normalize
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.profile_metadata import compact_profile_metadata
from app.services.sector_prompt_rag import strip_rule_lines


def extract_socio_demographic_profiles(
    markdown_text: str,
    is_statistical_basis_line: Callable[[str], bool],
) -> list[str]:
    profiles: list[str] = []
    seen: set[str] = set()
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet_match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if bullet_match is None:
            continue

        profile = bullet_match.group(1).strip()
        if is_statistical_basis_line(profile):
            continue
        profile = re.sub(r"\*\*(.*?)\*\*", r"\1", profile)
        profile = re.sub(r"__(.*?)__", r"\1", profile)
        profile = profile.strip("`*_ ")
        if is_statistical_basis_line(profile):
            continue
        for separator in (":", " - ", " – ", " — "):
            if separator in profile:
                profile = profile.split(separator, 1)[0].strip()
                break
        profile = profile.strip(" .;,-")
        if not profile or len(profile) > 180:
            continue
        if profile.casefold().startswith(
            (
                "socio-demographic",
                "statistical basis",
                "basis",
                "reason",
                "evidence",
            )
        ):
            continue
        key = normalize(profile)
        if key not in seen:
            seen.add(key)
            profiles.append(profile)
    return profiles


def parse_hazard_profile_items(response: str) -> list[dict[str, object]]:
    if is_llm_unavailable_response(response):
        return []
    parsed = parse_json_array(response)
    if not isinstance(parsed, list):
        return []

    profiles: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in parsed:
        variable_name = ""
        statistical_basis = ""
        source = "sector_prompt"
        if isinstance(item, str):
            name = item.strip().strip("`*_ ")
            explanation = ""
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("profile") or "").strip().strip("`*_ ")
            explanation = str(
                item.get("explanation")
                or item.get("reason")
                or item.get("description")
                or ""
            ).strip().strip("`*_ ")
            variable_name = str(
                item.get("variable_name")
                or item.get("variable")
                or item.get("predictor")
                or ""
            ).strip().strip("`*_ ")
            statistical_basis = str(
                item.get("statistical_basis")
                or item.get("basis")
                or item.get("statistical_evidence")
                or ""
            ).strip().strip("`*_ ")
            source = str(item.get("source") or "sector_prompt").strip().strip("`*_ ")
        else:
            continue
        if not name:
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        profile_item: dict[str, object] = {
            "name": name[:120],
            "profile": name[:120],
            "explanation": explanation[:260],
            "variable_name": variable_name[:160],
            "statistical_basis": statistical_basis[:600],
            "source": source[:40] if source else "sector_prompt",
        }
        if isinstance(item, dict):
            metadata_value = item.get("metadata")
            metadata = metadata_value if isinstance(metadata_value, dict) else {}
            target_population_option_ids = item.get("target_population_option_ids")
            if not isinstance(target_population_option_ids, list) or not target_population_option_ids:
                target_population_option_ids = metadata.get("target_population_option_ids")
            target_population_labels = item.get("target_population_labels")
            if not isinstance(target_population_labels, list) or not target_population_labels:
                target_population_labels = metadata.get("target_population_labels")
            if isinstance(target_population_option_ids, list) and target_population_option_ids:
                profile_item["target_population_option_ids"] = list(target_population_option_ids)
            if isinstance(target_population_labels, list) and target_population_labels:
                profile_item["target_population_labels"] = list(target_population_labels)
            compacted_metadata = compact_profile_metadata(item)
            if compacted_metadata:
                profile_item["metadata"] = compacted_metadata
        profiles.append(profile_item)
    return profiles[:12]


def clean_hazard_profile_item(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    name = strip_rule_lines(str(value.get("name") or value.get("profile") or "")).strip()
    if not name:
        return {}
    variable_name = str(value.get("variable_name") or value.get("variable") or "").strip()
    prefixed_match = re.match(
        r"^(?:PREDICTOR\s+)?[0-9]+[A-Z]\s*:\s*(.+)$",
        variable_name,
        flags=re.IGNORECASE,
    )
    if prefixed_match:
        variable_name = prefixed_match.group(1).strip()
    variable_token = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", variable_name)
    if variable_token:
        variable_name = variable_token.group(1)
    return {
        "name": name[:120],
        "profile": name[:120],
        "variable_name": variable_name[:160],
        "explanation": strip_rule_lines(str(value.get("explanation") or "")).strip()[:260],
        "statistical_basis": str(value.get("statistical_basis") or value.get("basis") or "").strip()[:600],
        "source": str(value.get("source") or "sector_prompt").strip()[:40] or "sector_prompt",
    }


def profile_from_predictor_entry(entry: str) -> dict[str, str]:
    header = re.search(
        r"(?m)^PREDICTOR\s+([0-9]+[A-Z]):\s+(.+?)\s*$",
        entry,
    )
    if not header:
        return {}
    predictor_id = header.group(1).strip()
    variable_text = header.group(2).strip()
    variable_name = variable_text.split("(", 1)[0].strip()
    level_match = re.search(r'\blevel:\s*"([^"]+)"', variable_text, flags=re.IGNORECASE)
    variable_label = humanize_predictor_label(variable_name)
    if level_match:
        profile_name = f"{variable_label}: {level_match.group(1).strip()}"
    elif "country-level" in variable_text.casefold():
        profile_name = f"Countries with higher {variable_label}"
    elif "continuous" in variable_text.casefold():
        profile_name = f"Higher {variable_label}"
    else:
        profile_name = variable_label

    direction_match = re.search(r"(?m)^\s*Direction\s*=\s*(.+?)\s*$", entry)
    direction = direction_match.group(1).strip() if direction_match else ""
    plain_match = re.search(
        r"(?ms)^\s*Plain-English:\s*(.+?)(?=^\s*(?:Odds ratio|p-value|Direction|COUNTRY PATTERN|PREDICTORS NOT CONFIRMED)|\Z)",
        entry,
    )
    plain = re.sub(r"\s+", " ", plain_match.group(1)).strip() if plain_match else ""
    explanation = plain or f"{profile_name} is listed as a confirmed predictor for this hazard."
    if direction and "lower" in direction.casefold() and "lower" not in explanation.casefold():
        explanation = f"Lower concern/protective predictor: {explanation}"
    elif direction and "protective" in direction.casefold() and "protective" not in explanation.casefold():
        explanation = f"Protective predictor: {explanation}"

    basis_parts = [f"PREDICTOR {predictor_id}: {variable_text}"]
    if direction:
        basis_parts.append(f"Direction: {direction}")
    if plain:
        basis_parts.append(f"Plain-English: {plain}")
    return {
        "variable_name": variable_name[:160],
        "profile": profile_name[:120],
        "name": profile_name[:120],
        "explanation": explanation[:260],
        "statistical_basis": "; ".join(basis_parts)[:600],
        "source": "sector_prompt",
    }


def humanize_predictor_label(value: str) -> str:
    label = re.sub(r"[_\-]+", " ", value).strip()
    label = re.sub(r"\s+", " ", label)
    if label.casefold().startswith("macro "):
        label = label[6:]
    return label[:1].upper() + label[1:] if label else "Confirmed predictor"
