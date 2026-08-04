import logging
from time import perf_counter
from typing import Any

import httpx

from app.config import get_settings
from app.services.llm_logging import log_llm_exchange, new_llm_request_id
from app.services.prompt_loader import load_nested_prompt_file

logger = logging.getLogger(__name__)

ChatMessage = dict[str, str]


async def ask_llm(prompt: str) -> str:
    return await ask_llm_chat(
        context=load_nested_prompt_file("llm/dr_transition_coach.txt"),
        messages=[{"role": "user", "content": prompt}],
    )


async def ask_llm_chat(
    context: str,
    messages: list[ChatMessage],
    *,
    stream: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 700,
    response_format: str | dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    if sync_server_llm_disabled():
        return "LLM requests are disabled on this sync-only server."
    chat_messages = [{"role": "system", "content": context}] + messages
    payload = {
        "model": settings.ollama_model,
        "messages": chat_messages,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if should_disable_thinking(settings.ollama_model):
        payload["think"] = False
    if response_format is not None:
        payload["format"] = response_format
    request_id = new_llm_request_id()
    started_at = perf_counter()

    try:
        async with httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout_seconds,
        ) as client:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            answer = _extract_chat_content(data).strip()
            log_llm_exchange(
                settings,
                request_id=request_id,
                provider="ollama",
                endpoint="/api/chat",
                model=settings.ollama_model,
                request=payload,
                response=data,
                status_code=response.status_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            if answer:
                return answer
            logger.warning("Ollama returned an empty response")
    except httpx.TimeoutException:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="ollama",
            endpoint="/api/chat",
            model=settings.ollama_model,
            request=payload,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=(
                f"Timeout after {settings.ollama_timeout_seconds} seconds "
                f"for model {settings.ollama_model}"
            ),
        )
        logger.warning(
            "Ollama request timed out after %s seconds for model %s",
            settings.ollama_timeout_seconds,
            settings.ollama_model,
        )
        return (
            f"The local model `{settings.ollama_model}` is taking longer than "
            f"{settings.ollama_timeout_seconds} seconds to respond. Please try again, "
            "or use a smaller/faster Ollama model for this workflow."
        )
    except httpx.HTTPStatusError as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="ollama",
            endpoint="/api/chat",
            model=settings.ollama_model,
            request=payload,
            response=exc.response.text,
            status_code=exc.response.status_code,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=f"HTTP {exc.response.status_code}",
        )
        logger.warning("Ollama returned HTTP %s: %s", exc.response.status_code, exc.response.text)
        return (
            f"Ollama returned HTTP {exc.response.status_code} for model "
            f"`{settings.ollama_model}`. Check that the model is installed and available."
        )
    except httpx.HTTPError as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="ollama",
            endpoint="/api/chat",
            model=settings.ollama_model,
            request=payload,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=repr(exc),
        )
        logger.exception("Ollama request failed")
        return (
            f"I cannot reach Ollama at `{settings.ollama_base_url}` right now. "
            "Please make sure the Ollama service is running."
        )
    except ValueError as exc:
        log_llm_exchange(
            settings,
            request_id=request_id,
            provider="ollama",
            endpoint="/api/chat",
            model=settings.ollama_model,
            request=payload,
            response=response.text if "response" in locals() else None,
            status_code=response.status_code if "response" in locals() else None,
            duration_ms=(perf_counter() - started_at) * 1000,
            error=repr(exc),
        )
        logger.exception("Ollama returned invalid JSON")
        return "Ollama returned an invalid response. Please try the request again."

    return (
        f"The local model `{settings.ollama_model}` returned an empty response. "
        "Please try again."
    )


def _extract_chat_content(data: dict[str, Any]) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content

    # Defensive fallback for alternate local server response shapes.
    response = data.get("response")
    if isinstance(response, str):
        return response

    return ""


def should_disable_thinking(model: str | None) -> bool:
    if not model:
        return False
    return model.strip().casefold().startswith("qwen3.5:")


def sync_server_llm_disabled() -> bool:
    settings = get_settings()
    return (
        settings.sync_enabled
        and str(settings.sync_mode or "").strip().casefold() == "server"
        and not settings.sync_server_expose_app_apis
    )
