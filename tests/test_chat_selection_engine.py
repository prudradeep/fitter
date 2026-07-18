import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.chat_hazard_steps import ChatHazardStepsMixin
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.chat_session import ChatSession
from app.schemas import Option


class _SelectionEngine(ChatHazardStepsMixin, ChatMitigationStepsMixin, ChatSelectionStepsMixin):
    def _available_country_names(self):
        return ["Germany", "Ireland", "Portugal"]

    def _available_region_names(self, session):
        return ["Bavaria", "Berlin", "Dublin", "Lombardy"]

    def _available_sector_names(self, session):
        return ["Energy", "Housing", "Transport"]

    def _hazard_options(self, session):
        return [
            Option(id=1, label="Heat stress"),
            Option(id=2, label="Energy poverty"),
            Option(id=3, label="Show hazards added by experts"),
        ]


class _AsyncSelectionEngine(_SelectionEngine):
    def __init__(self):
        self.applied_selection = None

    def _is_exact_current_selection(self, session, message):
        return False

    async def _handle_anytime_grounded_question(self, session_id, session, message):
        return None

    def _selection_dependencies_are_valid(self, session, selection, current_phase):
        return True

    async def _apply_pending_selection(self, session_id, session, selection):
        self.applied_selection = selection
        session.country = selection.get("country")
        session.region = selection.get("region")
        session.sector = selection.get("sector")
        return "applied"


