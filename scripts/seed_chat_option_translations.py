"""Pretranslate fixed chat option labels and save language bundles to the prompts table."""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLM_LOG_TO_DB", "false")

from app.db.session import SessionLocal  # noqa: E402
from app.models import Country, Prompt, Region, Sector  # noqa: E402
from app.schemas import Option  # noqa: E402
from app.services.chat_option_translations import (  # noqa: E402
    SUPPORTED_OPTION_LANGUAGES,
    option_translation_prompt_key,
)
from app.services.frontend_translation import (  # noqa: E402
    FrontendTranslationError,
    LANGUAGES,
    translate_frontend_texts,
)


def collect_fixed_options() -> list[str]:
    labels: set[str] = {
        "Show more",
        "Show More",
        "Show less",
        "Show more hazards",
        "Show more policies",
        "[Documented]",
        "[Inferred]",
        "Source",
        "Show source context",
        "Manage Sessions",
        "New Session",
        "Settings",
        "Open settings",
        "Close settings",
        "Voice",
        "Read assistant responses aloud.",
        "Typing effect",
        "Animate assistant responses as they appear.",
        "Validation check",
        "Strict mode for app validations.",
        "mode for app validations.",
        "Easy",
        "Strict",
        "Crowd Sourcing",
        "Share strictly checked co-created hazards and mitigation measures.",
        "Voice preference",
        "Choose how the assistant speaks.",
        "See UI guide again",
        "Replay the workspace tour.",
        "Main Knowledge Base",
        "Prompt Library",
        "Manage reusable AI instructions",
        "Auto",
        "Sync my Data",
        "Share new sessions, activity, and user-created analysis data from this point forward.",
        "Sync Now",
        "Export Current Session",
        "Import Session",
    }
    for path in (ROOT / "app" / "services").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "Option":
                continue
            for keyword in node.keywords:
                if keyword.arg == "label" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        labels.add(keyword.value.value)

    # These string-valued lists are returned as chat options without Option objects.
    from app.services import chat_options

    for name, value in vars(chat_options).items():
        if isinstance(value, Option):
            labels.add(value.label)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, Option):
                    labels.add(item.label)
                elif isinstance(item, str) and item.strip():
                    labels.add(item)
    return sorted(label.strip() for label in labels if label.strip())


def _chunks(values: list[str], max_items: int = 25, max_chars: int = 4500):
    chunk: list[str] = []
    chars = 0
    for value in values:
        if chunk and (len(chunk) >= max_items or chars + len(value) > max_chars):
            yield chunk
            chunk = []
            chars = 0
        chunk.append(value)
        chars += len(value)
    if chunk:
        yield chunk


async def _translate_chunk(values: list[str], language: str) -> dict[str, str]:
    attempts = 3 if len(values) == 1 else 2
    last_error: FrontendTranslationError | None = None
    for attempt in range(attempts):
        try:
            translated = await translate_frontend_texts(values, "en", language)
            if len(translated) != len(values):
                raise FrontendTranslationError("Translation count did not match option count.")
            return dict(zip(values, translated, strict=True))
        except FrontendTranslationError as exc:
            last_error = exc
            if "configured translation model is unavailable" in str(exc).casefold():
                raise
            if attempt + 1 < attempts:
                print(f"  {language}: retrying malformed batch ({len(values)} labels)", flush=True)

    if len(values) == 1:
        raise last_error or FrontendTranslationError("Could not translate a fixed option label.")

    midpoint = len(values) // 2
    print(f"  {language}: splitting malformed batch ({len(values)} labels)", flush=True)
    result = await _translate_chunk(values[:midpoint], language)
    result.update(await _translate_chunk(values[midpoint:], language))
    return result


async def seed(languages: list[str], force: bool) -> None:
    with SessionLocal() as db:
        labels = set(collect_fixed_options())
        labels.update(db.scalars(select(Country.name)).all())
        labels.update(db.scalars(select(Region.name)).all())
        labels.update(db.scalars(select(Sector.name)).all())
        labels = sorted(label.strip() for label in labels if label and label.strip())
        print(f"Found {len(labels)} fixed and reference option labels.", flush=True)
        for language in languages:
            key = option_translation_prompt_key(language)
            try:
                row = db.query(Prompt).filter(Prompt.prompt_key == key).one_or_none()
            except SQLAlchemyError as exc:
                raise RuntimeError(
                    "The prompts table is unavailable. Apply the application database migrations first."
                ) from exc
            existing: dict[str, str] = {}
            if row and not force:
                try:
                    parsed = json.loads(row.content)
                    if isinstance(parsed, dict):
                        existing = {
                            source: value
                            for source, value in parsed.items()
                            if isinstance(source, str) and isinstance(value, str)
                        }
                except (TypeError, json.JSONDecodeError):
                    pass
            missing = [label for label in labels if force or label not in existing]
            print(f"{language}: translating {len(missing)} labels", flush=True)
            if row is None:
                row = Prompt(prompt_key=key)
                db.add(row)
            row.category = "chat-option-translations"
            row.model = language
            row.display_name = f"Chat option translations ({LANGUAGES[language]})"
            row.content = json.dumps(existing, ensure_ascii=False, sort_keys=True)
            row.source_path = "app/services/chat_options.py"
            db.commit()
            translated_count = 0
            for chunk in _chunks(missing):
                existing.update(await _translate_chunk(chunk, language))
                row.content = json.dumps(existing, ensure_ascii=False, sort_keys=True)
                db.commit()
                translated_count += len(chunk)
                print(
                    f"  {language}: saved {translated_count}/{len(missing)} new labels",
                    flush=True,
                )
            print(f"{language}: stored {len(existing)} labels in the prompts table", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--languages",
        nargs="+",
        choices=sorted(SUPPORTED_OPTION_LANGUAGES),
        default=sorted(SUPPORTED_OPTION_LANGUAGES),
    )
    parser.add_argument("--force", action="store_true", help="Regenerate existing language bundles.")
    args = parser.parse_args()
    asyncio.run(seed(args.languages, args.force))


if __name__ == "__main__":
    main()
