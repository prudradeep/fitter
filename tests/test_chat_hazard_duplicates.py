import unittest

from app.services.chat_hazard_duplicates import (
    dedupe_hazard_names,
    hazard_duplicate_payloads,
    local_similar_hazards,
)


class ChatHazardDuplicateTests(unittest.TestCase):
    def test_dedupe_hazard_names_preserves_first_label(self) -> None:
        names = dedupe_hazard_names(
            ["Heating cost shock", " heating  cost shock ", "", None, "Regional job loss"]
        )

        self.assertEqual(names, ["Heating cost shock", "Regional job loss"])

    def test_local_similar_hazards_matches_word_overlap_once(self) -> None:
        matches = local_similar_hazards(
            "Heating cost shock for low income households",
            [
                "Heating cost shocks for low-income households",
                "Heating cost shocks for low-income households",
                "Regional employment decline",
            ],
        )

        self.assertEqual(matches, ["Heating cost shocks for low-income households"])

    def test_hazard_duplicate_payloads_limits_to_top_three_matches(self) -> None:
        payloads = hazard_duplicate_payloads(
            "Heating cost shock",
            ["Heating cost shock", "Heat cost shock", "Heating affordability shock", "Other"],
        )

        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[0]["existing_hazard"], "Heating cost shock")
        self.assertIn("selected sector or context", payloads[0]["reason"])


if __name__ == "__main__":
    unittest.main()
