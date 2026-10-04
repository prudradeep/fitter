import re
import json
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_options import normalize_for_match
from app.services.system_hazard_profile_names import profile_name_for_legacy_label


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
    mechanism_suggestions: list[dict[str, object]] | None = None,
    new_policy_interpretation: dict[str, list[str]] | None = None,
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
        text,
        country,
        region,
        sector,
        selected_policy,
        hazard,
        disadvantage_groups,
        mechanism_suggestions,
        new_policy_interpretation,
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
    _ = sector, hazard
    blocks = _selected_existing_policy_blocks(
        _factsheet_blocks(text, EXISTING_POLICY_FACTSHEET, "Title of the policy (existing)"),
        selected_policy,
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


def _selected_existing_policy_blocks(blocks: list[str], selected_policy: str | None) -> list[str]:
    """Return only fact-sheet entries for the policy selected by the user."""
    selected_key = normalize_for_match(selected_policy or "")
    if not selected_key:
        return []
    return [
        block
        for block in blocks
        if normalize_for_match(
            _field(block, "Title of the policy (existing)", _EXISTING_POLICY_FIELDS[1:])
        )
        == selected_key
    ]


def _new_policy_reference(
    text: str,
    country: str | None,
    region: str | None,
    sector: str | None,
    selected_policy: str | None,
    hazard: str | None,
    disadvantage_groups: list[str] | None,
    mechanism_suggestions: list[dict[str, object]] | None = None,
    new_policy_interpretation: dict[str, list[str]] | None = None,
) -> str:
    blocks = _rank_blocks(
        _factsheet_blocks(text, NEW_POLICY_FACTSHEET, "Title of the policy (new proposal)"),
        sector,
        selected_policy,
        hazard,
    )
    if not blocks:
        return ""
    context = ", ".join(value for value in (country, region, sector) if value)
    mitigation_context = (
        f"**Selected policy**: {selected_policy or 'Not provided'}\n\n"
        f"**Selected hazard**: {hazard or 'Not provided'}\n\n"
        f"**Selected context**: {context or 'Not provided'}\n\n"
    )
    groups = _disadvantage_group_section(blocks[:3], disadvantage_groups)
    return (
        "# Inspiration for New policy proposal\n\n"
        + mitigation_context
        + _policy_mechanisms_section(mechanism_suggestions)
        + _important_new_proposal_concepts_section(new_policy_interpretation)
        + groups
    )


def country_factsheet_inspiration_fields(
    db: Session,
    *,
    country: str | None,
    sector: str | None,
    selected_policy: str | None,
    hazard: str | None,
) -> list[dict[str, str]]:
    """Return relevant source fields as internal input for a grounded interpretation."""
    text = _country_factsheet_text(db, country)
    blocks = _rank_blocks(
        _factsheet_blocks(text, NEW_POLICY_FACTSHEET, "Title of the policy (new proposal)"),
        sector,
        selected_policy,
        hazard,
    )
    return _relevant_new_policy_fields(blocks, hazard)


def _policy_mechanisms_section(mechanism_suggestions: list[dict[str, object]] | None) -> str:
    """Format early, policy-level mechanism suggestions for the proposal context."""
    lines: list[str] = []
    seen: set[str] = set()
    for suggestion in mechanism_suggestions or []:
        mechanism = str(suggestion.get("mechanism") or "").strip()
        mechanism_key = normalize_for_match(mechanism)
        if not mechanism or mechanism_key in seen:
            continue
        seen.add(mechanism_key)
        linkage = str(suggestion.get("causal_linkage") or "").strip()
        if linkage:
            lines.append(
                f"- **{mechanism}:** {linkage}"
            )
        else:
            lines.append(f"- **{mechanism}**")
    if not lines:
        return ""
    return (
        "## Policy mechanisms to be considered for mitigation\n\n"
        + "\n".join(lines)
        + "\n\n"
    )


def _relevant_new_policy_fields(blocks: list[str], hazard: str | None) -> list[dict[str, str]]:
    """Select fact sheet entries most relevant to the selected hazard."""
    hazard_key = normalize_for_match(hazard or "")
    hazard_tokens = set(hazard_key.split())

    def relevance(block: str) -> tuple[int, int]:
        challenge = _new_policy_fields(block).get("Prioritised challenge addressed", "")
        challenge_key = normalize_for_match(challenge)
        return (
            int(bool(hazard_key and (hazard_key in challenge_key or challenge_key in hazard_key))),
            len(hazard_tokens & set(challenge_key.split())),
        )

    ranked = sorted(blocks, key=relevance, reverse=True)
    if not ranked:
        return []
    top_score = relevance(ranked[0])
    relevant_blocks = [
        block for block in ranked[:3] if relevance(block) == top_score and top_score != (0, 0)
    ] or ranked[:1]

    labels = (
        "Prioritised challenge addressed",
        "Policy description",
        "Participatory dimension & stakeholders",
    )
    return [
        {label: fields[label] for label in labels if fields.get(label)}
        for fields in (_new_policy_fields(block) for block in relevant_blocks)
    ]


def _important_new_proposal_concepts_section(
    interpretation: dict[str, list[str]] | None,
) -> str:
    """Show distinct, original explanations without raw fact sheet fields."""
    if not isinstance(interpretation, dict):
        return ""
    headings = (
        ("challenge", "Challenges to be addressed"),
        ("approach", "Possible ways to address the challenges"),
        ("stakeholders", "Possible stakeholders to involve"),
    )
    sections: list[str] = []
    for key, heading in headings:
        values = interpretation.get(key)
        if not isinstance(values, list):
            continue
        bullets = [str(value).strip() for value in values if str(value).strip()]
        if bullets:
            sections.append(f"### {heading}\n\n" + "\n".join(f"- {value}" for value in bullets))
    if not sections:
        return ""
    return "# Important concepts for your new proposal\n\n" + "\n\n".join(sections) + "\n\n"


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
        _append_group(groups, seen, profile_name_for_legacy_label(group))
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
