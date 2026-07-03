from __future__ import annotations

import re

from app.llm import ask_llm_chat
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.document_text import compact_text, html_to_text

VOICE_SUMMARY_CLOSING = ""


async def generate_voice_summary(message_html: str) -> str:
    text = _message_text(message_html)
    if not text:
        return VOICE_SUMMARY_CLOSING

    response = await ask_llm_chat(
        context=(
            "You create short voice-assistant summaries for assistant chat messages. "
            "Summarize only the provided message. Do not add facts. Use plain spoken English. "
            "Return only 1 or 2 short summary sentences."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Create a voice-only summary of this assistant message.\n\n"
                    f"{text[:6000]}"
                ),
            }
        ],
        temperature=0,
        max_tokens=130,
    )
    if is_llm_unavailable_response(response) or not response.strip():
        return fallback_voice_summary(message_html)
    return _clean_voice_summary(response, original_text=text)


def fallback_voice_summary(message_html: str) -> str:
    text = _message_text(message_html)
    if not text:
        return VOICE_SUMMARY_CLOSING
    sentences = _sentences(text)
    summary_sentences = sentences[:2] if sentences else [text[:220].strip()]
    return _clean_voice_summary(" ".join(summary_sentences), original_text=text)


def _message_text(message_html: str) -> str:
    return compact_text(html_to_text(str(message_html or "")))


def _clean_voice_summary(value: str, *, original_text: str = "") -> str:
    text = compact_text(str(value or ""))
    text = re.sub(
        re.escape(VOICE_SUMMARY_CLOSING) + r"\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    sentences = _sentences(text)
    if sentences:
        text = " ".join(sentences[:2])
    elif text:
        text = text[:240].strip()
    if _summary_covers_message(text, original_text):
        return text or VOICE_SUMMARY_CLOSING
    return f"{text} {VOICE_SUMMARY_CLOSING}".strip()


def _summary_covers_message(summary: str, original_text: str) -> bool:
    summary_key = _coverage_key(summary)
    original_key = _coverage_key(original_text)
    if not summary_key or not original_key:
        return False
    if len(original_key) <= 240:
        return summary_key == original_key or original_key in summary_key
    return len(summary_key) >= len(original_key) * 0.9


def _coverage_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _sentences(value: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", value)
        if len(sentence.strip()) >= 12
    ]
