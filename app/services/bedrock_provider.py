"""Amazon Bedrock chat adapter."""

import asyncio
import json
import os
from time import perf_counter
from typing import Any

import httpx

from app.config import Settings
from app.services.llm_logging import log_llm_exchange, new_llm_request_id


class BedrockProviderError(RuntimeError):
    """A Bedrock request could not be completed."""


class EmbeddingVector(list[float]):
    """Vector carrying the model ID used by the server."""

    def __init__(self, values: list[float], model_id: str) -> None:
        super().__init__(values)
        self.model_id = model_id


def _client(settings: Settings):
    try:
        import boto3
    except ModuleNotFoundError as exc:
        raise BedrockProviderError("Bedrock is selected, but boto3 is not installed.") from exc
    kwargs: dict[str, str] = {}
    if settings.aws_region.strip():
        kwargs["region_name"] = settings.aws_region.strip()
    bearer_token = settings.aws_bearer_token_bedrock.get_secret_value().strip()
    if bearer_token:
        # Botocore reads Bedrock API keys from the process environment.
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer_token
        return boto3.client("bedrock-runtime", **kwargs)
    if settings.aws_profile.strip():
        session = boto3.Session(profile_name=settings.aws_profile.strip())
        return session.client("bedrock-runtime", **kwargs)
    return boto3.client("bedrock-runtime", **kwargs)


def _chat_sync(
    settings: Settings,
    context: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    response_format: str | dict[str, Any] | None,
) -> str:
    model_id = settings.bedrock_model_id.strip()
    request_id = new_llm_request_id()
    if response_format == "json":
        context = f"{context}\nReturn valid JSON only. Do not use Markdown or code fences."
    elif isinstance(response_format, dict):
        context = (
            f"{context}\nReturn valid JSON only, matching this schema:\n"
            f"{json.dumps(response_format, ensure_ascii=False)}"
        )
    request: dict[str, Any] = {
        "modelId": model_id,
        "messages": [
            {"role": message["role"], "content": [{"text": message["content"]}]}
            for message in messages
        ],
        "inferenceConfig": {"temperature": temperature, "maxTokens": max_tokens},
    }
    if context:
        request["system"] = [{"text": context}]
    started_at = perf_counter()
    try:
        response = _client(settings).converse(**request)
        content = response.get("output", {}).get("message", {}).get("content", [])
        answer = "".join(item.get("text", "") for item in content if isinstance(item, dict)).strip()
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="bedrock",
            endpoint="Converse",
            model=model_id,
            request=request,
            response=response,
            status_code=200,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        if not answer:
            raise BedrockProviderError("Bedrock returned an empty response.")
        return answer
    except BedrockProviderError:
        raise
    except Exception as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="bedrock",
            endpoint="Converse",
            model=model_id,
            request=request,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=repr(exc),
        )
        raise BedrockProviderError(f"Bedrock chat request failed: {exc}") from exc


async def chat(
    settings: Settings,
    context: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    response_format: str | dict[str, Any] | None = None,
) -> str:
    return await asyncio.to_thread(
        _chat_sync,
        settings,
        context,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )


def _embedding_sync(settings: Settings, text: str) -> EmbeddingVector:
    model_id = settings.bedrock_embedding_model_id.strip()
    request_id = new_llm_request_id()
    payload = {"inputText": text}
    started_at = perf_counter()
    try:
        response = _client(settings).invoke_model(
            modelId=model_id,
            body=json.dumps(payload),
            contentType="application/json",
            accept="application/json",
        )
        data = json.loads(response["body"].read())
        raw = data.get("embedding")
        if not isinstance(raw, list) or not raw:
            raise ValueError("Bedrock returned an empty embedding.")
        values = EmbeddingVector([float(value) for value in raw], model_id)
        log_llm_exchange(
            settings, request_id=request_id, provider="bedrock", endpoint="InvokeModel",
            model=model_id, request=payload, response=data, status_code=200,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return values
    except Exception as exc:
        log_llm_exchange(
            settings, request_id=request_id, provider="bedrock", endpoint="InvokeModel",
            model=model_id, request=payload,
            duration_ms=(perf_counter() - started_at) * 1000, error=repr(exc),
        )
        raise BedrockProviderError(f"Bedrock embedding request failed: {exc}") from exc


async def embedding(settings: Settings, text: str) -> EmbeddingVector:
    return await asyncio.to_thread(_embedding_sync, settings, text)


async def proxy_embedding(settings: Settings, text: str) -> EmbeddingVector:
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{settings.sync_server_url.strip().rstrip('/')}/api/sync/llm/embedding",
                headers={"X-Sync-Token": settings.sync_api_token.strip()},
                json={"text": text},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        raise BedrockProviderError("The sync server timed out while creating Bedrock embeddings.") from exc
    except httpx.HTTPStatusError as exc:
        raise BedrockProviderError(
            f"The sync server rejected the Bedrock embedding request (HTTP {exc.response.status_code})."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise BedrockProviderError(f"Could not reach the sync server for Bedrock embeddings: {exc}") from exc
    if not isinstance(data, dict):
        raise BedrockProviderError("Invalid Bedrock embedding proxy response.")
    if data.get("error"):
        raise BedrockProviderError(str(data.get("detail") or "The sync server could not create Bedrock embeddings."))
    raw = data.get("embedding")
    if not isinstance(raw, list) or not raw:
        raise BedrockProviderError("The sync server returned an empty Bedrock embedding.")
    try:
        return EmbeddingVector([float(value) for value in raw], str(data.get("model") or "server-managed"))
    except (TypeError, ValueError) as exc:
        raise BedrockProviderError("The sync server returned an invalid Bedrock embedding.") from exc
