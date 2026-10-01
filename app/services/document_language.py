"""Language detection and English translation for user-supplied knowledge."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.llm import ask_llm_chat


class TextChunk(Protocol):
    content: str
    page_number: int | None


_ENGLISH_LANGUAGE_NAMES = {"en", "eng", "english", "english language"}
_LANGUAGE_ALIASES = {
    "arabic": "ar", "bulgarian": "bg", "croatian": "hr", "czech": "cs", "danish": "da",
    "dutch": "nl", "english": "en", "estonian": "et", "finnish": "fi", "french": "fr",
    "german": "de", "greek": "el", "hungarian": "hu", "italian": "it", "latvian": "lv",
    "lithuanian": "lt", "maltese": "mt", "polish": "pl", "portuguese": "pt", "romanian": "ro",
    "slovak": "sk", "slovenian": "sl", "spanish": "es", "swedish": "sv",
}
_LLM_FAILURE_PREFIXES = (
    "i cannot reach ollama", "ollama returned http", "ollama returned an invalid response",
    "the local model `", "llm requests are disabled",
)


def language_is_english(language: str | None) -> bool:
    return str(language or "").strip().casefold() in _ENGLISH_LANGUAGE_NAMES


async def detect_document_language(chunks: Sequence[TextChunk]) -> str:
    """Return the dominant document language as a two-letter ISO 639-1 code."""
    sample = "\n\n".join(chunk.content.strip() for chunk in chunks[:3] if chunk.content.strip())[:5000]
    if not sample:
        raise ValueError("No readable text was found for language detection.")
    result = await ask_llm_chat(
        context=(
            "Identify the dominant natural language of the supplied document text. "
            "Return only its ISO 639-1 two-letter code, with no explanation."
        ),
        messages=[{"role": "user", "content": sample}],
        temperature=0,
        max_tokens=8,
    )
    value = result.strip().casefold()
    if value.startswith(_LLM_FAILURE_PREFIXES):
        raise ValueError("The document language could not be detected.")
    for name, code in _LANGUAGE_ALIASES.items():
        if re.search(rf"\b{re.escape(name)}\b", value):
            return code
    token = re.fullmatch(r"\s*(?:language\s*[:=-]?\s*)?([a-z]{2})\s*[.!]?\s*", value)
    if token:
        return token.group(1)
    raise ValueError("The document language could not be detected.")


async def translate_chunks_to_english(
    chunks: Sequence[TextChunk], source_language: str
) -> list[TextChunk]:
    """Return English chunk copies, preserving each chunk's page number."""
    if language_is_english(source_language):
        return list(chunks)
    translated: list[TextChunk] = []
    for chunk in chunks:
        text = chunk.content.strip()
        if not text:
            continue
        result = await ask_llm_chat(
            context=(
                "You are a precise professional translator of public-policy and evidence documents. "
                "Translate the supplied text into English. Preserve headings, lists, article numbers, "
                "legal citations, names, numbers, and qualifications. Do not summarize, omit text, "
                "or add commentary. Return only the English translation."
            ),
            messages=[{"role": "user", "content": f"Source language: {source_language}\n\nText:\n{text}"}],
            temperature=0,
            max_tokens=1024,
        )
        value = result.strip()
        if not value or value.casefold().startswith(_LLM_FAILURE_PREFIXES):
            raise ValueError("The document could not be translated to English.")
        translated.append(type(chunk)(value, chunk.page_number))
    if not translated:
        raise ValueError("The document could not be translated to English.")
    return translated
