from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.document_text import compact_text, extract_pdf_page_texts
from app.services.system_inquiry_probe_library import system_inquiry_probe_record

SYSTEMS_CORPUS_PDF = Path(__file__).resolve().parents[2] / "kb" / "FITTER_D2.3_FINAL.pdf"
SYSTEMS_CORPUS_START_PAGE = 26
SYSTEMS_CORPUS_END_PAGE = 91
SYSTEMS_CORPUS_CHUNK_CHARS = 1800
SYSTEMS_CORPUS_MAX_EXPLANATION_CHUNKS = 3

LENS_TERMS = {
    "A": ("system", "dynamic", "feedback", "boundary", "interdepend", "leverage"),
    "B": ("frame", "knowledge", "assumption", "participation", "stakeholder", "purpose"),
    "C": ("justice", "equity", "vulnerable", "disadvantaged", "access", "distribution"),
    "D": ("portfolio", "measure", "interaction", "cumulative", "coverage", "policy"),
}


@lru_cache(maxsize=1)
def system_inquiry_corpus_index() -> dict[str, Any]:
    with SYSTEMS_CORPUS_PDF.open("rb") as handle:
        pages = extract_pdf_page_texts(handle.read())
    selected_pages = pages[SYSTEMS_CORPUS_START_PAGE - 1 : SYSTEMS_CORPUS_END_PAGE]
    chunks: list[dict[str, Any]] = []
    for offset, page_text in enumerate(selected_pages, start=SYSTEMS_CORPUS_START_PAGE):
        text = compact_text(page_text)
        if not text:
            continue
        start = 0
        part = 1
        while start < len(text):
            chunk_text = text[start : start + SYSTEMS_CORPUS_CHUNK_CHARS].strip()
            if chunk_text:
                chunks.append(
                    {
                        "chunk_id": f"D2.3-p{offset:03d}-{part}",
                        "document": "FITTER_D2.3_FINAL.pdf",
                        "page": offset,
                        "text": chunk_text,
                    }
                )
            start += SYSTEMS_CORPUS_CHUNK_CHARS
            part += 1
    return {
        "document": "FITTER_D2.3_FINAL.pdf",
        "page_start": SYSTEMS_CORPUS_START_PAGE,
        "page_end": SYSTEMS_CORPUS_END_PAGE,
        "chunks": chunks,
    }


def explain_system_inquiry_probe(probe_id: str) -> dict[str, Any]:
    record = system_inquiry_probe_record(probe_id) or {}
    lens_id = str(record.get("lens_id") or probe_id[:1] or "A").upper()
    family_key = lens_id[:1]
    terms = LENS_TERMS.get(family_key, ())
    chunks = _rank_corpus_chunks(terms, str(record.get("title") or probe_id))
    return {
        "probe_id": probe_id,
        "lens_id": lens_id,
        "title": str(record.get("title") or probe_id),
        "library_version": str(record.get("library_version") or "1.0"),
        "source": {
            "document": "FITTER_D2.3_FINAL.pdf",
            "pages": f"{SYSTEMS_CORPUS_START_PAGE}-{SYSTEMS_CORPUS_END_PAGE}",
        },
        "explanation": _short_explanation(family_key),
        "chunks": chunks[:SYSTEMS_CORPUS_MAX_EXPLANATION_CHUNKS],
    }


def _rank_corpus_chunks(terms: tuple[str, ...], title: str) -> list[dict[str, Any]]:
    index = system_inquiry_corpus_index()
    query_terms = {term.casefold() for term in terms}
    query_terms.update(word.casefold() for word in title.split() if len(word) > 4)
    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk in index.get("chunks") or []:
        text = str(chunk.get("text") or "").casefold()
        score = sum(text.count(term) for term in query_terms)
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], int(item[1].get("page") or 0)))
    return [
        {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "document": str(chunk.get("document") or ""),
            "page": int(chunk.get("page") or 0),
            "excerpt": str(chunk.get("text") or "")[:900],
        }
        for _, chunk in scored
    ]


def _short_explanation(family_key: str) -> str:
    if family_key == "A":
        return "This lens asks about structure, dynamics, boundaries, feedback, delay, and capacity."
    if family_key == "B":
        return "This lens asks which assumptions, framings, and forms of knowledge shape the measure."
    if family_key == "C":
        return "This lens asks how distribution, recognition, and procedural access affect justice."
    if family_key == "D":
        return "This lens asks how the current measure interacts with the wider measure portfolio."
    return "This lens explains why the system inquiry question is being asked."


def dump_system_inquiry_corpus_index(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(system_inquiry_corpus_index(), handle, ensure_ascii=False, indent=2)
