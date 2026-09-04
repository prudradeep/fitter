import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.chat_formatters import format_custom_hazards
from app.services.chat_hazard_catalog import ChatHazardCatalogMixin
from app.services.chat_session import ChatSession
from app.services.message_renderer import markdown_to_html


class CustomHazardEvidenceLabelTests(unittest.TestCase):
    def test_custom_hazard_cards_show_evidence_status_per_hazard(self):
        session = ChatSession(
            custom_hazards=["Hazard with evidence", "Hazard without evidence"],
            custom_hazard_evidence_statuses={
                "hazard with evidence": True,
                "hazard without evidence": False,
            },
            custom_hazard_evidence={
                "hazard with evidence": (
                    "Evidence URL: [Report\\_PDF](https://example.com/report.pdf)\n"
                    "Temporary evidence document ID: "
                    "9cbe3d02-f3ef-45dc-9df7-1a491ffdfd1e"
                ),
            },
            custom_hazard_summaries={
                "hazard with evidence": "Workers may face transition-related income loss.",
            },
            hazard_profiles={
                "Hazard with evidence": [{"name": "Workers"}],
                "Hazard without evidence": [{"name": "Households"}],
            },
        )

        html = format_custom_hazards(session)

        self.assertEqual(html.count("Evidence provided"), 1)
        self.assertEqual(html.count("Evidence not provided"), 1)
        self.assertIn("hazard-evidence-label--provided", html)
        self.assertIn("hazard-evidence-label--not-provided", html)
        self.assertIn('<button type="button" class="hazard-evidence-label', html)
        self.assertIn('data-evidence-url="https://example.com/report.pdf"', html)
        self.assertNotIn("Temporary evidence document ID", html)
        self.assertIn('<details class="hazard-card-summary">', html)
        self.assertIn("Workers may face transition-related income loss.", html)

        sanitized_html = markdown_to_html(html)
        self.assertIn(
            'data-evidence-url="https://example.com/report.pdf"',
            sanitized_html,
        )
        self.assertIn("data-evidence-text=", sanitized_html)
        self.assertIn("aria-label=", sanitized_html)

    def test_inline_evidence_uses_modal_payload_and_escapes_user_content(self):
        session = ChatSession(
            custom_hazards=["Custom <hazard>"],
            custom_hazard_evidence_statuses={"custom <hazard>": True},
            custom_hazard_evidence={
                "custom <hazard>": 'Evidence file: report.pdf\nClaim: "unsafe" <script>',
            },
            custom_hazard_summaries={
                "custom <hazard>": "Summary <strong>must not become markup</strong>",
            },
            hazard_profiles={"Custom <hazard>": [{"name": "Workers"}]},
        )

        html = format_custom_hazards(session)

        self.assertIn('data-evidence-url=""', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Summary &lt;strong&gt;must not become markup&lt;/strong&gt;", html)

    def test_saved_custom_hazards_hydrate_evidence_status_from_current_and_legacy_rows(self):
        service = ChatHazardCatalogMixin()
        service.user_id = "user-1"
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        all=lambda: [
                            SimpleNamespace(
                                name="Hazard with evidence",
                                evidence="Evidence URL: https://example.com/report.pdf",
                                summary="A stored summary.",
                            )
                        ]
                    ),
                    SimpleNamespace(
                        all=lambda: [
                            SimpleNamespace(
                                name="Legacy hazard",
                                evidence="Not provided",
                            )
                        ]
                    ),
                ]
            )
        )
        session = ChatSession(
            country_id="country-1",
            sector_id="sector-1",
            region_id="region-1",
        )

        hazards = service._saved_custom_hazards_for_context(session)

        self.assertEqual(hazards, ["Hazard with evidence", "Legacy hazard"])
        self.assertEqual(
            session.custom_hazard_evidence_statuses,
            {
                "hazard with evidence": True,
                "legacy hazard": False,
            },
        )
        self.assertEqual(
            session.custom_hazard_evidence,
            {"hazard with evidence": "Evidence URL: https://example.com/report.pdf"},
        )
        self.assertEqual(
            session.custom_hazard_summaries,
            {"hazard with evidence": "A stored summary."},
        )


if __name__ == "__main__":
    unittest.main()
