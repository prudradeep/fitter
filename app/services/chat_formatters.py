import re

from app.services.chat_session import ChatSession


def format_hazards(session: ChatSession) -> str:
    sections = [
        "### Sector Hazards from FITTER Survey",
        format_system_hazards(session),
    ]
    if session.custom_hazards:
        sections.extend(
            [
                "",
                "### Additional hazards specific to your region",
                format_custom_hazards(session),
            ]
        )
    return "\n".join(sections)


def format_system_hazards(session: ChatSession) -> str:
    hazards = list(session.hazards or [])
    if not hazards:
        return "- No hazards were returned by the LLM for the loaded sector prompt."
    lines: list[str] = []
    for hazard in hazards:
        display_hazard = _clean_hazard_display_name(hazard)
        lines.append(f"- **{display_hazard}**")
        _append_hazard_ranking(lines, session, hazard)
        _append_hazard_profiles(lines, session, hazard)
    return "\n".join(lines)


def format_custom_hazards(session: ChatSession) -> str:
    hazards = list(session.custom_hazards or [])
    if not hazards:
        return ""
    lines: list[str] = []
    for hazard in hazards:
        lines.append(f"- **{hazard}**")
        _append_hazard_profiles(lines, session, hazard)
    return "\n".join(lines)


def _append_hazard_profiles(lines: list[str], session: ChatSession, hazard: str) -> None:
    profiles = session.hazard_profiles or {}
    profile_values = profiles.get(hazard)
    if isinstance(profile_values, str):
        profile_list = [profile_values]
    else:
        profile_list = list(profile_values or [])
    for profile in profile_list:
        if isinstance(profile, dict):
            name = str(profile.get("name") or "").strip()
            explanation = str(profile.get("explanation") or "").strip()
            if not name:
                continue
            if explanation:
                lines.append(f"     - **{name}** — {explanation}")
            else:
                lines.append(f"     - **{name}**")
        else:
            lines.append(f"     - **{profile}**")


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
    lines.append(
        "     - Relevance "
        f"**{relevance}** | Salience {salience} | EffectSize {effect} | Reach {reach}"
    )
    profiles = ranking.get("profiles")
    if isinstance(profiles, list) and profiles:
        lines.append("     - Regional profile prevalence")
        for profile in profiles[:5]:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name") or "").strip()
            pct = _format_score(profile.get("population_pct"), suffix="%")
            national_pct = _format_score(profile.get("national_population_pct"), suffix="%")
            source = str(profile.get("source") or "").strip()
            dataset = str(profile.get("dataset") or "").strip()
            if name:
                lines.append(f"       - {name}: regional {pct}, national {national_pct} ({source}, {dataset})")


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
    if session.socio_demographic_profiles:
        sections.append(
            "Socio-demographic profiles identified by the assistant:\n"
            + "\n".join(f"- {profile}." for profile in session.socio_demographic_profiles)
        )
    elif session.socio_demographic_findings:
        sections.append(session.socio_demographic_findings.strip())
    if session.additional_dgs:
        sections.append(
            "Additional socio-demographic profiles added by the user:\n"
            + "\n".join(f"- {dg}." for dg in session.additional_dgs)
        )
    if not sections:
        return "- Use the socio-demographic profiles identified in the previous response."
    return "\n\n".join(sections)


def format_evaluation_answers(session: ChatSession) -> str:
    if not session.evaluation_answers:
        return "- No evaluation answers were recorded."

    lines: list[str] = []
    current_category = None
    for answer in session.evaluation_answers:
        category = str(answer["category"])
        if category != current_category:
            lines.append(f"\n### {category}")
            current_category = category

        question = normalize_markdown_text(str(answer["question"]))
        lines.append(f"\n{question}")
        lines.append(f"\n**Score: {answer['score']} / 10**")
        if answer.get("reason"):
            lines.append(f"\n- **Reason:** {answer['reason']}")
        if answer.get("evidence"):
            lines.append(f"- **Evidence:** {answer['evidence']}")

    return "\n".join(lines).strip()


def hazard_names(session: ChatSession) -> list[str]:
    hazards = list(session.hazards or [])
    hazards.extend(session.custom_hazards or [])
    return hazards


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
