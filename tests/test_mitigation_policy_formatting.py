import asyncio
import unittest
from unittest.mock import MagicMock

from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.message_renderer import markdown_to_html
from app.services.mitigation_policy_formatting import (
    format_mitigation_reference_links,
    mitigation_reference_link_values,
    normalize_current_policy_measure_title,
    simplify_mitigation_implementation_summary,
)


class MitigationPolicyFormattingTests(unittest.TestCase):
    def test_new_policy_candidates_are_rendered_as_separate_bullets(self) -> None:
        service = ChatService.__new__(ChatService)
        candidates = [
            {
                "policy_code": "P-01",
                "policy_title": "Targeted mobility grants",
                "policy_type": "Grant",
                "short_description": "Fund low-emission travel for eligible households.",
                "mitigation_effect": "High mitigation",
                "matched_target_groups": [
                    {"label": "Low-income households", "match_value": "Yes"}
                ],
            },
            {
                "policy_code": "P-02",
                "policy_title": "Accessible transit support",
                "policy_type": "Service",
                "short_description": "Expand accessible public transport services.",
                "mitigation_effect": "Medium mitigation",
                "matched_target_groups": [
                    {"label": "Disabled residents", "match_value": "Partially"}
                ],
            },
        ]

        content = service._separate_new_policy_suggestions(candidates)
        rendered = markdown_to_html(content)

        self.assertIn("P-01: Targeted mobility grants", rendered)
        self.assertIn("P-02: Accessible transit support", rendered)
        self.assertEqual(content.count("**Proposal:**"), 2)
        self.assertNotIn("combine", content.casefold())
        self.assertNotIn("integrated regional", content.casefold())
        self.assertEqual(
            service._extract_suggested_policy_proposal(content),
            "Fund low-emission travel for eligible households.",
        )

        service._ranked_new_policy_suggestions = MagicMock(return_value=candidates)
        section = asyncio.run(
            service._new_policy_suggestions_section(ChatSession(), limit=3)
        )
        self.assertEqual(section.count("**Proposal:**"), 2)
        self.assertIn("New policy proposals", section)

    def test_current_policy_section_is_retained_but_marked_hidden_for_ui(self) -> None:
        service = ChatService.__new__(ChatService)
        service._matched_mitigation_measure_example_rows = MagicMock(return_value=[])

        content = service._current_policy_implementations_section(ChatSession())
        rendered = markdown_to_html(content)

        self.assertIn("Current Policy Implementations", content)
        self.assertIn('class="current-policy-implementation-section"', rendered)
        self.assertIn("No matching current policy implementations", rendered)

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
