import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class GroundingModelService:
    """Adapters for optional dedicated reranker and NLI inference endpoints."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.reranker_status = (
            "DEDICATED_MODEL_READY" if self.settings.reranker_url.strip() else "NOT_CONFIGURED"
        )
        self.nli_status = "DEDICATED_NLI_READY" if self.settings.nli_url.strip() else "NOT_CONFIGURED"

    async def rerank(
        self,
        query: str,
        results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if (
            not results
            or not self.settings.reranker_url.strip()
            or self.reranker_status == "RETRIEVAL_SCORE_FALLBACK"
        ):
            return results

        documents = [str(result.get("content") or "") for result in results]
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.reranker_timeout_seconds,
            ) as client:
                response = await client.post(
                    self.settings.reranker_url,
                    json={"query": query, "documents": documents},
                )
                response.raise_for_status()
                scores = self._reranker_scores(response.json(), len(results))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self.reranker_status = "RETRIEVAL_SCORE_FALLBACK"
            logger.warning(
                "Dedicated reranker unavailable; using retrieval scores: %s",
                exc,
            )
            return results
        self.reranker_status = "DEDICATED_MODEL"

        reranked: list[dict[str, object]] = []
        for result, score in zip(results, scores, strict=True):
            reranked.append({**result, "retrieval_score": result.get("score"), "score": score})
        return sorted(reranked, key=lambda result: float(result.get("score") or 0.0), reverse=True)

    async def entail(
        self,
        premises: list[str],
        hypotheses: list[str],
    ) -> list[dict[str, object]] | None:
        if (
            not premises
            or len(premises) != len(hypotheses)
            or not self.settings.nli_url.strip()
            or self.nli_status == "STRICT_LLM_FALLBACK"
        ):
            return None
        try:
            async with httpx.AsyncClient(timeout=self.settings.nli_timeout_seconds) as client:
                response = await client.post(
                    self.settings.nli_url,
                    json={"pairs": [
                        {"premise": premise, "hypothesis": hypothesis}
                        for premise, hypothesis in zip(premises, hypotheses, strict=True)
                    ]},
                )
                response.raise_for_status()
                verdicts = self._nli_verdicts(response.json(), len(premises))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self.nli_status = "STRICT_LLM_FALLBACK"
            logger.warning(
                "Dedicated NLI unavailable; using strict LLM verification: %s",
                exc,
            )
            return None
        self.nli_status = "DEDICATED_NLI"
        return verdicts

    @staticmethod
    def _reranker_scores(payload: object, expected: int) -> list[float]:
        values = payload.get("scores") if isinstance(payload, dict) else payload
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError("Reranker response must contain one score per document.")
        scores = [float(value) for value in values]
        return [max(0.0, min(score, 1.0)) for score in scores]

    @staticmethod
    def _nli_verdicts(payload: object, expected: int) -> list[dict[str, object]]:
        values = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError("NLI response must contain one result per premise/hypothesis pair.")
        verdicts: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Each NLI result must be an object.")
            label = str(value.get("label") or "").strip().casefold()
            score = float(value.get("score") or 0.0)
            verdicts.append(
                {
                    "entailed": label in {"entailment", "entailed"} and score >= 0.5,
                    "label": label,
                    "score": max(0.0, min(score, 1.0)),
                }
            )
        return verdicts
