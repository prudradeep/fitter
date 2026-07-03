import re

from app.services.chat_formatters import normalize_markdown_text
from app.services.chat_options import normalize, normalize_for_match


GENERIC_POPULATION_EDIT_LABELS = {
    "group",
    "groups",
    "affected group",
    "affected groups",
    "population",
    "target population",
    "target populations",
    "affected population",
    "affected populations",
    "people",
    "residents",
    "households",
    "communities",
}


def clean_affected_group_label(value: str) -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip(" `*_#.-")
    match = re.match(r"^(.+?)\s*:\s*Add\s+\1\s*$", label, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.match(r"^(.+?)\s*:\s*Add\s+(.+)$", label, flags=re.IGNORECASE)
    if match and normalize_for_match(match.group(1)) == normalize_for_match(match.group(2)):
        return match.group(1).strip()

    return label


def _is_valid_population_edit_label(label: str) -> bool:
    key = normalize_for_match(label)
    if not key or len(key) < 3:
        return False
    return key not in {normalize_for_match(item) for item in GENERIC_POPULATION_EDIT_LABELS}


def _dedupe_population_labels(labels: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for label in labels:
        clean = clean_affected_group_label(label)
        key = normalize_for_match(clean)
        if clean and key not in seen and _is_valid_population_edit_label(clean):
            seen.add(key)
            cleaned.append(clean[:120])

    return cleaned


def split_affected_group_labels(value: str) -> list[str]:
    labels = [
        clean_affected_group_label(part)
        for part in re.split(r"\s*(?:,|;|\band\b)\s*", str(value or ""), flags=re.IGNORECASE)
    ]
    return _dedupe_population_labels(labels)


def parse_custom_affected_group_edit_message(message: str) -> dict[str, list[str]]:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text:
        return {"add": [], "remove": []}

    remove_match = re.match(
        r"^remove\s+(.+?)(?:\s+and\s+add\s+(.+))?$",
        text,
        flags=re.IGNORECASE,
    )
    if remove_match:
        return {
            "remove": split_affected_group_labels(remove_match.group(1)),
            "add": split_affected_group_labels(remove_match.group(2) or ""),
        }

    add_match = re.match(r"^add\s+(.+)$", text, flags=re.IGNORECASE)
    if add_match:
        return {
            "add": split_affected_group_labels(add_match.group(1)),
            "remove": [],
        }

    return {"add": [], "remove": []}


def clean_population_edit_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    cleaned = [
        re.sub(r"\s+", " ", normalize_markdown_text(str(item))).strip("`*_ #.-")
        for item in value
    ]

    return _dedupe_population_labels(
        [
            label
            for label in cleaned
            if normalize(label) not in {normalize("none"), normalize("n/a")}
        ]
    )


def fallback_population_edits(message: str) -> dict[str, list[str]]:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    edits = {"add": [], "remove": []}

    for key, pattern in {
        "add": r"\b(?:add|include)\b\s*:?\s*(.+?)(?=\b(?:remove|delete|exclude)\b|$)",
        "remove": r"\b(?:remove|delete|exclude)\b\s*:?\s*(.+?)(?=\b(?:add|include)\b|$)",
    }.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            edits[key] = split_affected_group_labels(match.group(1))

    return edits