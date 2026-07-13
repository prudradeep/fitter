import unittest

from app.services.custom_hazard_matching import (
    dedupe_groups,
    duplicate_candidates,
    extract_affected_groups,
    semantic_duplicate_similarity,
)
from app.services.enums import ConfidenceLevel


class CustomHazardMatchingTests(unittest.TestCase):
    def test_extracts_policy_group_aliases(self):
        groups = extract_affected_groups(
            "Local energy communities and low income households may face higher grid costs."
        )

        names = {group["group"] for group in groups}
        self.assertIn("Renewable energy communities", names)
        self.assertIn("Low-income households", names)

    def test_dedupes_groups_and_filters_generic_labels(self):
        groups = dedupe_groups(
            [
                {"group": "Low-income households", "confidence": ConfidenceLevel.HIGH.value},
                {"name": "low income households"},
                {"group": "people"},
            ]
        )

        self.assertEqual([group["group"] for group in groups], ["Low-income households"])

    def test_duplicate_candidates_merge_llm_and_known_matches(self):
        candidates = duplicate_candidates(
            "Regional employment shock in fossil fuel dependent energy regions",
            [
                "Regional job losses in coal and oil regions",
                "Slow heat pump uptake among homeowners",
            ],
            [
                {
                    "existing_hazard": "Regional job losses in coal and oil regions",
                    "similarity_score": 6,
                    "reason": "Same regional workforce impact.",
                }
            ],
        )

        self.assertEqual(candidates[0]["existing_hazard"], "Regional job losses in coal and oil regions")
        self.assertEqual(len(candidates), 1)
        self.assertGreaterEqual(candidates[0]["similarity_score"], 72)

    def test_semantic_duplicate_similarity_handles_paraphrases(self):
        score = semantic_duplicate_similarity(
            "Employment shock in fossil-fuel-dependent regions",
            "Job losses in fossil fuel dependent regions",
        )

        self.assertGreaterEqual(score, 0.70)


if __name__ == "__main__":
    unittest.main()
