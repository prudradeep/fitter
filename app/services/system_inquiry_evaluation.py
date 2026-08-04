from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_GOLD_SET_PATH = Path(__file__).resolve().parents[2] / "data" / "system_inquiry_gold_set.json"
DEFAULT_EXPERT_GOLD_SET_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "system_inquiry_gold_set_expert.json"
)


def load_system_inquiry_gold_set(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else DEFAULT_GOLD_SET_PATH
    with source.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("System inquiry gold set must contain at least one case.")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Gold-set cases must be objects.")
        if not str(case.get("case_id") or "").strip():
            raise ValueError("Every gold-set case needs a case_id.")
        probes = case.get("expert_probe_ids")
        if not isinstance(probes, list) or not probes:
            raise ValueError(f"Gold-set case {case.get('case_id')} needs expert_probe_ids.")
    return data


def load_expert_system_inquiry_gold_set(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else DEFAULT_EXPERT_GOLD_SET_PATH
    data = load_system_inquiry_gold_set(source)
    validate_expert_system_inquiry_gold_set(data)
    return data


def validate_expert_system_inquiry_gold_set(gold_set: dict[str, Any]) -> None:
    cases = [case for case in gold_set.get("cases") or [] if isinstance(case, dict)]
    if not 25 <= len(cases) <= 30:
        raise ValueError("Expert system inquiry gold set must contain 25-30 cases.")
    for case in cases:
        if str(case.get("label_source") or "").strip() != "consortium_expert":
            raise ValueError(
                f"Gold-set case {case.get('case_id')} is not marked consortium_expert."
            )
        reviewers = case.get("expert_reviewers")
        if not isinstance(reviewers, list) or not reviewers:
            raise ValueError(f"Gold-set case {case.get('case_id')} needs expert_reviewers.")


def evaluate_system_inquiry_predictions(
    gold_set: dict[str, Any],
    predictions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    true_positive = false_positive = false_negative = 0
    firing_counts: dict[str, int] = {}
    verify_three = verify_two_or_more = 0
    anchor_valid = anchor_checked = 0
    surfaced = held_by_cap = 0
    per_case: list[dict[str, Any]] = []

    cases = [case for case in gold_set.get("cases") or [] if isinstance(case, dict)]
    for case in cases:
        case_id = str(case.get("case_id") or "")
        expected = {str(item) for item in case.get("expert_probe_ids") or [] if str(item)}
        predicted_records = predictions.get(case_id, [])
        predicted = {
            str(item.get("probe_id") or "")
            for item in predicted_records
            if isinstance(item, dict) and str(item.get("candidate_status") or "selected") == "selected"
        }
        predicted.discard("")
        for probe_id in predicted:
            firing_counts[probe_id] = firing_counts.get(probe_id, 0) + 1

        true_positive += len(expected & predicted)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
        per_case.append(
            {
                "case_id": case_id,
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "missed": sorted(expected - predicted),
                "unexpected": sorted(predicted - expected),
            }
        )

        for record in predicted_records:
            if not isinstance(record, dict):
                continue
            status = str(record.get("candidate_status") or "")
            if status == "selected":
                surfaced += 1
            elif status == "held_cap":
                held_by_cap += 1
            if "anchor_valid" in record:
                anchor_checked += 1
                if bool(record.get("anchor_valid")):
                    anchor_valid += 1
            elif isinstance(record.get("anchor_counts"), dict):
                anchor_checked += 1
                if any(int(value or 0) > 0 for value in record["anchor_counts"].values()):
                    anchor_valid += 1
            votes = record.get("verify_votes")
            if votes is not None:
                try:
                    parsed_votes = int(votes)
                except (TypeError, ValueError):
                    parsed_votes = 0
                if parsed_votes >= 2:
                    verify_two_or_more += 1
                if parsed_votes == 3:
                    verify_three += 1

    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "case_count": len(cases),
        "probe_precision": _ratio(true_positive, precision_denominator),
        "probe_recall": _ratio(true_positive, recall_denominator),
        "anchor_validity_rate": _ratio(anchor_valid, anchor_checked),
        "verify_stability": _ratio(verify_three, verify_two_or_more),
        "firing_rate_per_probe": {
            probe_id: round(count / max(len(cases), 1), 4)
            for probe_id, count in sorted(firing_counts.items())
        },
        "surfaced": surfaced,
        "held_by_cap": held_by_cap,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "per_case": per_case,
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
