import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import ChatResponse
from app.services import custom_hazard_validation as validator
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.custom_hazard_state_machine import CUSTOM_HAZARD_STATES, CustomHazardHandler
from app.services.enums import ChatPhase


async def _json_response(*args, **kwargs):
    return json.dumps(
        {
            "mechanisms": ["Charging mandates increase infrastructure costs passed to users"],
            "relevant": True,
            "supported": True,
            "clear": True,
            "reason": "Supported by the supplied source.",
            "causal_linkage": "policy requirement -> higher delivery costs -> higher user prices",
        }
    )


class CustomHazardMechanismFitTests(unittest.TestCase):
    def test_mechanism_fit_replaces_policy_fit_in_new_state(self) -> None:
        state = validator.default_custom_hazard_state()
        self.assertIn("mechanism_fit", validator.DIMENSION_SEQUENCE)
        self.assertNotIn("twin_transition_policy_fit", validator.DIMENSION_SEQUENCE)
        self.assertEqual(state["selected_mechanism"], "")
        self.assertFalse(state["causal_linkage_confirmed"])

    def test_new_conversation_states_are_registered(self) -> None:
        self.assertEqual(
            CUSTOM_HAZARD_STATES[ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION].handler,
            CustomHazardHandler.CONFIRM_MECHANISM,
        )
        self.assertEqual(
            CUSTOM_HAZARD_STATES[ChatPhase.CUSTOM_HAZARD_MECHANISM_INPUT].handler,
            CustomHazardHandler.CAPTURE_MECHANISM,
        )
        self.assertEqual(
            CUSTOM_HAZARD_STATES[ChatPhase.CUSTOM_HAZARD_CAUSAL_LINKAGE_CONFIRMATION].handler,
            CustomHazardHandler.CONFIRM_CAUSAL_LINKAGE,
        )

    def test_suggestion_returns_concise_candidates(self) -> None:
        with patch.object(validator, "ask_llm_chat", _json_response):
            result = asyncio.run(
                validator.suggest_custom_hazard_mechanisms(
                    "Rural drivers face higher charging prices",
                    "Transport",
                    "Transition to electric vehicles",
                )
            )
        self.assertEqual(len(result), 1)
        self.assertIn("Charging mandates", result[0])

    def test_evidence_and_source_linkage_have_separate_gates(self) -> None:
        with patch.object(validator, "ask_llm_chat", _json_response):
            evidence = asyncio.run(
                validator.validate_hazard_evidence_relevance(
                    "Higher charging prices",
                    "The report documents higher charging prices.",
                )
            )
            linkage = asyncio.run(
                validator.validate_custom_hazard_mechanism_linkage(
                    "Higher charging prices",
                    "Infrastructure costs are passed to users",
                    "The policy requires new charging infrastructure.",
                    "policy",
                )
            )
        self.assertTrue(evidence["relevant"])
        self.assertTrue(linkage["supported"])
        self.assertIn("->", linkage["causal_linkage"])

    def test_old_dimension_state_is_migrated(self) -> None:
        merged = validator._merged_state(
            {
                "dimension_scores": {
                    "twin_transition_policy_fit": {
                        "score": 8,
                        "needs_clarification": False,
                    }
                }
            }
        )
        self.assertIn("mechanism_fit", merged["dimension_scores"])
        self.assertNotIn("twin_transition_policy_fit", merged["dimension_scores"])

    def test_confirmed_linkage_resumes_dimension_flow_before_population(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_CAUSAL_LINKAGE_CONFIRMATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "selected_mechanism": "Infrastructure costs are passed to drivers",
                "mechanism_causal_linkage": (
                    "charging requirement -> infrastructure costs -> higher charging costs"
                ),
                "reason": "The hazard fits the transport objective.",
            },
        )
        expected = ChatResponse(
            session_id="session-1",
            step="custom_hazard_dimension_check",
            bot_message="continued",
            session=session.summary(),
        )
        service._run_custom_hazard_dimension_check = AsyncMock(return_value=expected)

        response = asyncio.run(
            service._handle_custom_hazard_causal_linkage_confirmation(
                "session-1", session, "Yes"
            )
        )

        self.assertIs(response, expected)
        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
        self.assertTrue(session.custom_hazard["causal_linkage_confirmed"])
        self.assertGreaterEqual(
            session.custom_hazard["dimension_scores"]["mechanism_fit"]["score"], 8
        )


if __name__ == "__main__":
    unittest.main()
