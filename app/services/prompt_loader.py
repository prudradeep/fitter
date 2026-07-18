from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.resource_paths import resource_path

PROMPT_DIR = resource_path("app/prompts")

PROMPT_FILES = {
    "energy": "Energy_truth.txt",
    "housing": "Housing_truth.txt",
    "transport": "Transport_truth.txt",
}

MODEL_PROMPT_DIRS = {
    "qwen3.5:2b": "qwen3.5_2b",
    "qwen3.5:4b": "qwen3.5_4b",
    "ministral-3:8b": "ministral-3_8b",
    "qwen3.5:9b": "qwen3.5_9b",
    "ministral-3:14b": "ministral-3_14b",
    "mistral-small3.2:24b": "mistral-small3.2_24b",
    "qwen3.5:27b": "qwen3.5_27b",
}


def sector_prompt_name(sector: str | None) -> str:
    if not sector:
        return "default"
    return sector.strip().lower().replace(" ", "_")


@lru_cache
def load_sector_prompt(sector: str | None) -> str:
    prompt_path = PROMPT_DIR / PROMPT_FILES.get(
        sector_prompt_name(sector),
        "Default_system_prompt.txt",
    )
    if not prompt_path.exists():
        prompt_path = PROMPT_DIR / "Default_system_prompt.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


@lru_cache
def load_prompt_file(filename: str) -> str:
    prompt_path = (PROMPT_DIR / filename).resolve()
    if not prompt_path.exists() or prompt_path.parent != PROMPT_DIR.resolve():
        raise FileNotFoundError(f"Prompt file not found: {filename}")
    return prompt_path.read_text(encoding="utf-8").strip()


def load_nested_prompt_file(filename: str) -> str:
    return _load_nested_prompt_file(filename, get_settings().ollama_model)


@lru_cache
def _load_nested_prompt_file(filename: str, model: str | None) -> str:
    prompt_path = resolve_nested_prompt_path(filename, model)
    return prompt_path.read_text(encoding="utf-8").strip()


def resolve_nested_prompt_path(filename: str, model: str | None = None) -> Path:
    prompt_path = resolve_model_prompt_path(filename, model) or (PROMPT_DIR / filename).resolve()
    prompt_root = PROMPT_DIR.resolve()
    if not prompt_path.exists() or prompt_root not in prompt_path.parents:
        raise FileNotFoundError(f"Prompt file not found: {filename}")
    return prompt_path


def render_prompt_template(filename: str, **context: object) -> str:
    return load_nested_prompt_file(filename).format(**context).strip()


def model_prompt_name(model: str | None) -> str:
    if not model:
        return ""
    return model.strip().casefold()


def resolve_model_prompt_path(filename: str, model: str | None) -> Path | None:
    prompt_dir = MODEL_PROMPT_DIRS.get(model_prompt_name(model))
    relative_path = Path(filename)
    if not prompt_dir or len(relative_path.parts) != 2 or relative_path.parts[0] != "llm":
        return None

    model_prompt_path = (PROMPT_DIR / "llm" / prompt_dir / relative_path.parts[1]).resolve()
    model_prompt_root = (PROMPT_DIR / "llm" / prompt_dir).resolve()
    if model_prompt_path.exists() and model_prompt_path.parent == model_prompt_root:
        return model_prompt_path
    return None
