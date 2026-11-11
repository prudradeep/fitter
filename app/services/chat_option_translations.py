"""Database-backed display translations for fixed chat option labels."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Prompt


SUPPORTED_OPTION_LANGUAGES = {"de", "es", "pt", "it", "hu"}
OPTION_TRANSLATION_KEY_PREFIX = "chat-options/"


def option_translation_prompt_key(language: str) -> str:
    return f"{OPTION_TRANSLATION_KEY_PREFIX}{language}"


def load_chat_option_translations(db: Session, language: str) -> dict[str, str]:
    """Return stored canonical-label to translated-label mappings for a language."""
    if language not in SUPPORTED_OPTION_LANGUAGES:
        return {}
    try:
        prompt = db.scalar(
            select(Prompt).where(Prompt.prompt_key == option_translation_prompt_key(language))
        )
    except SQLAlchemyError:
        return {}
    if prompt is None:
        return {}
    try:
        translations = json.loads(prompt.content)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(translations, dict):
        return {}
    return {
        source: translated
        for source, translated in translations.items()
        if isinstance(source, str) and isinstance(translated, str) and source and translated
    }
