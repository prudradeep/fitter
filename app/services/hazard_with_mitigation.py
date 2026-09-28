import re
import json
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_options import normalize_for_match


HAZARD_WITH_MITIGATION_SOURCE_TYPE = "hazard_with_mitigation"
EXISTING_POLICY_FACTSHEET = "POLICY FACTSHEETS TO ADAPT EXISTING POLICIES"
NEW_POLICY_FACTSHEET = "POLICY FACTSHEETS FOR NEW POLICY PROPOSALS"


def country_factsheet_reference(
    db: Session,
    *,
    country: str | None,
    region: str | None = None,
    sector: str | None,
    selected_policy: str | None,
    hazard: str | None,
    proposal_type: str,
    disadvantage_groups: list[str] | None = None,
) -> str:
    """Render country/sector factsheet material for the chosen proposal path."""
    text = _country_factsheet_text(db, country)
    if not text:
        return ""
    if proposal_type == "existing_policy":
        return _existing_policy_reference(
            text, country, sector, selected_policy, hazard, disadvantage_groups
        )
    return _new_policy_reference(
        text, country, region, sector, selected_policy, hazard, disadvantage_groups
    )


def _country_factsheet_text(db: Session, country: str | None) -> str:
    country_key = normalize_for_match(country or "")
    if not country_key:
        return ""
    rows = db.execute(
        select(KnowledgeDocument.source_uri, KnowledgeChunk.chunk_index, KnowledgeChunk.content)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.scope == "main",
            KnowledgeDocument.source_type == HAZARD_WITH_MITIGATION_SOURCE_TYPE,
        )
        .order_by(KnowledgeDocument.source_uri, KnowledgeChunk.chunk_index)
    ).all()
    by_source: dict[str, list[str]] = {}
    for source_uri, _, content in rows:
        source = str(source_uri or "")
        source_name = source.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if normalize_for_match(source_name) != country_key:
            continue
        by_source.setdefault(source, []).append(str(content or ""))
    if not by_source:
        return ""
    return _merge_overlapping_chunks(next(iter(by_source.values())))


def _merge_overlapping_chunks(chunks: list[str]) -> str:
    merged = ""
    for chunk in chunks:
        value = str(chunk or "").strip()
        if not value:
            continue
        if not merged:
            merged = value
            continue
        overlap = min(len(merged), len(value), 300)
        while overlap and merged[-overlap:] != value[:overlap]:
            overlap -= 1
        merged += value[overlap:]
    return merged


def _factsheet_blocks(text: str, heading: str, title_label: str) -> list[str]:
    headings = list(
        re.finditer(
            f"{re.escape(EXISTING_POLICY_FACTSHEET)}|{re.escape(NEW_POLICY_FACTSHEET)}",
            text,
            re.IGNORECASE,
        )
    )
    blocks: list[str] = []
    for index, heading_match in enumerate(headings):
        if heading_match.group(0).casefold() != heading.casefold():
            continue
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading_match.end() : section_end]
        matches = list(re.finditer(re.escape(title_label), section, re.IGNORECASE))
        blocks.extend(
            section[
                match.start() : matches[position + 1].start()
                if position + 1 < len(matches)
                else len(section)
            ]
            for position, match in enumerate(matches)
        )
    return blocks


def _field(block: str, label: str, following_labels: tuple[str, ...]) -> str:
    match = re.search(re.escape(label) + r"(?:\s*:\s*|\s+)", block, re.IGNORECASE)
    if not match:
        return ""
    end = len(block)
    for following in following_labels:
        later = re.search(
            re.escape(following) + r"(?:\s*:\s*|\s+)",
            block[match.end() :],
            re.IGNORECASE,
        )
        if later:
            end = min(end, match.end() + later.start())
    return re.sub(r"\s+", " ", block[match.end() : end]).strip()


def _rank_blocks(
    blocks: list[str], sector: str | None, selected_policy: str | None, hazard: str | None
) -> list[str]:
    sector_key = normalize_for_match(sector or "")
    query_tokens = set(
        normalize_for_match(" ".join(value or "" for value in (selected_policy, hazard))).split()
    )

    def score(block: str) -> tuple[int, int]:
        sector_value = _field(
            block,
            "Sectoral focus",
            ("Original policy objectives", "Prioritised challenge addressed", "Policy description"),
        )
        sector_score = int(bool(sector_key and sector_key in normalize_for_match(sector_value)))
        token_score = len(query_tokens & set(normalize_for_match(block).split()))
        return sector_score, token_score

    return sorted(blocks, key=score, reverse=True)


def _existing_policy_reference(
    text: str,
    country: str | None,
    sector: str | None,
    selected_policy: str | None,
    hazard: str | None,
    disadvantage_groups: list[str] | None,
) -> str:
    blocks = _rank_blocks(
        _factsheet_blocks(text, EXISTING_POLICY_FACTSHEET, "Title of the policy (existing)"),
        sector,
        selected_policy,
        hazard,
    )
    cards: list[str] = []
    for block in blocks[:3]:
        adaptation = _field(block, "Proposed adaptations", ("Policy type(s)",))
        if adaptation:
            cards.append(
                f"{adaptation} "
                '<button class="factsheet-source-tag hazard-evidence-label--provided" '
                'type="button" '
                f'data-source-table="{escape(_existing_policy_source_table(block), quote=True)}" '
                'aria-label="Show the referenced factsheet data">Source</button>'
            )
    if not cards:
        return ""
    context = " · ".join(value for value in (country, sector) if value)
    groups = _disadvantage_group_section(blocks[:3], disadvantage_groups)
    return (
        "## Proposed adaptations\n\n"
        + (f"{context}\n\n" if context else "")
        + "\n\n".join(cards)
        + groups
    )


