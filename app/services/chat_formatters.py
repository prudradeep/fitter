import json
import re
from html import escape
from urllib.parse import urlsplit

from app.services.chat_session import ChatSession


ADDITIONAL_HAZARDS_INFO_TOOLTIP = (
    "The Experts primarily involved: Policy experts and policymakers; academic researchers "
    "and universities; think tanks and research institutes; civil society organisations; "
    "NGOs and advocacy organisations; trade unions and labour organisations; housing and "
    "energy sector experts; social service and welfare organisations; environmental and "
    "climate organisations; and community/intermediary organisations representing "
    "disadvantaged groups. The project intentionally recruited intermediary organisations "
    "and policy experts instead of individual disadvantaged citizens, because the workshops "
    "required enough technical and policy expertise to analyse transition policies and "
    "co-design mitigation measures. This also helped reduce power imbalances and represent "
    "disadvantaged groups' interests more broadly."
)

CUSTOM_HAZARDS_INFO_TOOLTIP = (
    "Co-created hazards accepted in strict mode can be shared when Crowd Sourcing "
    "is enabled. Easy-mode hazards remain private to the creator."
)


def _normalize_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _additional_hazards_info_icon() -> str:
    return (
        '<span class="additional-hazards-info" tabindex="0" '
        f'aria-label="{escape(ADDITIONAL_HAZARDS_INFO_TOOLTIP)}">'
        '<span aria-hidden="true">i</span>'
        '<span class="additional-hazards-tooltip" aria-hidden="true">'
        "<strong>The Experts involved in</strong>"
        "<span>The experts primarily involved: policy experts and policymakers, academic "
        "researchers and universities, think tanks and research institutes, CSOs, NGOs "
        "and advocacy organisations, trade unions and labour organisations, housing and "
        "energy sector experts, social service and welfare organisations, environmental "
        "and climate organisations, and community/intermediary organisations representing "
        "disadvantaged groups.</span>"
        "<span>The project intentionally recruited intermediary organisations and policy "
        "experts instead of individual disadvantaged citizens because the workshops required "
        "technical and policy expertise to analyse transition policies and co-design "
        "mitigation measures. This also reduced power imbalances and supported broader "
        "representation of disadvantaged groups' interests.</span>"
        "</span>"
        "</span>"
    )


def _additional_hazards_methodology_cta() -> str:
    return (
        '<button class="additional-hazards-methodology-cta" '
        'type="button" data-open-methodology="true">'
        "Show methodologies"
        "</button>"
    )


def _custom_hazards_info_icon() -> str:
    tooltip = escape(CUSTOM_HAZARDS_INFO_TOOLTIP)
    return (
        '<span class="additional-hazards-info" tabindex="0" '
        f'aria-label="{tooltip}">'
        '<span aria-hidden="true">i</span>'
        '<span class="additional-hazards-tooltip" aria-hidden="true">'
        "<strong>Platform users</strong>"
        f"<span>{tooltip}</span>"
        "</span>"
        "</span>"
    )


def _platform_users_source_button() -> str:
    return (
        '<button class="platform-users-source-button" type="button" '
        'data-open-platform-users="true">Platform users</button>'
    )


def _custom_hazard_visibility_label(session: ChatSession) -> str:
    if session.validation_mode == "strict" and session.crowd_sourcing_enabled:
        return "Global Visibility"
    return "Private Visibility"


def evidence_is_provided(value: object) -> bool:
    evidence = str(value or "").strip()
    return bool(evidence) and evidence.casefold() != "not provided"


def evidence_for_display(value: object) -> str:
    """Remove workflow-only evidence metadata from user-facing text."""
    evidence = normalize_markdown_text(str(value or ""))
    evidence = re.sub(
        r"(?im)^\s*Temporary evidence document ID:\s*[A-Za-z0-9-]+\s*$",
        "",
        evidence,
    ).strip()
    return evidence or "Not provided"


def _custom_hazard_has_evidence(session: ChatSession, hazard: str) -> bool:
    key = _normalize_key(hazard)
    statuses = session.custom_hazard_evidence_statuses or {}
    if key in statuses:
        return bool(statuses[key])
    if key == _normalize_key(session.accepted_custom_hazard):
        return evidence_is_provided(session.accepted_custom_hazard_evidence)
    return False


