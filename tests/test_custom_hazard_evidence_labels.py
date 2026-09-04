import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.chat_formatters import format_custom_hazards
from app.services.chat_hazard_catalog import ChatHazardCatalogMixin
from app.services.chat_session import ChatSession


class CustomHazardEvidenceLabelTests(unittest.TestCase):
    def test_custom_hazard_cards_show_evidence_status_per_hazard(self):
        session = ChatSession(
            custom_hazards=["Hazard with evidence", "Hazard without evidence"],
            custom_hazard_evidence_statuses={
                "hazard with evidence": True,
                "hazard without evidence": False,
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


if __name__ == "__main__":
    unittest.main()
