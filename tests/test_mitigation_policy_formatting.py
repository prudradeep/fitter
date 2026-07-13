import unittest

from app.services.mitigation_policy_formatting import (
    format_mitigation_reference_links,
    mitigation_reference_link_values,
    normalize_current_policy_measure_title,
    simplify_mitigation_implementation_summary,
)


class MitigationPolicyFormattingTests(unittest.TestCase):
    def test_normalize_current_policy_measure_title_removes_bullet_prefix(self) -> None:
        self.assertEqual(
            normalize_current_policy_measure_title("- home retrofit grants:"),
            "Home retrofit grants",
        )

    def test_reference_link_values_extracts_urls(self) -> None:
        self.assertEqual(
            mitigation_reference_link_values("See https://example.test/a; https://example.test/b"),
            ["https://example.test/a", "https://example.test/b"],
        )

    def test_reference_link_values_falls_back_to_text(self) -> None:
        self.assertEqual(
            mitigation_reference_link_values("Policy database record"),
            ["Policy database record"],
        )

    def test_simplify_summary_removes_profile_prefix(self) -> None:
        self.assertEqual(
            simplify_mitigation_implementation_summary(
                'For the profile "low-income households", grants lower upfront costs.'
            ),
            "Grants lower upfront costs.",
        )

    def test_format_reference_links_numbers_markdown_links(self) -> None:
        self.assertEqual(
            format_mitigation_reference_links("https://example.test/a https://example.test/b"),
            "[Reference 1](https://example.test/a); [Reference 2](https://example.test/b)",
        )


if __name__ == "__main__":
    unittest.main()
