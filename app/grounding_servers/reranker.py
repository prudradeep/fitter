from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.grounding_servers.model_runtime import reranker_model, sigmoid
from app.services.llm_logging import log_llm_exchange, new_llm_request_id

app = FastAPI(title="Dr Transition Reranker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
    allow_credentials=False,
)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    documents: list[str] = Field(min_length=1, max_length=100)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "reranker",
        "model": get_settings().reranker_model,
    }


@app.post("/rerank")
async def rerank(payload: RerankRequest) -> dict[str, list[float]]:
    settings = get_settings()
    request_id = new_llm_request_id()
    started_at = perf_counter()
    request_payload = payload.model_dump()
    documents = [document.strip() for document in payload.documents]
    if any(not document for document in documents):
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="dr-transition-reranker",
            endpoint="/rerank",
            model=settings.reranker_model,
            request=request_payload,
            status_code=422,
            duration_ms=(perf_counter() - started_at) * 1000,
            error="Documents must not be empty.",
        )
        raise HTTPException(status_code=422, detail="Documents must not be empty.")
    try:
        raw_scores = reranker_model().predict(
            [(payload.query, document) for document in documents],
            show_progress_bar=False,
        )
    except Exception as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="dr-transition-reranker",
            endpoint="/rerank",
            model=settings.reranker_model,
            request=request_payload,
            status_code=503,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=repr(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    scores = [round(sigmoid(float(score)), 6) for score in raw_scores]
    response_payload = {"scores": scores}
    log_llm_exchange(
        settings,
        request_id=request_id,
        provider="dr-transition-reranker",
        endpoint="/rerank",
        model=settings.reranker_model,
        request=request_payload,
        response=response_payload,
        status_code=200,
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return {"scores": scores}