def _custom_hazard_evidence(session: ChatSession, hazard: str) -> str:
    key = _normalize_key(hazard)
    evidence = (session.custom_hazard_evidence or {}).get(key)
    if not evidence and key == _normalize_key(session.accepted_custom_hazard):
        evidence = session.accepted_custom_hazard_evidence
    return evidence_for_display(evidence) if evidence_is_provided(evidence) else ""


def _custom_hazard_summary(session: ChatSession, hazard: str) -> str:
    key = _normalize_key(hazard)
    summary = (session.custom_hazard_summaries or {}).get(key)
    if not summary and key == _normalize_key(session.accepted_custom_hazard):
        summary = session.accepted_custom_hazard_summary
    return str(summary or "").strip()


def _evidence_http_url(evidence: str) -> str:
    match = re.search(r"(?im)^\s*Evidence URL:\s*(.+?)\s*$", evidence)
    candidate = match.group(1).strip() if match else evidence.strip()
    markdown_link = re.fullmatch(
        r"\[[^\]]*\]\(\s*(https?://[^\s)]+)(?:\s+['\"][^)]*['\"])?\s*\)",
        candidate,
        flags=re.IGNORECASE,
    )
    if markdown_link:
        candidate = markdown_link.group(1)
    elif candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1].strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def format_hazards(session: ChatSession, *, show_admin_details: bool = False) -> str:
    survey_hazards = [
        hazard for hazard in (session.hazards or []) if _hazard_has_profiles(session, hazard)
    ]
    survey_source_button = (
        '<button class="survey-source-button" type="button" '
        'data-open-survey-results="true">From the survey</button>'
    )
    sections = [
        f'<h3 class="hazard-group-heading">Top 3 {survey_source_button}</h3>',
        format_system_hazards(
            session,
            survey_hazards[:3],
            show_admin_details=show_admin_details,
        ),
        "",
        f'<h3 class="hazard-group-heading">Other hazards {survey_source_button}</h3>',
        format_system_hazards(
            session,
            survey_hazards[3:],
            show_admin_details=show_admin_details,
        ),
    ]
    if any(
        _hazard_has_profiles(session, hazard)
        for hazard in (session.custom_hazards or [])
    ):
        sections.extend(
            [
                "",
                '<h3 class="hazard-group-heading hazard-group-heading--with-info">'
                "Co-Created hazards "
                f"{_platform_users_source_button()}"
                f"{_custom_hazards_info_icon()}"
                "</h3>",
                format_custom_hazards(
                    session,
                    show_admin_details=show_admin_details,
                ),
            ]
        )
    if session.additional_hazards:
        sections.extend(
            [
                "",
                '<h3 class="hazard-group-heading hazard-group-heading--with-info">'
                "Additional hazards "
                "<span>By experts</span>"
                f"{_additional_hazards_info_icon()}"
                f"{_additional_hazards_methodology_cta()}"
                "</h3>",
                format_additional_hazards(
                    session,
                    show_admin_details=show_admin_details,
                ),
            ]
        )
    return "\n".join(sections)


def format_system_hazards(
    session: ChatSession,
    hazards: list[str] | None = None,
    *,
    show_admin_details: bool = False,
) -> str:
    hazards = list(session.hazards or []) if hazards is None else hazards
    if not hazards:
        return "- No hazards in this category."
    lines: list[str] = []
    for hazard in hazards:
        display_hazard = _clean_hazard_display_name(hazard)
        lines.append(
            '<article class="hazard-card">'
            '<div class="hazard-card-heading">'
            '<span class="hazard-alert-icon" aria-hidden="true">!</span>'
            f"<strong>{escape(display_hazard)}</strong>"
            "</div>"
        )
        _append_hazard_ranking(lines, session, hazard)
        _append_hazard_profiles(
            lines,
            session,
            hazard,
            show_admin_details=show_admin_details,
        )
        lines.append("</article>")
    return "\n".join(lines)


