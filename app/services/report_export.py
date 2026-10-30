import json
import math
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from io import BytesIO

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    AdditionalHazard,
    Country,
    CustomHazard,
    EvaluationQuestion,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    UserHazard,
    UserHazardSocioDemographic,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)


REPORT_SCOPE_CURRENT = "current"
REPORT_SCOPE_USER_HAZARD = "user_hazard"
REPORT_SCOPE_ALL_HAZARD = "all_hazard"
REPORT_SCOPES = {REPORT_SCOPE_CURRENT, REPORT_SCOPE_USER_HAZARD, REPORT_SCOPE_ALL_HAZARD}


@dataclass
class ReportResult:
    filename: str
    content: bytes


def mitigation_report_pdf(
    db: Session,
    user_session: UserSession,
    session_data: dict[str, object],
    *,
    scope: str,
    current_user_id: str | None,
) -> ReportResult:
    normalized_scope = scope if scope in REPORT_SCOPES else REPORT_SCOPE_CURRENT
    current_measure = _current_measure(db, user_session, session_data)
    if current_measure is None:
        raise ValueError("No mitigation measure is available for this session.")
    measures = _measures_for_scope(
        db,
        current_measure,
        scope=normalized_scope,
        current_user_id=current_user_id,
    )
    title = _scope_title(normalized_scope)
    pdf_title = _pdf_scope_title(normalized_scope, title)
    lines = _report_lines(
        db,
        user_session,
        session_data,
        measures,
        title=pdf_title,
    )
    filename = f"dr-transition-{_slug(title)}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return ReportResult(filename=filename, content=_simple_pdf(lines))


def _current_measure(
    db: Session,
    user_session: UserSession,
    session_data: dict[str, object],
) -> UserMitigationMeasure | None:
    record_id = str(session_data.get("mitigation_record_id") or "").strip()
    if record_id:
        row = db.get(UserMitigationMeasure, record_id)
        if row is not None:
            return row
    measure = str(session_data.get("mitigation_measure") or "").strip()
    query = (
        select(UserMitigationMeasure)
        .where(UserMitigationMeasure.user_session_id == user_session.id)
        .order_by(UserMitigationMeasure.created_at.desc(), UserMitigationMeasure.id.desc())
    )
    if measure:
        matching = db.scalar(query.where(UserMitigationMeasure.measure == measure))
        if matching is not None:
            return matching
    return db.scalar(query)


def _measures_for_scope(
    db: Session,
    current_measure: UserMitigationMeasure,
    *,
    scope: str,
    current_user_id: str | None,
) -> list[UserMitigationMeasure]:
    if scope == REPORT_SCOPE_CURRENT:
        return [current_measure]

    filters = _hazard_filters(current_measure)
    if not filters:
        return [current_measure]

    query = select(UserMitigationMeasure).where(*filters)
    if scope == REPORT_SCOPE_USER_HAZARD:
        query = query.join(UserSession, UserSession.id == UserMitigationMeasure.user_session_id).where(
            UserSession.user_id == current_user_id
        )
    rows = db.scalars(
        query.order_by(UserMitigationMeasure.created_at, UserMitigationMeasure.id)
    ).all()
    return list(rows) or [current_measure]


def _hazard_filters(measure: UserMitigationMeasure) -> list[object]:
    filters: list[object] = []
    for attr in (
        "user_hazard_id",
        "custom_hazard_id",
        "system_hazard_id",
        "additional_hazard_id",
    ):
        value = getattr(measure, attr, None)
        if value:
            filters.append(getattr(UserMitigationMeasure, attr) == value)
    return filters


def _scope_title(scope: str) -> str:
    if scope == REPORT_SCOPE_USER_HAZARD:
        return "All mitigation measures created by me against this hazard"
    if scope == REPORT_SCOPE_ALL_HAZARD:
        return "All mitigation measures created against this hazard from all users"
    return "Mitigation measure"


def _pdf_scope_title(scope: str, title: str) -> str:
    return ""


def _report_lines(
    db: Session,
    user_session: UserSession,
    session_data: dict[str, object],
    measures: list[UserMitigationMeasure],
    *,
    title: str,
) -> list[str]:
    hazard = str(session_data.get("selected_hazard") or "Selected hazard").strip()
    profile_rows = _hazard_profile_rows(db, session_data, measures)
    country = _session_label(db, session_data, "country", Country)
    region = _session_label(db, session_data, "region", Region)
    sector = _session_label(db, session_data, "sector", Sector)
    sector_objective = _sectoral_objective(sector)
    policy_summary_lines = _policy_summary_lines(
        str(session_data.get("selected_context_policy_summary") or ""),
        session_data.get("selected_context_policy_sources"),
    )
    hazard_details = _hazard_details(db, session_data, measures)
    lines = [
        "FITTER Digital Platform (Dr Transition) Report",
        title,
        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        "",
        "Analysis Coverage",
        f"- Country: {country}",
        f"- Region: {region}",
        f"- Sector: {sector}",
        "",
        "Sectoral Coverage",
        f"- Selected sector: {sector}",
        f"- Mapped sectoral objective under twin transition: {sector_objective}",
        "",
        "Policy in Focus",
        "POLICY_TITLE::" + _clean(str(session_data.get("selected_context_policy") or "Not available")),
        *(policy_summary_lines or ["- Policy summary is not available in this session."]),
        "",
        "Hazard in Highlight",
        f"- Hazard: {hazard}",
        *hazard_details,
        "HAZARD_PROFILE_TABLE::" + json.dumps(profile_rows[:8], ensure_ascii=False),
        "",
    ]
    for index, measure in enumerate(measures, start=1):
        lines.extend(_mitigation_measure_report_lines(db, measure, index, session_data, profile_rows))
    if len(measures) > 1:
        plan_cards = _comparison_cards(db, measures)
        lines.extend(
            [
                "",
                "Mitigation Plan Overview",
                "PLAN_SCORE_TABLE::" + json.dumps(plan_cards, ensure_ascii=False),
                "PLAN_RADAR_CHART::" + json.dumps(_radar_series(db, measures), ensure_ascii=False),
                "PLAN_COMBINED_EULER::" + json.dumps(_combined_target_groups(measures), ensure_ascii=False),
            ]
        )
    lines.extend(["", "Conclusions and Recommendations", _report_inference(_conclusion(measures))])
    return lines


def _combined_target_groups(measures: list[UserMitigationMeasure]) -> list[dict[str, object]]:
    return [
        {"label": f"M{index}", "groups": _dedupe_text_list(_json_list(measure.target_population))}
        for index, measure in enumerate(measures, start=1)
    ]


