"""Lightweight text-quality checks, deliberately independent of domain validation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from app.llm import ask_llm_chat

logger = logging.getLogger(__name__)

GibberishClassification = Literal["GIBBERISH", "LIKELY_MEANINGFUL", "UNCERTAIN"]
LLMClassification = Literal["meaningful", "gibberish", "uncertain"]
KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
KEYBOARD_TOKENS = frozenset({"qwerty", "qwertyui", "qwertyuiop", "asdf", "asdfgh", "asdfghjkl", "zxcv", "zxcvbnm", "hjkl"})
TOKEN_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
REPEATED_CHARS_RE = re.compile(r"(.)\1{5,}", re.UNICODE)


@dataclass(frozen=True)
class GibberishCheckResult:
    classification: GibberishClassification
    score: int
    reasons: list[str] = field(default_factory=list)
    normalized_text: str = ""


def normalize_input(text: str) -> str:
    """Normalize only the copy used by heuristics; caller text is untouched."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _tokens(value: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(value)]


def check_gibberish(text: str) -> GibberishCheckResult:
    value = normalize_input(text)
    if not value:
        return GibberishCheckResult("GIBBERISH", 0, ["empty_or_whitespace"], value)

    letters = sum(char.isalpha() for char in value)
    if letters == 0:
        return GibberishCheckResult("GIBBERISH", 0, ["no_alphabetic_text"], value)

    tokens = _tokens(value)
    compact = "".join(tokens)
    reasons: list[str] = []
    score = 0

    # Several long tokens with almost no vowels are usually random typing. Keep
    # the threshold conservative so technical abbreviations such as NUTS2 or
    # CO2 remain meaningful.
    vowels = sum(char.casefold() in "aeiouy" for char in compact)
    long_tokens = sum(len(re.sub(r"[^a-z]", "", token)) >= 5 for token in tokens)
    if len(tokens) >= 3 and letters >= 12 and long_tokens >= 2 and vowels / letters < 0.2:
        score += 4
        reasons.append("low_vowel_ratio")

    symbol_ratio = sum(not char.isalnum() and not char.isspace() for char in value) / max(len(value), 1)
    if len(value) >= 5 and symbol_ratio >= 0.55:
        score += 3
        reasons.append("excessive_symbols")
    if REPEATED_CHARS_RE.search(compact):
        score += 3
        reasons.append("excessive_character_repetition")

    keyboard_hits = [token for token in tokens if token in KEYBOARD_TOKENS or any(token == row or (len(token) >= 4 and token in row) for row in KEYBOARD_ROWS)]
    if keyboard_hits:
        score += 3
        reasons.append("keyboard_smash")
        # One accidental keyboard-looking word in a sentence is uncertain;
        # several such tokens, or a keyboard-only input, is strong evidence.
        if len(keyboard_hits) / max(len(tokens), 1) >= 0.75:
            score += 2
            reasons.append("high_random_token_ratio")

    duplicate_ratio = 1 - (len(set(tokens)) / len(tokens)) if tokens else 1
    if len(tokens) >= 4 and duplicate_ratio >= 0.6:
        score += 2
        reasons.append("excessive_duplicate_tokens")

    suspicious = []
    for token in tokens:
        letters_only = re.sub(r"[^a-z]", "", token)
        if len(letters_only) >= 5 and not re.search(r"[aeiouy]", letters_only):
            suspicious.append(token)
        elif len(letters_only) >= 6 and re.search(r"[^a-z]", token) is None and re.search(r"[aeiouy]", letters_only) is None:
            suspicious.append(token)
    if suspicious and len(suspicious) / max(len(tokens), 1) >= 0.5:
        score += 2
        reasons.append("high_random_token_ratio")
        if len(tokens) >= 3 and len(suspicious) >= 2:
            score += 3
            reasons.append("random_token_sequence")
    elif suspicious:
        score += 1
        reasons.append("suspicious_consonant_pattern")

    # A sequence of letters and digits is fine when it has natural token structure
    # (CO2, NUTS2, PM2.5, EU-ETS); only score unbroken long mixed strings.
    if re.fullmatch(r"[A-Za-z0-9]{12,}", value) and not re.search(r"[aeiouy]", value.casefold()):
        score += 1
        reasons.append("random_alphanumeric_pattern")

    classification: GibberishClassification = (
        "GIBBERISH" if score >= 5 else "LIKELY_MEANINGFUL" if score <= 1 else "UNCERTAIN"
    )
    return GibberishCheckResult(classification, score, reasons, value)


GIBBERISH_PROMPT = """You are a text-quality classifier.
Determine only whether the supplied text communicates interpretable human meaning.
Do not judge relevance, factual correctness, completeness, domain validity, or suitability for an application.
Technical terminology, abbreviations, place names, policy terminology, and imperfect English can be meaningful.
Classify random characters, keyboard smashing, meaningless word combinations, or text with no reasonably interpretable meaning as gibberish.
Return strict JSON only: {\"classification\": \"meaningful|gibberish|uncertain\", \"confidence\": 0.0, \"reason\": \"brief explanation\"}.
"""


async def validate_text_meaning(text: str) -> GibberishCheckResult:
    """Run the LLM only for deterministic uncertainty and preserve the local score."""
    result = check_gibberish(text)
    if result.classification != "UNCERTAIN":
        logger.debug("gibberish_check classification=%s score=%d reasons=%s llm_fallback=%s", result.classification, result.score, result.reasons, False)
        return result
    try:
        response = await ask_llm_chat(context=GIBBERISH_PROMPT, messages=[{"role": "user", "content": normalize_input(text)}], temperature=0.0, max_tokens=120)
        parsed = json.loads(response or "")
        classification = str(parsed.get("classification") or "uncertain").casefold()
        mapped: GibberishClassification = {"meaningful": "LIKELY_MEANINGFUL", "gibberish": "GIBBERISH"}.get(classification, "UNCERTAIN")
        result = GibberishCheckResult(mapped, result.score, result.reasons, result.normalized_text)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        result = GibberishCheckResult("UNCERTAIN", result.score, result.reasons, result.normalized_text)
    logger.debug("gibberish_check classification=%s score=%d reasons=%s llm_fallback=%s", result.classification, result.score, result.reasons, True)
    return result
