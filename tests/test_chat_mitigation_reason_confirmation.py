import asyncio
import unittest

from app.schemas import ChatResponse
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_options import REASON_CONFIRMATION_OPTIONS
from app.services.chat_selection_steps import ChatSelectionStepsMixin
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


class _ReasonConfirmationEngine(ChatMitigationStepsMixin):
    invalid_message = "Invalid"

    def _clear_mitigation_clarity_state(self, session):
        return None

    def _clear_mitigation_validation_state(self, session):
        return None

    def _current_policy_mitigation_measure(self, session):
        return "Current policy-based mitigation"

    def _mitigation_measure_examples(self, sector_id):
        return ""


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
    def test_reason_confirmation_has_three_policy_creation_options(self):
        self.assertEqual(
            [option.label for option in REASON_CONFIRMATION_OPTIONS],
            [
                "Modify existing policy",
                "Adopt mitigation proposal suggested above",
                "Create new proposal",
            ],
        )

    def test_reason_confirmation_keeps_other_options_menu(self):
        engine = ChatService.__new__(ChatService)
        session = ChatSession(selected_hazard="A hazard")
        response = ChatResponse(
            session_id="test-session",
            step="reason_confirmation",
            bot_message="Choose an approach",
            options=REASON_CONFIRMATION_OPTIONS,
            other_options=["Go back to list of hazards"],
            session=session.summary(),
        )

        engine._attach_other_options(response, session)

        self.assertIn("Go back to list of hazards", response.other_options)

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
            "create new proposal",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text(
                "The mitigation above dont make sense i want to add a new mitigation"
            ),
            "create new proposal",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text(
                "None of these mitigation measures fit. I want to add one."
            ),
            "create new proposal",
        )
        self.assertEqual(
            engine._reason_confirmation_action_from_open_text("modify existing policy"),
            "modify existing policy",
        )

    def test_open_text_write_new_mitigation_enters_measure_flow(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._handle_reason_confirmation(
                "test-session",
                session,
                "None of these mitigation measures fit. I want to add one.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(session.phase, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)

    def test_adopt_suggested_mitigation_includes_selected_context(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
            suggested_new_policy_proposal="Targeted heat pump support for vulnerable households",
            suggested_new_policy_reason=(
                "It lowers heat exposure and upfront costs for vulnerable households."
            ),
            suggested_new_policy_target_group_mechanisms=(
                "Low-income households receive higher grant coverage; older adults receive priority outreach."
            ),
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
        self.assertEqual(
            session.pending_mitigation_reason,
            "It lowers heat exposure and upfront costs for vulnerable households. "
            "Target-group mechanisms: Low-income households receive higher grant coverage; "
            "older adults receive priority outreach.",
        )
        self.assertIn("Country:", response.bot_message)
        self.assertIn("Germany", response.bot_message)
        self.assertIn("Region:", response.bot_message)
        self.assertIn("Bavaria", response.bot_message)
        self.assertIn("Sector:", response.bot_message)
        self.assertIn("Energy", response.bot_message)
        self.assertIn("Targeted heat pump support for vulnerable households", response.bot_message)
        self.assertIn("Reason:", response.bot_message)
        self.assertIn("lowers heat exposure", response.bot_message)
        self.assertIn("Target-group mechanisms", response.bot_message)
        self.assertIn("older adults", response.bot_message)

    def test_modify_existing_policy_uses_current_policy_mitigation(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills",
            socio_demographic_profiles=["Low-income households"],
            practical_considerations=["Eligibility checks must be simple"],
        )

        response = asyncio.run(
            engine._handle_reason_confirmation(
                "test-session",
                session,
                "Modify existing policy",
            )
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Current policy-based mitigation")
        self.assertIn("Higher electricity bills", session.pending_mitigation_reason)
        self.assertIn("modifies the existing policy", session.pending_mitigation_reason)

    def test_modify_existing_policy_uses_generated_custom_hazard_amendment(self):
        engine = _ReasonConfirmationEngine()
        session = ChatSession(
            selected_hazard="A co-created hazard",
            accepted_custom_hazard="A co-created hazard",
            accepted_custom_hazard_id="custom-1",
            suggested_existing_policy_modification=(
                "Replace the abrupt eligibility cutoff with tapered support."
            ),
        )

        response = asyncio.run(
            engine._handle_reason_confirmation(
                "test-session",
                session,
                "Modify existing policy",
            )
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(
            session.pending_mitigation_measure,
            "Replace the abrupt eligibility cutoff with tapered support.",
        )

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
