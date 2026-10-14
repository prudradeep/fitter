import re
import json
from difflib import SequenceMatcher
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_options import normalize_for_match
from app.services.summary_sources import source_button
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
    user_added_policy: bool = False,
) -> str:
    """Render country/sector factsheet material for the chosen proposal path."""
    text = _country_factsheet_text(db, country)
    if not text:
        return (
            _policy_mechanisms_section(mechanism_suggestions)
            + _disadvantage_group_section([], disadvantage_groups).lstrip()
            if proposal_type == "existing_policy" else ""
        )
    if proposal_type == "existing_policy":
        selected_policy_adaptations = "" if user_added_policy else _existing_policy_reference(
            text, country, sector, selected_policy, hazard, disadvantage_groups
        )
        return _policy_mechanisms_section(mechanism_suggestions) + (
            selected_policy_adaptations
            or _disadvantage_group_section([], disadvantage_groups).lstrip()
        )
    document = _country_factsheet_document(db, country)
    new_proposal_reference = _new_policy_reference(
        text,
        country,
        region,
        sector,
        selected_policy,
        hazard,
        disadvantage_groups,
        mechanism_suggestions,
        new_policy_interpretation,
        document,
    )
    return new_proposal_reference


def _country_factsheet_document(db: Session, country: str | None) -> dict[str, str]:
    country_key = normalize_for_match(country or "")
    if not country_key:
        return {}
    rows = db.execute(
        select(KnowledgeDocument.title, KnowledgeDocument.source_uri)
        .where(
            KnowledgeDocument.scope == "main",
            KnowledgeDocument.source_type == HAZARD_WITH_MITIGATION_SOURCE_TYPE,
        )
        .order_by(KnowledgeDocument.source_uri)
    ).all()
    for title, uri in rows:
        source_uri = str(uri or "")
        source_name = source_uri.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if normalize_for_match(source_name) == country_key:
            return {"title": str(title or "Policy factsheet"), "source_uri": source_uri}
    return {}


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


def _existing_policy_answer(label: str, value: str) -> str:
    """Remove the form's guiding question from an existing-policy answer."""
    prompts = {
        "Title of the policy (existing)": (
            r"^Which current policy, programme, or regulation are you adapting\?\s*"
            r"Provide its official name/title if possible\.\s*"
        ),
        "Sectoral focus": r"^Which sector is the policy linked to \(energy/housing, transport, etc\.\)\?\s*",
        "Original policy objectives": r"^What was the policy originally designed to achieve\?\s*",
    }
    pattern = prompts.get(label)
    return re.sub(pattern, "", value, flags=re.IGNORECASE).strip() if pattern else value.strip()


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
    _ = hazard
    blocks = _selected_existing_policy_blocks(
        _factsheet_blocks(text, EXISTING_POLICY_FACTSHEET, "Title of the policy (existing)"),
        selected_policy, sector,
    )
    cards: list[str] = []
    challenges: list[str] = []
    stakeholders: list[str] = []
    for block in blocks[:3]:
        fields = _existing_policy_fields(block)
        adaptation = fields.get("Proposed adaptations", "")
        if adaptation:
            cards.append(
                f"{adaptation} "
                '<button class="factsheet-source-tag hazard-evidence-label--provided" '
                'type="button" '
                f'data-source-table="{escape(_existing_policy_source_table(block), quote=True)}" '
                'aria-label="Show the referenced factsheet data">Source</button>'
            )
        if fields.get("Prioritised challenge addressed"):
            challenges.append(fields["Prioritised challenge addressed"])
        if fields.get("Participatory dimension & stakeholders"):
            stakeholders.append(fields["Participatory dimension & stakeholders"])
    if not cards:
        return ""
    context = " · ".join(value for value in (country, sector) if value)
    groups = _disadvantage_group_section(blocks[:3], disadvantage_groups)
    return (
        "## Proposed adaptations\n\n"
        + (f"{context}\n\n" if context else "")
        + "\n\n".join(cards)
        + ("\n\n### Challenges to be addressed\n\n" + "\n".join(f"- {value}" for value in challenges) if challenges else "")
        + ("\n\n### Stakeholders to involve\n\n" + "\n".join(f"- {value}" for value in stakeholders) if stakeholders else "")
        + groups
    )


