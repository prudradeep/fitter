import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

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
            CUSTOM_HAZARD_STATES[
                ChatPhase.CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION
            ].handler,
            CustomHazardHandler.CONFIRM_POLICY_DETAILS,
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
                    "Shift to Sustainable Mobility",
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

    def test_confirmed_suggestion_without_kb_policy_requests_policy_not_mechanism(self) -> None:
        service = ChatService.__new__(ChatService)
        service._shared_knowledge_results = AsyncMock(return_value=[])
        service.grounding_models = Mock()
        service.grounding_models.ground_results = AsyncMock(return_value=[])
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "resolved_hazard_text": "Higher charging costs",
                "selected_mechanism": "Charging mandates increase infrastructure costs",
            },
        )

        response = asyncio.run(
            service._handle_custom_hazard_mechanism_confirmation(
                "session-1", session, "Yes"
            )
        )

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertIn("supporting policy", response.bot_message)

    def test_rejected_suggestion_requests_clear_user_mechanism(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "selected_mechanism": "Charging mandates increase infrastructure costs",
            },
        )

        response = asyncio.run(
            service._handle_custom_hazard_mechanism_confirmation(
                "session-1", session, "No, provide a mechanism"
            )
        )

        self.assertEqual(response.step, "custom_hazard_mechanism_input")
        self.assertEqual(response.input_mode, "textarea")
        self.assertIn("specific process", response.bot_message)

    def test_no_suggested_mechanism_informs_user_and_requests_theirs(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
            sector="Transport",
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "resolved_hazard_text": "Higher charging costs",
            },
        )

        with patch(
            "app.services.chat_custom_hazard_mechanism.suggest_custom_hazard_mechanisms",
            AsyncMock(return_value=[]),
        ):
            response = asyncio.run(
                service._custom_hazard_mechanism_suggestion_step(
                    "session-1", session
                )
            )

        self.assertEqual(response.step, "custom_hazard_mechanism_input")
        self.assertIn("could not identify", response.bot_message)

    def test_kb_policy_details_are_shown_for_confirmation(self) -> None:
        service = ChatService.__new__(ChatService)
        result = {
            "title": "Charging Infrastructure Regulation",
            "source_uri": "charging-policy.pdf",
            "page_number": 12,
            "content": "Operators must deploy additional charging infrastructure.",
        }
        service._shared_knowledge_results = AsyncMock(return_value=[result])
        service.grounding_models = Mock()
        service.grounding_models.ground_results = AsyncMock(return_value=[result])
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "resolved_hazard_text": "Higher charging costs",
                "selected_mechanism": "Infrastructure costs are passed to drivers",
            },
        )
        policy = {
            "supported": True,
            "summary": "The regulation requires expanded charging infrastructure.",
            "policy_details": "Operators must add charging capacity.",
            "reason": "The mandate creates deployment costs.",
            "causal_linkage": "capacity mandate -> deployment costs -> higher charging costs",
        }

        with patch(
            "app.services.chat_custom_hazard_mechanism.summarize_custom_hazard_supporting_policy",
            AsyncMock(return_value=policy),
        ):
            response = asyncio.run(
                service._handle_custom_hazard_mechanism_confirmation(
                    "session-1", session, "Yes"
                )
            )

        self.assertEqual(response.step, "custom_hazard_policy_details_confirmation")
        self.assertIn("As I understand it", response.bot_message)
        self.assertIn("Operators must add charging capacity", response.bot_message)
        self.assertEqual(
            [option.label for option in response.options],
            ["Confirm policy", "Provide a different policy"],
        )

        linkage_response = asyncio.run(
            service._handle_custom_hazard_policy_details_confirmation(
                "session-1", session, "Confirm policy"
            )
        )
        self.assertEqual(
            linkage_response.step, "custom_hazard_causal_linkage_confirmation"
        )
        self.assertIn("capacity mandate", linkage_response.bot_message)

    def test_irrelevant_supplied_policy_offers_clarify_or_replace(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(
            return_value="The document contains general transport aspirations."
        )
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "resolved_hazard_text": "Higher charging costs",
                "selected_mechanism": "Infrastructure costs are passed to drivers",
                "policy_reference_document_ids": ["policy-1"],
                "policy_reference_available": True,
            },
        )

        with patch(
            "app.services.chat_custom_hazard_mechanism.summarize_custom_hazard_supporting_policy",
            AsyncMock(
                return_value={
                    "supported": False,
                    "summary": "",
                    "policy_details": "",
                    "reason": "No concrete charging provision was found.",
                    "causal_linkage": "",
                }
            ),
        ):
            response = asyncio.run(
                service._validate_current_custom_hazard_mechanism_source(
                    "session-1", session
                )
            )

        self.assertTrue(response.error)
        self.assertIn("No concrete charging provision", response.bot_message)
        self.assertEqual(
            [option.label for option in response.options],
            [
                "Go back to list of hazards",
                "Clarify the relevance",
                "Provide policy again",
                "Revise mechanism",
            ],
        )

    def test_relevance_clarification_is_checked_and_acknowledged(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Higher charging costs",
                "resolved_hazard_text": "Higher charging costs",
                "selected_mechanism": "Infrastructure costs are passed to drivers",
                "policy_reference_context": "Article 4 requires new charging capacity.",
                "awaiting_policy_reference": True,
            },
        )

        clarify = asyncio.run(
            service._handle_custom_hazard_clarification(
                "session-1", session, "Clarify the relevance"
            )
        )
        self.assertEqual(
            clarify.step, "custom_hazard_policy_relevance_clarification"
        )
        self.assertEqual(clarify.input_mode, "textarea")

        policy = {
            "supported": True,
            "summary": "Article 4 requires expanded charging capacity.",
            "policy_details": "Article 4 places the expansion duty on operators.",
            "reason": "The duty causes deployment costs.",
            "causal_linkage": "Article 4 duty -> deployment costs -> higher prices",
        }
        with patch(
            "app.services.chat_custom_hazard_input.summarize_custom_hazard_supporting_policy",
            AsyncMock(return_value=policy),
        ):
            response = asyncio.run(
                service._handle_custom_hazard_clarification(
                    "session-1",
                    session,
                    "Article 4 creates the infrastructure duty that drives these costs.",
                )
            )

        self.assertEqual(
            response.step, "custom_hazard_causal_linkage_confirmation"
        )
        self.assertIn("Supporting policy accepted", response.bot_message)
        self.assertIn("Article 4 requires expanded", response.bot_message)


if __name__ == "__main__":
    unittest.main()
