"""Source context for individual summary points."""

import json
import re
from html import escape


_WORD = re.compile(r"[\w]{4,}", re.UNICODE)
_BULLET = re.compile(r"^\s*-\s+(?:\*\*(.+?):\*\*\s*)?(.*)$")
_STOP = {"about", "after", "also", "from", "into", "more", "not", "only", "other", "policy", "that", "their", "them", "there", "these", "this", "those", "with", "would"}


def source_button(rows: list[dict[str, str]]) -> str:
    """Use the chat's existing Provided evidence modal."""
    payload = escape(json.dumps(rows, ensure_ascii=False), quote=True)
    return (
        '<button class="factsheet-source-tag hazard-evidence-label--provided" '
        f'type="button" data-source-table="{payload}" '
        'aria-label="Show source context">Source</button>'
    )


def summary_points(markdown: str):
    """Yield section, point index, label and text in display order."""
    section = ""
    counts: dict[str, int] = {}
    for line in markdown.splitlines():
        if line.startswith("### "):
            section = line[4:].strip().casefold()
            counts.setdefault(section, 0)
            continue
        match = _BULLET.match(line)
        if section and match:
            index = counts[section]
            counts[section] += 1
            yield section, index, (match.group(1) or "").strip(), match.group(2).strip()


def _terms(value: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(value) if word.casefold() not in _STOP}


def policy_point_sources(markdown: str, chunks: list[dict[str, object]], clarifications: list[str] | None = None) -> dict[str, list[list[dict[str, str]]]]:
    """Match each point to the closest supplied passage, retaining actual source text."""
    sources: dict[str, list[list[dict[str, str]]]] = {}
    passages = [chunk for chunk in chunks if str(chunk.get("content") or "").strip()]
    for section, _index, label, detail in summary_points(markdown):
        rows: list[dict[str, str]] = []
        query = _terms(f"{label} {detail}")
        passage = None
        passage_overlap = 0
        if passages:
            passage = max(
                passages,
                key=lambda item: (
                    len(query & _terms(str(item.get("content") or ""))) / max(len(query), 1),
                    len(query & _terms(str(item.get("content") or ""))),
                ),
            )
            passage_overlap = len(query & _terms(str(passage.get("content") or "")))
        clarification = max(
            clarifications or [],
            key=lambda value: len(query & _terms(value)),
            default="",
        )
        clarification_overlap = len(query & _terms(clarification))
        if clarification and clarification_overlap > passage_overlap:
            rows = [
                {"field": "Source", "value": "User-provided clarification"},
                {"field": "Context used", "value": clarification},
            ]
        elif passage and passage_overlap:
            rows = [
                {"field": "Document", "value": str(passage.get("title") or "Policy document")},
                *([{"field": "Source", "value": str(passage["source_uri"])}] if passage.get("source_uri") else []),
                *([{"field": "Page", "value": str(passage["page_number"])}] if passage.get("page_number") else []),
                {"field": "Referenced excerpt", "value": str(passage["content"])},
            ]
        else:
            rows = [
                {"field": "Source", "value": "Source passage unavailable"},
                {"field": "Summary point", "value": detail or label},
            ]
        sources.setdefault(section, []).append(rows)
    return sources


def policy_summary_with_buttons(markdown: str, sources: dict[str, list[list[dict[str, str]]]] | None) -> str:
    section = ""
    counts: dict[str, int] = {}
    output = []
    for line in markdown.splitlines():
        if line.startswith("### "):
            section = line[4:].strip().casefold()
            counts[section] = 0
        match = _BULLET.match(line)
        if section and match:
            index = counts.get(section, 0)
            counts[section] = index + 1
            choices = (sources or {}).get(section, [])
            if index < len(choices):
                line += " " + source_button(choices[index])
        output.append(line)
    return "\n".join(output)