def format_custom_hazards(
    session: ChatSession,
    *,
    show_admin_details: bool = False,
) -> str:
    hazards = [
        hazard
        for hazard in (session.custom_hazards or [])
        if _hazard_has_profiles(session, hazard)
    ]
    if not hazards:
        return ""
    lines: list[str] = []
    for hazard in hazards:
        has_evidence = _custom_hazard_has_evidence(session, str(hazard))
        evidence = _custom_hazard_evidence(session, str(hazard)) if has_evidence else ""
        evidence_url = _evidence_http_url(evidence) if evidence else ""
        summary = _custom_hazard_summary(session, str(hazard))
        evidence_label = "Evidence provided" if has_evidence else "Evidence not provided"
        evidence_class = "provided" if has_evidence else "not-provided"
        if has_evidence:
            evidence_action = (
                '<button type="button" '
                f'class="hazard-evidence-label hazard-evidence-label--{evidence_class}" '
                f'data-evidence-url="{escape(evidence_url, quote=True)}" '
                f'data-evidence-text="{escape(evidence, quote=True)}" '
                f'aria-label="View evidence for {escape(str(hazard), quote=True)}">'
                f"{evidence_label}</button>"
            )
        else:
            evidence_action = (
                f'<span class="hazard-evidence-label hazard-evidence-label--{evidence_class}">'
                f"{evidence_label}</span>"
            )
        lines.append(
            '<article class="hazard-card">'
            '<div class="hazard-card-heading">'
            '<span class="hazard-alert-icon" aria-hidden="true">!</span>'
            f"<strong>{escape(str(hazard))}</strong>"
            '<span class="hazard-card-labels">'
            f'<span class="hazard-visibility-label">{escape(_custom_hazard_visibility_label(session))}</span>'
            f"{evidence_action}"
            "</span>"
            "</div>"
        )
        if summary:
            lines.append(
                '<details class="hazard-card-summary">'
                '<summary>Hazard summary</summary>'
                f"<p>{escape(summary)}</p>"
                "</details>"
            )
        _append_hazard_profiles(
            lines,
            session,
            hazard,
            show_admin_details=show_admin_details,
        )
        lines.append("</article>")
    return "\n".join(lines)


def format_additional_hazards(
    session: ChatSession,
    *,
    show_admin_details: bool = False,
) -> str:
    hazards = [
        str(hazard).strip()
        for hazard in (session.additional_hazards or [])
        if str(hazard).strip()
    ]
    if not hazards:
        return ""
    lines: list[str] = []
    for hazard in hazards:
        lines.append(
            '<article class="hazard-card hazard-card--additional">'
            '<div class="hazard-card-heading">'
            '<span class="hazard-alert-icon" aria-hidden="true">!</span>'
            f"<strong>{escape(hazard)}</strong>"
            "</div>"
        )
        _append_hazard_profiles(
            lines,
            session,
            hazard,
            show_admin_details=show_admin_details,
        )
        lines.append("</article>")
    return "\n".join(lines)


def _append_hazard_profiles(
    lines: list[str],
    session: ChatSession,
    hazard: str,
    *,
    show_admin_details: bool = False,
) -> None:
    profiles = session.hazard_profiles or {}
    profile_values = profiles.get(hazard)
    if isinstance(profile_values, str):
        profile_list = [profile_values]
    else:
        profile_list = list(profile_values or [])
    ranking = (session.hazard_rankings or {}).get(hazard, {})
    ranked_profiles = ranking.get("profiles", []) if isinstance(ranking, dict) else []
    population_by_profile = {
        normalize_markdown_text(str(item.get("name") or item.get("profile") or "")).casefold(): item
        for item in ranked_profiles
        if isinstance(item, dict)
    }
    profile_items: list[dict[str, object]] = []
    for profile in profile_list:
        if isinstance(profile, dict):
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            explanation = str(profile.get("explanation") or "").strip()
            variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip()
            variable_type = str(profile.get("variable_type") or "").strip()
            statistical_basis = str(profile.get("statistical_basis") or "").strip()
            source = str(profile.get("source") or "").strip()
            target_population_labels = _list_from_profile_or_metadata(
                profile,
                "target_population_labels",
            )
            population_lookup_labels = _list_from_profile_or_metadata(
                profile,
                "population_lookup_labels",
            )
        else:
            name = str(profile).strip()
            explanation = ""
            variable_name = ""
            variable_type = ""
            statistical_basis = ""
            source = ""
            target_population_labels = []
            population_lookup_labels = []
        if not name:
            continue
        population = population_by_profile.get(normalize_markdown_text(name).casefold(), {})
        regional, national = _profile_population_values(profile, population, explanation)
        explanation = _without_population_sentence(explanation)
        profile_items.append(
            {
                "name": name,
                "explanation": explanation,
                "variable_name": variable_name,
                "variable_type": variable_type,
                "statistical_basis": statistical_basis,
                "source": source,
                "target_population_labels": target_population_labels,
                "population_lookup_labels": population_lookup_labels,
                "regional": regional,
                "national": national,
            }
        )
    profile_items = _combine_covered_profile_items(profile_items)
    profile_rows = [
        _render_profile_row(item, show_admin_details=show_admin_details)
        for item in profile_items
    ]
    if not profile_rows:
        return
    count = len(profile_rows)
    region = escape(str(session.region or "the selected region"))
    profile_label = "profile" if count == 1 else "profiles"
    lines.append(
        "<details class=\"hazard-profiles\">"
        f"<summary>Influence on <strong>{region}</strong>"
        f" <span>({count} socio-demographic {profile_label})</span></summary>"
        '<div class="hazard-population-table"><table>'
        '<thead><tr><th scope="col">Affected population profile</th>'
        '<th scope="col">Regional</th><th scope="col">National</th></tr></thead>'
        f"<tbody>{''.join(profile_rows)}</tbody></table></div>"
        "</details>"
    )


