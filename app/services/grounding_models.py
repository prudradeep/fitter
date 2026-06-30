import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class GroundingModelService:
    """Adapters for optional dedicated reranker and NLI inference endpoints."""

    max_reranker_query_chars = 16000
    max_reranker_documents_per_request = 100
    max_nli_premise_chars = 30000
    max_nli_hypothesis_chars = 10000
    max_nli_pairs_per_request = 100

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

        query_text = self._truncate_text(query, self.max_reranker_query_chars)
        if not query_text:
            return results

        indexed_documents = [
            (index, str(result.get("content") or "").strip())
            for index, result in enumerate(results)
            if str(result.get("content") or "").strip()
        ]
        if not indexed_documents:
            return results

        try:
            scores_by_index: dict[int, float] = {}
            async with httpx.AsyncClient(timeout=self.settings.reranker_timeout_seconds) as client:
                for chunk in self._chunks(
                    indexed_documents,
                    self.max_reranker_documents_per_request,
                ):
                    response = await client.post(
                        self.settings.reranker_url,
                        json={
                            "query": query_text,
                            "documents": [document for _, document in chunk],
                        },
                    )
                    response.raise_for_status()
                    chunk_scores = self._reranker_scores(response.json(), len(chunk))
                    for (index, _), score in zip(chunk, chunk_scores, strict=True):
                        scores_by_index[index] = score
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self.reranker_status = "RETRIEVAL_SCORE_FALLBACK"
            logger.warning(
                "Dedicated reranker unavailable; using retrieval scores: %s",
                exc,
            )
            return results
        self.reranker_status = "DEDICATED_MODEL"

        reranked: list[dict[str, object]] = []
        for index, result in enumerate(results):
            score = scores_by_index.get(index)
            if score is None:
                score = float(result.get("score") or 0.0)
            reranked.append({**result, "retrieval_score": result.get("score"), "score": score})
        return sorted(reranked, key=lambda result: float(result.get("score") or 0.0), reverse=True)

    async def ground_results(
        self,
        query: str,
        results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Apply reranking and NLI prioritization before results reach an LLM."""
        reranked = await self.rerank(query, results)
        if not reranked:
            return reranked

        verdicts = await self.entail(
            [str(result.get("content") or "") for result in reranked],
            [query] * len(reranked),
        )
        if verdicts is None:
            return reranked

        grounded = [
            {
                **result,
                "nli_label": verdict.get("label"),
                "nli_score": verdict.get("score"),
                "nli_entailed": verdict.get("entailed"),
            }
            for result, verdict in zip(reranked, verdicts, strict=True)
        ]
        # Query text is often a question or keyword bundle rather than a formal
        # hypothesis. Preserve all retrieved context, but prioritize direct entailment.
        return sorted(
            grounded,
            key=lambda result: (
                bool(result.get("nli_entailed")),
                float(result.get("nli_score") or 0.0)
                if result.get("nli_entailed")
                else float(result.get("score") or 0.0),
            ),
            reverse=True,
        )

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
        indexed_pairs: list[dict[str, object]] = []
        verdicts: list[dict[str, object]] = [
            {"entailed": False, "label": "neutral", "score": 0.0}
            for _ in premises
        ]
        for index, (premise, hypothesis) in enumerate(
            zip(premises, hypotheses, strict=True)
        ):
            premise_text = self._truncate_text(premise, self.max_nli_premise_chars)
            hypothesis_text = self._truncate_text(
                hypothesis,
                self.max_nli_hypothesis_chars,
            )
            if not premise_text or not hypothesis_text:
                continue
            indexed_pairs.append(
                {
                    "__index": index,
                    "premise": premise_text,
                    "hypothesis": hypothesis_text,
                }
            )
        if not indexed_pairs:
            return verdicts
        try:
            async with httpx.AsyncClient(timeout=self.settings.nli_timeout_seconds) as client:
                for chunk in self._chunks(indexed_pairs, self.max_nli_pairs_per_request):
                    request_pairs = [
                        {
                            "premise": pair["premise"],
                            "hypothesis": pair["hypothesis"],
                        }
                        for pair in chunk
                    ]
                    response = await client.post(
                        self.settings.nli_url,
                        json={"pairs": request_pairs},
                    )
                    response.raise_for_status()
                    chunk_verdicts = self._nli_verdicts(response.json(), len(chunk))
                    for pair, verdict in zip(chunk, chunk_verdicts, strict=True):
                        verdicts[int(pair["__index"])] = verdict
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

    @staticmethod
    def _truncate_text(value: object, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip()

    @staticmethod
    def _chunks(values: list[object], size: int) -> list[list[object]]:
        return [values[index:index + size] for index in range(0, len(values), size)]