def unique_policy_title_match(
    candidates: list[tuple[str, str]], selected_policy: str | None,
) -> str | None:
    """Select one title by exact, contained, then unambiguous similar match."""
    selected_key = normalize_for_match(selected_policy or "")
    if not selected_key:
        return None
    exact: list[str] = []
    contained: list[str] = []
    similar: list[tuple[float, str]] = []
    for title, identifier in candidates:
        title_key = normalize_for_match(title.split("*", 1)[0])
        if title_key == selected_key:
            exact.append(identifier)
        elif len(selected_key) >= 12 and (
            f" {selected_key} " in f" {title_key} "
            or f" {title_key} " in f" {selected_key} "
        ):
            contained.append(identifier)
        else:
            selected_words = {word for word in selected_key.split() if len(word) >= 4}
            title_words = {word for word in title_key.split() if len(word) >= 4}
            shared_words = selected_words & title_words
            if len(shared_words) < 2:
                continue
            word_overlap = len(shared_words) / min(len(selected_words), len(title_words))
            shared_phrase = SequenceMatcher(None, selected_key, title_key).find_longest_match(
                0, len(selected_key), 0, len(title_key)
            ).size / min(len(selected_key), len(title_key))
            similar.append((0.65 * word_overlap + 0.35 * shared_phrase, identifier))
    if exact:
        return exact[0] if len(exact) == 1 else None
    if contained:
        return contained[0] if len(contained) == 1 else None
    similar.sort(key=lambda match: match[0], reverse=True)
    if similar and similar[0][0] >= 0.55 and (
        len(similar) == 1 or similar[0][0] - similar[1][0] >= 0.12
    ):
        return similar[0][1]
    return None


def _selected_existing_policy_blocks(
    blocks: list[str], selected_policy: str | None, sector: str | None = None
) -> list[str]:
    """Match one policy using the objectives lookup's title and sector rules."""
    sector_key = normalize_for_match(sector or "")
    candidates = []
    for block in blocks:
        block_sector = _existing_policy_answer(
            "Sectoral focus", _field(block, "Sectoral focus", _EXISTING_POLICY_FIELDS[2:])
        )
        if sector_key and block_sector and normalize_for_match(block_sector) != sector_key:
            continue
        title = _existing_policy_answer(
            "Title of the policy (existing)",
            _field(block, "Title of the policy (existing)", _EXISTING_POLICY_FIELDS[1:]),
        )
        candidates.append((title, block))
    matched = unique_policy_title_match(candidates, selected_policy)
    return [matched] if matched is not None else []


def country_factsheet_policy_objectives(
    db: Session, *, country: str | None, sector: str | None, selected_policy: str | None
) -> str:
    """Read the selected existing policy's original objectives from its country factsheet."""
    if not country or not selected_policy:
        return ""
    text = _country_factsheet_text(db, country)
    if not text:
        return ""
    blocks = _factsheet_blocks(text, EXISTING_POLICY_FACTSHEET, "Title of the policy (existing)")
    eligible = [
        block for block in blocks
        if _field(block, "Original policy objectives", _EXISTING_POLICY_FIELDS[3:])
    ]
    matched = _selected_existing_policy_blocks(eligible, selected_policy, sector)
    return (
        _existing_policy_answer(
            "Original policy objectives",
            _field(matched[0], "Original policy objectives", _EXISTING_POLICY_FIELDS[3:]),
        ) if matched else ""
    )


def country_existing_policy_adaptation_sources(
    db: Session, *, country: str | None, sector: str | None, selected_policy: str | None,
    ignore_title_match: bool = False,
) -> tuple[bool, list[dict[str, str]]]:
    """Return same-country factsheets eligible to inform a policy without one."""
    text = _country_factsheet_text(db, country)
    blocks = _factsheet_blocks(text, EXISTING_POLICY_FACTSHEET, "Title of the policy (existing)")
    if not ignore_title_match and _selected_existing_policy_blocks(blocks, selected_policy, sector):
        return True, []
    sector_key = normalize_for_match(sector or "")
    document = _country_factsheet_document(db, country)
    candidates: list[dict[str, str]] = []
    for block in blocks:
        fields = _existing_policy_fields(block)
        focus_key = normalize_for_match(fields.get("Sectoral focus", ""))
        if sector_key and focus_key and focus_key != sector_key:
            continue
        if fields.get("Original policy objectives") and fields.get("Proposed adaptations"):
            fields["_source_document"] = document.get("title", "Policy adaptation factsheet")
            fields["_source_uri"] = document.get("source_uri", "")
            candidates.append(fields)
    return False, candidates


