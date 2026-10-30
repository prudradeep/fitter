"""Source context for individual summary points."""

import json
import re
from collections import Counter
from html import escape


_WORD = re.compile(r"[\w]{4,}", re.UNICODE)
_BULLET = re.compile(r"^\s*-\s+(?:\*\*(.+?):\*\*\s*)?(.*)$")
_STOP = {"about", "after", "also", "from", "into", "more", "not", "only", "other", "policy", "that", "their", "them", "there", "these", "this", "those", "with", "would"}
_GENERIC_LABELS = {
    "benefit", "benefited group", "beneficiary", "mechanism", "objective",
    "policy detail", "policy details", "intended benefits", "target actors beneficiaries",
}
_TRAILING_BASIS = re.compile(
    r"\s*(?:\*\*)?(Inferred|Documented(?:\s*[-—]\s*(?:Strong|Moderate|Weak))?)(?:\*\*)?\s*$",
    re.IGNORECASE,
)
_DANGLING_END = re.compile(r"\b(?:a|an|and|at|by|for|from|in|of|on|or|the|to|with)$", re.I)


def _display_point(label: str, detail: str, rows: list[dict[str, str]]) -> tuple[str, str]:
    """Remove embedded evidence text and finish a saved truncated point when possible."""
    while _TRAILING_BASIS.search(detail):
        detail = _TRAILING_BASIS.sub("", detail).strip()
    if detail.endswith((".", "!", "?")) and not _DANGLING_END.search(detail[:-1]):
        return label, detail
    fragment = re.split(r"(?<=[.!?])\s+", detail)[-1]
    for row in rows:
        if row.get("field") != "Referenced excerpt" or not fragment:
            continue
        excerpt = re.sub(r"\s+", " ", str(row.get("value") or ""))
        match = re.search(re.escape(fragment) + r"[^.!?]{0,300}[.!?]", excerpt, re.I)
        if match:
            return label, detail[:len(detail) - len(fragment)] + match.group(0)
    prefix = detail[:len(detail) - len(fragment)].strip() if fragment else ""
    if prefix.count("(") > prefix.count(")"):
        opening = prefix.rfind("(")
        sentence_end = max(prefix.rfind(mark, 0, opening) for mark in (". ", "! ", "? "))
        prefix = prefix[:sentence_end + 1].strip() if sentence_end >= 0 else ""
    if prefix:
        return label, prefix
    if detail and not _DANGLING_END.search(detail):
        return label, detail.rstrip(" ,;:") + "."
    return label, ""


def claim_basis(claim: str, rows: list[dict[str, str]]) -> str:
    """Give a conservative label to a claim and its actual supporting excerpt."""
    explicit = next(
        (str(row.get("value") or "").strip() for row in rows
         if row.get("field") == "Basis"),
        "",
    )
    has_source = any(
        row.get("field") in {"Document", "Source", "Referenced excerpt"}
        and str(row.get("value") or "").strip()
        and str(row.get("value") or "").strip() != "Source passage unavailable"
        for row in rows
    )
    if explicit.startswith("Documented") and has_source:
        strength = next(
            (str(row.get("value") or "").strip().title() for row in rows
             if row.get("field") == "Evidence strength"),
            "",
        )
        if strength in {"Strong", "Moderate", "Weak"}:
            return f"Documented — {strength}"
        return explicit if "—" in explicit else "Documented — Moderate"
    excerpts = [
        str(row.get("value") or "").strip()
        for row in rows if row.get("field") == "Referenced excerpt"
    ]
    normalized_claim = re.sub(r"\s+", " ", claim).strip(" .:;\n").casefold()
    if normalized_claim and any(
        normalized_claim in re.sub(r"\s+", " ", excerpt).casefold()
        for excerpt in excerpts
    ):
        return "Documented — Strong"
    return "Inferred"


def source_button(
    rows: list[dict[str, str]], *, basis: str | None = None, claim: str = "",
) -> str:
    """Use the chat's existing Provided evidence modal."""
    basis = basis or claim_basis(claim, rows)
    strength = re.search(r"\b(Strong|Moderate|Weak)\b", basis, re.I)
    if strength and not any(row.get("field") == "Evidence strength" for row in rows):
        rows = [*rows, {"field": "Evidence strength", "value": strength.group(1).title()}]
    payload = escape(json.dumps(rows, ensure_ascii=False), quote=True)
    displayed_basis = "[Documented]" if basis.casefold().startswith("documented") else "[Inferred]"
    return (
        f'<span class="evidence-basis-label">{escape(displayed_basis)}</span> '
        '<button class="factsheet-source-tag hazard-evidence-label--provided" '
        f'type="button" data-source-table="{payload}" '
        'aria-label="Show source context">Source</button>'
    )


def _is_unidentified_summary_bullet(line: str) -> bool:
    match = _BULLET.match(line)
    if not match:
        return False
    label = (match.group(1) or "").strip()
    detail = match.group(2).strip()
    if not label and ":" in detail:
        label, detail = detail.split(":", 1)

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    return (
        normalized(label) == "not identified"
        and normalized(detail) in {
            "not stated in the supplied text",
            "not stated in supplied text",
        }
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
        if section == "mechanisms":
            basis = "Documented" if "**Documented**" in detail else "Inferred"
            rows.insert(0, {"field": "Basis", "value": basis})
        sources.setdefault(section, []).append(rows)
    return sources


def policy_summary_with_buttons(markdown: str, sources: dict[str, list[list[dict[str, str]]]] | None) -> str:
    blocks: list[list[str]] = []
    block: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("### ") and block:
            blocks.append(block)
            block = []
        block.append(line)
    if block:
        blocks.append(block)

    section = ""
    counts: dict[str, int] = {}
    output = []
    for block in blocks:
        heading = block[0] if block and block[0].startswith("### ") else ""
        if heading:
            section = heading[4:].strip().casefold()
            bullets = [line for line in block[1:] if _BULLET.match(line)]
            if bullets and not any(
                not _is_unidentified_summary_bullet(line) for line in bullets
            ):
                continue
            counts.setdefault(section, 0)
        labels = [(_BULLET.match(line).group(1) or "").strip() for line in block if _BULLET.match(line)]
        label_counts = Counter(re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip() for label in labels if label)
        for line in block:
            if line.startswith("### "):
                output.append(line)
                continue
            match = _BULLET.match(line)
            if section and match:
                index = counts.get(section, 0)
                counts[section] = index + 1
                if _is_unidentified_summary_bullet(line):
                    continue
                choices = (sources or {}).get(section, [])
                rows = choices[index] if index < len(choices) else []
                label, detail = _display_point((match.group(1) or "").strip(), match.group(2).strip(), rows)
                if not detail:
                    continue
                label_key = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
                if label_key in _GENERIC_LABELS or label_counts[label_key] > 1:
                    label = ""
                line = f"- **{label}:** {detail}" if label else f"- {detail}"
                if index < len(choices):
                    if not any(
                        row.get("field") == "Source"
                        and row.get("value") == "Source passage unavailable"
                        for row in rows
                    ):
                        basis = claim_basis(
                            " ".join(part for part in (label, detail) if part),
                            rows,
                        )
                        if _TRAILING_BASIS.search(match.group(2)) and re.search(r"\bInferred\b", match.group(2), re.I):
                            basis = "Inferred"
                        line += " " + source_button(rows, basis=basis)
                    else:
                        line += ' <span class="evidence-basis-label">[Inferred]</span>'
                else:
                    line += ' <span class="evidence-basis-label">[Inferred]</span>'
            output.append(line)
    return "\n".join(output)
