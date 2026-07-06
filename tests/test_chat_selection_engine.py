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
        return ["Bavaria", "Berlin"]

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
