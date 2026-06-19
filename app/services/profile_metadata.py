from __future__ import annotations


PROFILE_COLUMN_KEYS = {
    "name",
    "profile",
    "explanation",
    "variable_name",
    "variable",
    "statistical_basis",
    "basis",
    "source",
}


def compact_profile_metadata(profile: dict[str, object]) -> dict[str, object]:
    """Keep auxiliary metadata without recursively embedding profile columns."""
    compacted: dict[str, object] = {}
    current: object = profile
    visited: set[int] = set()

    while isinstance(current, dict) and id(current) not in visited:
        visited.add(id(current))
        for key, value in current.items():
            if key == "metadata" or key in PROFILE_COLUMN_KEYS:
                continue
            compacted.setdefault(key, value)
        current = current.get("metadata")

    return compacted
