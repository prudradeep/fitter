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
        self.assertIn("Full flow - reason to evidence decision", categories)
        self.assertIn("Full flow - confirm affected groups", categories)

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

        structural_safety_cases = [
            row
            for row in rows
            if "missing smoke detectors and window guards" in row["User Hazard"]
        ]
        self.assertTrue(structural_safety_cases)
        self.assertTrue(
            all(row["Expected Action"] == "REJECT_REWRITE" for row in structural_safety_cases)
        )

        employment_shock_cases = [
            row
            for row in rows
            if "job losses and local tax-base decline from coal power phase-out"
            in row["User Hazard"]
        ]
        self.assertTrue(employment_shock_cases)
        self.assertTrue(
            all(row["Expected Action"] == "ACCEPT_HAZARD_NAME" for row in employment_shock_cases)
        )

        renoviction_cases = [
            row
            for row in rows
            if "renovation cost burden and renoviction" in row["User Hazard"]
        ]
        self.assertTrue(renoviction_cases)
        self.assertTrue(
            all(row["Expected Action"] == "ACCEPT_HAZARD_NAME" for row in renoviction_cases)
        )

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

    def test_cases_cover_complete_custom_hazard_flow_spec(self) -> None:
        rows = make_test_cases()
        full_flow_rows = [row for row in rows if row["Category"].startswith("Full flow - ")]
        categories = {row["Category"] for row in full_flow_rows}

        expected_categories = {
            "Full flow - reason to evidence decision",
            "Full flow - open no evidence",
            "Full flow - evidence input requested",
            "Full flow - URL evidence in open chat",
            "Full flow - evidence skip",
            "Full flow - evidence contradiction",
            "Full flow - reason quality rejection",
            "Full flow - reason reveals sector mismatch",
            "Full flow - context clarification resolved",
            "Full flow - duplicate confirmation",
            "Full flow - duplicate override",
            "Full flow - duplicate use existing",
            "Full flow - grounding clarification resolved",
            "Full flow - generic affected group rejected",
            "Full flow - add affected group asks reason",
            "Full flow - added affected group reason accepted",
            "Full flow - edit affected group reason",
            "Full flow - remove user added group",
            "Full flow - system group removal blocked",
            "Full flow - confirm affected groups",
            "Full flow - no profiles target population",
            "Full flow - strict crowd review notice",
            "Full flow - strict crowd success notice",
        }
        self.assertTrue(expected_categories.issubset(categories))
        self.assertTrue(all(row["Execution Scope"] == "Spec only" for row in full_flow_rows))
        self.assertTrue(all(row["Reason / Justification"] for row in full_flow_rows))

        evidence_rows = [
            row
            for row in full_flow_rows
            if row["Category"]
            in {
                "Full flow - open no evidence",
                "Full flow - evidence input requested",
                "Full flow - URL evidence in open chat",
                "Full flow - evidence skip",
                "Full flow - evidence contradiction",
            }
        ]
        self.assertTrue(all(row["Evidence Decision"] for row in evidence_rows))

        group_rows = [
            row
            for row in full_flow_rows
            if row["Category"]
            in {
                "Full flow - generic affected group rejected",
                "Full flow - add affected group asks reason",
                "Full flow - added affected group reason accepted",
                "Full flow - edit affected group reason",
                "Full flow - remove user added group",
                "Full flow - system group removal blocked",
                "Full flow - confirm affected groups",
            }
        ]
        self.assertTrue(all(row["Affected Group Review Action"] for row in group_rows))

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
