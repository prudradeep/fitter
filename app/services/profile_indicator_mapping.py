from __future__ import annotations

import hashlib


MOCK_EUROSTAT_DATASETS = (
    "ilc_li41",
    "demo_r_pjangroup",
    "ilc_mdes01_r",
    "lfst_r_lfu3rt",
    "demo_r_pjangroup",
    "ilc_peps11n",
    "ilc_mdsd18",
    "ilc_lvho07_r",
    "ilc_mdes05_r",
    "ilc_mdes04_r",
    "ilc_mdes03_r",
    "ilc_li10_r",
    "demo_r_pjangroup",
    "demo_r_pjangroup",
    "ilc_lvhl21n",
    "hlth_silc_08b_r",
    "edat_lfse_22",
    "edat_lfse_16",
    "yth_empl_110",
    "edat_lfse_04",
    "lfst_r_lfe2emprt",
    "ilc_di11_r",
)


def _mock_dataset_for_profile(profile_name: str) -> str:
    digest = hashlib.sha256(profile_name.casefold().encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(MOCK_EUROSTAT_DATASETS)
    return f"{MOCK_EUROSTAT_DATASETS[index]} (mocked)"


def proposed_indicator_details(profile: object, profile_name: str = "") -> tuple[str, str]:
    """Return proposed Eurostat labels, falling back until the API is integrated."""
    profile_data = profile if isinstance(profile, dict) else {}
    metadata = profile_data.get("metadata")
    indicator_mapping = metadata.get("indicator_mapping") if isinstance(metadata, dict) else {}
    if not isinstance(indicator_mapping, dict):
        indicator_mapping = {}

    dataset = str(indicator_mapping.get("proposed_eurostat_dataset") or "").strip()
    indicator_label = str(indicator_mapping.get("proposed_indicator_label") or "").strip()
    name = profile_name.strip() or str(
        profile_data.get("name") or profile_data.get("profile") or "affected population profile"
    ).strip()
    return (
        dataset or _mock_dataset_for_profile(name),
        indicator_label or f"Mock indicator for {name}",
    )
