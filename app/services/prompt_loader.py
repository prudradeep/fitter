from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

PROMPT_FILES = {
    "energy": "Energy_system_prompt.txt",
    "housing": "Housing_system_prompt.txt",
    "transport": "Transport_system_prompt.txt",
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
