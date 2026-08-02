import re
from collections.abc import Callable

from app.services.chat_hazard_duplicates import hazard_similarity_words
from app.services.chat_options import compact_for_match, normalize_for_match


InvalidTextChecker = Callable[[str], bool]


def local_mitigation_measure_error(
    mitigation_measure: str,
    is_invalid_user_text: InvalidTextChecker,
) -> str | None:
    if is_invalid_user_text(mitigation_measure):
        return (
            "The mitigation measure appears to contain gibberish, keyboard mashing, "
            "or text that is not meaningful. Please rewrite it as a clear policy action."
        )
    if len(compact_for_match(mitigation_measure)) < 8:
        return "The mitigation measure is too short. Please write a clearer policy action."
    return None


def local_mitigation_field_error(
    mitigation_measure: str,
    reason: str,
    is_invalid_user_text: InvalidTextChecker,
) -> str | None:
    measure_error = local_mitigation_measure_error(mitigation_measure, is_invalid_user_text)
    if measure_error:
        return measure_error
    return local_mitigation_reason_error(reason, is_invalid_user_text, mitigation_measure)


def local_mitigation_reason_error(
    reason: str,
    is_invalid_user_text: InvalidTextChecker,
    mitigation_measure: str | None = None,
) -> str | None:
    if is_invalid_user_text(reason):
        return (
            "The reason appears to contain gibberish, keyboard mashing, or text that "
            "is not meaningful. Please explain why this measure would reduce the "
            "selected hazard for the affected groups."
        )

    normalized = normalize_for_match(reason)
    compact = compact_for_match(reason)
    if len(compact) < 8:
        return "The reason is too short. Please explain the mechanism in a little more detail."
    if mitigation_measure and mitigation_response_repeats_source(reason, mitigation_measure):
        return (
            "The reason repeats the mitigation measure. Please explain how this "
            "measure reduces the selected hazard for the affected groups."
        )

    non_answer_patterns = (
        r"\b(?:i\s+)?don\s*t\s+know\b",
        r"\b(?:i\s+)?do\s+not\s+know\b",
        r"\bno\s+idea\b",
        r"\bnot\s+sure\b",
        r"\bunsure\b",
        r"\bcan(?:not|t)\s+say\b",
        r"\bdon\s*t\s+have\s+(?:a\s+)?reason\b",
        r"\bno\s+reason\b",
        r"\bnot\s+applicable\b",
        r"\bn/?a\b",
    )
    if any(re.search(pattern, normalized) for pattern in non_answer_patterns):
        return (
            "The reason is ambiguous. Please explain how the mitigation measure "
            "would reduce the selected hazard for the affected groups."
        )

    mechanism_terms = {
        "reduce",
        "reduces",
        "reducing",
        "lower",
        "lowers",
        "lowering",
        "prevent",
        "prevents",
        "preventing",
        "avoid",
        "avoids",
        "avoiding",
        "support",
        "supports",
        "supporting",
        "help",
        "helps",
        "helping",
        "improve",
        "improves",
        "improving",
        "increase",
        "increases",
        "increasing",
        "provide",
        "provides",
        "providing",
        "protect",
        "protects",
        "protecting",
        "enable",
        "enables",
        "enabling",
        "ensure",
        "ensures",
        "ensuring",
        "address",
        "addresses",
        "addressing",
        "mitigate",
        "mitigates",
        "mitigating",
        "target",
        "targets",
        "targeting",
        "because",
        "by",
        "through",
        "so",
    }
    tokens = set(normalized.split())
    if len(tokens) < 4 or not tokens & mechanism_terms:
        return (
            "The reason is too vague or unrelated to the mitigation context. "
            "Please describe the mechanism, for example how the measure lowers "
            "exposure, cost, exclusion, or vulnerability for the affected groups."
        )

    return None


def local_mitigation_clarification_error(
    clarification: str,
    sources: list[str],
) -> str | None:
    for source in sources:
        if mitigation_response_repeats_source(clarification, source):
            return (
                "The clarification repeats information already provided. Please add "
                "new details that explain the mitigation mechanism or missing context."
            )
    return None


def mitigation_response_repeats_source(response: str, source: str) -> bool:
    response_key = normalize_for_match(_strip_response_label(response))
    source_key = normalize_for_match(_strip_response_label(source))
    if not response_key or not source_key:
        return False
    if response_key == source_key:
        return True

    response_compact = compact_for_match(response_key)
    source_compact = compact_for_match(source_key)
    if len(response_compact) >= 16 and len(source_compact) >= 16:
        if response_compact in source_compact or source_compact in response_compact:
            return True

    response_words = hazard_similarity_words(response_key)
    source_words = hazard_similarity_words(source_key)
    if not response_words or not source_words:
        return False
    overlap = len(response_words & source_words)
    smaller_overlap = overlap / max(1, min(len(response_words), len(source_words)))
    larger_overlap = overlap / max(1, max(len(response_words), len(source_words)))
    return smaller_overlap >= 0.9 and larger_overlap >= 0.8


def _strip_response_label(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    while True:
        updated = re.sub(
            r"^(?:reason|justification|clarification|mitigation measure|mitigation)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if updated == text:
            return text
        text = updated


def mitigations_are_similar(left: str, right: str) -> bool:
    left_key = normalize_for_match(left)
    right_key = normalize_for_match(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    left_compact = compact_for_match(left)
    right_compact = compact_for_match(right)
    if left_compact in right_compact or right_compact in left_compact:
        return True
    left_words = hazard_similarity_words(left_key)
    right_words = hazard_similarity_words(right_key)
    overlap = len(left_words & right_words)
    smaller_overlap = overlap / max(1, min(len(left_words), len(right_words)))
    larger_overlap = overlap / max(1, max(len(left_words), len(right_words)))
    return smaller_overlap >= 0.8 or (smaller_overlap >= 0.65 and larger_overlap >= 0.45)
