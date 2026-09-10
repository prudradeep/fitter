import unittest

from app.services.chat_formatters import format_hazards
from app.services.chat_persistence import ChatPersistenceMixin
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.message_renderer import render_message


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
        self.assertIn("Proposed Eurostat dataset: edat_lfse_22 (mocked)", html)
        self.assertIn("Proposed indicator label: Mock indicator for Low-income households", html)
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
        self.assertLess(html.index("Proposed Eurostat dataset:"), html.index("Reference:"))

    def test_hazard_profile_uses_proposed_indicator_metadata_when_available(self):
        service = ChatService.__new__(ChatService)
        service.is_admin = False
        profile = {
            **PROFILE,
            "metadata": {
                "indicator_mapping": {
                    "proposed_eurostat_dataset": "ilc_li41",
                    "proposed_indicator_label": "At-risk-of-poverty rate by NUTS 2 region",
                }
            },
        }

        html = service._format_hazard_profiles_markdown("Energy poverty", [profile])

        self.assertIn("Proposed Eurostat dataset: ilc_li41", html)
        self.assertIn(
            "Proposed indicator label: At-risk-of-poverty rate by NUTS 2 region",
            html,
        )

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
        self.assertIn("Proposed Eurostat dataset: edat_lfse_22 (mocked)", html)
        self.assertIn("Proposed indicator label: Mock indicator for Low-income households", html)
        self.assertNotIn("Reference:", html)
        self.assertNotIn("Mapped target population:", html)
        self.assertNotIn("Eurostat population lookup:", html)

    def test_hazard_overview_marks_each_category_for_distinct_styling(self):
        session = ChatSession(
            hazards=["Top 1", "Top 2", "Top 3", "Other"],
            custom_hazards=["Co-created"],
            additional_hazards=["Additional"],
            hazard_profiles={
                hazard: [PROFILE]
                for hazard in ["Top 1", "Top 2", "Top 3", "Other", "Co-created", "Additional"]
            },
        )

        html = format_hazards(session)

        self.assertIn("hazard-group-heading--top", html)
        self.assertIn("hazard-group-heading--other", html)
        self.assertIn("hazard-group-heading--co-created", html)
        self.assertIn("hazard-group-heading--additional", html)
        self.assertEqual(html.count('class="hazard-group-divider" role="separator"'), 3)
        self.assertIn('class="additional-hazards-source-label"', html)
        self.assertIn("hazard-group-intro--co-created", html)
        self.assertIn("created by platform users", html)
        self.assertIn("hazard-group-intro--additional", html)
        self.assertIn("identified by policy and subject-matter experts", html)

    def test_ranked_hazard_keeps_metric_data_ctas_after_sanitizing(self):
        session = ChatSession(
            sector="Transport",
            hazards=["More pollution exposure"],
            hazard_profiles={"More pollution exposure": [PROFILE]},
            hazard_rankings={
                "More pollution exposure": {
                    "hazard_slug": "more_pollution_exposure",
                    "relevance_score": 4.2,
                    "salience_score": 3.1,
                    "effect_size_score": 0.8,
                    "reach_score": 0.3,
                }
            },
        )

        message = render_message(
            "hazards_overview.md",
            sector="Transport",
            region="Calabria",
            hazards=format_hazards(session),
        )

        self.assertEqual(message.count('class="metric-data-cta"'), 2)
        self.assertIn('data-metric="salience"', message)
        self.assertIn('data-metric="effect_size"', message)
        self.assertIn('data-source-key="more_pollution_exposure"', message)
        self.assertIn('data-hazard-name="More pollution exposure"', message)

    def test_hazard_overview_shows_sector_survey_respondent_count(self):
        message = render_message(
            "hazards_overview.md",
            sector="Energy",
            region="Bavaria",
            survey_count=407,
            hazards="",
        )

        self.assertIn("Number of people responded in the survey: <strong>407</strong>.", message)

    def test_hazard_intro_note_styles_survive_message_sanitizing(self):
        session = ChatSession(
            custom_hazards=["Co-created"],
            additional_hazards=["Additional"],
            hazard_profiles={"Co-created": [PROFILE], "Additional": [PROFILE]},
        )

        message = render_message(
            "hazards_overview.md",
            sector="Transport",
            region="Calabria",
            hazards=format_hazards(session),
        )

        self.assertIn('class="hazard-group-intro hazard-group-intro--co-created"', message)
        self.assertIn('class="hazard-group-intro hazard-group-intro--additional"', message)
        self.assertEqual(message.count('class="hazard-group-divider" role="separator"'), 3)

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