def _list_from_profile_or_metadata(profile: dict[str, object], key: str) -> list[object]:
    value = profile.get(key)
    if isinstance(value, list) and value:
        return list(value)
    metadata = profile.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _group_hazard_profile_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    ordered: list[dict[str, object]] = []
    for item in items:
        group_info = _target_population_group_info(item)
        if group_info is None:
            ordered.append(item)
            continue
        question, options = group_info
        key = normalize_markdown_text(question).casefold()
        group = grouped.get(key)
        if group is None:
            group = {
                "name": _display_target_population_question(question),
                "explanation": "",
                "variable_name": question,
                "variable_type": "",
                "statistical_basis": "",
                "source": "target_population_group",
                "target_population_labels": [],
                "population_lookup_labels": [],
                "regional_values": [],
                "national_values": [],
                "options": [],
                "is_grouped_target_population": True,
            }
            grouped[key] = group
            ordered.append(group)
        for option in options:
            _append_unique(group["options"], option)
        for label in item.get("target_population_labels", []):
            _append_unique(group["target_population_labels"], str(label))
        for label in item.get("population_lookup_labels", []):
            _append_unique(group["population_lookup_labels"], str(label))
        if item.get("statistical_basis") and not group.get("statistical_basis"):
            group["statistical_basis"] = str(item.get("statistical_basis") or "")
        regional = _numeric_population(item.get("regional"))
        national = _numeric_population(item.get("national"))
        if regional is not None:
            group["regional_values"].append(regional)
        if national is not None:
            group["national_values"].append(national)

    for item in ordered:
        if item.get("is_grouped_target_population"):
            item["regional"] = _average_population(item.get("regional_values"))
            item["national"] = _average_population(item.get("national_values"))
    return ordered


def _combine_covered_profile_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    if len(items) < 2:
        return items

    label_sets = [_mapped_label_key_set(item) for item in items]
    covered_by: dict[int, int] = {}
    for child_index, child_labels in enumerate(label_sets):
        if not child_labels:
            continue
        parent_candidates = [
            (len(parent_labels), parent_index)
            for parent_index, parent_labels in enumerate(label_sets)
            if parent_index != child_index
            and child_labels < parent_labels
        ]
        if parent_candidates:
            _, parent_index = max(parent_candidates)
            covered_by[child_index] = parent_index

    if not covered_by:
        return items

    combined = [dict(item) for item in items]
    for child_index, parent_index in covered_by.items():
        child_name = str(items[child_index].get("name") or "").strip()
        if not child_name:
            continue
        covered_names = combined[parent_index].setdefault("covered_profile_names", [])
        _append_unique(covered_names, child_name)

    return [
        item
        for index, item in enumerate(combined)
        if index not in covered_by
    ]


def _mapped_label_key_set(item: dict[str, object]) -> set[str]:
    labels = item.get("target_population_labels")
    if not isinstance(labels, list) or not labels:
        labels = item.get("population_lookup_labels")
    if not isinstance(labels, list):
        return set()
    return {
        normalize_markdown_text(str(label)).casefold()
        for label in labels
        if str(label).strip()
    }


def _target_population_group_info(item: dict[str, object]) -> tuple[str, list[str]] | None:
    labels = [
        str(label).strip()
        for label in item.get("target_population_labels", [])
        if str(label).strip()
    ]
    label_groups = [_split_target_population_label(label) for label in labels]
    label_groups = [group for group in label_groups if group is not None]
    if label_groups:
        question = label_groups[0][0]
        options = [option for group_question, option in label_groups if group_question == question]
        return question, options or [str(item.get("name") or "").strip()]

    source = str(item.get("source") or "").strip().casefold()
    statistical_basis = str(item.get("statistical_basis") or "").strip().casefold()
    question = str(item.get("variable_name") or "").strip()
    name = str(item.get("name") or "").strip()
    if (
        question
        and name
        and (
            source == "target_population"
            or "user-selected socio-demographic question response" in statistical_basis
        )
    ):
        return question, [_target_population_option_label(question, name)]
    return None


