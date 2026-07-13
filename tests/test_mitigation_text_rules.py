import unittest

from app.services.mitigation_text_rules import (
    local_mitigation_field_error,
    local_mitigation_reason_error,
    mitigations_are_similar,
)


def _valid_text(_: str) -> bool:
    return False


def _invalid_text(_: str) -> bool:
    return True


class MitigationTextRulesTests(unittest.TestCase):
    def test_reason_requires_mechanism(self) -> None:
        error = local_mitigation_reason_error("This is a nice idea", _valid_text)

        self.assertIsNotNone(error)
        self.assertIn("too vague", error)

    def test_reason_accepts_clear_mechanism(self) -> None:
        error = local_mitigation_reason_error(
            "It reduces costs by providing targeted grants to affected households.",
            _valid_text,
        )

        self.assertIsNone(error)

    def test_field_error_prioritizes_invalid_measure(self) -> None:
        error = local_mitigation_field_error("asdf", "because it helps", _invalid_text)

        self.assertIsNotNone(error)
        self.assertIn("mitigation measure", error)

    def test_similar_mitigations_match_paraphrase_overlap(self) -> None:
        self.assertTrue(
            mitigations_are_similar(
                "Provide grants for low-income households to install heat pumps",
                "Targeted heat pump grants for low income households",
            )
        )


if __name__ == "__main__":
    unittest.main()
