import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import ChatResponse
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_options import (
    ADOPT_INSPIRED_MITIGATION,
    INSPIRED_MITIGATION_CONFIRMATION_OPTIONS,
)
from app.services.chat_session import ChatSession, ChatSessionStore
from app.services.chat_service import ChatService


CONCEPTS = {
    "challenge": ["Tenants lack clear information about energy choices."],
    "approach": ["Local advice could help tenants compare suppliers and understand their rights."],
    "stakeholders": ["Municipal offices and tenant groups could deliver the advice."],
}


class _InspiredMeasureEngine(ChatMitigationStepsMixin):
    def _hazard_with_mitigation_factsheet_reference(self, session, **kwargs):
        return "# Important concepts for your new proposal"

    async def _interpret_new_policy_factsheet(self, session):
        return CONCEPTS

    def _mitigation_measure_examples(self, sector_id):
        return ""

    def _clear_mitigation_clarity_state(self, session):
        return None

    def _clear_mitigation_validation_state(self, session):
        return None

    async def _start_mitigation_clarification_step(self, session_id, session, measure, reason):
        session.pending_mitigation_measure = measure
        session.pending_mitigation_reason = reason
        session.phase = "mitigation_clarity"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=measure,
            session=session.summary(),
        )


class AdoptInspiredMitigationTests(unittest.TestCase):
    def _session(self):
        return ChatSession(
            country="Germany",
            region="Berlin",
            sector="Energy",
            selected_context_policy="Clean Energy Access Act",
            selected_context_policy_summary=(
                "Expands renewable power while protecting household affordability."
            ),
            selected_hazard="Rising electricity costs",
            socio_demographic_profiles=["Low-income tenants"],
            additional_dgs=["Older renters"],
            mitigation_proposal_type="new_policy",
        )

    def test_option_appears_with_interpreted_concepts(self):
        engine = _InspiredMeasureEngine()
        session = self._session()

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        self.assertEqual([option.label for option in response.options], [ADOPT_INSPIRED_MITIGATION])
        self.assertEqual(session.new_policy_inspiration, CONCEPTS)

    def test_option_is_not_shown_for_existing_policy(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        self.assertEqual(response.options, [])
        self.assertIsNone(session.new_policy_inspiration)

    def test_click_shows_measure_from_shown_concepts_for_confirmation(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        asyncio.run(engine._create_mitigation_measure_step("session-1", session))
        mocked_llm = AsyncMock(
            return_value=(
                '{"measure":"Create a Berlin tenant energy advice service with municipal '
                'offices and tenant groups.","reason":"Clear supplier and rights guidance '
                'can help tenants respond to rising electricity costs."}'
            )
        )
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(
                engine._capture_mitigation_measure("session-1", session, ADOPT_INSPIRED_MITIGATION)
            )

        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertEqual(session.phase, "inspired_mitigation_confirmation")
        self.assertIn("tenant energy advice service", response.bot_message)
        self.assertEqual(
            [option.label for option in response.options],
            [option.label for option in INSPIRED_MITIGATION_CONFIRMATION_OPTIONS],
        )
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("tenant energy advice service", session.pending_inspired_mitigation_measure)
        self.assertIn("rising electricity costs", session.pending_inspired_mitigation_reason)
        llm_call = mocked_llm.await_args.kwargs
        llm_input = json.loads(llm_call["messages"][0]["content"])
        self.assertEqual(llm_input["country"], "Germany")
        self.assertEqual(llm_input["region"], "Berlin")
        self.assertEqual(llm_input["sector"], "Energy")
        self.assertEqual(llm_input["selected_policy"], "Clean Energy Access Act")
        self.assertIn("renewable power", llm_input["selected_policy_summary"])
        self.assertEqual(llm_input["selected_hazard"], "Rising electricity costs")
        self.assertEqual(llm_input["affected_groups"], ["Low-income tenants"])
        self.assertEqual(llm_input["additional_affected_groups"], ["Older renters"])
        self.assertIn("Local advice could help tenants", llm_input["important_concepts"]["approach"][0])
        self.assertIn("selected twin-transition policy", llm_call["context"])
        self.assertIn("policy's implementation", llm_call["context"])

    def test_yes_continues_with_the_confirmed_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."

        response = asyncio.run(
            engine._handle_inspired_mitigation_confirmation("session-1", session, "Yes")
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Create a tenant energy advice service.")
        self.assertEqual(session.pending_mitigation_reason, "Help tenants compare suppliers.")
        self.assertIsNone(session.pending_inspired_mitigation_measure)

    def test_no_requests_user_written_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        session.new_policy_inspiration = CONCEPTS

        response = asyncio.run(
            engine._handle_inspired_mitigation_confirmation("session-1", session, "No")
        )

        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(response.input_mode, "mitigation_measure")
        self.assertIn("write your own", response.bot_message)
        self.assertEqual(response.options, [])
        self.assertEqual(session.phase, "mitigation_measure")
        self.assertIsNone(session.pending_inspired_mitigation_measure)
        self.assertIsNone(session.new_policy_inspiration)

    def test_confirmation_survives_session_restore(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        restored = ChatSessionStore().put("session-1", vars(session).copy())

        response = engine._inspired_mitigation_confirmation_response("session-1", restored)

        self.assertEqual(restored.phase, "inspired_mitigation_confirmation")
        self.assertIn("Create a tenant energy advice service.", response.bot_message)

        service = ChatService.__new__(ChatService)
        resumed_response = service._repeat_current_options("session-1", restored, "", False)
        self.assertEqual(resumed_response.step, "inspired_mitigation_confirmation")
        self.assertIn("Create a tenant energy advice service.", resumed_response.bot_message)

    def test_chat_dispatch_handles_confirmation_before_text_quality_check(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._validate_text_meaning = AsyncMock(
            side_effect=AssertionError("Confirmation must bypass text quality checks")
        )
        expected = ChatResponse(
            session_id="session-1",
            step="mitigation_clarity",
            bot_message="Confirmed measure",
            session=self._session().summary(),
        )
        service._handle_inspired_mitigation_confirmation = AsyncMock(return_value=expected)
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"

        response = asyncio.run(service._chat_response("session-1", session, "Yes"))

        self.assertIs(response, expected)
        service._handle_inspired_mitigation_confirmation.assert_awaited_once_with(
            "session-1", session, "Yes"
        )

    def test_click_without_concepts_does_not_create_a_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()

        response = asyncio.run(
            engine._capture_mitigation_measure("session-1", session, ADOPT_INSPIRED_MITIGATION)
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)

    def test_chat_dispatch_handles_button_before_text_quality_check(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._validate_text_meaning = AsyncMock(
            side_effect=AssertionError("The option must bypass text quality checks")
        )
        expected = ChatResponse(
            session_id="session-1",
            step="inspired_mitigation_confirmation",
            bot_message="Draft measure",
            session=self._session().summary(),
        )
        service._adopt_inspired_mitigation_response = AsyncMock(return_value=expected)
        session = self._session()
        session.phase = "mitigation_measure"
        session.new_policy_inspiration = CONCEPTS

        response = asyncio.run(
            service._chat_response("session-1", session, ADOPT_INSPIRED_MITIGATION)
        )

        self.assertIs(response, expected)
        service._adopt_inspired_mitigation_response.assert_awaited_once_with(
            "session-1", session
        )


if __name__ == "__main__":
    unittest.main()