def _mitigation_measure_report_lines(
    db: Session,
    measure: UserMitigationMeasure,
    index: int,
    session_data: dict[str, object],
    profile_rows: list[dict[str, str]],
) -> list[str]:
    creation = _json_object(measure.creation_details_json)
    target_data = _json_object(measure.target_groups_json)
    inquiry = _json_object(measure.system_inquiry_json)
    is_current = str(session_data.get("mitigation_record_id") or "") == measure.id

    proposal_type = str(
        (session_data.get("mitigation_proposal_type") if is_current else None)
        or creation.get("proposal_type")
        or ""
    ).strip()
    if not proposal_type:
        proposal_type = "new_policy" if creation.get("new_policy_title") else (
            "existing_policy" if creation.get("selected_policy") else ""
        )
    type_label = {
        "existing_policy": "Adaptation of the selected policy",
        "new_policy": "New Mitigation Proposal",
    }.get(proposal_type, proposal_type)
    policy_title = _clean(str(creation.get("new_policy_title") or ""))
    title = policy_title or _clean(measure.measure)
    description = _clean(str(creation.get("new_policy_description") or measure.reason or ""))
    if not _has_report_content(description):
        description = ""
    creation_summary = str(creation.get("summary") or "").strip()
    final_conclusion = str(measure.conclusion or "").strip()

    lines = [f"MEASURE_SECTION::Mitigation Measure {index}"]
    if type_label:
        lines.append(f"MEASURE_TYPE::{type_label}")
    if title:
        lines.append(f"MEASURE_TITLE::{title}")
    if description:
        lines.append("MEASURE_SUBSECTION::Description")
        description_fields = _measure_description_fields(description)
        if description_fields:
            for label, detail in description_fields:
                lines.extend([f"MEASURE_FIELD::{label}", detail])
        else:
            lines.append(description)
    if _has_report_content(_clean(creation_summary)) or _has_report_content(_clean(final_conclusion)):
        summary_lines = _measure_summary_lines(creation_summary or final_conclusion)
        if summary_lines:
            lines.extend(["MEASURE_SUBSECTION::Full Final Summary", *summary_lines])
    if creation_summary and final_conclusion and _clean(creation_summary) != _clean(final_conclusion):
        conclusion_lines = _measure_summary_lines(final_conclusion)
        if conclusion_lines:
            lines.extend(["MEASURE_SUBSECTION::Final Review Conclusion", *conclusion_lines])

    pathways = creation.get("disadvantaged_group_pathways")
    if is_current and isinstance(session_data.get("mitigation_dg_benefit_explanations"), dict):
        pathways = session_data.get("mitigation_dg_benefit_explanations")
    pathway_lines: list[str] = []
    if isinstance(pathways, dict):
        for group, pathway in pathways.items():
            if _has_report_content(str(pathway or "")):
                pathway_lines.append(f"- {group}: {_clean(str(pathway))}")
    mechanisms = creation.get("mechanisms")
    if isinstance(mechanisms, list):
        for mechanism in mechanisms:
            if _has_report_content(str(mechanism or "")):
                pathway_lines.append(f"- Mechanism: {_clean(str(mechanism))}")
    guidance = creation.get("mechanism_guidance")
    if isinstance(guidance, dict):
        for group, value in guidance.items():
            detail = _flatten_report_value(value)
            if _has_report_content(detail):
                pathway_lines.append(f"- {group}: {detail}")
    if pathway_lines:
        lines.extend(["MEASURE_SUBSECTION::Disadvantaged Group Pathways and Mechanisms", *pathway_lines])

    ai_reflection = _clean(str(creation.get("mechanism_reflection") or ""))
    if is_current:
        ai_reflection = _clean(str(session_data.get("mitigation_mechanism_reflection") or ai_reflection))
    if _has_report_content(ai_reflection):
        lines.extend(["MEASURE_SUBSECTION::AI Reflection", ai_reflection])
    answers = creation.get("access_barrier_answers") or target_data.get("target_population_answers")
    if is_current and isinstance(session_data.get("target_population_answers"), list):
        answers = session_data.get("target_population_answers")
    clarification_lines = _access_barrier_answer_lines(answers)
    history = (
        session_data.get("mitigation_clarification_history")
        if is_current
        else creation.get("mitigation_clarification_history")
    )
    clarification_lines.extend(_access_barrier_answer_lines(history))
    if clarification_lines:
        lines.extend(["MEASURE_SUBSECTION::Access Barrier Clarification Answers", *clarification_lines])

    equity = _clean(str(creation.get("equity") or ""))
    if is_current:
        equity = _clean(str(session_data.get("mitigation_equity") or equity))
    venn_payload = _population_venn_payload(profile_rows, measure)
    if _has_report_content(equity) or _has_population_venn_data(venn_payload):
        lines.append("MEASURE_SUBSECTION::Final Reflection on Targeted Groups")
        if _has_report_content(equity):
            lines.append(equity)
        if _has_population_venn_data(venn_payload):
            lines.append("POPULATION_VENN::" + json.dumps(venn_payload, ensure_ascii=False))

    evaluation_answers = [
        answer
        for answer in _evaluation_answers(db, measure.id)
        if isinstance(answer.get("numeric_score"), int)
    ]
    if evaluation_answers:
        lines.append("MEASURE_SUBSECTION::Self Evaluation Scores")
        for answer in evaluation_answers:
            lines.append(
                f"- {answer['criterion']}: {answer['score']}/10. "
                f"Comments: {answer['comments']}. Evidence: {answer['evidence']}"
            )
        radar_data = _radar_series(db, [measure])
        if _has_radar_scores(radar_data):
            lines.append("RADAR_CHART::" + json.dumps(radar_data, ensure_ascii=False))

    inquiry_lines: list[str] = []
    inquiry_reflection = _clean(str(inquiry.get("reflection") or inquiry.get("summary") or ""))
    if _has_report_content(inquiry_reflection):
        inquiry_lines.append(inquiry_reflection)
    annotations = inquiry.get("annotations")
    if isinstance(annotations, list):
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            label = str(annotation.get("lens_title") or annotation.get("lens_id") or "Systems inquiry").strip()
            state = str(annotation.get("resolution_state") or "").replace("_", " ").strip()
            observation = _clean(str(annotation.get("observation") or ""))
            question = _clean(str(annotation.get("question") or ""))
            response = _clean(str(annotation.get("followup_response") or annotation.get("user_response") or ""))
            evaluation = _clean(str(annotation.get("evaluation") or ""))
            detail = "; ".join(
                f"{name}: {part}"
                for name, part in (
                    ("Status", state),
                    ("Observation", observation),
                    ("Question", question),
                    ("Response", response),
                    ("Reflection", evaluation),
                )
                if part
            )
            if detail:
                inquiry_lines.append(f"- {label}: {detail}")
    if inquiry_lines:
        lines.extend(["MEASURE_SUBSECTION::Systems Thinking and Systems Inquiry", *inquiry_lines])
    return lines + [""]


