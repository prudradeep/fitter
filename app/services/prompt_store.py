from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Prompt
from app.resource_paths import resource_path

PROMPT_ROOT = resource_path("app/prompts").resolve()
CHAT_TEMPLATE_ROOT = resource_path("app/templates/chat").resolve()
PROMPT_DB_ENABLED = False
SECTOR_PROMPT_KEYS = {
    "Default_system_prompt.txt",
    "Energy_truth.txt",
    "Housing_truth.txt",
    "Transport_truth.txt",
}


def prompt_key_for_path(path: Path) -> str:
    path = path.resolve()
    if PROMPT_ROOT in (path, *path.parents):
        return path.relative_to(PROMPT_ROOT).as_posix()
    if CHAT_TEMPLATE_ROOT in (path, *path.parents):
        return f"chat/{path.relative_to(CHAT_TEMPLATE_ROOT).as_posix()}"
    return path.name


def prompt_metadata(prompt_key: str) -> tuple[str, str | None, str]:
    parts = Path(prompt_key).parts
    if len(parts) >= 2 and parts[0] == "chat":
        category = "chat"
        model = None
    elif prompt_key in SECTOR_PROMPT_KEYS:
        category = "sector"
        model = None
    elif len(parts) == 3 and parts[0] == "llm":
        category = "llm-model"
        model = parts[1]
    elif len(parts) >= 2 and parts[0] == "llm":
        category = "llm"
        model = None
    else:
        category = "general"
        model = None
    display_name = prompt_key.replace("/", " / ")
    return category, model, display_name


def seed_prompts_from_files(*, overwrite: bool = False) -> int:
    global PROMPT_DB_ENABLED
    if prompt_source() == "file":
        return 0
    if not PROMPT_ROOT.exists():
        return 0
    with SessionLocal() as db:
        count = seed_prompts_from_files_for_session(db, overwrite=overwrite)
        db.commit()
        PROMPT_DB_ENABLED = True
        return count


def enable_prompt_db_reads() -> None:
    global PROMPT_DB_ENABLED
    if prompt_source() == "file":
        PROMPT_DB_ENABLED = False
        return
    PROMPT_DB_ENABLED = True


def enable_prompt_db_reads_if_rows() -> bool:
    if prompt_source() == "file":
        return False
    try:
        with SessionLocal() as db:
            has_prompts = bool(db.scalar(select(Prompt.id).limit(1)))
    except SQLAlchemyError:
        return False
    if has_prompts:
        enable_prompt_db_reads()
    return has_prompts


def seed_prompts_from_files_for_session(db: Session, *, overwrite: bool = False) -> int:
    changed = 0
    prompt_paths = list(PROMPT_ROOT.rglob("*.txt")) + list(CHAT_TEMPLATE_ROOT.rglob("*.md"))
    for path in sorted(prompt_paths):
        if not path.is_file():
            continue
        prompt_key = prompt_key_for_path(path)
        prompt = db.scalar(select(Prompt).where(Prompt.prompt_key == prompt_key))
        if prompt is not None and not overwrite:
            continue
        content = path.read_text(encoding="utf-8").strip()
        category, model, display_name = prompt_metadata(prompt_key)
        if prompt is None:
            prompt = Prompt(prompt_key=prompt_key)
            db.add(prompt)
        prompt.category = category
        prompt.model = model
        prompt.display_name = display_name
        prompt.content = content
        prompt.source_path = prompt_key
        changed += 1
    return changed


def load_prompt_from_db(prompt_key: str) -> str | None:
    if not should_read_prompts_from_db():
        return None
    try:
        with SessionLocal() as db:
            prompt = db.scalar(select(Prompt).where(Prompt.prompt_key == prompt_key))
            if prompt is None:
                return None
            return prompt.content.strip()
    except SQLAlchemyError:
        return None


def prompt_source() -> str:
    from app.config import get_settings

    return str(get_settings().prompt_source or "auto").strip().casefold()


def should_read_prompts_from_db() -> bool:
    source = prompt_source()
    if source == "file":
        return False
    if source == "db":
        return True
    return PROMPT_DB_ENABLED


def list_prompts(db: Session) -> list[Prompt]:
    return list(
        db.scalars(
            select(Prompt).order_by(Prompt.category, Prompt.model, Prompt.prompt_key)
        ).all()
    )
