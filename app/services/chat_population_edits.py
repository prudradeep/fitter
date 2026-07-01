import re

from app.services.chat_formatters import normalize_markdown_text
from app.services.chat_options import normalize, normalize_for_match


def clean_affected_group_label(value: str) -> str:
    label = re.sub(r"\s+", " ", value).strip(" `*_#.-")
    match = re.match(r"^(.+?)\s*:\s*Add\s+\1\s*$", label, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.match(r"^(.+?)\s*:\s*Add\s+(.+)$", label, flags=re.IGNORECASE)
    if match and normalize_for_match(match.group(1)) == normalize_for_match(match.group(2)):
        return match.group(1).strip()
    return label


def split_affected_group_labels(value: str) -> list[str]:
    labels = []
    for part in re.split(r"\s*,\s*", value):
        label = clean_affected_group_label(part)
        if label:
            labels.append(label)
    return labels


def parse_custom_affected_group_edit_message(message: str) -> dict[str, list[str]]:
    text = re.sub(r"\s+", " ", message).strip()
    if not text:
        return {"add": [], "remove": []}

    remove_match = re.match(
        r"^remove\s+(.+?)(?:\s+and\s+add\s+(.+))?$",
        text,
        flags=re.IGNORECASE,
    )
    if remove_match:
        remove_target = clean_affected_group_label(remove_match.group(1))
        add_text = remove_match.group(2) or ""
        return {
            "remove": [remove_target] if remove_target else [],
            "add": split_affected_group_labels(add_text),
        }

    add_match = re.match(r"^add\s+(.+)$", text, flags=re.IGNORECASE)
    if add_match:
        add_text = add_match.group(1)
        generic_add_prompts = {
            normalize_for_match("affected group"),
            normalize_for_match("group"),
        }
        if normalize_for_match(add_text) in generic_add_prompts:
            return {"add": [], "remove": []}
        return {"add": split_affected_group_labels(add_text), "remove": []}

    return {"add": [], "remove": []}


def clean_population_edit_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        label = re.sub(r"\s+", " ", normalize_markdown_text(str(item))).strip("`*_ #.-")
        if label and normalize(label) not in {normalize("none"), normalize("n/a")}:
            cleaned.append(label[:120])
    return list(dict.fromkeys(cleaned))


def fallback_population_edits(message: str) -> dict[str, list[str]]:
    edits = {"add": [], "remove": []}
    for key, pattern in {
        "add": r"\b(?:add|include)\b\s*:?\s*(.+)",
        "remove": r"\b(?:remove|delete|exclude)\b\s*:?\s*(.+)",
    }.items():
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if not match:
            continue
        segment = re.split(
            r"\b(?:add|include|remove|delete|exclude)\b\s*:?",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        edits[key] = [
            item.strip(" `*_#.-")
            for item in re.split(r",|;|\band\b", segment)
            if item.strip(" `*_#.-")
        ]
    return edits