def _access_barrier_answer_lines(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    pending_question = ""
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().casefold()
        content = _clean(str(item.get("content") or ""))
        if role == "assistant" and content:
            pending_question = content
            continue
        if role == "user" and content:
            if pending_question:
                lines.append(f"- Clarification question: {pending_question}")
            lines.append(f"- Clarification answer: {content}")
            pending_question = ""
            continue
        question = _clean(str(item.get("question") or item.get("prompt") or ""))
        answer = item.get("selected") or item.get("answer") or item.get("response")
        answer_text = _flatten_report_value(answer)
        if not _has_report_content(answer_text):
            continue
        if question and answer_text:
            lines.append(f"- {question}: {answer_text}")
        elif answer_text:
            lines.append(f"- {answer_text}")
    return lines


def _measure_description_fields(description: str) -> list[tuple[str, str]]:
    labels = {
        "mechanisms mitigated": "Mechanisms Mitigated",
        "mapped policy": "Mapped Policy",
        "mechanism reflection": "Mechanism Reflection",
    }
    marker = re.compile(
        r"(?i)(?<!\w)(mechanisms mitigated|mapped policy|mechanism reflection):\s*"
    )
    matches = list(marker.finditer(description))
    if not matches:
        return []
    prefix = description[:matches[0].start()].strip(" ;.\n")
    result: list[tuple[str, str]] = []
    if prefix and prefix.casefold() not in {"description", "description:"}:
        result.append(("Description", _clean(prefix.removeprefix("Description:").strip())))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(description)
        value = _clean(description[match.end():end].strip(" ;.\n"))
        if value:
            result.append((labels[match.group(1).casefold()], value))
    return result


def _measure_summary_lines(summary: str) -> list[str]:
    if not summary.strip():
        return []
    text = re.sub(r"(?is)<button\b[^>]*>.*?</button>", " ", summary)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s*[-*]\s+(?=\*\*[^*]+:\*\*)", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*(?=\s+[A-Z])", r"\n### \1\n", text)
    text = re.sub(r"\s*#{1,6}\s*", "\n### ", text)
    text = re.sub(r"\s*\*\*([^*]+):\*\*\s*", r"\n- **\1:** ", text)
    text = re.sub(r"\s+-\s+(?=\*\*[^*]+\*\*:)", "\n- ", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading_match = re.match(r"^#{1,4}\s+(.+)$", line)
        if heading_match:
            label = _clean(re.sub(r"[*_`]+", "", heading_match.group(1))).rstrip(":")
            if label:
                lines.append(f"MEASURE_FIELD::{label}")
            continue
        field_match = re.match(r"^[-*]\s*\*\*(.+?)\*\*:\s*(.*)$", line)
        if field_match:
            label = _clean(re.sub(r"[*_`]+", "", field_match.group(1)))
            value = _clean(re.sub(r"[*_`]+", "", field_match.group(2)))
            if value and _has_report_content(value):
                lines.extend([f"MEASURE_FIELD::{label}", _report_inference(value)])
            continue
        label_match = re.match(r"^[-*]?\s*(.+?):\s*$", line)
        if label_match:
            label = _clean(re.sub(r"[*_`#]+", "", label_match.group(1)))
            if label:
                lines.append(f"MEASURE_FIELD::{label}")
            continue
        line = _clean(re.sub(r"[*_`]+", "", line))
        line = re.sub(r"^[-*]\s+", "", line)
        if _has_report_content(line):
            lines.append(f"- {_report_inference(line)}")
    return lines


def _report_inference(value: str) -> str:
    if re.search(r"\b(?:Documented|Inferred)\b", value, re.I):
        return value
    return f"{value} [Inferred]" if value.strip() else value


def _flatten_report_value(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(part for part in (_flatten_report_value(item) for item in value) if part)
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {detail}"
            for key, item in value.items()
            if (detail := _flatten_report_value(item))
        )
    return _clean(str(value or ""))


def _has_report_content(value: str) -> bool:
    return _clean(value).casefold() not in {
        "", "not available", "not recorded", "none", "n/a", "no data",
    }


def _session_label(db: Session, data: dict[str, object], key: str, model) -> str:
    value = _clean(str(data.get(key) or ""))
    if value:
        return value
    row_id = str(data.get(f"{key}_id") or "").strip()
    row = db.get(model, row_id) if row_id else None
    return _clean(str(getattr(row, "name", "") or "Not available"))


def _policy_summary_lines(
    summary: str, sources: object = None,
) -> list[str]:
    if not summary.strip():
        return []
    from app.services.summary_sources import claim_basis

    source_map = sources if isinstance(sources, dict) else {}
    normalized = re.sub(r"\s*(###\s+)", r"\n\1", summary.strip())
    chunks = re.split(r"(?m)^###\s+", normalized)
    output: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        heading, _, body = chunk.partition("\n")
        if not body and " - " in heading:
            heading, body = heading.split(" - ", 1)
        heading = _clean(re.sub(r"[*_`]+", "", heading))
        body = re.sub(
            r"(?i)\*{0,2}not identified:\*{0,2}\s*not stated in the supplied text\.?",
            "",
            body,
        )
        body = re.sub(r"\s+-\s+(?=\*\*[^*]+:\*\*)", "\n- ", body)
        body = re.sub(r"(?m)^\s*[-*]\s*", "\n- ", body)
        entries: list[str] = []
        for entry in body.splitlines():
            entry = _clean(entry)
            entry = re.sub(r"[*_`]+", "", entry).strip(" -")
            if not entry or re.search(r"(?i)not identified:\s*not stated in the supplied text", entry):
                continue
            section_sources = source_map.get(heading.casefold(), [])
            rows = (
                section_sources[len(entries)]
                if isinstance(section_sources, list) and len(entries) < len(section_sources)
                else []
            )
            basis = claim_basis(entry, rows) if isinstance(rows, list) else "Inferred"
            entries.append(f"- {entry} [{basis}]")
        if entries:
            if heading:
                output.append(f"POLICY_SUBSECTION::{heading}")
            output.extend(entries)
    return output


def _sectoral_objective(sector: str) -> str:
    try:
        from app.services.custom_hazard_validation import policy_objective_for_sector

        return policy_objective_for_sector(sector)
    except (ImportError, AttributeError):
        return "the selected sector policy objective"


def _hazard_details(
    db: Session,
    session_data: dict[str, object],
    measures: list[UserMitigationMeasure],
) -> list[str]:
    details: list[str] = []
    custom_payload = session_data.get("custom_hazard")
    if isinstance(custom_payload, dict):
        for label, key in (("Summary", "summary"), ("Reason", "reason"), ("Evidence", "evidence")):
            value = _clean(str(custom_payload.get(key) or ""))
            if value:
                details.append(f"- {label}: {_report_inference(value) if key == 'summary' else value}")

    for model, attr in (
        (CustomHazard, "custom_hazard_id"),
        (UserHazard, "user_hazard_id"),
        (SystemHazard, "system_hazard_id"),
        (AdditionalHazard, "additional_hazard_id"),
    ):
        record_id = next((str(getattr(measure, attr, "") or "") for measure in measures if getattr(measure, attr, None)), "")
        row = db.get(model, record_id) if record_id else None
        if row is None:
            continue
        for label, field in (("Summary", "summary"), ("Reason", "reason"), ("Evidence", "evidence")):
            value = _clean(str(getattr(row, field, None) or ""))
            if value and not any(item.casefold().startswith(f"- {label.casefold()}:") for item in details):
                details.append(f"- {label}: {_report_inference(value) if field == 'summary' else value}")
        break
    return details


def _measure_creation_lines(index: int, measure: UserMitigationMeasure) -> list[str]:
    target_groups = _json_list(measure.target_population)
    creation = _json_object(measure.creation_details_json)
    lines = [
        f"- Measure {index}: {_clean(measure.measure)}",
        f"  Reason: {_clean(measure.reason) or 'Not available'}",
        f"  Target groups: {_list_text(target_groups)}",
        f"  Mechanisms mitigated: {_list_text(creation.get('mechanisms') if isinstance(creation.get('mechanisms'), list) else [])}",
        f"  Equity: {_clean(str(creation.get('equity') or '')) or 'Not available'}",
    ]
    payload = _json_object(measure.system_inquiry_json)
    summary = str(payload.get("summary") or "").strip()
    if summary:
        lines.append(f"  Systems inquiry: {_report_inference(summary)}")
    return lines


def _measure_card_payload(
    index: int,
    measure: UserMitigationMeasure,
    profile_rows: list[dict[str, str]],
) -> dict[str, object]:
    payload = _json_object(measure.system_inquiry_json)
    creation = _json_object(measure.creation_details_json)
    return {
        "label": f"Measure {index}",
        "measure": _clean(measure.measure),
        "reason": _clean(measure.reason) or "Not available",
        "target_groups": _json_list(measure.target_population),
        "mechanisms": creation.get("mechanisms") or [],
        "equity": str(creation.get("equity") or "").strip(),
        "system_inquiry": str(payload.get("summary") or "").strip(),
        "population_venn": _population_venn_payload(profile_rows, measure),
    }


def _population_venn_payload(
    profile_rows: list[dict[str, str]],
    measure: UserMitigationMeasure,
) -> dict[str, object]:
    affected = [row["profile"] for row in profile_rows if row.get("profile")]
    return {
        "affected": _dedupe_text_list(affected),
        "targets": _dedupe_text_list(_json_list(measure.target_population)),
    }


def _hazard_profile_rows(
    db: Session,
    session_data: dict[str, object],
    measures: list[UserMitigationMeasure],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in (
        session_data.get("affected_profile_details")
        or session_data.get("selected_hazard_displayed_profile_details")
        or []
    ):
        if not isinstance(item, dict):
            continue
        profile = _clean(str(item.get("name") or item.get("profile") or ""))
        if not profile:
            continue
        rows.append(
            {
                "profile": profile,
                "source": _source_label(str(item.get("source") or item.get("variable_type") or "")),
                "basis": _clean(str(item.get("statistical_basis") or item.get("explanation") or "")),
            }
        )
    for item in (
        session_data.get("affected_profiles")
        or session_data.get("selected_hazard_displayed_profiles")
        or []
    ):
        profile = _clean(str(item or ""))
        if profile:
            rows.append({"profile": profile, "source": "Session summary", "basis": ""})
    hazard_filters = _combined_hazard_profile_filters(measures)
    for model in (SystemHazardSocioDemographic, UserHazardSocioDemographic):
        if not hazard_filters:
            continue
        query = select(model).where(*_hazard_filters_for_model(model, measures))
        for row in db.scalars(query).all():
            rows.append(
                {
                    "profile": _clean(str(getattr(row, "profile", "") or "")),
                    "source": _source_label(str(getattr(row, "source", "") or "")),
                    "basis": _clean(
                        str(
                            getattr(row, "statistical_basis", None)
                            or getattr(row, "evidence", None)
                            or getattr(row, "explanation", None)
                            or ""
                        )
                    ),
                }
            )
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        profile = row["profile"]
        key = profile.casefold()
        if not profile or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_text_list(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(str(value or ""))
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _combined_hazard_profile_filters(measures: list[UserMitigationMeasure]) -> bool:
    return any(
        getattr(measure, attr, None)
        for measure in measures
        for attr in ("system_hazard_id", "user_hazard_id", "custom_hazard_id", "additional_hazard_id")
    )


def _hazard_filters_for_model(
    model,
    measures: list[UserMitigationMeasure],
) -> list[object]:
    filters: list[object] = []
    attr_pairs = [
        ("system_hazard_id", "system_hazard_id"),
        ("user_hazard_id", "user_hazard_id"),
        ("custom_hazard_id", "custom_hazard_id"),
        ("additional_hazard_id", "additional_hazard_id"),
    ]
    for model_attr, measure_attr in attr_pairs:
        if not hasattr(model, model_attr):
            continue
        values = sorted(
            {
                str(getattr(measure, measure_attr) or "")
                for measure in measures
                if str(getattr(measure, measure_attr) or "")
            }
        )
        if values:
            filters.append(getattr(model, model_attr).in_(values))
    return [or_(*filters)] if filters else []


def _profile_names_text(profile_rows: list[dict[str, str]]) -> str:
    names = [row["profile"] for row in profile_rows if row.get("profile")]
    return "; ".join(names) if names else "Not available"


def _profile_sources_text(profile_rows: list[dict[str, str]]) -> str:
    if not profile_rows:
        return "Not available"
    parts = []
    for row in profile_rows[:8]:
        source = row.get("source") or "Unspecified source"
        basis = row.get("basis") or ""
        detail = f"{row['profile']} ({source})"
        if basis:
            detail += f": {_fit_text(basis, 120)}"
        parts.append(detail)
    return "; ".join(parts)


def _hazard_narrative(hazard: str, profile_rows: list[dict[str, str]]) -> str:
    if not profile_rows:
        return (
            "Narrative assessment: The report links mitigation measures to the selected hazard. "
            "No socio-demographic profile evidence was available in the restored session data."
        )
    return (
        "Narrative assessment: The hazard analysis identifies the socio-demographic groups "
        f"most relevant to {hazard}, with source labels retained so mitigation coverage can be "
        "checked against the evidence base."
    )


def _source_label(value: str) -> str:
    normalized = value.strip().casefold()
    labels = {
        "sector_prompt": "Sector statistical data",
        "d4_2_pdf": "FITTER D4.2 source evidence",
        "llm": "LLM-assisted profile extraction",
        "user_review": "User-reviewed profile",
        "user_validated": "User-validated profile",
        "custom_hazard_extraction": "Custom hazard extraction",
        "macro": "Macro socio-demographic profile",
        "individual": "Individual socio-demographic profile",
    }
    return labels.get(normalized, value.strip() or "Unspecified source")


def _system_inquiry_summary_lines(measures: list[UserMitigationMeasure]) -> list[str]:
    lines = ["Narrative assessment:"]
    found = False
    for index, measure in enumerate(measures, start=1):
        payload = _json_object(measure.system_inquiry_json)
        annotations = payload.get("annotations")
        if not isinstance(annotations, list) or not annotations:
            continue
        found = True
        lines.append(f"- Measure {index}:")
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            lens = str(annotation.get("lens_title") or annotation.get("lens_id") or "Systems inquiry").strip()
            state = str(annotation.get("resolution_state") or "open").replace("_", " ")
            response = _clean(str(annotation.get("user_response") or annotation.get("evaluation") or ""))
            lines.append(f"  - {lens}: {state}. {response}")
    if not found:
        lines.append("- No systems inquiry annotations were recorded for the selected report scope.")
    return lines


def _evaluation_answers(db: Session, mitigation_measure_id: str) -> list[dict[str, str]]:
    rows = db.execute(
        select(
            EvaluationQuestion.question,
            EvaluationQuestion.chart_title,
            UserQuestionResponse.score,
            UserQuestionResponse.reason,
            UserQuestionResponse.evidence,
        )
        .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
        .where(UserQuestionResponse.mitigation_measure_id == mitigation_measure_id)
        .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
    ).all()
    answers: list[dict[str, str]] = []
    for question, chart_title, score, reason, evidence in rows:
        answers.append(
            {
                "criterion": _clean(str(chart_title or question or "Evaluation criterion")),
                "score": str(score) if score is not None else "Not scored",
                "numeric_score": int(score) if score is not None else None,
                "evidence": _clean(str(evidence or "Not provided")),
                "comments": _clean(str(reason or "Not provided")),
            }
        )
    return answers


def _radar_series(
    db: Session,
    measures: list[UserMitigationMeasure],
) -> dict[str, object]:
    criteria: list[str] = []
    series: list[dict[str, object]] = []
    for index, measure in enumerate(measures, start=1):
        answers = _evaluation_answers(db, measure.id)
        scores: dict[str, int] = {}
        for answer in answers:
            criterion = str(answer["criterion"])
            if criterion not in criteria:
                criteria.append(criterion)
            numeric_score = answer.get("numeric_score")
            if isinstance(numeric_score, int):
                scores[criterion] = numeric_score
        series.append(
            {
                "label": f"M{index}",
                "measure": _clean(measure.measure),
                "scores": scores,
            }
        )
    return {"criteria": criteria[:8], "series": series}


def _comparison_cards(
    db: Session,
    measures: list[UserMitigationMeasure],
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for index, measure in enumerate(measures, start=1):
        scores = [
            int(answer["numeric_score"])
            for answer in _evaluation_answers(db, measure.id)
            if isinstance(answer.get("numeric_score"), int)
        ]
        average = round(sum(scores) / len(scores), 1) if scores else None
        cards.append(
            {
                "label": f"M{index}",
                "measure": _clean(measure.measure),
                "target_groups": _json_list(measure.target_population),
                "average_score": average,
                "evaluation_count": len(scores),
            }
        )
    return cards


def _comparison_summary(db: Session, measures: list[UserMitigationMeasure]) -> str:
    if len(measures) <= 1:
        return (
            "This report covers one mitigation measure, so the comparison focuses on the "
            "measure's evaluation profile, target-group coverage, and unresolved system-inquiry "
            "items."
        )
    scored: list[tuple[int, float]] = []
    missing_scores = 0
    for index, measure in enumerate(measures, start=1):
        scores = [
            int(answer["numeric_score"])
            for answer in _evaluation_answers(db, measure.id)
            if isinstance(answer.get("numeric_score"), int)
        ]
        if scores:
            scored.append((index, round(sum(scores) / len(scores), 1)))
        else:
            missing_scores += 1
    opening = (
        f"This comparison brings together {len(measures)} mitigation measures linked to the "
        "selected hazard. Use the score table, spider chart, and summary cards to compare "
        "strength, coverage, and implementation readiness."
    )
    if scored:
        leader_index, leader_score = max(scored, key=lambda item: item[1])
        opening += (
            f" M{leader_index} currently has the strongest recorded average score "
            f"({leader_score}/10), but the preferred package should also consider evidence "
            "quality, target-group fit, and unresolved inquiry gaps."
        )
    else:
        opening += " No comparable evaluation scores are available yet, so comparison should start with target-group fit and evidence completeness."
    if missing_scores:
        opening += f" {missing_scores} measure(s) still need full evaluation scoring."
    return opening


def _conclusion(measures: list[UserMitigationMeasure]) -> str:
    if not measures:
        return "No mitigation measures were available for conclusion."
    return (
        "Prioritise measures with clear target-group coverage, explicit implementation mechanisms, "
        "and stronger evidence or system-inquiry resolution where gaps remain."
    )


def _evidence_sources(db: Session, measures: list[UserMitigationMeasure]) -> str:
    ids = [measure.id for measure in measures]
    if not ids:
        return "Not provided"
    rows = db.scalars(
        select(UserQuestionResponse.evidence).where(
            UserQuestionResponse.mitigation_measure_id.in_(ids),
            UserQuestionResponse.evidence.is_not(None),
        )
    ).all()
    evidence = [_clean(str(row or "")) for row in rows if _clean(str(row or ""))]
    return _list_text(evidence)


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_clean(str(item or "")) for item in parsed if _clean(str(item or ""))]


def _json_object(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list_text(value: object) -> str:
    if isinstance(value, list):
        labels = [_clean(str(item or "")) for item in value if _clean(str(item or ""))]
        return "; ".join(labels) if labels else "Not available"
    text = _clean(str(value or ""))
    return text or "Not available"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80] or "report"


def _simple_pdf(lines: list[str]) -> bytes:
    pages = _layout_pdf_pages(lines)
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"))
    for index, page_stream in enumerate(pages):
        page_object = 3 + index * 2
        content_object = page_object + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> "
                f"/F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> >> >> "
                f"/Contents {content_object} 0 R >>"
            ).encode("ascii")
        )
        stream = page_stream.encode("latin-1", errors="replace")
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("ascii"))
        buffer.write(obj)
        buffer.write(b"\nendobj\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return buffer.getvalue()


def _layout_pdf_pages(lines: list[str]) -> list[str]:
    sections = {
        "Analysis Coverage",
        "Sectoral Coverage",
        "Policy in Focus",
        "Hazard in Highlight",
        "Mitigation Plan Overview",
        "Comparison of Mitigation Measures",
        "Conclusions and Recommendations",
    }
    title = lines[0] if lines else "FITTER Digital Platform (Dr Transition) Report"
    subtitle = lines[1] if len(lines) > 1 else "Mitigation report"
    generated = lines[2] if len(lines) > 2 else ""
    body = lines[4:] if len(lines) > 4 else lines[3:]

    pages: list[list[str]] = []
    current = _cover_page(title, subtitle, generated)
    y = 548

    def new_page() -> None:
        nonlocal current, y
        pages.append(current)
        current = _page_shell(len(pages) + 1, subtitle)
        y = 720

    def ensure_space(required: int) -> tuple[list[str], int]:
        if y - required < 58:
            new_page()
        return current, y

    index = 0
    while index < len(body):
        line = body[index]
        text = _clean(line)
        if not text:
            y -= 8
            index += 1
            continue
        if text.startswith("MEASURE_SECTION::"):
            label = text.removeprefix("MEASURE_SECTION::")
            ensure_space(42)
            current.append(_rect(42, y - 22, 528, 25, 0.36, 0.16, 0.75))
            current.append(_text(54, y - 15, label.upper(), "/F2", 10, 1, 1, 1))
            y -= 40
            index += 1
            continue
        if text.startswith("MEASURE_TYPE::"):
            label = text.removeprefix("MEASURE_TYPE::")
            ensure_space(32)
            current.append(_rect(50, y - 24, 512, 27, 0.95, 0.93, 1.0))
            current.append(_text(62, y - 15, "PROPOSAL TYPE", "/F2", 6, 0.36, 0.16, 0.75))
            current.append(_text(150, y - 16, _fit_text(label, 68), "/F2", 9, 0.10, 0.08, 0.18))
            y -= 35
            index += 1
            continue
        if text.startswith("MEASURE_TITLE::"):
            title = text.removeprefix("MEASURE_TITLE::")
            wrapped = textwrap.wrap(title, width=78, replace_whitespace=False) or [title]
            height = 38 + 12 * (len(wrapped) - 1)
            ensure_space(height + 7)
            current.append(_rect(50, y - height, 512, height, 0.95, 0.93, 1.0))
            current.append(_rect(50, y - height, 5, height, 0.36, 0.16, 0.75))
            current.append(_text(64, y - 13, "MITIGATION MEASURE TITLE", "/F2", 6, 0.36, 0.16, 0.75))
            title_y = y - 29
            for title_line in wrapped:
                current.append(_text(64, title_y, title_line, "/F2", 10, 0.10, 0.08, 0.18))
                title_y -= 12
            y -= height + 9
            index += 1
            continue
        if text.startswith("MEASURE_FIELD::"):
            label = text.removeprefix("MEASURE_FIELD::")
            next_height = _line_required_height(body[index + 1]) if index + 1 < len(body) else 0
            ensure_space(22 + next_height)
            current.append(_text(54, y - 12, _fit_text(label.upper(), 80), "/F2", 7, 0.36, 0.16, 0.75))
            y -= 20
            index += 1
            continue
        if text.startswith("MEASURE_SUBSECTION::"):
            label = text.removeprefix("MEASURE_SUBSECTION::")
            following_height = _line_required_height(body[index + 1]) if index + 1 < len(body) else 0
            next_line = body[index + 1] if index + 1 < len(body) else ""
            if next_line.startswith("POPULATION_VENN::"):
                following_height = 170
            elif next_line.startswith("RADAR_CHART::"):
                following_height = 330
            elif next_line.startswith("MEASURE_FIELD::") and index + 2 < len(body):
                following_height = 22 + _line_required_height(body[index + 2])
            ensure_space(32 + following_height)
            current.append(_rect(50, y - 19, 4, 18, 0.36, 0.16, 0.75))
            current.append(_text(60, y - 13, _fit_text(label, 74), "/F2", 9, 0.20, 0.23, 0.34))
            y -= 31
            index += 1
            continue
        if text.startswith("POLICY_TITLE::"):
            policy_title = text.removeprefix("POLICY_TITLE::")
            title_lines = textwrap.wrap(policy_title, width=78, replace_whitespace=False) or [policy_title]
            card_height = 37 + 12 * (len(title_lines) - 1)
            ensure_space(card_height + 8)
            current.append(_rect(50, y - card_height, 512, card_height, 0.95, 0.93, 1.0))
            current.append(_rect(50, y - card_height, 5, card_height, 0.36, 0.16, 0.75))
            current.append(_text(66, y - 13, "SELECTED POLICY", "/F2", 6, 0.36, 0.16, 0.75))
            title_y = y - 29
            for title_line in title_lines:
                current.append(_text(66, title_y, title_line, "/F2", 10, 0.10, 0.08, 0.18))
                title_y -= 12
            y -= card_height + 10
            index += 1
            continue
        if text.startswith("POLICY_SUBSECTION::"):
            label = text.removeprefix("POLICY_SUBSECTION::")
            ensure_space(30)
            current.append(_rect(54, y - 18, 4, 17, 0.36, 0.16, 0.75))
            current.append(_text(66, y - 13, _fit_text(label, 72), "/F2", 9, 0.20, 0.23, 0.34))
            y -= 31
            index += 1
            continue
        if text.startswith("EVAL_ROW::"):
            ensure_space(42)
            y = _draw_evaluation_row(current, text.removeprefix("EVAL_ROW::"), y)
            index += 1
            continue
        if text.startswith("RADAR_CHART::"):
            chart_data = _marker_payload(text, "RADAR_CHART::")
            if _has_radar_scores(chart_data):
                ensure_space(330)
                y = _draw_radar_chart(current, chart_data, y)
            index += 1
            continue
        if text.startswith("POPULATION_VENN::"):
            population = _marker_payload(text, "POPULATION_VENN::")
            if isinstance(population, dict) and _has_population_venn_data(population):
                ensure_space(170)
                y = _draw_measure_population_venn(current, population, y)
            index += 1
            continue
        if text.startswith("COMPARISON_CARDS::"):
            cards = _marker_payload(text, "COMPARISON_CARDS::")
            if isinstance(cards, list) and cards:
                required = 58 * min(len(cards), 5) + 18
                ensure_space(required)
                y = _draw_comparison_cards(current, cards, y)
            index += 1
            continue
        if text.startswith("PLAN_SCORE_TABLE::"):
            cards = _marker_payload(text, "PLAN_SCORE_TABLE::")
            if isinstance(cards, list) and cards:
                current, y = _draw_plan_score_table(current, cards, y, ensure_space)
            index += 1
            continue
        if text.startswith("PLAN_RADAR_CHART::"):
            chart_data = _marker_payload(text, "PLAN_RADAR_CHART::")
            if _has_radar_scores(chart_data):
                series_count = len(chart_data.get("series", [])) if isinstance(chart_data, dict) else 0
                ensure_space(330 + max(0, (series_count - 1) // 5) * 12)
                y = _draw_radar_chart(current, chart_data, y, title="Overlapping self-evaluation scores")
            index += 1
            continue
        if text.startswith("PLAN_COMBINED_EULER::"):
            groups = _marker_payload(text, "PLAN_COMBINED_EULER::")
            if isinstance(groups, list) and groups:
                ensure_space(230 + 13 * len(groups))
                y = _draw_combined_target_euler(current, groups, y)
            index += 1
            continue
        if text.startswith("HAZARD_CONTEXT::"):
            context = _marker_payload(text, "HAZARD_CONTEXT::")
            if isinstance(context, dict):
                ensure_space(112)
                y = _draw_hazard_context(current, context, y)
            index += 1
            continue
        if text.startswith("HAZARD_PROFILE_TABLE::"):
            rows = _marker_payload(text, "HAZARD_PROFILE_TABLE::")
            if isinstance(rows, list):
                ensure_space(42 + 34 * max(1, min(len(rows), 8)))
                y = _draw_profile_table(current, rows, y)
            index += 1
            continue
        if text.startswith("MEASURE_CARD::"):
            card = _marker_payload(text, "MEASURE_CARD::")
            if isinstance(card, dict):
                required = _line_required_height(text)
                ensure_space(required)
                y = _draw_measure_card(current, card, y)
            index += 1
            continue
        if text in sections:
            ensure_space(_section_required_height(text, body, index))
            current.append(_rect(42, y - 22, 528, 25, 0.36, 0.16, 0.75))
            current.append(_text(54, y - 15, text.upper(), "/F2", 10, 1, 1, 1))
            y -= 40
            index += 1
            continue
        if text == "Criterion | Score | Evidence | Comments":
            ensure_space(34 + _next_content_required_height(body, index + 1))
            current.append(_rect(50, y - 20, 512, 22, 0.07, 0.11, 0.20))
            current.append(_text(60, y - 14, "Criterion", "/F2", 8, 1, 1, 1))
            current.append(_text(232, y - 14, "Score", "/F2", 8, 1, 1, 1))
            current.append(_text(282, y - 14, "Evidence", "/F2", 8, 1, 1, 1))
            current.append(_text(410, y - 14, "Comments", "/F2", 8, 1, 1, 1))
            y -= 30
            index += 1
            continue
        if text.startswith("Measure ") and not text.startswith("- Measure"):
            ensure_space(34 + _next_content_required_height(body, index + 1))
            current.append(_rect(50, y - 20, 512, 24, 0.95, 0.92, 1.0))
            current.append(_text(62, y - 14, _fit_text(text, 96), "/F2", 9, 0.10, 0.08, 0.18))
            y -= 30
            index += 1
            continue
        if text.startswith("- ") or text.startswith("  - "):
            ensure_space(_line_required_height(text))
            y = _draw_bullet(current, text, y, indent=70 if text.startswith("  ") else 58)
            index += 1
            continue
        if text.startswith("|") or " | " in text:
            ensure_space(_line_required_height(text))
            y = _draw_table_like_row(current, text, y)
            index += 1
            continue
        ensure_space(_line_required_height(text))
        y = _draw_paragraph(current, text, y)
        index += 1

    pages.append(current)
    return ["\n".join(page) for page in pages]


def _next_content_required_height(lines: list[str], start_index: int) -> int:
    for line in lines[start_index:]:
        text = _clean(line)
        if not text:
            continue
        return _line_required_height(text)
    return 0


def _section_required_height(section: str, lines: list[str], section_index: int) -> int:
    return 40 + _next_content_required_height(lines, section_index + 1)


def _next_lines_required_height(lines: list[str], start_index: int, count: int) -> int:
    total = 0
    found = 0
    for line in lines[start_index:]:
        text = _clean(line)
        if not text:
            continue
        total += _line_required_height(text)
        found += 1
        if found >= count:
            break
    return total


def _line_required_height(text: str) -> int:
    if text.startswith("MEASURE_CARD::"):
        card = _marker_payload(text, "MEASURE_CARD::")
        if isinstance(card, dict) and _has_population_venn_data(card.get("population_venn")):
            return 300
        return 128
    if text.startswith("EVAL_ROW::"):
        return 44
    if text.startswith("RADAR_CHART::"):
        return 330
    if text.startswith("COMPARISON_CARDS::"):
        cards = _marker_payload(text, "COMPARISON_CARDS::")
        if isinstance(cards, list):
            return 58 * min(len(cards), 5) + 18
        return 76
    if text.startswith("HAZARD_CONTEXT::"):
        return 114
    if text.startswith("HAZARD_PROFILE_TABLE::"):
        rows = _marker_payload(text, "HAZARD_PROFILE_TABLE::")
        row_count = len(rows) if isinstance(rows, list) else 1
        return 42 + 34 * max(1, min(row_count, 8))
    if text == "Criterion | Score | Evidence | Comments":
        return 34
    if text.startswith("Measure ") and not text.startswith("- Measure"):
        return 34
    if text.startswith("- ") or text.startswith("  - "):
        content = text.lstrip(" -")
        width = 100 if not text.startswith("  ") else 96
        return 16 + 13 * max(1, len(textwrap.wrap(content, width=width, replace_whitespace=False)))
    if text.startswith("|") or " | " in text:
        return 24
    return 18 + 13 * max(1, len(textwrap.wrap(text, width=112, replace_whitespace=False)))


def _cover_page(title: str, subtitle: str, generated: str) -> list[str]:
    page = _page_shell(1, subtitle)
    generated_y = 672 if not subtitle else 640
    cover = [
        _rect(0, 618, 612, 174, 0.07, 0.11, 0.20),
        _rect(0, 600, 612, 20, 0.36, 0.16, 0.75),
        _text(48, 735, "FITTER Digital Platform (Dr Transition)", "/F2", 13, 0.85, 0.78, 1.0),
        _text(48, 700, "Report", "/F2", 25, 1, 1, 1),
    ]
    if subtitle:
        cover.append(_text(49, 672, _fit_text(subtitle, 62), "/F1", 13, 0.90, 0.93, 1.0))
    cover.extend(
        [
            _text(49, generated_y, generated, "/F1", 9, 0.78, 0.84, 0.94),
        ]
    )
    page.extend(cover)
    return page


def _page_shell(page_number: int, subtitle: str) -> list[str]:
    return [
        _rect(0, 0, 612, 792, 1, 1, 1),
        _rect(0, 772, 612, 20, 0.07, 0.11, 0.20),
        _text(42, 778, "FITTER Digital Platform (Dr Transition)", "/F2", 8, 1, 1, 1),
        _text(420, 778, _fit_text(subtitle, 34), "/F1", 7, 0.83, 0.87, 0.94),
        _line(42, 44, 570, 44, 0.86, 0.88, 0.92),
        _text(42, 28, "Report", "/F1", 8, 0.40, 0.46, 0.55),
        _text(540, 28, f"Page {page_number}", "/F1", 8, 0.40, 0.46, 0.55),
    ]


def _draw_hazard_context(commands: list[str], context: dict[str, object], y: int) -> int:
    tiles = [
        ("Country", str(context.get("country") or "Not available")),
        ("Region", str(context.get("region") or "Not available")),
        ("Sector", str(context.get("sector") or "Not available")),
        ("Selected hazard", str(context.get("hazard") or "Not available")),
    ]
    commands.append(_rect(50, y - 96, 512, 104, 0.98, 0.99, 1.0))
    commands.append(_line(50, y + 8, 562, y + 8, 0.82, 0.84, 0.90))
    positions = [(62, y - 28, 112), (190, y - 28, 112), (318, y - 28, 112), (62, y - 72, 368)]
    for (label, value), (x, row_y, width) in zip(tiles, positions):
        commands.append(_rect(x - 6, row_y - 12, width, 34, 1.0, 1.0, 1.0))
        commands.append(_text(x, row_y + 8, label.upper(), "/F2", 6, 0.40, 0.46, 0.55))
        commands.append(_text(x, row_y - 6, _fit_text(value, 46), "/F2", 8, 0.10, 0.08, 0.18))
    return y - 114


def _draw_profile_table(commands: list[str], rows: list[object], y: int) -> int:
    commands.append(_text(54, y, "Socio-demographic profile evidence", "/F2", 11, 0.10, 0.08, 0.18))
    y -= 20
    commands.append(_rect(50, y - 18, 512, 22, 0.07, 0.11, 0.20))
    commands.append(_text(62, y - 12, "Profile", "/F2", 8, 1, 1, 1))
    commands.append(_text(330, y - 12, "Source", "/F2", 8, 1, 1, 1))
    y -= 28
    visible_rows = rows[:8] if rows else []
    if not visible_rows:
        commands.append(_rect(50, y - 24, 512, 30, 0.98, 0.98, 1.0))
        commands.append(_text(62, y - 10, "No socio-demographic profile evidence was available.", "/F1", 8, 0.36, 0.42, 0.52))
        return y - 38
    for index, item in enumerate(visible_rows):
        if not isinstance(item, dict):
            continue
        fill = (1.0, 1.0, 1.0) if index % 2 == 0 else (0.98, 0.98, 1.0)
        commands.append(_rect(50, y - 30, 512, 34, *fill))
        commands.append(_line(50, y + 4, 562, y + 4, 0.88, 0.89, 0.93))
        _draw_cell(commands, str(item.get("profile") or ""), 62, y - 8, 42, 2)
        _draw_cell(commands, str(item.get("source") or ""), 330, y - 8, 34, 2)
        y -= 34
    return y - 8


def _draw_population_venn(commands: list[str], payload: dict[str, object], y: int) -> int:
    affected = _object_text_list(payload.get("affected"))
    targets = _object_text_list(payload.get("targets"))
    affected_count = len(affected)
    target_count = len(targets)
    overlap_count = len({item.casefold() for item in affected} & {item.casefold() for item in targets})

    commands.append(_rect(50, y - 188, 512, 198, 0.99, 0.995, 1.0))
    commands.append(_line(50, y + 10, 562, y + 10, 0.84, 0.87, 0.92))
    commands.append(_text(64, y - 12, "Affected and target populations", "/F2", 12, 0.10, 0.08, 0.18))
    commands.append(_text(64, y - 28, "Static PDF view of hazard profiles and mitigation target groups", "/F1", 8, 0.36, 0.42, 0.52))

    hazard_radius = min(86, max(54, 46 + affected_count * 10))
    target_radius = min(74, max(46, 42 + target_count * 10))
    hazard_x, target_x, circle_y = 236, 382, y - 103
    if overlap_count:
        target_x = 346
    commands.append(_circle(hazard_x, circle_y, hazard_radius, 0.48, 0.70, 0.78))
    commands.append(_circle(target_x, circle_y, target_radius, 0.55, 0.39, 0.88))
    if overlap_count:
        commands.append(_circle((hazard_x + target_x) / 2, circle_y, 24, 0.42, 0.48, 0.82))
        commands.append(_text(282, circle_y - 4, f"Overlap {overlap_count}", "/F2", 8, 1, 1, 1))

    commands.append(_text(hazard_x - 42, circle_y + 6, "Hazard profiles", "/F2", 8, 1, 1, 1))
    commands.append(_text(hazard_x - 5, circle_y - 10, str(affected_count), "/F2", 11, 1, 1, 1))
    commands.append(_text(target_x - 44, circle_y + 6, "Mitigation targets", "/F2", 8, 1, 1, 1))
    commands.append(_text(target_x - 4, circle_y - 10, str(target_count), "/F2", 11, 1, 1, 1))

    detail_y = y - 208
    _draw_population_detail_card(
        commands,
        "Hazard profiles affected population",
        affected,
        50,
        detail_y,
        246,
        (0.27, 0.59, 0.69),
    )
    _draw_population_detail_card(
        commands,
        "Mitigation measure target population",
        targets,
        316,
        detail_y,
        246,
        (0.42, 0.20, 0.84),
    )
    return detail_y - 114


def _draw_measure_population_venn(commands: list[str], payload: dict[str, object], y: int) -> int:
    affected = _object_text_list(payload.get("affected"))
    targets = _object_text_list(payload.get("targets"))
    affected_count = len(affected)
    target_count = len(targets)
    overlap_count = len({item.casefold() for item in affected} & {item.casefold() for item in targets})

    commands.append(_rect(62, y - 138, 488, 146, 1.0, 1.0, 1.0))
    commands.append(_line(62, y + 8, 550, y + 8, 0.86, 0.88, 0.92))
    commands.append(_text(74, y - 12, "Euler diagram: affected and target groups", "/F2", 10, 0.10, 0.08, 0.18))

    hazard_radius = min(55, max(34, 30 + affected_count * 5))
    target_radius = min(48, max(30, 28 + target_count * 5))
    hazard_x, target_x, circle_y = 204, 292, y - 70
    if overlap_count:
        target_x = 268
    commands.append(_circle(hazard_x, circle_y, hazard_radius, 0.48, 0.70, 0.78))
    commands.append(_circle(target_x, circle_y, target_radius, 0.55, 0.39, 0.88))
    if overlap_count:
        commands.append(_circle((hazard_x + target_x) / 2, circle_y, 14, 0.42, 0.48, 0.82))
    commands.append(_text(hazard_x - 32, circle_y + 4, "Hazard profiles", "/F2", 7, 1, 1, 1))
    commands.append(_text(hazard_x - 3, circle_y - 10, str(affected_count), "/F2", 9, 1, 1, 1))
    commands.append(_text(target_x - 34, circle_y + 4, "Target groups", "/F2", 7, 1, 1, 1))
    commands.append(_text(target_x - 3, circle_y - 10, str(target_count), "/F2", 9, 1, 1, 1))
    if overlap_count:
        commands.append(_text(228, y - 124, f"Overlap: {overlap_count}", "/F2", 7, 0.28, 0.33, 0.45))

    _draw_compact_population_list(commands, "Hazard profiles", affected, 360, y - 34, 84, (0.27, 0.59, 0.69))
    _draw_compact_population_list(commands, "Target population", targets, 360, y - 82, 84, (0.42, 0.20, 0.84))
    return y - 154


def _draw_compact_population_list(
    commands: list[str],
    title: str,
    values: list[str],
    x: int,
    y: int,
    width_chars: int,
    accent: tuple[float, float, float],
) -> None:
    commands.append(_text(x, y, title, "/F2", 7, *accent))
    row_y = y - 13
    for value in (values[:2] if values else ["Not available"]):
        commands.append(_text(x, row_y, "-", "/F2", 7, *accent))
        _draw_cell(commands, value, x + 12, row_y, width_chars // 2, 1)
        row_y -= 13
    if len(values) > 2:
        commands.append(_text(x + 12, row_y, f"+ {len(values) - 2} more", "/F1", 6, 0.36, 0.42, 0.52))


def _draw_population_detail_card(
    commands: list[str],
    title: str,
    values: list[str],
    x: int,
    y: int,
    width: int,
    accent: tuple[float, float, float],
) -> int:
    commands.append(_rect(x, y - 82, width, 90, 0.98, 0.99, 1.0))
    commands.append(_rect(x, y - 82, 4, 90, *accent))
    commands.append(_text(x + 14, y - 14, title, "/F2", 9, 0.20, 0.25, 0.33))
    list_values = values[:4] if values else ["Not available"]
    row_y = y - 34
    for value in list_values:
        commands.append(_text(x + 18, row_y, "-", "/F2", 9, *accent))
        _draw_cell(commands, value, x + 32, row_y, 31, 1)
        row_y -= 16
    if len(values) > 4:
        commands.append(_text(x + 32, row_y, f"+ {len(values) - 4} more", "/F1", 7, 0.36, 0.42, 0.52))
    return y - 96


def _draw_measure_card(commands: list[str], card: dict[str, object], y: int) -> int:
    target_groups = card.get("target_groups")
    targets = _list_text(target_groups if isinstance(target_groups, list) else [])
    system_inquiry = _clean(str(card.get("system_inquiry") or "")) or "Not recorded"
    commands.append(_rect(50, y - 106, 512, 114, 0.98, 0.97, 1.0))
    commands.append(_line(50, y + 8, 562, y + 8, 0.79, 0.74, 0.92))
    commands.append(_text(62, y - 10, str(card.get("label") or "Measure"), "/F2", 10, 0.36, 0.16, 0.75))
    commands.append(_text(130, y - 10, _fit_text(str(card.get("measure") or ""), 86), "/F2", 9, 0.10, 0.08, 0.18))
    commands.append(_text(62, y - 32, "Rationale", "/F2", 7, 0.40, 0.46, 0.55))
    _draw_cell(commands, str(card.get("reason") or "Not available"), 130, y - 32, 75, 2)
    commands.append(_text(62, y - 58, "Target groups", "/F2", 7, 0.40, 0.46, 0.55))
    _draw_cell(commands, targets, 130, y - 58, 75, 2)
    commands.append(_text(62, y - 84, "Systems inquiry", "/F2", 7, 0.40, 0.46, 0.55))
    _draw_cell(commands, system_inquiry, 130, y - 84, 75, 2)
    y -= 128
    population_venn = card.get("population_venn")
    if isinstance(population_venn, dict) and _has_population_venn_data(population_venn):
        return _draw_measure_population_venn(commands, population_venn, y)
    return y


def _draw_paragraph(commands: list[str], text: str, y: int, *, x: int = 54, width: int = 112) -> int:
    wrapped = textwrap.wrap(text, width=width, replace_whitespace=False) or [""]
    for line in wrapped:
        if y < 62:
            break
        commands.append(_text(x, y, line, "/F1", 9, 0.12, 0.16, 0.23))
        y -= 13
    return y - 4


def _draw_bullet(commands: list[str], text: str, y: int, *, indent: int) -> int:
    content = text.lstrip(" -")
    wrapped = textwrap.wrap(content, width=100 if indent < 65 else 96, replace_whitespace=False) or [content]
    commands.append(_text(indent - 13, y, "-", "/F2", 10, 0.36, 0.16, 0.75))
    for index, line in enumerate(wrapped):
        commands.append(_text(indent, y, line, "/F1", 9, 0.12, 0.16, 0.23))
        y -= 13 if index < len(wrapped) - 1 else 16
    return y


def _draw_table_like_row(commands: list[str], text: str, y: int) -> int:
    parts = [part.strip(" -") for part in text.split("|")]
    while len(parts) < 4:
        parts.append("")
    row_height = 38
    commands.append(_rect(50, y - row_height + 8, 512, row_height, 0.98, 0.98, 1.0))
    commands.append(_line(50, y + 8, 562, y + 8, 0.86, 0.88, 0.92))
    columns = [(60, 25), (232, 8), (282, 18), (410, 24)]
    for (x, width), value in zip(columns, parts[:4]):
        wrapped = textwrap.wrap(value, width=width, replace_whitespace=False)[:2] or [""]
        line_y = y - 6
        for line in wrapped:
            commands.append(_text(x, line_y, line, "/F1", 7, 0.12, 0.16, 0.23))
            line_y -= 10
    return y - row_height - 4


def _draw_evaluation_row(commands: list[str], payload: str, y: int) -> int:
    data = _json_marker_object(payload)
    criterion = str(data.get("criterion") or "Evaluation criterion")
    score = str(data.get("score") or "Not scored")
    evidence = str(data.get("evidence") or "Not provided")
    comments = str(data.get("comments") or "Not provided")
    row_height = 44
    commands.append(_rect(50, y - row_height + 8, 512, row_height, 1.0, 1.0, 1.0))
    commands.append(_line(50, y + 8, 562, y + 8, 0.86, 0.88, 0.92))
    commands.append(_line(222, y + 8, 222, y - row_height + 8, 0.91, 0.92, 0.95))
    commands.append(_line(272, y + 8, 272, y - row_height + 8, 0.91, 0.92, 0.95))
    commands.append(_line(400, y + 8, 400, y - row_height + 8, 0.91, 0.92, 0.95))
    _draw_cell(commands, criterion, 60, y - 6, 25, 2)
    commands.append(_text(240, y - 8, score, "/F2", 11, 0.36, 0.16, 0.75))
    _draw_cell(commands, evidence, 282, y - 6, 19, 2)
    _draw_cell(commands, comments, 410, y - 6, 22, 2)
    return y - row_height


def _draw_cell(
    commands: list[str],
    value: str,
    x: int,
    y: int,
    width: int,
    max_lines: int,
) -> None:
    wrapped = textwrap.wrap(_clean(value), width=width, replace_whitespace=False)[:max_lines] or [""]
    for line in wrapped:
        commands.append(_text(x, y, line, "/F1", 7, 0.12, 0.16, 0.23))
        y -= 10


def _draw_radar_chart(
    commands: list[str], data: object, y: int, *, title: str | None = None
) -> int:
    if not isinstance(data, dict):
        return y
    criteria = [str(item) for item in data.get("criteria", []) if str(item).strip()]
    series = [item for item in data.get("series", []) if isinstance(item, dict)]
    if len(criteria) < 3 or not series:
        return y
    chart_title = title or ("Individual self-evaluation profile" if len(series) == 1 else "Evaluation score profile")
    commands.append(_text(190, y - 8, chart_title, "/F2", 14, 0.10, 0.08, 0.18))
    center_x = 306
    center_y = y - 170
    radius = 105
    colors = [
        (0.36, 0.16, 0.75),
        (0.05, 0.55, 0.66),
        (0.86, 0.30, 0.16),
        (0.10, 0.55, 0.28),
        (0.50, 0.32, 0.04),
    ]
    points_by_level: list[list[tuple[float, float]]] = []
    for level in (2, 4, 6, 8, 10):
        level_radius = radius * level / 10
        points = [_radar_point(center_x, center_y, level_radius, index, len(criteria)) for index in range(len(criteria))]
        points_by_level.append(points)
        commands.append(_polyline(points + [points[0]], 0.88, 0.89, 0.91, width=0.6))
    for index, criterion in enumerate(criteria):
        outer = _radar_point(center_x, center_y, radius, index, len(criteria))
        label = _radar_point(center_x, center_y, radius + 25, index, len(criteria))
        commands.append(_line(center_x, center_y, int(outer[0]), int(outer[1]), 0.90, 0.91, 0.93))
        commands.append(
            _text(
                int(label[0]) - 28,
                int(label[1]),
                _fit_text(criterion, 26),
                "/F1",
                7,
                0.25,
                0.31,
                0.40,
            )
        )
    for score_label, level in (("0", 0), ("5", 5), ("10", 10)):
        commands.append(_text(center_x - 18, int(center_y + radius * level / 10), score_label, "/F1", 7, 0.40, 0.46, 0.55))
    legend_y = center_y - radius - 36
    for index, item in enumerate(series):
        color = colors[index % len(colors)]
        scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        points = [
            _radar_point(
                center_x,
                center_y,
                radius * max(0, min(10, int(scores.get(criterion, 0) or 0))) / 10,
                axis_index,
                len(criteria),
            )
            for axis_index, criterion in enumerate(criteria)
        ]
        if any(point != (center_x, center_y) for point in points):
            commands.append(_polygon(points, *color, fill_alpha=False))
            commands.append(_polyline(points + [points[0]], *color, width=1.8))
            for px, py in points:
                commands.append(_circle(px, py, 2.2, *color))
        legend_x = 78 + (index % 5) * 96
        legend_row = index // 5
        commands.append(_rect(legend_x, legend_y - 4, 10, 7, *color))
        commands.append(_text(legend_x + 14, legend_y - 3 - legend_row * 12, _fit_text(str(item.get("label") or f"M{index + 1}"), 10), "/F2", 8, 0.12, 0.16, 0.23))
    return y - 318 - max(0, (len(series) - 1) // 5) * 12


def _draw_plan_score_table(
    commands: list[str], cards: list[object], y: int, ensure_space: object
) -> tuple[list[str], int]:
    commands, y = ensure_space(50)
    commands.append(_text(54, y - 6, "Self-evaluation score by mitigation measure", "/F2", 10, 0.10, 0.08, 0.18))
    y -= 20
    commands, y = ensure_space(24)
    commands.append(_rect(50, y - 17, 512, 20, 0.07, 0.11, 0.20))
    commands.append(_text(60, y - 12, "Measure", "/F2", 8, 1, 1, 1))
    commands.append(_text(155, y - 12, "Mitigation measure", "/F2", 8, 1, 1, 1))
    commands.append(_text(430, y - 12, "Average", "/F2", 8, 1, 1, 1))
    commands.append(_text(500, y - 12, "Scored", "/F2", 8, 1, 1, 1))
    y -= 24
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        commands, y = ensure_space(25)
        commands.append(_rect(50, y - 16, 512, 22, 0.97, 0.95, 1.0) if index % 2 == 0 else _rect(50, y - 16, 512, 22, 0.98, 0.99, 1.0))
        commands.append(_text(60, y - 10, str(card.get("label") or f"M{index + 1}"), "/F2", 8, 0.36, 0.16, 0.75))
        commands.append(_text(155, y - 10, _fit_text(str(card.get("measure") or ""), 45), "/F1", 8, 0.10, 0.08, 0.18))
        average = card.get("average_score")
        commands.append(_text(435, y - 10, "—" if average is None else f"{average}/10", "/F2", 8, 0.05, 0.55, 0.66))
        commands.append(_text(510, y - 10, str(card.get("evaluation_count") or 0), "/F1", 8, 0.10, 0.08, 0.18))
        y -= 24
    return commands, y - 8


def _draw_combined_target_euler(commands: list[str], groups: list[object], y: int) -> int:
    valid = [item for item in groups if isinstance(item, dict)]
    if not valid:
        return y
    commands.append(_text(54, y - 6, "Combined Euler diagram of targeted disadvantaged groups", "/F2", 10, 0.10, 0.08, 0.18))
    y -= 24
    colors = [(0.36, 0.16, 0.75), (0.05, 0.55, 0.66), (0.86, 0.30, 0.16), (0.10, 0.55, 0.28), (0.50, 0.32, 0.04)]
    shown = valid
    radius = min(50, max(20, 245 / max(1, len(shown))))
    start_x = 96
    spacing = min(66, 368 / max(1, len(shown) - 1)) if len(shown) > 1 else 0
    center_y = y - 57
    normalized: list[set[str]] = []
    for index, item in enumerate(shown):
        target_list = _object_text_list(item.get("groups"))
        normalized.append({value.casefold() for value in target_list})
        center_x = start_x + index * spacing
        color = colors[index % len(colors)]
        commands.append(_circle_outline(center_x, center_y, radius, *color))
        commands.append(_text(int(center_x - 11), int(center_y + 5), str(item.get("label") or f"M{index + 1}"), "/F2", 8, *color))
        commands.append(_text(int(center_x - 8), int(center_y - 9), str(len(target_list)), "/F1", 7, 0.25, 0.31, 0.40))
    shared = set().union(*(items for items in normalized)) if normalized else set()
    shared = {group for group in shared if sum(group in items for items in normalized) > 1}
    shared_names = sorted(
        {
            group
            for item in shown
            for group in _object_text_list(item.get("groups"))
            if group.casefold() in shared
        },
        key=str.casefold,
    )
    commands.append(_text(54, center_y - radius - 17, _fit_text(f"Groups targeted by multiple measures ({len(shared)}): {_list_text(shared_names)}", 100), "/F2", 7, 0.25, 0.31, 0.40))
    list_y = center_y - radius - 31
    for index, item in enumerate(shown):
        names = _object_text_list(item.get("groups"))
        label = str(item.get("label") or f"M{index + 1}")
        commands.append(_text(54, list_y, f"{label}: {_fit_text(_list_text(names), 82)}", "/F1", 7, *colors[index % len(colors)]))
        list_y -= 13
    return list_y - 8


def _circle_outline(x: float, y: float, radius: float, r: float, g: float, b: float) -> str:
    c = radius * 0.55228475
    return (
        f"{r:.3f} {g:.3f} {b:.3f} RG 1.5 w "
        f"{x + radius:.1f} {y:.1f} m "
        f"{x + radius:.1f} {y + c:.1f} {x + c:.1f} {y + radius:.1f} {x:.1f} {y + radius:.1f} c "
        f"{x - c:.1f} {y + radius:.1f} {x - radius:.1f} {y + c:.1f} {x - radius:.1f} {y:.1f} c "
        f"{x - radius:.1f} {y - c:.1f} {x - c:.1f} {y - radius:.1f} {x:.1f} {y - radius:.1f} c "
        f"{x + c:.1f} {y - radius:.1f} {x + radius:.1f} {y - c:.1f} {x + radius:.1f} {y:.1f} c S"
    )


def _draw_comparison_cards(commands: list[str], cards: list[object], y: int) -> int:
    commands.append(_text(54, y, "Mitigation comparison summary", "/F2", 11, 0.10, 0.08, 0.18))
    y -= 18
    for index, card in enumerate(cards[:6]):
        if not isinstance(card, dict):
            continue
        height = 54
        fill = (0.97, 0.95, 1.0) if index % 2 == 0 else (0.98, 0.99, 1.0)
        commands.append(_rect(50, y - height + 8, 512, height, *fill))
        commands.append(_line(50, y + 8, 562, y + 8, 0.83, 0.80, 0.94))
        label = str(card.get("label") or f"M{index + 1}")
        score = card.get("average_score")
        score_text = "Not scored" if score is None else f"Avg score {score}/10"
        commands.append(_text(62, y - 8, label, "/F2", 10, 0.36, 0.16, 0.75))
        commands.append(_text(118, y - 8, _fit_text(str(card.get("measure") or ""), 72), "/F2", 9, 0.10, 0.08, 0.18))
        commands.append(_text(458, y - 8, score_text, "/F2", 8, 0.05, 0.55, 0.66))
        target_groups = card.get("target_groups")
        targets = _list_text(target_groups if isinstance(target_groups, list) else [])
        commands.append(_text(118, y - 25, _fit_text(f"Target groups: {targets}", 92), "/F1", 8, 0.36, 0.42, 0.52))
        commands.append(_text(118, y - 40, f"Evaluation criteria scored: {card.get('evaluation_count') or 0}", "/F1", 8, 0.36, 0.42, 0.52))
        y -= height + 8
    return y


def _rect(x: int, y: int, w: int, h: int, r: float, g: float, b: float) -> str:
    return f"{r:.3f} {g:.3f} {b:.3f} rg {x} {y} {w} {h} re f"


def _line(x1: int, y1: int, x2: int, y2: int, r: float, g: float, b: float) -> str:
    return f"{r:.3f} {g:.3f} {b:.3f} RG 0.8 w {x1} {y1} m {x2} {y2} l S"


def _polyline(
    points: list[tuple[float, float]],
    r: float,
    g: float,
    b: float,
    *,
    width: float = 1.0,
) -> str:
    if not points:
        return ""
    start = points[0]
    segments = [f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {start[0]:.1f} {start[1]:.1f} m"]
    segments.extend(f"{x:.1f} {y:.1f} l" for x, y in points[1:])
    segments.append("S")
    return " ".join(segments)


def _polygon(
    points: list[tuple[float, float]],
    r: float,
    g: float,
    b: float,
    *,
    fill_alpha: bool = False,
) -> str:
    if not points:
        return ""
    start = points[0]
    fill = "f" if fill_alpha else "S"
    segments = [f"{r:.3f} {g:.3f} {b:.3f} RG {r:.3f} {g:.3f} {b:.3f} rg 1.0 w {start[0]:.1f} {start[1]:.1f} m"]
    segments.extend(f"{x:.1f} {y:.1f} l" for x, y in points[1:])
    segments.append(f"h {fill}")
    return " ".join(segments)


def _circle(x: float, y: float, radius: float, r: float, g: float, b: float) -> str:
    # Bezier approximation for a small filled circle.
    c = radius * 0.55228475
    return (
        f"{r:.3f} {g:.3f} {b:.3f} rg "
        f"{x + radius:.1f} {y:.1f} m "
        f"{x + radius:.1f} {y + c:.1f} {x + c:.1f} {y + radius:.1f} {x:.1f} {y + radius:.1f} c "
        f"{x - c:.1f} {y + radius:.1f} {x - radius:.1f} {y + c:.1f} {x - radius:.1f} {y:.1f} c "
        f"{x - radius:.1f} {y - c:.1f} {x - c:.1f} {y - radius:.1f} {x:.1f} {y - radius:.1f} c "
        f"{x + c:.1f} {y - radius:.1f} {x + radius:.1f} {y - c:.1f} {x + radius:.1f} {y:.1f} c f"
    )


def _text(x: int, y: int, value: str, font: str, size: int, r: float, g: float, b: float) -> str:
    return f"BT {r:.3f} {g:.3f} {b:.3f} rg {font} {size} Tf {x} {y} Td ({_pdf_text(value)}) Tj ET"


def _fit_text(value: str, max_chars: int) -> str:
    text = _clean(value)
    return text if len(text) <= max_chars else f"{text[: max_chars - 3].rstrip()}..."


def _marker_payload(text: str, prefix: str) -> object:
    payload = text.removeprefix(prefix)
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return {}


def _json_marker_object(payload: str) -> dict[str, object]:
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_radar_scores(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    criteria = data.get("criteria")
    series = data.get("series")
    return isinstance(criteria, list) and len(criteria) >= 3 and isinstance(series, list) and bool(series)


def _has_population_venn_data(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(_object_text_list(data.get("affected")) or _object_text_list(data.get("targets")))


def _object_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(str(item or "")) for item in value if _clean(str(item or ""))]


def _radar_point(
    center_x: float,
    center_y: float,
    radius: float,
    index: int,
    total: int,
) -> tuple[float, float]:
    angle = -math.pi / 2 + (2 * math.pi * index / max(1, total))
    return center_x + radius * math.cos(angle), center_y - radius * math.sin(angle)


def _pdf_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )
