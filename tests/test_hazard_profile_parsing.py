import json
import textwrap
import unittest

from app.services.hazard_profile_parsing import (
    clean_hazard_profile_item,
    extract_socio_demographic_profiles,
    humanize_predictor_label,
    parse_hazard_profile_items,
    profile_from_predictor_entry,
)


class HazardProfileParsingTests(unittest.TestCase):
    def test_extract_socio_demographic_profiles_filters_basis_lines_and_duplicates(self):
        markdown = """
        Intro paragraph
        - **Low-income households**: higher exposure to energy cost increases.
        - Statistical basis: odds ratio 1.4
        1. low-income households - duplicate spelling
        * Rural residents — transport alternatives are limited.
        """

        profiles = extract_socio_demographic_profiles(
            markdown,
            lambda value: value.casefold().startswith("statistical basis"),
        )

        self.assertEqual(profiles, ["Low-income households", "Rural residents"])

    def test_parse_hazard_profile_items_normalizes_json_profiles(self):
        response = json.dumps(
            [
                {
                    "name": " Rural households ",
                    "reason": " Limited access ",
                    "predictor": "macro_energy_access",
                    "basis": "OR=1.7",
                    "metadata": {
                        "target_population_option_ids": ["tp-1"],
                        "target_population_labels": ["Rural households"],
                    },
                },
                "Rural households",
                "Workers",
            ]
        )

        profiles = parse_hazard_profile_items(response)

        self.assertEqual([profile["name"] for profile in profiles], ["Rural households", "Workers"])
        self.assertEqual(profiles[0]["variable_name"], "macro_energy_access")
        self.assertEqual(profiles[0]["target_population_option_ids"], ["tp-1"])

    def test_clean_hazard_profile_item_strips_predictor_prefix(self):
        profile = clean_hazard_profile_item(
            {
                "profile": "Workers",
                "variable_name": "PREDICTOR 12A: worker_share (continuous)",
                "explanation": "Use the confirmed population mapping.",
            }
        )

        self.assertEqual(profile["name"], "Workers")
        self.assertEqual(profile["variable_name"], "worker_share")
        self.assertEqual(profile["explanation"], "Use the confirmed population mapping.")

    def test_profile_from_predictor_entry_builds_named_profile_and_basis(self):
        profile = profile_from_predictor_entry(
            textwrap.dedent("""
            PREDICTOR 2B: macro_income_level (level: "low")
            Direction = Higher concern
            Plain-English: Lower income communities show more transition exposure.
            Odds ratio = 1.9
            """).strip()
        )

        self.assertEqual(profile["name"], "Income level: low")
        self.assertEqual(profile["variable_name"], "macro_income_level")
        self.assertIn("PREDICTOR 2B", profile["statistical_basis"])
        self.assertEqual(humanize_predictor_label("macro_income_level"), "Income level")


if __name__ == "__main__":
    unittest.main()
