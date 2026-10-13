"""Read the selected policy's D4.2 portfolio fields."""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_options import normalize_for_match
from app.services.hazard_with_mitigation import (
    _merge_overlapping_chunks,
    unique_policy_title_match,
)


@dataclass(frozen=True)
class PolicyReference:
    document: str
    source_uri: str
    page_number: int | None
    fields: dict[str, str]
    field_pages: dict[str, int] = field(default_factory=dict)

    def page_for_field(self, label: str) -> int | None:
        return self.field_pages.get(label, self.page_number)


_D42_LABELS = (
    "Country", "Sector", "Policy Title", "Policy Type", "Sectoral Challenge Addressed",
    "Original Policy Objective", "Existing Gaps", "Proposed Adjustment",
    "Description of the Proposal", "Transformative Category", "Transformative scoring",
    "Feasibility scoring", "Policy code",
)
_PAGE_HEADER = re.compile(
    r"D4\.2 Methodology of the co-creation process and report on mitigation measures\s+"
    r"FITTER-EU\s+Page\s*\|\s*\d+",
    re.IGNORECASE,
)


def _d42_field(record: str, label: str) -> str:
    start = re.search(rf"\b{re.escape(label)}\b\s*:?[ \t]*", record, re.IGNORECASE)
    if not start:
        return ""
    stop_labels = "|".join(re.escape(value) for value in _D42_LABELS if value != label)
    stop = re.search(rf"\b(?:{stop_labels})\b\s*:?[ \t]*", record[start.end():], re.IGNORECASE)
    end = start.end() + stop.start() if stop else len(record)
    return re.sub(r"\s+", " ", _PAGE_HEADER.sub(" ", record[start.end():end])).strip(" :;,-")


def d42_policy_reference(
    db: Session, *, country: str | None, sector: str | None, selected_policy: str | None,
) -> PolicyReference | None:
    """Find one matching existing-policy record in D4.2 portfolio sections 6–11."""
    if not country or not sector or not selected_policy:
        return None
    rows = db.execute(
        select(KnowledgeDocument.source_uri, KnowledgeChunk.page_number,
               KnowledgeChunk.chunk_index, KnowledgeChunk.content)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.scope == "main",
            KnowledgeDocument.source_type == "pdf",
            KnowledgeDocument.source_uri.ilike("%FITTER D4.2 FINAL.pdf"),
        )
        .order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index)
    ).all()
    by_page: dict[int, list[str]] = {}
    source_uri = ""
    for uri, page, _index, content in rows:
        if page is not None:
            by_page.setdefault(page, []).append(str(content or ""))
            source_uri = str(uri or "")
    pages = {page: _merge_overlapping_chunks(chunks) for page, chunks in by_page.items()}
    starts = [page for page, text in pages.items()
              if re.search(r"\b6\.\s+German policy portfolio\b", text, re.IGNORECASE)]
    if not starts:
        return None
    portfolio_start = min(starts)
    candidates: dict[str, tuple[int, str, list[tuple[int, str]]]] = {}
    for page in sorted(pages):
        if page < portfolio_start:
            continue
        parts = re.split(r"\bPolicy code\s*:?[ \t]*", pages[page], flags=re.IGNORECASE)
        for part in parts[1:]:
            code_match = re.match(r"\s*([A-Z]{2}[_\s]+[A-Z]{1,3}[_\s]+(?:AD|N)[_\s]*\d+)\b", part, re.IGNORECASE)
            if not code_match:
                continue
            code = re.sub(r"\s+", "", code_match.group(1)).upper()
            record = part[code_match.end():]
            previous = candidates.get(code)
            candidates[code] = (
                previous[0] if previous else page,
                f"{previous[1]} {record}" if previous else record,
                [*previous[2], (page, record)] if previous else [(page, record)],
            )
    country_key = normalize_for_match(country)
    sector_key = normalize_for_match(sector)
    matches: dict[str, tuple[int, dict[str, str], list[tuple[int, str]]]] = {}
    titles: list[tuple[str, str]] = []
    for code, (first_page, record, parts) in candidates.items():
        fields = {label: _d42_field(record, label) for label in _D42_LABELS[:-1]}
        if (
            normalize_for_match(fields.get("Country", "")) == country_key
            and normalize_for_match(fields.get("Sector", "")) == sector_key
        ):
            matches[code] = (first_page, {key: value for key, value in fields.items() if value}, parts)
            titles.append((fields.get("Policy Title", ""), code))
    matched_code = unique_policy_title_match(titles, selected_policy)
    if matched_code is None:
        return None
    page, fields, parts = matches[matched_code]
    field_pages: dict[str, int] = {}
    for label in fields:
        for part_page, part in parts:
            if re.search(rf"\b{re.escape(label)}\b", part, re.IGNORECASE):
                field_pages[label] = part_page
                break
    return PolicyReference(
        document=PurePosixPath(source_uri.replace("\\", "/")).name or "FITTER D4.2 FINAL.pdf",
        source_uri=source_uri,
        page_number=page,
        fields=fields,
        field_pages=field_pages,
    )