def _split_target_population_label(label: str) -> tuple[str, str] | None:
    if ":" not in label:
        return None
    question, option = [part.strip() for part in label.split(":", 1)]
    if not question or not option:
        return None
    return question, option


def _target_population_option_label(question: str, profile_name: str) -> str:
    question = question.strip().rstrip(".")
    profile_name = profile_name.strip()
    if not question:
        return profile_name
    if normalize_markdown_text(profile_name).casefold() == normalize_markdown_text(question).casefold():
        return "Yes"
    not_prefix = f"Not {question[:1].lower()}{question[1:]}"
    if normalize_markdown_text(profile_name).casefold() == normalize_markdown_text(not_prefix).casefold():
        return "No"
    prefix = f"{question}:"
    if normalize_markdown_text(profile_name).casefold().startswith(
        normalize_markdown_text(prefix).casefold()
    ):
        return profile_name.split(":", 1)[1].strip()
    if "age" in normalize_markdown_text(question).casefold() and profile_name.casefold().startswith("age "):
        return profile_name[4:].strip()
    return profile_name


def _display_target_population_question(question: str) -> str:
    cleaned = re.sub(r"\s+", " ", question.strip().rstrip("."))
    normalized = normalize_markdown_text(cleaned).casefold()
    aliases = {
        "age range": "Age group",
    }
    return aliases.get(normalized, cleaned)


def _append_unique(values: object, value: str) -> None:
    if not isinstance(values, list):
        return
    cleaned = re.sub(r"\s+", " ", value).strip()
    if cleaned and cleaned.casefold() not in {str(item).casefold() for item in values}:
        values.append(cleaned)


def _render_profile_row(
    item: dict[str, object],
    *,
    show_admin_details: bool = False,
) -> str:
    name = str(item.get("name") or "").strip()
    variable_name = str(item.get("variable_name") or "").strip()
    variable_type = str(item.get("variable_type") or "").strip()
    regional = item.get("regional")
    national = item.get("national")
    description_parts = []
    covered_profile_names = item.get("covered_profile_names")
    if isinstance(covered_profile_names, list) and covered_profile_names:
        description_parts.append(
            "Combined profiles: "
            + escape("; ".join(str(name) for name in covered_profile_names if str(name).strip()))
        )
    if item.get("is_grouped_target_population"):
        options = [
            str(option).strip()
            for option in item.get("options", [])
            if str(option).strip()
        ]
        if options:
            description_parts.append("Selected options: " + escape("; ".join(options)))
    explanation = str(item.get("explanation") or "").strip()
    if explanation and not show_admin_details:
        explanation = _strip_profile_admin_detail_lines(explanation)
    if explanation:
        description_parts.append(escape(explanation))
    statistical_basis = str(item.get("statistical_basis") or "").strip()
    if show_admin_details and statistical_basis:
        description_parts.append(f"Reference: {escape(statistical_basis)}")
    target_population_labels = item.get("target_population_labels")
    population_lookup_labels = item.get("population_lookup_labels")
    if (
        show_admin_details
        and not item.get("is_grouped_target_population")
        and isinstance(target_population_labels, list)
        and target_population_labels
    ):
        description_parts.append(
            "Mapped target population: "
            + escape("; ".join(str(label) for label in target_population_labels if str(label).strip()))
        )
    elif (
        show_admin_details
        and not item.get("is_grouped_target_population")
        and isinstance(population_lookup_labels, list)
        and population_lookup_labels
    ):
        description_parts.append(
            "Mapped target population: "
            + escape("; ".join(str(label) for label in population_lookup_labels if str(label).strip()))
        )
    if (
        show_admin_details
        and isinstance(population_lookup_labels, list)
        and population_lookup_labels
    ):
        description_parts.append(
            "Eurostat population lookup: "
            + escape("; ".join(str(label) for label in population_lookup_labels if str(label).strip()))
        )
    description = (
        f"<small>{'<br>'.join(description_parts)}</small>"
        if description_parts
        else ""
    )
    macro_label = (
        '<span class="profile-type-label">macro</span>'
        if _is_macro_profile(variable_name, variable_type)
        else ""
    )
    return (
        "<tr>"
        f'<th scope="row"><strong>{escape(name)}</strong>{macro_label}{description}</th>'
        f'<td>{_format_population(regional)}{_population_comparison(regional, national)}</td>'
        f'<td>{_format_population(national)}</td>'
        "</tr>"
    )


