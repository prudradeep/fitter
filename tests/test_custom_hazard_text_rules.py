import unittest

from app.services.custom_hazard_text_rules import (
    custom_hazard_sector_mismatch_reason,
    custom_hazard_sector_rewrite_suggestion,
    deterministic_custom_hazard_input_review,
    plain_custom_hazard_rejection_reason,
    sector_signal_scores,
)


class CustomHazardTextRulesTests(unittest.TestCase):
    def test_sector_signal_scores_detect_transport_and_housing_terms(self):
        scores = sector_signal_scores(
            "EV charging access for renters in apartments without parking"
        )

        self.assertGreater(scores["transport"], 0)
        self.assertGreater(scores["housing"], 0)

    def test_sector_mismatch_reason_identifies_wrong_sector(self):
        reason = custom_hazard_sector_mismatch_reason(
            selected_sector="Transport",
            hazard="Households lose access to affordable clean heating",
        )

        self.assertIsNotNone(reason)
        self.assertIn("Energy", reason or "")
        self.assertIn("Transport", reason or "")

    def test_rewrite_suggestion_preserves_meaning_for_selected_sector(self):
        suggestion = custom_hazard_sector_rewrite_suggestion(
            selected_sector="Transport",
            hazard="EV home-charging disadvantage for renters and apartment dwellers",
        )

        self.assertIn("Keep the affected group and harm", suggestion)
        self.assertIn("For Transport", suggestion)
        self.assertIn("home-charging", suggestion)

    def test_plain_hazard_rejection_allows_transition_mechanism(self):
        reason = plain_custom_hazard_rejection_reason(
            selected_sector="Housing",
            hazard="Renters face higher costs from retrofit policy",
        )

        self.assertIsNone(reason)

    def test_plain_hazard_rejection_flags_general_household_safety(self):
        reason = plain_custom_hazard_rejection_reason(
            selected_sector="Housing",
            hazard="Carbon monoxide poisoning from domestic heating",
        )

        self.assertIsNotNone(reason)
        self.assertIn("general household safety risk", reason or "")

    def test_deterministic_review_accepts_clear_sector_hazard(self):
        review = deterministic_custom_hazard_input_review(
            selected_sector="Energy",
            hazard="Small businesses face utility arrears from dynamic electricity pricing and smart meter rollout",
        )

        self.assertIsNotNone(review)
        self.assertTrue(review["valid"])

    def test_deterministic_review_rejects_benefit_statement(self):
        review = deterministic_custom_hazard_input_review(
            selected_sector="Transport",
            hazard="EV charging grants support taxi drivers",
        )

        self.assertIsNotNone(review)
        self.assertFalse(review["valid"])
        self.assertIn("benefit", str(review["reason"]))


if __name__ == "__main__":
    unittest.main()
