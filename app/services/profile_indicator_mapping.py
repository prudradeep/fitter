from __future__ import annotations


def proposed_indicator_details(profile: object, profile_name: str = "") -> tuple[str, str]:
    """Prefer explicitly selected metadata, then use other available details."""
    profile_data = profile if isinstance(profile, dict) else {}
    metadata = profile_data.get("metadata")
    indicator_mapping = metadata.get("indicator_mapping") if isinstance(metadata, dict) else {}
    if not isinstance(indicator_mapping, dict):
        indicator_mapping = {}

    dataset = str(
        indicator_mapping.get("proposed_eurostat_dataset")
        or profile_data.get("proposed_eurostat_dataset")
        or ""
    ).strip()
    indicator_label = str(indicator_mapping.get("proposed_indicator_label") or "").strip()
    return dataset, indicator_label
