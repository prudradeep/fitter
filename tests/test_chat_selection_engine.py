import unittest

from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.chat_session import ChatSession


class _SelectionEngine(ChatSelectionStepsMixin):
    def _available_country_names(self):
        return ["Germany", "Ireland"]

    def _available_region_names(self, session):
        return ["Bavaria", "Berlin"]

    def _available_sector_names(self, session):
        return ["Energy", "Housing"]


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
