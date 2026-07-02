from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from app.llm import ask_llm_chat
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.prompt_loader import load_nested_prompt_file

logger = logging.getLogger(__name__)

QuestionIntent = dict[str, bool | str]
MessageIntent = dict[str, str]
SUPPORTED_MESSAGE_INTENTS = {
    "selection",
    "multi_selection",
    "question",
    "confirmation_yes",
    "confirmation_no",
    "change_country",
    "change_region",
    "change_sector",
    "restart_selection",
    "unclear",
}


async def detect_user_question_intent(
    message: str,
    context: dict | None = None,
    fallback: Callable[[str], bool] | None = None,
) -> QuestionIntent:
    value = str(message or "").strip()
    if not value:
        return _intent(False, "low", "Empty message.")

    prompt = json.dumps(
        {
            "message": value,
            "context": context or {},
        },
        ensure_ascii=False,
    )
    response = await ask_llm_chat(
        context=load_nested_prompt_file("llm/question_intent_detector.txt"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=180,
    )
    if is_llm_unavailable_response(response):
        return _fallback_intent(value, fallback, "LLM unavailable.")

    parsed = _extract_json_object(response)
    if not isinstance(parsed, dict):
        return _fallback_intent(value, fallback, "Detector did not return valid JSON.")

    is_question = parsed.get("is_question")
    confidence = str(parsed.get("confidence") or "").strip().casefold()
    reason = str(parsed.get("reason") or "").strip()
    if not isinstance(is_question, bool) or confidence not in {"high", "medium", "low"}:
        return _fallback_intent(value, fallback, "Detector JSON did not match schema.")
    return _intent(is_question, confidence, reason or "Classified by question intent detector.")


async def detect_message_intent(
    message: str,
    context: dict | None = None,
) -> MessageIntent:
    value = str(message or "").strip()
    if not value:
        return _message_intent("unclear", "low", "Empty message.")

    prompt = json.dumps(
        {
            "message": value,
            "context": context or {},
        },
        ensure_ascii=False,
    )
    response = await ask_llm_chat(
        context=load_nested_prompt_file("llm/message_intent_detector.txt"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=180,
    )
    if is_llm_unavailable_response(response):
        return _fallback_message_intent(value, "LLM unavailable.")

    parsed = _extract_json_object(response)
    if not isinstance(parsed, dict):
        return _fallback_message_intent(value, "Detector did not return valid JSON.")

    intent = str(parsed.get("intent") or "").strip().casefold()
    confidence = str(parsed.get("confidence") or "").strip().casefold()
    reason = str(parsed.get("reason") or "").strip()
    if intent not in SUPPORTED_MESSAGE_INTENTS or confidence not in {"high", "medium", "low"}:
        return _fallback_message_intent(value, "Detector JSON did not match schema.")
    return _message_intent(intent, confidence, reason or "Classified by message intent detector.")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            logger.warning("Question intent detector returned non-JSON text: %s", text)
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("Question intent detector returned invalid JSON object: %s", text)
            return None
    return value if isinstance(value, dict) else None


def _fallback_intent(
    message: str,
    fallback: Callable[[str], bool] | None,
    reason: str,
) -> QuestionIntent:
    is_question = bool(fallback(message) if fallback else False)
    return _intent(is_question, "medium" if is_question else "low", reason)


def _intent(is_question: bool, confidence: str, reason: str) -> QuestionIntent:
    return {
        "is_question": is_question,
        "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
        "reason": reason,
    }


def _fallback_message_intent(message: str, reason: str) -> MessageIntent:
    normalized = _normalize_for_intent(message)
    if normalized in {"yes", "yeah", "correct", "right", "ok", "okay", "confirm", "proceed"}:
        return _message_intent("confirmation_yes", "medium", reason)
    if normalized in {"no", "wrong", "cancel", "incorrect", "not this"}:
        return _message_intent("confirmation_no", "medium", reason)
    if normalized in {"restart", "start over", "reset everything", "reset"}:
        return _message_intent("restart_selection", "medium", reason)
    if normalized in {"change country", "choose another country", "go back to country"}:
        return _message_intent("change_country", "medium", reason)
    if normalized in {"change region", "select another region", "go back to region"}:
        return _message_intent("change_region", "medium", reason)
    if normalized in {"change sector", "choose another sector", "go back to sector"}:
        return _message_intent("change_sector", "medium", reason)
    if _fallback_question_heuristic(message):
        return _message_intent("question", "medium", reason)
    if " or " in f" {normalized} ":
        return _message_intent("unclear", "low", reason)
    return _message_intent("selection", "low", reason)


def _message_intent(intent: str, confidence: str, reason: str) -> MessageIntent:
    return {
        "intent": intent if intent in SUPPORTED_MESSAGE_INTENTS else "unclear",
        "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
        "reason": reason,
    }


def _normalize_for_intent(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _fallback_question_heuristic(message: str) -> bool:
    value = str(message or "").strip()
    if not value:
        return False
    if "?" in value:
        return True
    normalized = _normalize_for_intent(value)
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "what ",
            "why ",
            "how ",
            "when ",
            "where ",
            "which ",
            "who ",
            "whose ",
            "can ",
            "could ",
            "should ",
            "would ",
            "is ",
            "are ",
            "do ",
            "does ",
            "did ",
            "explain ",
            "tell me ",
        )
    )
