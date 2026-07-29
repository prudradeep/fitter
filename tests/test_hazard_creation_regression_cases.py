from __future__ import annotations

import unittest

from tests.generate_hazard_creation_test_cases import SECTORS, make_test_cases


class HazardCreationRegressionCasesTests(unittest.TestCase):
    def test_cases_cover_valid_invalid_and_cross_sector_matrix(self) -> None:
        rows = make_test_cases()
        categories = {row["Category"] for row in rows}

        self.assertIn("Valid hazard for selected sector", categories)
        self.assertIn("Invalid or non-hazard input", categories)
        self.assertIn("Wrong-sector hazard", categories)
        self.assertIn("Hazard title clarification flow", categories)
        self.assertIn("Hazard clarification after reason", categories)
        self.assertIn("Grounding dimension clarification flow", categories)

        wrong_sector_pairs = {
            (row["Selected Sector"], row["Notes"].split(" hazard submitted")[0].replace("A ", ""))
            for row in rows
            if row["Category"] == "Wrong-sector hazard"
        }
        expected_pairs = {
            (selected_sector, source_sector)
            for selected_sector in SECTORS
            for source_sector in SECTORS
            if selected_sector != source_sector
        }
        self.assertEqual(wrong_sector_pairs, expected_pairs)

    def test_cases_cover_recent_hazard_validation_regressions(self) -> None:
        rows = make_test_cases()

        grocery_cases = [
            row
            for row in rows
            if "grocery prices reduce household purchasing power" in row["User Hazard"]
        ]
        self.assertTrue(grocery_cases)
        self.assertTrue(all(row["Expected Action"] == "REJECT_REWRITE" for row in grocery_cases))

        clarification_cases = [
            row
            for row in rows
            if row["Category"] == "Hazard title clarification flow"
        ]
        actions = {row["Expected Action"] for row in clarification_cases}
        self.assertIn("ASK_TITLE_CLARIFICATION", actions)
        self.assertIn("REASK_TITLE_CLARIFICATION", actions)
        self.assertIn("ACCEPT_HAZARD_NAME", actions)

        context_cases = [
            row
            for row in rows
            if row["Category"] == "Hazard clarification after reason"
        ]
        self.assertTrue(context_cases)
        self.assertTrue(
            all(row["Expected Action"] == "ASK_CONTEXT_CLARIFICATION" for row in context_cases)
        )
        self.assertTrue(
            all(row["Expected Input Mode"] == "textarea" for row in context_cases)
        )

        grounding_cases = [
            row
            for row in rows
            if row["Category"] == "Grounding dimension clarification flow"
        ]
        self.assertTrue(grounding_cases)
        self.assertTrue(
            all(row["Expected Action"] == "ASK_GROUNDING_CLARIFICATION" for row in grounding_cases)
        )
        self.assertTrue(
            all(row["Expected Step"] == "custom_hazard_clarification" for row in grounding_cases)
        )

    def test_valid_cases_exist_for_every_sector(self) -> None:
        rows = make_test_cases()
        sectors_with_valid_cases = {
            row["Selected Sector"]
            for row in rows
            if row["Category"] == "Valid hazard for selected sector"
        }
        self.assertEqual(sectors_with_valid_cases, set(SECTORS))


if __name__ == "__main__":
    unittest.main()
