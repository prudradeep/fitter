import math
from functools import lru_cache

from app.config import get_settings


def require_cross_encoder():
    try:
        from sentence_transformers import CrossEncoder
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Grounding model dependencies are not installed. Run `uv sync --extra grounding`."
        ) from exc
    return CrossEncoder


@lru_cache(maxsize=1)
def reranker_model():
    cross_encoder = require_cross_encoder()
    return cross_encoder(get_settings().reranker_model, max_length=512)


@lru_cache(maxsize=1)
def nli_model():
    cross_encoder = require_cross_encoder()
    return cross_encoder(get_settings().nli_model, max_length=512)


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    maximum = max(values)
    exponents = [math.exp(value - maximum) for value in values]
    total = sum(exponents)
    return [value / total for value in exponents]


def nli_labels(model, count: int) -> list[str]:
    config = getattr(getattr(model, "model", None), "config", None)
    id_to_label = getattr(config, "id2label", {}) if config is not None else {}
    labels = [str(id_to_label.get(index) or "").casefold() for index in range(count)]
    if any("entail" in label for label in labels):
        return labels
    if count == 3:
        return ["contradiction", "entailment", "neutral"]
    return [f"label_{index}" for index in range(count)]
