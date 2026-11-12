"""Generate offline language variants for the static HTML guide pages."""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# A local generation run should not depend on the application's runtime log DB.
os.environ.setdefault("LLM_LOG_TO_DB", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.frontend_translation import (  # noqa: E402
    LANGUAGES,
    FrontendTranslationError,
    translate_frontend_texts,
)


STATIC_ROOT = ROOT / "app" / "static"
SOURCE_FILES = (
    "about.html",
    "intro.html",
    "hazard/index.html",
    "energy-hbg.html",
    "housing-hbg.html",
    "transport-hbg.html",
    "policy-summary.html",
    "mitigation_planning.html",
    "systems_thinking_inquiry.html",
    "thankyou.html",
    "methodology.html",
    "fitter-predictive-model.html",
    "policy-context.html",
    "relevance.html",
)
TRANSLATABLE_ATTRIBUTES = {"alt", "aria-label", "placeholder", "title", "value"}
SKIP_TEXT_TAGS = {"code", "noscript", "pre", "script", "style", "template"}


class HtmlTranslationCollector(HTMLParser):
    """Keep HTML structure intact while exposing visible text and labels."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, Any]] = []
        self.entries: list[str] = []
        self._skip_stack: list[str] = []
        self.feed(source)
        self.close()

    def _add_entry(self, value: str) -> int | None:
        if not value.strip():
            return None
        index = len(self.entries)
        self.entries.append(value)
        return index

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or f"<{tag}>"
        translated_attrs = []
        for name, value in attrs:
            index = (
                self._add_entry(value)
                if value is not None and name.casefold() in TRANSLATABLE_ATTRIBUTES
                else None
            )
            translated_attrs.append((name, value, index))
        self.tokens.append(("start", (raw, tag.casefold(), translated_attrs)))
        if tag.casefold() in SKIP_TEXT_TAGS:
            self._skip_stack.append(tag.casefold())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or f"<{tag}/>"
        translated_attrs = []
        for name, value in attrs:
            index = (
                self._add_entry(value)
                if value is not None and name.casefold() in TRANSLATABLE_ATTRIBUTES
                else None
            )
            translated_attrs.append((name, value, index))
        self.tokens.append(("startend", (raw, translated_attrs)))

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if self._skip_stack and self._skip_stack[-1] == normalized:
            self._skip_stack.pop()
        self.tokens.append(("raw", f"</{tag}>"))

    def handle_data(self, data: str) -> None:
        index = None if self._skip_stack else self._add_entry(data)
        self.tokens.append(("text", (data, index)))

    def handle_comment(self, data: str) -> None:
        self.tokens.append(("raw", f"<!--{data}-->"))

    def handle_decl(self, decl: str) -> None:
        self.tokens.append(("raw", f"<!{decl}>"))

    def handle_pi(self, data: str) -> None:
        self.tokens.append(("raw", f"<?{data}>") )

    def translated_html(self, translated: list[str]) -> str:
        result: list[str] = []
        for kind, value in self.tokens:
            if kind == "raw":
                result.append(value)
            elif kind == "text":
                original, index = value
                if index is None:
                    result.append(original)
                else:
                    leading = original[: len(original) - len(original.lstrip())]
                    trailing = original[len(original.rstrip()):]
                    result.append(leading + html.escape(translated[index], quote=False) + trailing)
            elif kind == "startend":
                raw, attrs = value
                result.append(self._translated_tag(raw, attrs, translated))
            else:
                raw, _tag, attrs = value
                result.append(self._translated_tag(raw, attrs, translated))
        return "".join(result)

    @staticmethod
    def _translated_tag(
        raw: str,
        attrs: list[tuple[str, str | None, int | None]],
        translated: list[str],
    ) -> str:
        if not any(index is not None for _, _, index in attrs):
            return raw
        rendered = []
        for name, value, index in attrs:
            if value is None:
                rendered.append(name)
            else:
                attr_value = translated[index] if index is not None else value
                rendered.append(f'{name}="{html.escape(attr_value, quote=True)}"')
        closing = "/>" if raw.rstrip().endswith("/>") else ">"
        return "<" + raw.lstrip()[1:].split(None, 1)[0].rstrip("/>") + (
            (" " + " ".join(rendered)) if rendered else ""
        ) + closing


def _chunks(entries: list[str], max_items: int = 60, max_chars: int = 8000):
    batch: list[str] = []
    char_count = 0
    for entry in entries:
        if len(entry) > 4000:
            raise ValueError(f"A static HTML text fragment exceeds 4000 characters ({len(entry)}).")
        if batch and (len(batch) >= max_items or char_count + len(entry) > max_chars):
            yield batch
            batch = []
            char_count = 0
        batch.append(entry)
        char_count += len(entry)
    if batch:
        yield batch


async def translate_page(source: str, language: str) -> str:
    parser = HtmlTranslationCollector(source)
    translations: list[str] = []
    for batch in _chunks(parser.entries):
        for attempt in range(3):
            try:
                translations.extend(await translate_frontend_texts(batch, "en", language))
                break
            except FrontendTranslationError:
                if attempt == 2:
                    raise
                print(f"    {language}: retrying a malformed model response", flush=True)
    if len(translations) != len(parser.entries):
        raise RuntimeError("Translation count did not match the extracted HTML text count.")
    return parser.translated_html(translations)


def set_document_language(path: Path, language: str) -> None:
    source = path.read_text(encoding="utf-8")
    updated = re.sub(
        r'(<html\b[^>]*?\blang\s*=\s*)(["\'])[^"\']*\2',
        lambda match: f"{match.group(1)}{match.group(2)}{language}{match.group(2)}",
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    if updated != source:
        path.write_text(updated, encoding="utf-8", newline="")


async def generate(languages: list[str], force: bool, files: list[str]) -> None:
    for relative in files:
        source_path = STATIC_ROOT / relative
        source = source_path.read_text(encoding="utf-8")
        parser = HtmlTranslationCollector(source)
        print(f"{relative}: {len(parser.entries)} text fragments", flush=True)
        for language in languages:
            target_path = source_path.with_name(f"{source_path.stem}.{language}{source_path.suffix}")
            if target_path.exists() and not force and target_path.stat().st_mtime >= source_path.stat().st_mtime:
                set_document_language(target_path, language)
                print(f"  {language}: current, skipping", flush=True)
                continue
            translated = await translate_page(source, language)
            target_path.write_text(translated, encoding="utf-8", newline="")
            set_document_language(target_path, language)
            print(f"  {language}: wrote {target_path.relative_to(STATIC_ROOT)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", nargs="+", choices=LANGUAGES.keys(), default=["de", "es", "pt", "it", "hu"])
    parser.add_argument("--files", nargs="+", choices=SOURCE_FILES, default=list(SOURCE_FILES), help="Only generate selected source pages.")
    parser.add_argument("--force", action="store_true", help="Regenerate even current language variants.")
    args = parser.parse_args()
    asyncio.run(generate(args.languages, args.force, args.files))


if __name__ == "__main__":
    main()