def _new_policy_reference(
    text: str,
    country: str | None,
    region: str | None,
    sector: str | None,
    selected_policy: str | None,
    hazard: str | None,
    disadvantage_groups: list[str] | None,
) -> str:
    blocks = _rank_blocks(
        _factsheet_blocks(text, NEW_POLICY_FACTSHEET, "Title of the policy (new proposal)"),
        sector,
        selected_policy,
        hazard,
    )
    if not blocks:
        return ""
    summaries: list[str] = []
    for block in blocks[:3]:
        summary = _new_policy_summary(block)
        if summary:
            summaries.append(
                f"{summary} "
                '<button class="factsheet-source-tag hazard-evidence-label--provided" '
                'type="button" '
                f'data-source-table="{escape(_new_policy_source_table(block), quote=True)}" '
                'aria-label="Show the referenced factsheet data">Source</button>'
            )
    if not summaries:
        return ""
    context = ", ".join(value for value in (country, region, sector) if value)
    mitigation_context = (
        f"Selected policy: {selected_policy or 'Not provided'}\n\n"
        f"Selected hazard: {hazard or 'Not provided'}\n\n"
        f"Selected context: {context or 'Not provided'}\n\n"
    )
    groups = _disadvantage_group_section(blocks[:3], disadvantage_groups)
    return (
        "## Inspiration for New policy proposal\n\n"
        + mitigation_context
        + "\n\n".join(summaries)
        + groups
    )


_NEW_POLICY_FIELDS = (
    "Title of the policy (new proposal)",
    "Sectoral focus",
    "Prioritised challenge addressed",
    "Policy description",
    "Policy type(s)",
    "Participatory dimension & stakeholders",
    "Target population",
    "Systemic focus",
    "Potential risks/barriers",
    "Drivers/enablers",
    "Time horizon",
    "Feasibility & resources",
)

_EXISTING_POLICY_FIELDS = (
    "Title of the policy (existing)",
    "Sectoral focus",
    "Original policy objectives",
    "Proposed adaptations",
    "Policy type(s)",
    "Target population",
    "Systemic focus",
)

_SOURCE_TABLE_EXCLUDED_FIELDS = {
    "Sectoral focus",
    "Policy type(s)",
    "Participatory dimension & stakeholders",
    "Target population",
    "Systemic focus",
    "Time horizon",
    "Feasibility & resources",
}


def _new_policy_summary(block: str) -> str:
    values = _new_policy_fields(block)
    sentences: list[str] = []
    challenge = values.get("Prioritised challenge addressed")
    description = values.get("Policy description")
    stakeholders = values.get("Participatory dimension & stakeholders")
    risks = values.get("Potential risks/barriers")
    drivers = values.get("Drivers/enablers")
    if challenge:
        sentences.append(f"This proposal addresses {challenge.rstrip('.')}.")
    if description:
        sentences.append(description.rstrip(".") + ".")
    if stakeholders:
        sentences.append(f"It involves {stakeholders.rstrip('.')}.")
    if risks:
        sentences.append(f"Key delivery risks include {risks.rstrip('.')}.")
    if drivers:
        sentences.append(f"It can be enabled by {drivers.rstrip('.')}.")
    return " ".join(sentences)


def _new_policy_source_table(block: str) -> str:
    return json.dumps(
        [
            {"field": label, "value": value}
            for label, value in _new_policy_fields(block).items()
            if value and label not in _SOURCE_TABLE_EXCLUDED_FIELDS
        ],
        ensure_ascii=False,
    )


def _existing_policy_source_table(block: str) -> str:
    return json.dumps(
        [
            {"field": label, "value": value}
            for label, value in _existing_policy_fields(block).items()
            if value and label not in _SOURCE_TABLE_EXCLUDED_FIELDS
        ],
        ensure_ascii=False,
    )


def _new_policy_fields(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, label in enumerate(_NEW_POLICY_FIELDS):
        value = _field(block, label, _NEW_POLICY_FIELDS[index + 1 :])
        if value:
            values[label] = value
    return values


def _existing_policy_fields(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, label in enumerate(_EXISTING_POLICY_FIELDS):
        value = _field(block, label, _EXISTING_POLICY_FIELDS[index + 1 :])
        if value:
            values[label] = value
    return values


def _disadvantage_group_section(
    blocks: list[str], session_groups: list[str] | None
) -> str:
    groups: list[str] = []
    seen: set[str] = set()
    for group in session_groups or []:
        _append_group(groups, seen, group)
    for block in blocks:
        target_match = re.search(
            r"Target population[^\n]*\n(.*?)(?=\nSystemic focus\s*\n|\Z)",
            block,
            re.IGNORECASE | re.DOTALL,
        )
        target_population = target_match.group(1) if target_match else ""
        current_dimension = ""
        for raw_line in target_population.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.endswith(":"):
                current_dimension = line.rstrip(":")
                continue
            for selected in re.findall(r"(?i)(?:^|\s)x\s*([^☐\n]+)", line):
                value = re.sub(r"\s+", " ", selected).strip(" ;,.")
                if value:
                    _append_group(
                        groups,
                        seen,
                        f"{current_dimension}: {value}" if current_dimension else value,
                    )
    if not groups:
        return ""
    return "\n\n## Disadvantage groups to consider for this mitigation measure\n\n" + "\n".join(
        f"- {group}" for group in groups
    )


def _append_group(groups: list[str], seen: set[str], value: str) -> None:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    key = normalize_for_match(cleaned)
    if cleaned and key and key not in seen:
        seen.add(key)
        groups.append(cleaned)
