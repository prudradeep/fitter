"""Pretranslate fixed chat option labels and save language bundles to the prompts table."""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLM_LOG_TO_DB", "false")

CURATED_UI_TRANSLATIONS = {
    "de": {
        "Language disclaimer:": "Sprachhinweis:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result": "Bitte beachten Sie, dass die Plattform nur die endgültigen Chat-Ausgaben übersetzt. Die gesamte KI-Verarbeitung erfolgt auf Englisch. Daher kann es bei der Echtzeitübersetzung zu Fehlern kommen.",
        "Other Options": "Weitere Optionen",
        "Deep dive into statistical findings": "Statistische Ergebnisse genauer untersuchen",
        "Analysis Stage": "Analysestufe",
        "Country": "Land",
        "Region": "Region",
        "Sector": "Sektor",
        "Policy": "Richtlinie",
        "Hazards": "Gefahren",
        "Mitigation": "Minderung",
        "Evaluation": "Bewertung",
        "Manage Sessions": "Sitzungen verwalten",
        "New Session": "Neue Sitzung",
        "Export Current Session": "Aktuelle Sitzung exportieren",
        "Import Session": "Sitzung importieren",
    },
    "es": {
        "Language disclaimer:": "Aviso sobre el idioma:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result": "Ten en cuenta que la plataforma solo traduce las respuestas finales del chat. Todo el procesamiento de IA se realiza en inglés. Por ello, pueden producirse errores en la traducción en tiempo real.",
        "Other Options": "Otras opciones",
        "Deep dive into statistical findings": "Profundizar en los resultados estadísticos",
        "Analysis Stage": "Etapa del análisis",
        "Country": "País",
        "Region": "Región",
        "Sector": "Sector",
        "Policy": "Política",
        "Hazards": "Riesgos",
        "Mitigation": "Mitigación",
        "Evaluation": "Evaluación",
        "Manage Sessions": "Gestionar sesiones",
        "New Session": "Nueva sesión",
        "Export Current Session": "Exportar sesión actual",
        "Import Session": "Importar sesión",
    },
    "pt": {
        "Language disclaimer:": "Aviso sobre o idioma:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result": "Note que a plataforma traduz apenas as respostas finais do chat. Todo o processamento de IA é realizado em inglês. Por isso, podem ocorrer erros na tradução em tempo real.",
        "Other Options": "Outras opções",
        "Deep dive into statistical findings": "Aprofundar a análise dos resultados estatísticos",
        "Analysis Stage": "Etapa da análise",
        "Country": "País",
        "Region": "Região",
        "Sector": "Setor",
        "Policy": "Política",
        "Hazards": "Riscos",
        "Mitigation": "Mitigação",
        "Evaluation": "Avaliação",
        "Manage Sessions": "Gerir sessões",
        "New Session": "Nova sessão",
        "Export Current Session": "Exportar sessão atual",
        "Import Session": "Importar sessão",
    },
    "it": {
        "Language disclaimer:": "Avviso sulla lingua:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result": "La piattaforma traduce solo le risposte finali della chat. L'intera elaborazione dell'IA avviene in inglese. Di conseguenza, la traduzione in tempo reale può contenere errori.",
        "Other Options": "Altre opzioni",
        "Deep dive into statistical findings": "Approfondisci i risultati statistici",
        "Analysis Stage": "Fase dell'analisi",
        "Country": "Paese",
        "Region": "Regione",
        "Sector": "Settore",
        "Policy": "Politica",
        "Hazards": "Rischi",
        "Mitigation": "Mitigazione",
        "Evaluation": "Valutazione",
        "Manage Sessions": "Gestisci sessioni",
        "New Session": "Nuova sessione",
        "Export Current Session": "Esporta sessione corrente",
        "Import Session": "Importa sessione",
    },
    "hu": {
        "Language disclaimer:": "Nyelvi tájékoztató:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result": "A platform csak a végleges chatválaszokat fordítja le. A teljes MI-feldolgozás angolul történik. Emiatt a valós idejű fordítás hibákat tartalmazhat.",
        "Other Options": "Egyéb lehetőségek",
        "Deep dive into statistical findings": "A statisztikai eredmények részletes vizsgálata",
        "Analysis Stage": "Elemzési szakasz",
        "Country": "Ország",
        "Region": "Régió",
        "Sector": "Ágazat",
        "Policy": "Szakpolitika",
        "Hazards": "Kockázatok",
        "Mitigation": "Mérséklés",
        "Evaluation": "Értékelés",
        "Manage Sessions": "Munkamenetek kezelése",
        "New Session": "Új munkamenet",
        "Export Current Session": "Aktuális munkamenet exportálása",
        "Import Session": "Munkamenet importálása",
    },
}

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
        "Language disclaimer:",
        "Please note that the platform only translates the final chat outputs. The entire AI processing takes place in “English”. There may be errors in real-time translation as a result",
        "Manage Sessions",
        "New Session",
        "Other Options",
        "Deep dive into statistical findings",
        "Analysis Stage",
        "Country",
        "Region",
        "Sector",
        "Policy",
        "Hazards",
        "Mitigation",
        "Evaluation",
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


