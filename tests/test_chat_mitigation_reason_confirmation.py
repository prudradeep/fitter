import asyncio
import unittest

from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.chat_session import ChatSession


class _ReasonConfirmationEngine(ChatMitigationStepsMixin):
    invalid_message = "Invalid"

    def _clear_mitigation_clarity_state(self, session):
        return None

    def _clear_mitigation_validation_state(self, session):
        return None

    def _current_policy_mitigation_measure(self, session):
        return "Current policy-based mitigation"


class _ReasonSelectionEngine(_ReasonConfirmationEngine, ChatSelectionStepsMixin):
    def _available_country_names(self):
        return ["Germany"]

    def _available_region_names(self, session):
        return ["Bavaria"]

    def _available_sector_names(self, session):
        return ["Energy", "Housing", "Transport"]

    def _selection_dependencies_are_valid(self, session, selection, current_phase):
        return True

    async def _apply_pending_selection(self, session_id, session, selection):
        if selection.get("sector"):
            session.sector = selection["sector"]
            session.selected_hazard = None
            session.pending_mitigation_measure = None
            session.phase = "hazards"
            return "selection-applied"
        return None


class ReasonConfirmationOpenConversationTests(unittest.TestCase):
    def test_open_text_maps_to_adopt_suggested_mitigation(self):
        engine = _ReasonConfirmationEngine()

        self.assertEqual(
            engine._reason_confirmation_action_from_open_text("show the proposed mitigation measure"),
            "adopt mitigation proposal suggested above",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text("use the suggested proposal"),
            "adopt mitigation proposal suggested above",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text("write my own"),
            "yes",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text("not now"),
            "no",
        )

    def test_adopt_suggested_mitigation_includes_selected_context(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
            suggested_new_policy_proposal="Targeted heat pump support for vulnerable households",
        )

        response = asyncio.run(
            engine._handle_reason_confirmation(
                "test-session",
                session,
                "show the proposed mitigation measure",
            )
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Targeted heat pump support for vulnerable households")
        self.assertIn("Country:", response.bot_message)
        self.assertIn("Germany", response.bot_message)
        self.assertIn("Region:", response.bot_message)
        self.assertIn("Bavaria", response.bot_message)
        self.assertIn("Sector:", response.bot_message)
        self.assertIn("Energy", response.bot_message)
        self.assertIn("Targeted heat pump support for vulnerable households", response.bot_message)

    def test_adopt_falls_back_to_current_policy_mitigation(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(country="Germany", region="Bavaria", sector="Energy")

        response = asyncio.run(
            engine._handle_reason_confirmation("test-session", session, "adopt it")
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Current policy-based mitigation")

    def test_change_sector_is_not_captured_as_mitigation_measure(self):
        engine = _ReasonSelectionEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._handle_reason_confirmation("test-session", session, "change sector to Housing")
        )

        self.assertEqual(response, "selection-applied")
        self.assertEqual(session.sector, "Housing")
        self.assertIsNone(session.selected_hazard)
        self.assertIsNone(session.pending_mitigation_measure)


if __name__ == "__main__":
    unittest.main()
