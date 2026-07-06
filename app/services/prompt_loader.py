from functools import lru_cache

from app.resource_paths import resource_path

PROMPT_DIR = resource_path("app/prompts")

PROMPT_FILES = {
    "energy": "Energy_truth.txt",
    "housing": "Housing_truth.txt",
    "transport": "Transport_truth.txt",
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


@lru_cache
def load_nested_prompt_file(filename: str) -> str:
    prompt_path = (PROMPT_DIR / filename).resolve()
    prompt_root = PROMPT_DIR.resolve()
    if not prompt_path.exists() or prompt_root not in prompt_path.parents:
        raise FileNotFoundError(f"Prompt file not found: {filename}")
    return prompt_path.read_text(encoding="utf-8").strip()


def render_prompt_template(filename: str, **context: object) -> str:
    return load_nested_prompt_file(filename).format(**context).strip()
