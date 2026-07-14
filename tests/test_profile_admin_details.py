import unittest

from app.services.chat_formatters import format_hazards
from app.services.chat_persistence import ChatPersistenceMixin
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


PROFILE = {
    "name": "Low-income households",
    "explanation": "More exposed to energy affordability risks.",
    "statistical_basis": "PREDICTOR 1A: income; Plain-English: lower income increases concern",
    "target_population_labels": ["Income group: Low income"],
    "population_lookup_labels": ["Eurostat income quintile 1"],
    "regional_population_pct": 29.2,
    "national_population_pct": 15.8,
}


class ProfileAdminDetailsTests(unittest.TestCase):
    def test_hazard_profile_markdown_hides_admin_details_for_non_admin(self):
        service = ChatService.__new__(ChatService)
        service.is_admin = False

        html = service._format_hazard_profiles_markdown("Energy poverty", [PROFILE])

        self.assertIn("Low-income households", html)
        self.assertIn("More exposed to energy affordability risks.", html)
        self.assertNotIn("Reference:", html)
        self.assertNotIn("Plain-English:", html)
        self.assertNotIn("Mapped target population:", html)
        self.assertNotIn("Eurostat population lookup:", html)

    def test_hazard_profile_markdown_shows_admin_details_for_admin(self):
        service = ChatService.__new__(ChatService)
        service.is_admin = True

        html = service._format_hazard_profiles_markdown("Energy poverty", [PROFILE])

        self.assertIn("Reference:", html)
        self.assertIn("Plain-English:", html)
        self.assertIn("Mapped target population:", html)
        self.assertIn("Eurostat population lookup:", html)

    def test_hazard_overview_formatter_hides_admin_details_by_default(self):
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Energy",
            hazards=["Energy poverty"],
            hazard_profiles={"Energy poverty": [PROFILE]},
        )

        html = format_hazards(session)

        self.assertIn("Low-income households", html)
        self.assertNotIn("Reference:", html)
        self.assertNotIn("Mapped target population:", html)
        self.assertNotIn("Eurostat population lookup:", html)

    def test_persisted_message_display_strips_admin_details_for_non_admin(self):
        service = ChatService.__new__(ChatService)
        service.is_admin = False
        content = (
            '<small>More exposed.<br>Reference: PREDICTOR 1A; '
            'Plain-English: lower income increases concern<br>'
            'Mapped target population: Income group: Low income<br>'
            'Eurostat population lookup: Eurostat income quintile 1</small>'
        )

        sanitized = ChatPersistenceMixin._chat_message_display_content(service, content)

        self.assertIn("More exposed.", sanitized)
        self.assertNotIn("Reference:", sanitized)
        self.assertNotIn("Plain-English:", sanitized)
        self.assertNotIn("Mapped target population:", sanitized)
        self.assertNotIn("Eurostat population lookup:", sanitized)


if __name__ == "__main__":
    unittest.main()
