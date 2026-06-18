from __future__ import annotations


def min_max_normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    minimum = min(values.values())
    maximum = max(values.values())
    if maximum == minimum:
        return {key: 1.0 if value > 0 else 0.0 for key, value in values.items()}
    return {key: (value - minimum) / (maximum - minimum) for key, value in values.items()}
