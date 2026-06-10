from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.grounding_servers.model_runtime import reranker_model, sigmoid

app = FastAPI(title="Dr Transition Reranker")


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
    documents = [document.strip() for document in payload.documents]
    if any(not document for document in documents):
        raise HTTPException(status_code=422, detail="Documents must not be empty.")
    try:
        raw_scores = reranker_model().predict(
            [(payload.query, document) for document in documents],
            show_progress_bar=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    scores = [round(sigmoid(float(score)), 6) for score in raw_scores]
    return {"scores": scores}