def _numeric_population(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average_population(values: object) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    numeric_values = [
        value for value in (_numeric_population(item) for item in values) if value is not None
    ]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _format_population(value: object) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _is_macro_profile(variable_name: str, variable_type: str = "") -> bool:
    return (
        variable_type.strip().casefold() == "macro"
        or variable_name.strip().casefold().startswith("macro_")
    )


def _strip_profile_admin_detail_lines(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not re.match(
            r"\s*(?:Reference|Plain[- ]English|Mapped target population|"
            r"Eurostat population lookup):",
            line,
            flags=re.IGNORECASE,
        )
    ).strip()


def _profile_population_values(
    profile: object, population: object, explanation: str
) -> tuple[object, object]:
    profile_data = profile if isinstance(profile, dict) else {}
    population_data = population if isinstance(population, dict) else {}
    regional = profile_data.get("regional_population_pct") or profile_data.get("population_pct")
    national = profile_data.get("national_population_pct")
    regional = regional if regional is not None else population_data.get("population_pct")
    national = national if national is not None else population_data.get("national_population_pct")
    if regional is not None and national is not None:
        return regional, national
    match = re.search(
        r"(?i)(?:represents about|population share is)\s+([0-9]+(?:\.[0-9]+)?)%"
        r"(?:\s+of the regional population, compared with|\s+regionally and)\s+"
        r"([0-9]+(?:\.[0-9]+)?)%\s+nationally",
        explanation,
    )
    return (match.group(1), match.group(2)) if match else (regional, national)


def _without_population_sentence(explanation: str) -> str:
    cleaned = re.sub(
        r"(?i)\s*(?:This profile represents about [0-9.]+% of the regional population, "
        r"compared with [0-9.]+% nationally\.|Across \d+ matched Eurostat profiles, the average "
        r"population share is [0-9.]+% regionally and [0-9.]+% nationally\.)",
        "",
        explanation,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _population_comparison(regional: object, national: object) -> str:
    try:
        difference = float(regional) - float(national)
    except (TypeError, ValueError):
        return ""
    if abs(difference) < 0.05:
        return '<span class="population-trend is-equal" title="Equal to national" aria-label="equal to national">•</span>'
    if difference > 0:
        return '<span class="population-trend is-up" title="Higher than national" aria-label="higher than national">↑</span>'
    return '<span class="population-trend is-down" title="Lower than national" aria-label="lower than national">↓</span>'


def _append_hazard_ranking(lines: list[str], session: ChatSession, hazard: str) -> None:
    rankings = session.hazard_rankings or {}
    ranking = rankings.get(hazard)
    if ranking is None:
        hazard_key = normalize_markdown_text(hazard).casefold()
        for stored_hazard, stored_ranking in rankings.items():
            if normalize_markdown_text(str(stored_hazard)).casefold() == hazard_key:
                ranking = stored_ranking
                break
    if not isinstance(ranking, dict):
        return
    relevance = _format_score(ranking.get("relevance_score"))
    salience = _format_score(ranking.get("salience_score"))
    effect = _format_score(ranking.get("effect_size_score"))
    reach = _format_score(ranking.get("reach_score"))
    metrics = (
        (
            "Relevance",
            relevance,
            "Overall ranking score used to order hazards. Calculation: Salience + "
            "Effect size + Reach. Higher means the hazard combines stronger concern, "
            "stronger profile association, and/or broader affected population reach.",
        ),
        (
            "Salience",
            salience,
            "How prominent this hazard is in the sectoral survey data. "
            "Calculation: average concern score × ratio of respondents above "
            "the high-concern threshold(>12). Higher means more people "
            "are strongly concerned and/or concern is more intense.",
        ),
        (
            "Effect size",
            effect,
            "Average absolute log odds ratio across confirmed positive "
            "predictors for the hazard. Odds ratio is defined as the odds "
            "of a person rating a hazard as severe given a set of affected "
            "population groups.",
        ),
        (
            "Reach",
            reach,
            "Average population percentage in the region across all the affected population groups, "
            "Calculation: average of regional population share of per affected population "
            "profile, as availbale on Eurostat.",
        ),
    )
    raw_values = [ranking.get(key) for key in (
        "relevance_score", "salience_score", "effect_size_score", "reach_score"
    )]
    metric_items = "".join(
        f'<div class="metric-tile" data-value="{escape(str(raw_value), quote=True)}">'
        f"<dt>{_metric_label_html(label, tooltip)}</dt><dd>{value}</dd></div>"
        for (label, value, tooltip), raw_value in zip(metrics, raw_values)
    )
    lines.append(f'<dl class="hazard-metrics">{metric_items}</dl>')


def _metric_label_html(label: str, tooltip: str) -> str:
    label_text = escape(label)
    if not tooltip:
        return label_text
    tooltip_text = escape(tooltip)
    return (
        '<span class="metric-label">'
        f"{label_text}"
        '<span class="metric-info" tabindex="0" role="button" '
        f'aria-label="{tooltip_text}">'
        "?"
        f'<span class="metric-tooltip" role="tooltip">{tooltip_text}</span>'
        "</span>"
        "</span>"
    )


def _format_score(value: object, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.2f}{suffix}"


def format_additional_dgs(session: ChatSession) -> str:
    if not session.additional_dgs:
        return "- No additional socio-demographic profiles were added."
    return "\n".join(f"- {dg}." for dg in session.additional_dgs)


def format_all_dgs(session: ChatSession) -> str:
    sections: list[str] = []
    assistant_profile_keys = {
        _normalize_key(profile)
        for profile in (session.socio_demographic_profiles or [])
        if str(profile or "").strip()
    }
    if session.socio_demographic_profiles:
        sections.append(
            "Socio-demographic profiles identified by the assistant:\n"
            + "\n".join(f"- {profile}." for profile in session.socio_demographic_profiles)
        )
    elif session.socio_demographic_findings:
        sections.append(session.socio_demographic_findings.strip())
    additional_dgs = [
        dg
        for dg in (session.additional_dgs or [])
        if _normalize_key(dg) not in assistant_profile_keys
    ]
    if additional_dgs:
        sections.append(
            "Additional socio-demographic profiles added by the user:\n"
            + "\n".join(f"- {dg}." for dg in additional_dgs)
        )
    if not sections:
        return "- Use the socio-demographic profiles identified in the previous response."
    return "\n\n".join(sections)


def format_evaluation_answers(
    session: ChatSession,
    historical_series: list[dict[str, object]] | None = None,
) -> str:
    if not session.evaluation_answers:
        return "- No evaluation answers were recorded."

    lines: list[str] = []
    current_category = None
    for answer in session.evaluation_answers:
        category = str(answer["category"])
        if category != current_category:
            lines.append(f"\n### {category}")
            current_category = category

        title = normalize_markdown_text(
            str(answer.get("chart_title") or answer["question"])
        )
        lines.append(f"\n**{title}: {answer['score']} / 10**")
        if answer.get("reason"):
            lines.append(f"\n- **Reason:** {answer['reason']}")
        if answer.get("evidence"):
            lines.append(f"- **Evidence:** {answer['evidence']}")

    labels = [
        normalize_markdown_text(str(answer.get("chart_title") or answer["question"]))
        for answer in session.evaluation_answers
    ]
    categories = [str(answer["category"]) for answer in session.evaluation_answers]
    scores = [int(answer["score"]) for answer in session.evaluation_answers]
    series = [
        {
            "name": f"Current — {session.mitigation_measure or 'Mitigation measure'}",
            "values": scores,
            "current": True,
        },
        *(historical_series or []),
    ]
    chart = (
        '\n<div class="evaluation-radar-chart js-evaluation-radar-chart" '
        f'data-labels="{escape(json.dumps(labels), quote=True)}" '
        f'data-categories="{escape(json.dumps(categories), quote=True)}" '
        f'data-values="{escape(json.dumps(scores), quote=True)}" '
        f'data-series="{escape(json.dumps(series), quote=True)}" '
        'role="img" aria-label="Radar chart of evaluation answers from 1 to 10"></div>'
    )
    return "\n".join(lines).strip() + chart


def hazard_names(session: ChatSession) -> list[str]:
    hazards = [
        hazard for hazard in (session.hazards or []) if _hazard_has_profiles(session, hazard)
    ]
    hazards.extend(
        hazard
        for hazard in (session.custom_hazards or [])
        if _hazard_has_profiles(session, hazard)
    )
    hazards.extend(
        hazard
        for hazard in (session.additional_hazards or [])
        if _hazard_has_profiles(session, hazard)
    )
    return hazards


def _hazard_has_profiles(session: ChatSession, hazard: str) -> bool:
    profiles = session.hazard_profiles or {}
    values = profiles.get(hazard)
    if values is None:
        key = str(hazard or "").strip().casefold()
        values = next(
            (
                stored_values
                for stored_hazard, stored_values in profiles.items()
                if str(stored_hazard or "").strip().casefold() == key
            ),
            None,
        )
    items = [values] if isinstance(values, str) else list(values or [])
    return any(
        (
            isinstance(item, dict)
            and bool(str(item.get("name") or item.get("profile") or "").strip())
        )
        or (isinstance(item, str) and bool(item.strip()))
        for item in items
    )


def _clean_hazard_display_name(value: str) -> str:
    cleaned = re.sub(r"(?i)^HAZARD\s+\d+\.\s*", "", str(value or "")).strip()
    return "\n".join(
        line
        for line in cleaned.splitlines()
        if not re.fullmatch(r"[─═\-_=]{6,}", line.strip())
    ).strip()


def normalize_markdown_text(value: str) -> str:
    return (
        value.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def build_validation_context(sector_prompt: str, session: ChatSession) -> str:
    hazards = "\n".join(f"- {item}" for item in (session.hazards or []))
    stats = extract_sector_stats(sector_prompt, session.sector)
    relevant_evidence = extract_relevant_prompt_sections(sector_prompt)
    return f"""
Sector: {session.sector}
Country: {session.country}
Region: {session.region}

Existing hazards:
{hazards or "- No generated hazards are available."}

Compact sector statistics:
{stats}

Relevant statistical context:
{relevant_evidence}
""".strip()


def extract_relevant_prompt_sections(prompt: str, max_chars: int = 12000) -> str:
    markers = [
        "THE EXACT 7 ENERGY SECTOR HAZARDS",
        "HAZARD COMPARISON",
        "EXECUTIVE SUMMARY",
        "PLAIN-ENGLISH RESULTS REPORT",
        "STATISTICAL RESULTS",
    ]
    snippets: list[str] = []

    for marker in markers:
        index = prompt.find(marker)
        if index == -1:
            continue
        snippets.append(prompt[index : index + 2500])

    if not snippets:
        snippets.append(prompt[:max_chars])

    combined = "\n\n---\n\n".join(snippets)
    return combined[:max_chars]


def extract_sector_stats(prompt: str, sector: str | None) -> str:
    stats: list[str] = [f"- Prompt source: `{sector or 'Selected sector'}_system_prompt.txt`"]

    model_n = re.search(r'"n"\s*:\s*(\d+)', prompt)
    if model_n:
        stats.append(f"- Model sample used in results: **n={model_n.group(1)}**")

    mcfadden = re.search(r'"mcfadden_interpretation"\s*:\s*"([^"]+)"', prompt)
    if mcfadden:
        stats.append(f"- McFadden model fit: {clean_prompt_text(mcfadden.group(1))}")

    nagelkerke = re.search(r'"nagelkerke_interpretation"\s*:\s*"([^"]+)"', prompt)
    if nagelkerke:
        stats.append(f"- Nagelkerke model fit: {clean_prompt_text(nagelkerke.group(1))}")

    lasso_total = re.search(r'"n_total"\s*:\s*(\d+)', prompt)
    lasso_selected = re.search(r'"n_selected"\s*:\s*(\d+)', prompt)
    if lasso_total and lasso_selected:
        stats.append(
            f"- LASSO selected **{lasso_selected.group(1)}** of "
            f"**{lasso_total.group(1)}** candidate predictors."
        )

    hazard_matches = re.findall(
        r"HAZARD\s+\d+:\s*(.+?)\n\s*Mean score:\s*([^\n]+)",
        prompt,
        flags=re.IGNORECASE,
    )
    if hazard_matches:
        stats.append("- Hazard ranking from the prompt:")
        for hazard, score_line in hazard_matches[:7]:
            stats.append(f"  - **{hazard.strip()}** — {score_line.strip()}")

    if len(stats) == 1:
        stats.append("- Sector-specific statistical details are loaded in the TXT prompt.")

    return "\n".join(stats)


def clean_prompt_text(value: str) -> str:
    return (
        value.replace(r"\u2014", "-")
        .replace(r"\u2013", "-")
        .replace(r"\u00b2", "2")
        .replace(r"\u03bb", "lambda")
    )