def _commit_bundle(db, key: str, language: str, content: str) -> None:
    """Retry prompt writes when the SQLite database is briefly busy."""
    for attempt in range(7):
        try:
            row = db.query(Prompt).filter(Prompt.prompt_key == key).one_or_none()
            if row is None:
                row = Prompt(prompt_key=key)
                db.add(row)
            row.category = "chat-option-translations"
            row.model = language
            row.display_name = f"Chat option translations ({LANGUAGES[language]})"
            row.content = content
            row.source_path = "app/services/chat_options.py"
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            if "locked" not in str(exc).casefold() or attempt == 6:
                raise
            delay = min(0.5 * (2**attempt), 8)
            print(
                f"  {language}: database is busy; retrying write in {delay:g}s "
                f"({attempt + 1}/6)",
                flush=True,
            )
            time.sleep(delay)


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


async def seed(
    languages: list[str],
    force: bool,
    selected_labels: set[str] | None = None,
) -> None:
    with SessionLocal() as db:
        labels = set(collect_fixed_options())
        labels.update(db.scalars(select(Country.name)).all())
        labels.update(db.scalars(select(Region.name)).all())
        labels.update(db.scalars(select(Sector.name)).all())
        if selected_labels is not None:
            labels.intersection_update(selected_labels)
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
            curated = CURATED_UI_TRANSLATIONS.get(language, {})
            curated_missing = {
                label: curated[label]
                for label in labels
                if label in curated and (force or label not in existing)
            }
            existing.update(curated_missing)
            missing = [
                label
                for label in labels
                if label not in curated and (force or label not in existing)
            ]
            if curated_missing:
                _commit_bundle(
                    db,
                    key,
                    language,
                    json.dumps(existing, ensure_ascii=False, sort_keys=True),
                )
                print(
                    f"{language}: stored {len(curated_missing)} curated UI translations",
                    flush=True,
                )
            print(f"{language}: translating {len(missing)} labels", flush=True)
            _commit_bundle(
                db,
                key,
                language,
                json.dumps(existing, ensure_ascii=False, sort_keys=True),
            )
            translated_count = 0
            for chunk in _chunks(missing):
                existing.update(await _translate_chunk(chunk, language))
                _commit_bundle(
                    db,
                    key,
                    language,
                    json.dumps(existing, ensure_ascii=False, sort_keys=True),
                )
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
    parser.add_argument(
        "--labels",
        nargs="+",
        help="Translate only these exact source labels from the fixed and reference label set.",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.languages, args.force, set(args.labels) if args.labels else None))


if __name__ == "__main__":
    main()
