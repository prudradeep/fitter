from time import perf_counter

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.grounding_servers.model_runtime import nli_labels, nli_model, softmax
from app.services.llm_logging import log_llm_exchange, new_llm_request_id

app = FastAPI(title="Dr Transition NLI")


class EntailmentPair(BaseModel):
    premise: str = Field(min_length=1, max_length=30000)
    hypothesis: str = Field(min_length=1, max_length=10000)


class EntailmentRequest(BaseModel):
    pairs: list[EntailmentPair] = Field(min_length=1, max_length=100)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "nli",
        "model": get_settings().nli_model,
    }


@app.post("/entail")
async def entail(payload: EntailmentRequest) -> dict[str, list[dict[str, float | str]]]:
    settings = get_settings()
    request_id = new_llm_request_id()
    started_at = perf_counter()
    request_payload = payload.model_dump()
    try:
        model = nli_model()
        logits = model.predict(
            [(pair.premise, pair.hypothesis) for pair in payload.pairs],
            apply_softmax=False,
            show_progress_bar=False,
        )
    except Exception as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="dr-transition-nli",
            endpoint="/entail",
            model=settings.nli_model,
            request=request_payload,
            status_code=503,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=repr(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results: list[dict[str, float | str]] = []
    for row in logits:
        probabilities = softmax([float(value) for value in row])
        labels = nli_labels(model, len(probabilities))
        best_index = max(range(len(probabilities)), key=probabilities.__getitem__)
        results.append(
            {
                "label": labels[best_index],
                "score": round(probabilities[best_index], 6),
            }
        )
    response_payload = {"results": results}
    log_llm_exchange(
        settings,
        request_id=request_id,
        provider="dr-transition-nli",
        endpoint="/entail",
        model=settings.nli_model,
        request=request_payload,
        response=response_payload,
        status_code=200,
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return response_payload
