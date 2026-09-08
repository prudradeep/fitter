import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
    def test_co_created_policy_sections_use_associated_reference_sources(self) -> None:
        service = ChatService.__new__(ChatService)
        service._mitigation_policy_reference_results = MagicMock(
            return_value=[
                {
                    "title": "Regional retrofit policy",
                    "page_number": 12,
                    "source_uri": "https://example.test/policy",
                    "content": "Eligibility ends when household income exceeds the fixed threshold.",
                }
            ]
        )
        session = ChatSession(
            selected_hazard="Households lose retrofit support at the income threshold",
            accepted_custom_hazard="Households lose retrofit support at the income threshold",
            accepted_custom_hazard_id="custom-1",
            custom_hazard={"policy_reference_available": True},
        )
        response = (
            '{"policy_points":[{"text":"A fixed income threshold abruptly ends '
            'eligibility.","source_ids":["S1"]}],'
            '"suggested_modification":"Replace the cutoff with tapered support."}'
        )

        with patch(
            "app.services.chat_mitigation_creation_policy.ask_llm_chat",
            new=AsyncMock(return_value=response),
        ):
            content = asyncio.run(service._co_created_hazard_policy_sections(session))

        self.assertIn("Policy point that causes this hazard", content)
        self.assertIn("A fixed income threshold abruptly ends eligibility", content)
        self.assertIn("Regional retrofit policy, page 12", content)
        self.assertIn("Suggested modification to the policy causing the hazard", content)
        self.assertIn("Replace the cutoff with tapered support", content)
        self.assertEqual(
            session.suggested_existing_policy_modification,
            "Replace the cutoff with tapered support.",
        )

    def test_co_created_policy_sections_do_not_invent_without_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        service._mitigation_policy_reference_results = MagicMock(return_value=[])
        session = ChatSession(
            selected_hazard="A co-created hazard",
            accepted_custom_hazard="A co-created hazard",
            accepted_custom_hazard_id="custom-1",
        )

        content = asyncio.run(service._co_created_hazard_policy_sections(session))

        self.assertIn("No supporting policy reference is associated", content)
        self.assertIn("No grounded modification can be suggested", content)

    def test_system_hazard_does_not_get_co_created_policy_sections(self) -> None:
        service = ChatService.__new__(ChatService)
        content = asyncio.run(
            service._co_created_hazard_policy_sections(
                ChatSession(selected_hazard="A system hazard")
            )
        )
        self.assertEqual(content, "")

    def test_co_created_sections_precede_general_and_new_policy_sections(self) -> None:
        service = ChatService.__new__(ChatService)
        service._matched_mitigation_measure_examples = MagicMock(return_value="")
        service._mitigation_policy_reference_results = MagicMock(return_value=[])
        service._build_deep_dive_messages = AsyncMock(return_value=("context", []))
        service._co_created_hazard_policy_sections = AsyncMock(
            return_value="POLICY POINT\n\nPOLICY MODIFICATION"
        )
        service._current_policy_implementations_section = MagicMock(
            return_value="CURRENT POLICY"
        )
        service._new_policy_suggestions_section = AsyncMock(
            return_value="NEW POLICY"
        )
        session = ChatSession(
            selected_hazard="A co-created hazard",
            accepted_custom_hazard="A co-created hazard",
            accepted_custom_hazard_id="custom-1",
        )
        practical_response = (
            '{"title":"# Practical Considerations","hazard":"A co-created hazard",'
            '"context":"European twin-transition policy implementation",'
            '"themes":[{"heading":"## Delivery",'
            '"summary":"GENERAL CONSIDERATIONS",'
            '"concerns":["- Check access barriers."]}]}'
        )

        with patch(
            "app.services.chat_service.ask_llm_chat",
            new=AsyncMock(return_value=practical_response),
        ):
            content = asyncio.run(service._practical_policy_recommendations(session))

        self.assertLess(content.index("POLICY POINT"), content.index("POLICY MODIFICATION"))
        self.assertLess(
            content.index("POLICY MODIFICATION"),
            content.index("General considerations to mitigate the negative effects"),
        )
        self.assertLess(
            content.index("General considerations to mitigate the negative effects"),
            content.index("NEW POLICY"),
        )
        self.assertNotIn("CURRENT POLICY", content)
        service._current_policy_implementations_section.assert_not_called()

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

    def test_policy_target_groups_are_combined_by_category_and_match(self) -> None:
        service = ChatService.__new__(ChatService)
        target_groups = [
            {"label": "Age range: 18-25", "match_value": "Partially"},
            {"label": "Age range: 25-35", "match_value": "Partially"},
            {"label": "Age range: >65", "match_value": "Partially"},
            {"label": "EU citizenship: No", "match_value": "Partially"},
            {"label": "EU citizenship: Yes", "match_value": "Yes"},
            {"label": "Level of income: High income", "match_value": "Partially"},
            {"label": "Level of income: Low income", "match_value": "Yes"},
            {"label": "Level of income: Medium income", "match_value": "Partially"},
        ]

        summary = service._policy_target_group_summary(target_groups)

        self.assertIn("Age range: 18-25, 25-35, >65 (Partially)", summary)
        self.assertIn("EU citizenship: No (Partially); Yes (Yes)", summary)
        self.assertIn(
            "Level of income: High income, Medium income (Partially); Low income (Yes)",
            summary,
        )
        self.assertEqual(summary.count("Age range:"), 1)
        self.assertEqual(summary.count("Level of income:"), 1)

    def test_why_this_helps_does_not_repeat_target_group_list(self) -> None:
        service = ChatService.__new__(ChatService)
        candidates = [
            {
                "policy_title": "Targeted retrofit support",
                "mitigation_effect": "High mitigation",
                "matched_target_groups": [
                    {"label": "Age range: 18-25", "match_value": "Partially"},
                    {"label": "Age range: 25-35", "match_value": "Partially"},
                ],
            }
        ]

        content = service._separate_new_policy_suggestions(candidates)

        self.assertEqual(content.count("Age range:"), 1)
        self.assertIn("Age range: 18-25, 25-35 (Partially)", content)
        self.assertIn("addresses the target groups listed above", content)

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