def inferred_adaptations_reference(
    sources: list[dict[str, str]], *, adaptation_ids: list[int],
    challenge_ids: list[int], stakeholder_ids: list[int],
    adaptation_points: list[tuple[int, str]] | None = None,
    selected_policy: str | None = None,
    selected_hazard: str | None = None,
) -> str:
    """Display only factsheet text chosen by semantic relevance checks."""
    def source_line(index: int, field: str, value: str) -> str:
        source = sources[index - 1]
        rows = [
            {"field": "Document", "value": source.get("_source_document", "Policy adaptation factsheet")},
            *([{"field": "Source", "value": source["_source_uri"]}]
              if source.get("_source_uri") else []),
            {"field": "Related policy", "value": source.get("Title of the policy (existing)", "")},
            {"field": "Factsheet field", "value": field},
            {"field": "Referenced excerpt", "value": value},
            *([{"field": "Context used", "value": (
                f"Related to selected policy: {selected_policy or 'Not specified'}; "
                f"selected hazard: {selected_hazard or 'Not specified'}"
            )}] if selected_policy or selected_hazard else []),
        ]
        return f"- {value} {source_button(rows)}"

    def values(ids: list[int], field: str) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for index in ids:
            if not isinstance(index, int) or not 1 <= index <= len(sources):
                continue
            value = sources[index - 1].get(field, "").strip()
            key = normalize_for_match(value)
            if value and key not in seen:
                seen.add(key)
                result.append(source_line(index, field, value))
        return result

    adaptations = (
        [source_line(index, "Proposed adaptations", point) for index, point in adaptation_points
         if 1 <= index <= len(sources) and point.strip()]
        if adaptation_points is not None else values(adaptation_ids, "Proposed adaptations")
    )
    if not adaptations:
        return ""
    challenges = values(challenge_ids, "Prioritised challenge addressed")
    stakeholders = values(stakeholder_ids, "Participatory dimension & stakeholders")
    return "\n\n".join((
        "## Challenges to be addressed\n\n" + (
            "\n".join(challenges) if challenges else "No matching factsheet challenge was found."
        ),
        "## Proposed adaptations for the policy\n\n" + "\n".join(adaptations),
        "## Stakeholders to involve\n\n" + (
            "\n".join(stakeholders) if stakeholders else "No matching factsheet stakeholders were found."
        ),
    ))


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
    document: dict[str, str] | None = None,
) -> str:
    blocks = _rank_blocks(
        _factsheet_blocks(text, NEW_POLICY_FACTSHEET, "Title of the policy (new proposal)"),
        sector,
        selected_policy,
        hazard,
    )
    if not blocks:
        return ""
    sector_key = normalize_for_match(sector or "")
    source_blocks = [
        block for block in blocks
        if (
            focus_key := normalize_for_match(
                _field(block, "Sectoral focus", _NEW_POLICY_FIELDS[2:])
            )
        ) and (sector_key in focus_key or focus_key in sector_key)
    ] if sector_key else blocks
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
        + _policy_mechanisms_section(
            mechanism_suggestions,
            context={"policy": selected_policy or "", "hazard": hazard or "", "country": country or "", "sector": sector or ""},
        )
        + _important_new_proposal_concepts_section(
            new_policy_interpretation,
            fields=_relevant_new_policy_fields(source_blocks, hazard),
            document=document,
        )
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
    sector_key = normalize_for_match(sector or "")
    if sector_key:
        blocks = [
            block for block in blocks
            if (
                focus_key := normalize_for_match(
                    _field(block, "Sectoral focus", _NEW_POLICY_FIELDS[2:])
                )
            ) and (sector_key in focus_key or focus_key in sector_key)
        ]
    return _relevant_new_policy_fields(blocks, hazard)


def _policy_mechanisms_section(
    mechanism_suggestions: list[dict[str, object]] | None,
    *,
    context: dict[str, str] | None = None,
) -> str:
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
        button = source_button([
            {"field": "Source", "value": "Generated from selected policy and hazard context"},
            {"field": "Selected policy", "value": (context or {}).get("policy", "")},
            {"field": "Selected hazard", "value": (context or {}).get("hazard", "")},
            {"field": "Country", "value": (context or {}).get("country", "")},
            {"field": "Sector", "value": (context or {}).get("sector", "")},
            {"field": "Context used", "value": f"{mechanism}: {linkage}" if linkage else mechanism},
        ]) if context else ""
        if linkage:
            lines.append(
                f"- **{mechanism}:** {linkage}" + (f" {button}" if button else "")
            )
        else:
            lines.append(f"- **{mechanism}**" + (f" {button}" if button else ""))
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
    *,
    fields: list[dict[str, str]] | None = None,
    document: dict[str, str] | None = None,
) -> str:
    """Show distinct, original explanations without raw fact sheet fields."""
    if not isinstance(interpretation, dict):
        return ""
    headings = (
        ("challenge", "Challenges to be addressed", "Prioritised challenge addressed"),
        ("approach", "Proposed adaptations for the policy", "Policy description"),
        ("stakeholders", "Possible stakeholders to involve", "Participatory dimension & stakeholders"),
    )
    sections: list[str] = []
    for key, heading, field_label in headings:
        values = interpretation.get(key)
        if not isinstance(values, list):
            continue
        bullets = [str(value).strip() for value in values if str(value).strip()]
        if bullets:
            excerpts = list(dict.fromkeys(
                str(item.get(field_label) or "").strip() for item in (fields or [])
                if str(item.get(field_label) or "").strip()
            ))
            rows = [
                {"field": "Document", "value": (document or {}).get("title", "Policy factsheet")},
                *([{"field": "Source", "value": document["source_uri"]}]
                  if document and document.get("source_uri") else []),
                {"field": "Factsheet field", "value": field_label},
                {"field": "Referenced excerpt", "value": "\n\n".join(excerpts)},
            ]
            sections.append(f"### {heading}\n\n" + "\n".join(
                f"- {value} {source_button([*rows, {'field': 'Context used', 'value': value}])}"
                if excerpts else f"- {value}"
                for value in bullets
            ))
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
    "Prioritised challenge addressed",
    "Limitations or gaps",
    "Proposed adaptations",
    "Policy type(s)",
    "Participatory dimension & stakeholders",
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
        value = _existing_policy_answer(
            label, _field(block, label, _EXISTING_POLICY_FIELDS[index + 1 :])
        )
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
