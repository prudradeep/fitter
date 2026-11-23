from __future__ import annotations


def proposed_indicator_details(profile: object, profile_name: str = "") -> tuple[str, str]:
    """Prefer explicitly selected metadata, then use other available details."""
    profile_data = profile if isinstance(profile, dict) else {}
    metadata = profile_data.get("metadata")
    indicator_mapping = metadata.get("indicator_mapping") if isinstance(metadata, dict) else {}
    if not isinstance(indicator_mapping, dict):
        indicator_mapping = {}

    data_set = profile_data.get("data_set")
    eurostat_data = data_set.get("eurostat") if isinstance(data_set, dict) else {}
    if not isinstance(eurostat_data, dict):
        eurostat_data = {}
    eurostat_metadata = eurostat_data.get("metadata")
    eurostat_mapping = (
        eurostat_metadata.get("indicator_mapping")
        if isinstance(eurostat_metadata, dict)
        else {}
    )
    if not isinstance(eurostat_mapping, dict):
        eurostat_mapping = {}
    population_context = profile_data.get("population_context")
    context_items = population_context if isinstance(population_context, list) else []
    context_dataset = next(
        (
            str(item.get("proposed_eurostat_dataset") or "").strip()
            for item in context_items
            if isinstance(item, dict) and item.get("proposed_eurostat_dataset")
        ),
        "",
    )
    context_indicator = next(
        (
            str(item.get("proposed_indicator_label") or "").strip()
            for item in context_items
            if isinstance(item, dict) and item.get("proposed_indicator_label")
        ),
        "",
    )

    dataset = str(
        indicator_mapping.get("proposed_eurostat_dataset")
        or eurostat_mapping.get("proposed_eurostat_dataset")
        or eurostat_data.get("dataset_code")
        or profile_data.get("proposed_eurostat_dataset")
        or context_dataset
        or ""
    ).strip()
    indicator_label = str(
        indicator_mapping.get("proposed_indicator_label")
        or eurostat_mapping.get("proposed_indicator_label")
        or profile_data.get("proposed_indicator_label")
        or context_indicator
        or ""
    ).strip()
    return dataset, indicator_label
