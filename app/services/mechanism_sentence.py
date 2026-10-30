"""Small, conservative checks for mechanism text shown to users."""

import re


_DANGLING_END = re.compile(
    r"\b(?:a|an|and|at|by|for|from|in|of|on|or|the|through|to|with)\s*[.!?]$",
    re.IGNORECASE,
)


def clean_mechanism_sentence(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-•")


def is_complete_mechanism_sentence(value: object) -> bool:
    text = clean_mechanism_sentence(value)
    return (
        len(text.split()) >= 3
        and text.endswith((".", "!", "?"))
        and not _DANGLING_END.search(text)
    )
