import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.llm import ask_llm_chat
from app.services.chat_json import parse_json_object
from app.services.chat_formatters import normalize_markdown_text
from app.services.knowledge_base import KnowledgeBaseService
from app.services.prompt_loader import render_prompt_template

logger = logging.getLogger(__name__)

VALID_VERDICTS = {"VALID", "INVALID", "NEEDS_CLARIFICATION"}


class EvidenceContradictionService:
    """Validate L2 user evidence against the authoritative L1 knowledge base."""

    def __init__(
        self,
        db: Session | None,
        user_id: int | None = None,
        *,
        l1_scope: str = "main",
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.l1_scope = l1_scope

    async def extract_evidence_concepts(
        self,
        *,
        claim_type: str,
        claim_text: str,
        evidence_text: str,
        sector: str,
        country: str,
        region: str,
    ) -> dict[str, Any]:
        if not evidence_text.strip():
            return {}
        response = await ask_llm_chat(
            context=render_prompt_template("llm/evidence_concepts_extraction.txt"),
            messages=[
                {
                    "role": "user",
                    "content": render_prompt_template(
                        "llm/evidence_concepts_extraction_user.txt",
                        claim_type=claim_type,
                        sector=sector or "Not provided",
                        country=country or "Not provided",
                        region=region or "Not provided",
                        claim_text=claim_text or "Not provided",
                        evidence_text=evidence_text[:12000],
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=900,
        )
        parsed = parse_json_object(response)
        return parsed or {}

    async def retrieve_core_kb_matches(
        self,
        *,
        concepts: dict[str, Any],
        claim_text: str,
        sector: str,
        country: str,
        region: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        query = _concept_query(concepts, claim_text, sector, country, region)
        if not query:
            return []
        try:
            return await KnowledgeBaseService(
                self.db,
                self.user_id,
                scope=self.l1_scope,
            ).search(query, limit=limit)
        except Exception:
            logger.exception("L1 core knowledge-base lookup failed during evidence contradiction check")
            return []

    async def detect_contradictions(
        self,
        *,
        claim_type: str,
        claim_text: str,
        l2_concepts: dict[str, Any],
        l1_matches: list[dict[str, Any]],
        evidence_text: str,
        sector: str,
        country: str,
        region: str,
    ) -> dict[str, Any]:
        if not l2_concepts or not l1_matches:
            return _needs_clarification(
                "Evidence could not be compared reliably with the core knowledge base.",
                matched_l2_concepts=_concept_items(l2_concepts),
                matched_l1_concepts=_match_items(l1_matches),
            )
        response = await ask_llm_chat(
            context=render_prompt_template("llm/evidence_contradiction_detection.txt"),
            messages=[
                {
                    "role": "user",
                    "content": render_prompt_template(
                        "llm/evidence_contradiction_detection_user.txt",
                        claim_type=claim_type,
                        sector=sector or "Not provided",
                        country=country or "Not provided",
                        region=region or "Not provided",
                        claim_text=claim_text or "Not provided",
                        l2_concepts=json.dumps(l2_concepts, ensure_ascii=True, indent=2),
                        l2_evidence=evidence_text[:9000],
                        l1_matches=_format_l1_matches(l1_matches),
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        return _normalize_verdict(
            parse_json_object(response),
            fallback_l2=l2_concepts,
            fallback_l1=l1_matches,
        )

    async def detect_contraindications(
        self,
        *,
        claim_type: str,
        claim_text: str,
        l2_concepts: dict[str, Any],
        l1_matches: list[dict[str, Any]],
        evidence_text: str,
        sector: str,
        country: str,
        region: str,
    ) -> dict[str, Any]:
        result = await self.detect_contradictions(
            claim_type=claim_type,
            claim_text=claim_text,
            l2_concepts=l2_concepts,
            l1_matches=l1_matches,
            evidence_text=evidence_text,
            sector=sector,
            country=country,
            region=region,
        )
        if result.get("contraindication_found"):
            result["verdict"] = "INVALID"
        return result

    async def validate_evidence_against_kb(
        self,
        *,
        claim_type: str,
        claim_text: str,
        evidence_text: str,
        l2_evidence_context: str = "",
        sector: str = "",
        country: str = "",
        region: str = "",
    ) -> dict[str, Any]:
        evidence_context = _compact_context(l2_evidence_context, evidence_text)
        if not evidence_context:
            return _needs_clarification(
                "No readable user evidence was available for contradiction checking."
            )

        l2_concepts = await self.extract_evidence_concepts(
            claim_type=claim_type,
            claim_text=claim_text,
            evidence_text=evidence_context,
            sector=sector,
            country=country,
            region=region,
        )
        if not l2_concepts:
            return _needs_clarification(
                "Evidence concepts could not be extracted reliably.",
                evidence_summary=evidence_context[:500],
            )

        l1_matches = await self.retrieve_core_kb_matches(
            concepts=l2_concepts,
            claim_text=claim_text,
            sector=sector,
            country=country,
            region=region,
        )
        if not l1_matches:
            return _needs_clarification(
                "No matching L1 core knowledge-base concepts were found.",
                matched_l2_concepts=_concept_items(l2_concepts),
                evidence_summary=str(l2_concepts.get("evidence_summary") or "")[:500],
            )

        return await self.detect_contraindications(
            claim_type=claim_type,
            claim_text=claim_text,
            l2_concepts=l2_concepts,
            l1_matches=l1_matches,
            evidence_text=evidence_context,
            sector=sector,
            country=country,
            region=region,
        )


async def extract_evidence_concepts(**kwargs: Any) -> dict[str, Any]:
    service = EvidenceContradictionService(kwargs.pop("db", None), kwargs.pop("user_id", None))
    return await service.extract_evidence_concepts(**kwargs)


async def retrieve_core_kb_matches(**kwargs: Any) -> list[dict[str, Any]]:
    service = EvidenceContradictionService(kwargs.pop("db", None), kwargs.pop("user_id", None))
    return await service.retrieve_core_kb_matches(**kwargs)


async def detect_contradictions(**kwargs: Any) -> dict[str, Any]:
    service = EvidenceContradictionService(kwargs.pop("db", None), kwargs.pop("user_id", None))
    return await service.detect_contradictions(**kwargs)


async def detect_contraindications(**kwargs: Any) -> dict[str, Any]:
    service = EvidenceContradictionService(kwargs.pop("db", None), kwargs.pop("user_id", None))
    return await service.detect_contraindications(**kwargs)


async def validate_evidence_against_kb(**kwargs: Any) -> dict[str, Any]:
    service = EvidenceContradictionService(kwargs.pop("db", None), kwargs.pop("user_id", None))
    return await service.validate_evidence_against_kb(**kwargs)


def _normalize_verdict(
    payload: dict[str, Any] | None,
    *,
    fallback_l2: dict[str, Any],
    fallback_l1: list[dict[str, Any]],
) -> dict[str, Any]:
    if payload is None:
        return _needs_clarification(
            "The contradiction-check response was not reliable JSON.",
            matched_l2_concepts=_concept_items(fallback_l2),
            matched_l1_concepts=_match_items(fallback_l1),
        )
    verdict = str(payload.get("verdict") or "NEEDS_CLARIFICATION").strip().upper()
    if verdict not in VALID_VERDICTS:
        verdict = "NEEDS_CLARIFICATION"
    contradiction_found = bool(payload.get("contradiction_found"))
    contraindication_found = bool(payload.get("contraindication_found"))
    if contradiction_found or contraindication_found:
        verdict = "INVALID"
    confidence = _clamp_float(payload.get("confidence"))
    if verdict == "VALID" and confidence <= 0:
        verdict = "NEEDS_CLARIFICATION"
    return {
        "verdict": verdict,
        "confidence": confidence,
        "contradiction_found": contradiction_found,
        "contraindication_found": contraindication_found,
        "matched_l1_concepts": _coerce_list(payload.get("matched_l1_concepts"))
        or _match_items(fallback_l1),
        "matched_l2_concepts": _coerce_list(payload.get("matched_l2_concepts"))
        or _concept_items(fallback_l2),
        "reason": str(payload.get("reason") or "").strip()
        or "Evidence needs clarification against the core knowledge base.",
        "clarification_questions": [
            str(item).strip()
            for item in _coerce_list(payload.get("clarification_questions"))
            if str(item).strip()
        ],
        "evidence_summary": str(payload.get("evidence_summary") or "").strip(),
        "kb_support_summary": str(payload.get("kb_support_summary") or "").strip(),
    }


def _needs_clarification(
    reason: str,
    *,
    matched_l1_concepts: list[Any] | None = None,
    matched_l2_concepts: list[Any] | None = None,
    evidence_summary: str = "",
    kb_support_summary: str = "",
) -> dict[str, Any]:
    return {
        "verdict": "NEEDS_CLARIFICATION",
        "confidence": 0.0,
        "contradiction_found": False,
        "contraindication_found": False,
        "matched_l1_concepts": matched_l1_concepts or [],
        "matched_l2_concepts": matched_l2_concepts or [],
        "reason": reason,
        "clarification_questions": [
            "Please provide evidence that explicitly connects the transition policy, mechanism, affected group, location, and claimed outcome."
        ],
        "evidence_summary": evidence_summary,
        "kb_support_summary": kb_support_summary,
    }


def _concept_query(
    concepts: dict[str, Any],
    claim_text: str,
    sector: str,
    country: str,
    region: str,
) -> str:
    values: list[str] = [claim_text, sector, country, region]
    for key in (
        "sector",
        "policy",
        "hazard",
        "affected_group",
        "mitigation_measure",
        "claimed_mechanism",
        "location",
        "expected_outcome",
    ):
        value = concepts.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value or ""))
    return normalize_markdown_text(" ".join(values)).strip()


def _compact_context(l2_evidence_context: str, evidence_text: str) -> str:
    parts = [
        normalize_markdown_text(str(l2_evidence_context or "")).strip(),
        normalize_markdown_text(str(evidence_text or "")).strip(),
    ]
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        if part and part not in seen:
            cleaned.append(part)
            seen.add(part)
    return "\n\n".join(cleaned).strip()


def _format_l1_matches(matches: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(matches, start=1):
        title = str(item.get("title") or "Core KB source")
        score = item.get("score")
        score_text = f", score {score}" if score is not None else ""
        content = normalize_markdown_text(str(item.get("content") or "")).strip()
        if content:
            lines.append(f"- [L1-{index}] {title}{score_text}: {content[:1200]}")
    return "\n".join(lines) or "- No L1 matches."


def _match_items(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": str(item.get("title") or "Core KB source"),
            "score": item.get("score"),
            "content": normalize_markdown_text(str(item.get("content") or ""))[:400],
        }
        for item in matches
        if str(item.get("content") or "").strip()
    ]


def _concept_items(concepts: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key, value in concepts.items():
        if key in {"evidence_summary", "reason"}:
            continue
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value or "").strip()
        if text:
            items.append({"type": key, "value": text})
    return items


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(number, 1.0))