class ChatSelectionEngineTests(unittest.TestCase):
    @staticmethod
    def _combined_selection_case_matrix():
        countries = ["Germany", "Ireland", "Portugal"]
        regions = ["Bavaria", "Berlin", "Dublin", "Lombardy"]
        sectors = ["Energy", "Housing", "Transport"]
        cases = []

        full_templates = [
            "country {country} region {region} sector {sector}",
            "Use {country} {region} {sector}",
        ]
        country_sector_templates = [
            "country {country} sector {sector}",
            "Use {country} with {sector}",
            "I want {country} {sector}",
            "Set country to {country} and sector to {sector}",
        ]
        country_region_templates = [
            "country {country} region {region}",
            "Use {country} and {region}",
            "Set country to {country} and region to {region}",
            "I want {country} in {region}",
        ]
        region_sector_templates = [
            "region {region} sector {sector}",
            "Use {region} with {sector}",
            "Set region to {region} and sector to {sector}",
            "I want {sector} in {region}",
        ]

        for country in countries:
            for region in regions:
                for sector in sectors:
                    for template in full_templates:
                        cases.append(
                            (
                                ChatSession(),
                                template.format(country=country, region=region, sector=sector),
                                {"country": country, "region": region, "sector": sector},
                            )
                        )

        for country in countries:
            for sector in sectors:
                for template in country_sector_templates:
                    cases.append(
                        (
                            ChatSession(),
                            template.format(country=country, sector=sector),
                            {"country": country, "region": None, "sector": sector},
                        )
                    )

        for country in countries:
            for region in regions:
                for template in country_region_templates:
                    cases.append(
                        (
                            ChatSession(),
                            template.format(country=country, region=region),
                            {"country": country, "region": region, "sector": None},
                        )
                    )

        for region in regions:
            for sector in sectors:
                for template in region_sector_templates:
                    cases.append(
                        (
                            ChatSession(country="Germany"),
                            template.format(region=region, sector=sector),
                            {"country": None, "region": region, "sector": sector},
                        )
                    )

        return cases[:200]

    def test_200_combined_selection_message_variants(self):
        engine = _SelectionEngine()
        cases = self._combined_selection_case_matrix()

        self.assertEqual(len(cases), 200)
        for index, (session, message, expected) in enumerate(cases, start=1):
            with self.subTest(index=index, message=message):
                self.assertEqual(
                    engine._deterministic_selection_from_text(session, message),
                    expected,
                )

    def test_embedded_exact_country_region_sector_ignores_conversational_filler(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(
            ChatSession(),
            "I want to start with Housing sector in Bavaria Germany",
        )

        self.assertEqual(
            selection,
            {"country": "Germany", "region": "Bavaria", "sector": "Housing"},
        )

    def test_long_bavarian_selection_uses_region_alias(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(
            ChatSession(),
            "For this assessment, I want to focus on the Bavarian housing transition context in Germany.",
        )

        self.assertEqual(
            selection,
            {"country": "Germany", "region": "Bavaria", "sector": "Housing"},
        )

    def test_informational_phrase_with_context_values_is_selection(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(
            ChatSession(country="Germany", region="Bavaria", sector="Energy"),
            "I want to know about the housing sector in Berlin, Germany",
        )

        self.assertEqual(
            selection,
            {"country": "Germany", "region": "Berlin", "sector": "Housing"},
        )

    def test_combined_country_region_sector_selection_phrases(self):
        engine = _SelectionEngine()
        cases = [
            (
                ChatSession(),
                "Use Germany, Bavaria, and Housing",
                {"country": "Germany", "region": "Bavaria", "sector": "Housing"},
            ),
            (
                ChatSession(),
                "Set country to Ireland, region Dublin and sector Transport",
                {"country": "Ireland", "region": "Dublin", "sector": "Transport"},
            ),
            (
                ChatSession(),
                "I want Portugal Energy for Berlin",
                {"country": "Portugal", "region": "Berlin", "sector": "Energy"},
            ),
        ]

        for session, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    engine._deterministic_selection_from_text(session, message),
                    expected,
                )

    def test_combined_country_and_sector_selection_phrases(self):
        engine = _SelectionEngine()
        cases = [
            (
                ChatSession(),
                "Portugal with Energy sector",
                {"country": "Portugal", "region": None, "sector": "Energy"},
            ),
            (
                ChatSession(),
                "Country Germany and sector Transport",
                {"country": "Germany", "region": None, "sector": "Transport"},
            ),
            (
                ChatSession(region="Bavaria"),
                "I will go with Ireland Housing",
                {"country": "Ireland", "region": None, "sector": "Housing"},
            ),
        ]

        for session, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    engine._deterministic_selection_from_text(session, message),
                    expected,
                )

    def test_combined_country_and_region_selection_phrases(self):
        engine = _SelectionEngine()
        cases = [
            (
                ChatSession(),
                "Germany and Bavaria",
                {"country": "Germany", "region": "Bavaria", "sector": None},
            ),
            (
                ChatSession(),
                "Country Ireland region Dublin",
                {"country": "Ireland", "region": "Dublin", "sector": None},
            ),
            (
                ChatSession(sector="Energy"),
                "Set it to Portugal and Berlin",
                {"country": "Portugal", "region": "Berlin", "sector": None},
            ),
        ]

        for session, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    engine._deterministic_selection_from_text(session, message),
                    expected,
                )

    def test_combined_region_and_sector_selection_phrases(self):
        engine = _SelectionEngine()
        cases = [
            (
                ChatSession(country="Germany"),
                "Bavaria Housing",
                {"country": None, "region": "Bavaria", "sector": "Housing"},
            ),
            (
                ChatSession(country="Ireland"),
                "Use Dublin for Transport",
                {"country": None, "region": "Dublin", "sector": "Transport"},
            ),
            (
                ChatSession(country="Italy"),
                "Region Lombardy and sector Energy",
                {"country": None, "region": "Lombardy", "sector": "Energy"},
            ),
        ]

        for session, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    engine._deterministic_selection_from_text(session, message),
                    expected,
                )

    def test_natural_country_question_is_deterministic_selection(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(
            ChatSession(),
            "Can we look at Portugal?",
        )

        self.assertEqual(selection, {"country": "Portugal", "region": None, "sector": None})

    def test_natural_country_phrase_does_not_infer_region(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(
            ChatSession(),
            "I will go with Portugal",
        )

        self.assertEqual(selection, {"country": "Portugal", "region": None, "sector": None})

    def test_natural_region_phrases_are_deterministic_selection(self):
        engine = _SelectionEngine()

        self.assertEqual(
            engine._deterministic_selection_from_text(
                ChatSession(country="Germany"),
                "Use Bavaria as the region",
            ),
            {"country": None, "region": "Bavaria", "sector": None},
        )
        self.assertEqual(
            engine._deterministic_selection_from_text(
                ChatSession(country="Ireland"),
                "Set it to Dublin",
            ),
            {"country": None, "region": "Dublin", "sector": None},
        )
        self.assertEqual(
            engine._deterministic_selection_from_text(
                ChatSession(country="Italy"),
                "The region is Lombardy",
            ),
            {"country": None, "region": "Lombardy", "sector": None},
        )

    def test_unsupported_country_alias_is_detected_in_embedded_text(self):
        self.assertTrue(ChatSelectionStepsMixin._mentions_unsupported_country("Quiero Francia"))
        self.assertTrue(ChatSelectionStepsMixin._mentions_unsupported_country("France Energy"))

    def test_new_full_selection_from_any_step_can_change_existing_selection(self):
        engine = _AsyncSelectionEngine()
        session = ChatSession(country="Germany", region="Berlin", sector="Housing")

        response = asyncio.run(
            engine._open_selection_response_from_any_step(
                "session-1",
                session,
                "I want to start with Transport sector in Bavaria Germany",
                current_phase="sector",
            )
        )

        self.assertEqual(response, "applied")
        self.assertEqual(
            engine.applied_selection,
            {"country": "Germany", "region": "Bavaria", "sector": "Transport"},
        )

    def test_pairwise_selection_from_any_step_applies_without_llm(self):
        engine = _AsyncSelectionEngine()
        session = ChatSession(country="Germany", phase="region")

        response = asyncio.run(
            engine._open_selection_response_from_any_step(
                "session-1",
                session,
                "Use Bavaria and Housing",
                current_phase="region",
            )
        )

        self.assertEqual(response, "applied")
        self.assertEqual(
            engine.applied_selection,
            {"country": None, "region": "Bavaria", "sector": "Housing"},
        )

    def test_sector_synonym_phrase_asks_for_confirmation(self):
        engine = _AsyncSelectionEngine()
        session = ChatSession(country="Germany", region="Bavaria", phase="sector")

        response = asyncio.run(
            engine._maybe_apply_conversational_selection(
                "session-1",
                session,
                "power and electricity",
                current_phase="sector",
            )
        )

        self.assertIsNotNone(response)
        self.assertEqual(response.step, "selection_confirmation")
        self.assertIn("Energy", response.bot_message)
        self.assertEqual(
            session.pending_selection_confirmation,
            {"country": None, "region": None, "sector": "Energy"},
        )
        self.assertIsNone(engine.applied_selection)

    def test_sector_synonym_phrase_confirmation_works_for_each_sector(self):
        engine = _AsyncSelectionEngine()
        cases = [
            ("power and electricity", "Energy"),
            ("buildings and homes", "Housing"),
            ("mobility and public transit", "Transport"),
        ]

        for message, sector in cases:
            with self.subTest(message=message):
                session = ChatSession(country="Germany", region="Bavaria", phase="sector")
                response = asyncio.run(
                    engine._maybe_apply_conversational_selection(
                        "session-1",
                        session,
                        message,
                        current_phase="sector",
                    )
                )

                self.assertIsNotNone(response)
                self.assertEqual(response.step, "selection_confirmation")
                self.assertEqual(session.pending_selection_confirmation["sector"], sector)

    def test_hazard_listing_cache_payload_excludes_custom_hazards(self):
        session = ChatSession(
            hazards=["System hazard"],
            additional_hazards=["Additional hazard"],
            custom_hazards=["Custom hazard"],
            hazard_profiles={
                "System hazard": [{"name": "System profile"}],
                "Additional hazard": [{"name": "Additional profile"}],
                "Custom hazard": [{"name": "Custom profile"}],
            },
            hazard_rankings={
                "System hazard": {"relevance_score": 1.5},
                "Custom hazard": {"relevance_score": 9.9},
            },
        )

        payload = _SelectionEngine._hazard_listing_cache_payload(session)

        self.assertEqual(payload["system_hazards"], ["System hazard"])
        self.assertEqual(payload["additional_hazards"], ["Additional hazard"])
        self.assertIn("System hazard", payload["hazard_profiles"])
        self.assertIn("Additional hazard", payload["hazard_profiles"])
        self.assertNotIn("Custom hazard", payload["hazard_profiles"])
        self.assertEqual(
            payload["hazard_rankings"],
            {"System hazard": {"relevance_score": 1.5}},
        )

    def test_hazard_listing_cache_payload_restores_session(self):
        session = ChatSession()
        payload = {
            "system_hazards": ["System hazard"],
            "additional_hazards": ["Additional hazard"],
            "hazard_profiles": {
                "System hazard": [{"name": "System profile"}],
                "Additional hazard": [{"name": "Additional profile"}],
            },
            "hazard_rankings": {"System hazard": {"relevance_score": 1.5}},
        }

        restored = _SelectionEngine._apply_hazard_listing_cache_payload(session, payload)

        self.assertTrue(restored)
        self.assertEqual(session.hazards, ["System hazard"])
        self.assertEqual(session.additional_hazards, ["Additional hazard"])
        self.assertEqual(
            session.hazard_profiles["Additional hazard"],
            [{"name": "Additional profile"}],
        )
        self.assertEqual(
            session.hazard_rankings,
            {"System hazard": {"relevance_score": 1.5}},
        )

    def test_country_ordinal_references_current_options(self):
        engine = _SelectionEngine()

        self.assertEqual(
            engine._ordinal_selection_from_text(ChatSession(), "the last one", "country"),
            {"country": "Portugal", "region": None, "sector": None},
        )
        self.assertEqual(
            engine._ordinal_selection_from_text(ChatSession(), "2nd one", "country"),
            {"country": "Ireland", "region": None, "sector": None},
        )
        self.assertEqual(
            engine._ordinal_selection_from_text(ChatSession(), "2nd last", "country"),
            {"country": "Ireland", "region": None, "sector": None},
        )

    def test_region_ordinal_references_current_options(self):
        engine = _SelectionEngine()

        self.assertEqual(
            engine._ordinal_selection_from_text(ChatSession(country="Germany"), "second one", "region"),
            {"country": None, "region": "Berlin", "sector": None},
        )

    def test_post_sector_open_text_maps_to_main_options_only(self):
        engine = _SelectionEngine()

        self.assertEqual(engine._post_sector_label_from_open_text("next step"), "Start Mitigation Planning")
        self.assertEqual(engine._post_sector_label_from_open_text("Create mitigation"), "Start Mitigation Planning")
        self.assertEqual(
            engine._post_sector_label_from_open_text("Create mitigation measure"),
            "Start Mitigation Planning",
        )
        self.assertEqual(engine._post_sector_label_from_open_text("second one"), "Add a new Hazard")
        self.assertEqual(engine._post_sector_label_from_open_text("Create a new hazard"), "Add a new Hazard")
        self.assertEqual(
            engine._post_sector_label_from_open_text("I want to create a new hazard"),
            "Add a new Hazard",
        )
        self.assertEqual(engine._post_sector_label_from_open_text("last one"), "Refresh hazards and DGs")
        self.assertEqual(engine._post_sector_label_from_open_text("Update hazards list"), "Refresh hazards and DGs")
        self.assertIsNone(engine._post_sector_label_from_open_text("Other Options"))

    def test_socio_demographic_open_text_maps_to_mitigation_actions(self):
        engine = _SelectionEngine()

        self.assertEqual(
            engine._socio_demographic_label_from_open_text("Create mitigation"),
            "Create Mitigation Measure",
        )
        self.assertEqual(
            engine._socio_demographic_label_from_open_text("Please create a mitigation measure"),
            "Create Mitigation Measure",
        )
        self.assertEqual(
            engine._socio_demographic_label_from_open_text("add more DGs"),
            "Add more DGs",
        )

    def test_open_hazard_selection_from_text(self):
        engine = _SelectionEngine()
        session = ChatSession(country="Germany", region="Bavaria", sector="Energy")

        self.assertEqual(
            engine._open_hazard_selection_from_text(session, "I want to mitigate heat stress"),
            "Heat stress",
        )
        self.assertEqual(
            engine._open_hazard_selection_from_text(session, "focus on Energy poverty"),
            "Energy poverty",
        )
        self.assertEqual(
            engine._open_hazard_selection_from_text(session, "second one"),
            "Energy poverty",
        )

    def test_open_navigation_actions_from_post_sector_context(self):
        engine = _SelectionEngine()
        session = ChatSession(country="Germany", region="Bavaria", sector="Energy")

        self.assertEqual(
            engine._selection_action_from_open_text(session, "go back", "sector"),
            "change_sector",
        )
        self.assertEqual(
            engine._selection_action_from_open_text(session, "select another region", "sector"),
            "change_region",
        )
        self.assertEqual(
            engine._selection_action_from_open_text(session, "start over", "sector"),
            "restart_selection",
        )
        self.assertEqual(
            engine._selection_action_from_open_text(session, "restart from the beginning", "sector"),
            "restart_selection",
        )
        self.assertEqual(
            engine._selection_action_from_open_text(session, "start again", "sector"),
            "restart_selection",
        )

    def test_already_selected_country_mentions_full_context_after_sector(self):
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Energy",
            phase="hazards",
        )

        message = ChatSelectionStepsMixin._already_selected_message(session, "Italy", "", "")

        self.assertIn("Italy, Abruzzo, and Energy are already selected.", message)
        self.assertIn("already reviewing hazards", message)
        self.assertNotIn("Please choose a region", message)

    def test_already_selected_country_still_asks_for_region_when_region_missing(self):
        session = ChatSession(country="Italy")

        message = ChatSelectionStepsMixin._already_selected_message(session, "Italy", "", "")

        self.assertEqual(message, "Italy is already selected. Please choose a region.")

    def test_already_selected_country_and_region_asks_for_sector_when_sector_missing(self):
        session = ChatSession(country="Italy", region="Abruzzo")

        message = ChatSelectionStepsMixin._already_selected_message(session, "Italy", "", "")

        self.assertEqual(message, "Italy and Abruzzo are already selected. Please choose a sector.")

    def test_exact_sector_before_country_is_outside_current_phase(self):
        engine = _SelectionEngine()
        selection = engine._deterministic_selection_from_text(ChatSession(), "Housing")

        self.assertTrue(
            engine._selection_is_outside_current_phase(
                ChatSession(),
                selection,
                "country",
            )
        )

    def test_llm_resolved_valid_selection_applies_without_confirmation(self):
        engine = _AsyncSelectionEngine()
        session = ChatSession()

        with (
            patch(
                "app.services.chat_selection_steps.detect_message_intent",
                new=AsyncMock(
                    return_value={
                        "intent": "selection",
                        "confidence": "high",
                        "reason": "User selected options.",
                    }
                ),
            ),
            patch(
                "app.services.chat_selection_steps.resolve_selection",
                new=AsyncMock(
                    return_value={
                        "matched": True,
                        "country": "Germany",
                        "region": "Bavaria",
                        "sector": "Energy",
                        "confidence": "high",
                        "reason": "All options matched.",
                    }
                ),
            ),
        ):
            response = asyncio.run(
                engine._maybe_apply_conversational_selection(
                    "session-1",
                    session,
                    "I'll go with the Energy sector in Bavaria Germany",
                    "country",
                )
            )

        self.assertEqual(response, "applied")
        self.assertEqual(
            engine.applied_selection,
            {"country": "Germany", "region": "Bavaria", "sector": "Energy"},
        )
        self.assertIsNone(session.pending_selection_confirmation)

    def test_question_form_selection_applies_when_resolver_matches_option(self):
        engine = _AsyncSelectionEngine()
        session = ChatSession()

        with (
            patch(
                "app.services.chat_selection_steps.detect_message_intent",
                new=AsyncMock(
                    return_value={
                        "intent": "question",
                        "confidence": "high",
                        "reason": "Question-shaped selection.",
                    }
                ),
            ),
            patch(
                "app.services.chat_selection_steps.resolve_selection",
                new=AsyncMock(
                    return_value={
                        "matched": True,
                        "country": "Portugal",
                        "region": None,
                        "sector": None,
                        "confidence": "high",
                        "reason": "Country matched.",
                    }
                ),
            ),
        ):
            response = asyncio.run(
                engine._maybe_apply_conversational_selection(
                    "session-1",
                    session,
                    "Can we look at Portugal?",
                    "country",
                )
            )

        self.assertEqual(response, "applied")
        self.assertEqual(engine.applied_selection["country"], "Portugal")
