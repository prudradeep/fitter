import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.schemas import ChatResponse
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import default_custom_hazard_state


def _run(coro):
    return asyncio.run(coro)


def _session(phase: str = "custom_hazard_dimension_check") -> ChatSession:
    state = default_custom_hazard_state()
    state.update(
        {
            "raw_text": "Coal phase-out employment shock",
            "resolved_hazard_text": "Coal phase-out employment shock",
            "reason": "Mine closures can cause concentrated local job losses.",
        }
    )
    return ChatSession(
        sector="Energy",
        country="Germany",
        region="Saxony",
        phase=phase,
        pending_hazard="Coal phase-out employment shock",
        pending_hazard_reason="Mine closures can cause concentrated local job losses.",
        custom_hazard=state,
    )


def _service() -> ChatService:
    service = ChatService.__new__(ChatService)
    service.grounding_models = Mock()
    service.grounding_models.ground_results = AsyncMock()
    return service


class CustomHazardEvidenceReflectionTests(unittest.TestCase):
    def test_kb_evidence_is_reflected_before_user_evidence_question(self):
        service = _service()
        session = _session()
        result = {
            "title": "Transition report",
            "source_uri": "report.pdf",
            "page_number": 7,
            "content": "Mine closure caused concentrated local employment losses.",
        }
        service._shared_knowledge_results = AsyncMock(return_value=[result])
        service.grounding_models.ground_results.return_value = [result]

        with patch(
            "app.services.chat_custom_hazard_evidence.reflect_on_custom_hazard_kb_evidence",
            AsyncMock(
                return_value={
                    "supported": True,
                    "reflection": "The evidence indicates a localized employment shock.",
                    "relationship": "Mine closure -> job losses -> employment shock.",
                    "reason": "Direct support.",
                }
            ),
        ):
            response = _run(service._start_hazard_evidence_flow("session-1", session))

        self.assertEqual(response.step, "custom_hazard_evidence_reflection_confirmation")
        self.assertEqual([option.label for option in response.options], ["Agree", "Disagree"])
        self.assertIn("AI reflection", response.bot_message)
        self.assertEqual(session.phase, "custom_hazard_evidence_reflection_confirmation")

    def test_no_kb_support_asks_if_user_has_evidence(self):
        service = _service()
        session = _session()
        service._shared_knowledge_results = AsyncMock(return_value=[])
        service.grounding_models.ground_results.return_value = []

        with patch(
            "app.services.chat_custom_hazard_evidence.reflect_on_custom_hazard_kb_evidence",
            AsyncMock(return_value={"supported": False}),
        ):
            response = _run(service._start_hazard_evidence_flow("session-1", session))

        self.assertEqual(response.step, "custom_hazard_evidence_decision")
        self.assertEqual([option.label for option in response.options], ["Yes", "No"])
        self.assertIn("Do you have evidence for this hazard", response.bot_message)

    def test_no_user_evidence_continues_to_mechanism_fit(self):
        service = _service()
        session = _session("add_hazard_evidence_decision")
        session.custom_hazard.update(
            {
                "evidence_decision_asked": True,
                "dimension_scores": {
                    "policy_objective_fit": {
                        "score": 8,
                        "status": "SUPPORTED",
                        "needs_clarification": False,
                    }
                },
            }
        )
        expected = ChatResponse(
            session_id="session-1",
            step="custom_hazard_mechanism_confirmation",
            bot_message="mechanism",
            session=session.summary(),
        )
        service._custom_hazard_mechanism_suggestion_step = AsyncMock(return_value=expected)

        response = _run(
            service._handle_hazard_evidence_decision("session-1", session, "No")
        )

        self.assertIs(response, expected)
        service._custom_hazard_mechanism_suggestion_step.assert_awaited_once_with(
            "session-1", session
        )

    def test_agree_accepts_kb_reflection_and_routes_to_mechanism(self):
        service = _service()
        session = _session("custom_hazard_evidence_reflection_confirmation")
        session.custom_hazard.update(
            {
                "evidence_reflection": "The evidence indicates a localized employment shock.",
                "evidence_relationship": "Mine closure -> job losses -> employment shock.",
            }
        )
        expected = ChatResponse(
            session_id="session-1",
            step="custom_hazard_mechanism_confirmation",
            bot_message="mechanism",
            session=session.summary(),
        )
        service._route_custom_hazard_next_action = AsyncMock(return_value=expected)

        response = _run(
            service._handle_hazard_evidence_reflection_confirmation(
                "session-1", session, "Agree"
            )
        )

        self.assertIs(response, expected)
        self.assertTrue(session.custom_hazard["evidence_reflection_confirmed"])
        self.assertTrue(session.custom_hazard["evidence_relevant"])
        self.assertIn("Knowledge-base evidence", session.custom_hazard["evidence"])

    def test_disagree_requests_users_own_reflection(self):
        service = _service()
        session = _session("custom_hazard_evidence_reflection_confirmation")

        response = _run(
            service._handle_hazard_evidence_reflection_confirmation(
                "session-1", session, "Disagree"
            )
        )

        self.assertEqual(response.step, "custom_hazard_evidence_reflection_input")
        self.assertEqual(response.input_mode, "textarea")
        self.assertIn("Add your reflection", response.bot_message)

    def test_supported_user_reflection_is_acknowledged_and_accepted(self):
        service = _service()
        session = _session("custom_hazard_evidence_reflection_input")
        session.custom_hazard["evidence_kb_context"] = "[S1] Report: Local job losses followed closure."
        expected = ChatResponse(
            session_id="session-1",
            step="custom_hazard_mechanism_confirmation",
            bot_message="mechanism",
            session=session.summary(),
        )
        service._route_custom_hazard_next_action = AsyncMock(return_value=expected)

        with patch(
            "app.services.chat_custom_hazard_evidence.validate_custom_hazard_evidence_reflection",
            AsyncMock(
                return_value={
                    "supported": True,
                    "acknowledgement": "Your interpretation is supported.",
                    "relationship": "Closure -> job losses -> employment shock.",
                    "reason": "",
                }
            ),
        ):
            response = _run(
                service._capture_hazard_evidence_reflection(
                    "session-1", session, "The shock is concentrated near mine closures."
                )
            )

        self.assertIs(response, expected)
        self.assertIn("Your interpretation is supported", session.custom_hazard["evidence_relationship_notice"])
        self.assertEqual(
            session.custom_hazard["evidence_user_reflection"],
            "The shock is concentrated near mine closures.",
        )

    def test_unsupported_user_reflection_requests_user_evidence(self):
        service = _service()
        session = _session("custom_hazard_evidence_reflection_input")
        session.custom_hazard["evidence_kb_context"] = "[S1] Report: General transition context."

        with patch(
            "app.services.chat_custom_hazard_evidence.validate_custom_hazard_evidence_reflection",
            AsyncMock(
                return_value={
                    "supported": False,
                    "reason": "The source does not support the claimed regional effect.",
                }
            ),
        ):
            response = _run(
                service._capture_hazard_evidence_reflection(
                    "session-1", session, "The effect is unique to Saxony."
                )
            )

        self.assertEqual(response.step, "custom_hazard_evidence")
        self.assertEqual(response.input_mode, "evidence_only")
        self.assertIn("does not support", response.bot_message)
        self.assertEqual(
            session.custom_hazard["evidence_user_reflection"],
            "The effect is unique to Saxony.",
        )

    def test_user_evidence_is_validated_against_hazard_and_prior_reflection(self):
        service = _service()
        session = _session("add_hazard_evidence_input")
        session.custom_hazard["evidence_user_reflection"] = (
            "The shock is concentrated near mine closures."
        )
        service._user_evidence_context_for_contradiction_check = AsyncMock(
            return_value="Readable evidence"
        )
        service._route_custom_hazard_next_action = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_mechanism_confirmation",
                bot_message="mechanism",
                session=session.summary(),
            )
        )

        with patch(
            "app.services.chat_custom_hazard_evidence.validate_hazard_evidence_relevance",
            AsyncMock(
                return_value={
                    "relevant": True,
                    "reason": "The report supports the claim.",
                    "causal_linkage": "Closure -> local job losses -> shock.",
                }
            ),
        ) as relevance:
            _run(service._validate_staged_custom_hazard("session-1", session, "Evidence file"))

        validation_target = relevance.await_args.args[0]
        self.assertIn("Coal phase-out employment shock", validation_target)
        self.assertIn("The shock is concentrated near mine closures", validation_target)
        linkage = session.custom_hazard["linkage_analysis"]["evidence_hazard_linkage"]
        self.assertIn("supports your reflection", linkage["reason"])

    def test_unclear_user_evidence_explains_mismatch_and_offers_both_retries(self):
        service = _service()
        session = _session("add_hazard_evidence_input")
        service._user_evidence_context_for_contradiction_check = AsyncMock(
            return_value="Readable but unrelated evidence"
        )

        with patch(
            "app.services.chat_custom_hazard_evidence.validate_hazard_evidence_relevance",
            AsyncMock(
                return_value={
                    "relevant": False,
                    "reason": "The source discusses employment but not mine closures.",
                    "causal_linkage": "",
                }
            ),
        ):
            response = _run(
                service._validate_staged_custom_hazard(
                    "session-1", session, "Evidence file: unrelated.pdf"
                )
            )

        self.assertTrue(response.error)
        self.assertIn("not mine closures", response.bot_message)
        self.assertEqual(
            [option.label for option in response.options],
            [
                "Go back to list of hazards",
                "Provide evidence again",
                "Clarify the relevance",
            ],
        )


if __name__ == "__main__":
    unittest.main()
