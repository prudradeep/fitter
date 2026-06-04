from app.models import Country, Region, Sector
from app.schemas import Option

POST_SECTOR_OPTIONS = [
    Option(id=1, label="Start Mitigation Planning"),
    Option(id=2, label="Add a new Hazard"),
    Option(id=3, label="Dive deeper into statistical findings"),
]

STATS_DEEP_DIVE_OPTIONS = [
    Option(id=1, label="Start Mitigation Planning"),
    Option(id=2, label="Add a new Hazard"),
]

SOCIO_DEMOGRAPHIC_OPTIONS = [
    Option(id=1, label="Create Mitigation Measure"),
    Option(id=2, label="Add more DGs"),
]

REASON_CONFIRMATION_OPTIONS = [
    Option(id=1, label="Yes"),
    Option(id=2, label="No"),
]

MITIGATION_REVIEW_OPTIONS = [
    Option(id=1, label="Move to next step"),
]

OTHER_NAV_OPTIONS = [
    "Analyse another hazard in the same sector",
    "Write hazard again",
    "Write mitigation measure again",
    "Select another region",
    "Choose a different sector",
    "Start over with a different country",
]

EVALUATION_CATEGORIES = [
    "The transformative impact",
    "Feasibility and Implementation",
]


def option_list(rows: list[Country] | list[Region] | list[Sector]) -> list[Option]:
    return [Option(id=row.id, label=row.name) for row in rows]


def normalize(value: str) -> str:
    return value.strip().casefold()


def normalize_for_match(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def compact_for_match(value: str) -> str:
    return normalize_for_match(value).replace(" ", "")


def levenshtein_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)

    for left_index, left_char in enumerate(left, start=1):
        current[0] = left_index
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current[right_index] = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + cost,
            )
        previous, current = current, previous

    return previous[len(right)]


def fuzzy_score(input_value: str, option_label: str) -> float:
    query = compact_for_match(input_value)
    label = compact_for_match(option_label)
    if not query or not label:
        return 0
    if query == label:
        return 1
    if query in label:
        return min(0.95, 0.66 + len(query) / len(label))

    distance = levenshtein_distance(query, label)
    length = max(len(query), len(label))
    return 1 - distance / length


def best_fuzzy_label(input_value: str, labels: list[str], threshold: float = 0.45) -> str | None:
    best_label = None
    best_score = 0.0

    for label in labels:
        score = fuzzy_score(input_value, label)
        if score > best_score:
            best_label = label
            best_score = score

    if best_label is not None and best_score >= threshold:
        return best_label
    return None


def exact_option_label(message: str, options: list[Option]) -> str | None:
    stripped = message.strip()
    for option in options:
        if str(option.id) == stripped or normalize(option.label) == normalize(stripped):
            return option.label
    return None


def match_option_label(message: str, options: list[Option], threshold: float = 0.45) -> str | None:
    exact_label = exact_option_label(message, options)
    if exact_label is not None:
        return exact_label
    return best_fuzzy_label(message.strip(), [option.label for option in options], threshold)
