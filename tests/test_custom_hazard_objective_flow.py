import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    DIMENSION_STAGES,
    DIMENSION_WEIGHTS,
    default_custom_hazard_state,
    _deferred_dimension,
    validate_custom_hazard_dimensions,
)
from app.services.enums import ChatPhase


class CustomHazardObjectiveFlowTests(unittest.TestCase):
    def _session(self, phase=ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK):
        state = default_custom_hazard_state()
        state["raw_text"] = "Charging costs increase for rural taxi drivers"
        state["resolved_hazard_text"] = state["raw_text"]
        return ChatSession(
            session_key="objective-flow", phase=phase,
            country="Italy", region="Calabria", sector="Transport",
            selected_context_policy="Electric mobility policy",
            selected_context_policy_summary=(
                "Policy objectives: Expand electric mobility. Target actors/beneficiaries: "
                "taxi drivers. Mechanism: require charging infrastructure."
            ),
            custom_hazard=state,
        )

    def test_objectives_are_distinct_weighted_stages(self):
        self.assertEqual(DIMENSION_STAGES[:2], (
            ("sectoral_objective_fit",), ("policy_objective_fit",),
        ))
        self.assertEqual(DIMENSION_WEIGHTS["sectoral_objective_fit"], 0.10)
        self.assertEqual(DIMENSION_WEIGHTS["policy_objective_fit"], 0.15)
        self.assertAlmostEqual(sum(DIMENSION_WEIGHTS.values()), 1.0)

    def test_policy_stage_receives_selected_policy_summary(self):
        session = self._session()
        state = session.custom_hazard
        state["selected_policy"] = session.selected_context_policy
        state["selected_policy_summary"] = session.selected_context_policy_summary
        response = json.dumps({
            "dimension_scores": {"policy_objective_fit": {
                "score": 8, "reason": "Charging requirements can raise rural drivers' costs.",
                "needs_clarification": False,
            }},
            "context_fit": {"country_region_fit": {"supported": True}},
        })
        with patch("app.services.custom_hazard_validation.ask_llm_chat", AsyncMock(return_value=response)) as llm:
            result = asyncio.run(validate_custom_hazard_dimensions(
                state["raw_text"], session.sector, session.country, session.region,
                [], state, dimensions_to_validate=("policy_objective_fit",),
            ))
        payload = llm.await_args.kwargs["messages"][0]["content"]
        self.assertIn("taxi drivers", payload)
        self.assertIn("Electric mobility policy", payload)
        self.assertEqual(result["dimension_scores"]["policy_objective_fit"]["score"], 8)
        self.assertEqual(result["dimension_scores"]["sectoral_objective_fit"]["status"], "DEFERRED")

    def test_sectoral_confirmation_precedes_policy_confirmation(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        session.custom_hazard["dimension_scores"] = {
            "sectoral_objective_fit": {"score": 8, "needs_clarification": False},
            "policy_objective_fit": {"score": 8, "needs_clarification": False},
        }
        self.assertEqual(service._pending_custom_hazard_objective_confirmation(session), "sectoral_objective_fit")
        session.custom_hazard["sectoral_objective_confirmed"] = True
        self.assertEqual(service._pending_custom_hazard_objective_confirmation(session), "policy_objective_fit")
        session.custom_hazard["policy_objective_confirmed"] = True
        self.assertIsNone(service._pending_custom_hazard_objective_confirmation(session))

    def test_sectoral_confirmation_shows_status_score_reason_and_objective(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        session.custom_hazard["dimension_scores"] = {
            "sectoral_objective_fit": {
                "score": 8,
                "reason": "Charging costs can burden rural drivers during the mobility shift.",
                "needs_clarification": False,
            },
        }

        response = service._custom_hazard_objective_confirmation_step(
            "objective-flow", session, "sectoral_objective_fit"
        )
        message = response.bot_message
        self.assertIn("<h2>Sectoral Objective Fit</h2>", message)
        self.assertIn("<strong>Supported</strong> (score: 8/10)", message)
        self.assertIn("Charging costs can burden rural drivers", message)
        self.assertIn("<strong>Objective:</strong> Shift to Sustainable Mobility", message)
        self.assertLess(message.index("Supported"), message.index("Charging costs"))
        self.assertLess(message.index("Charging costs"), message.index("Objective:"))

    def test_policy_confirmation_omits_full_policy_summary(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        session.custom_hazard["dimension_scores"] = {
            "policy_objective_fit": {
                "score": 7,
                "reason": "Charging requirements may raise rural drivers' costs.",
                "needs_clarification": False,
            },
        }

        response = service._custom_hazard_objective_confirmation_step(
            "objective-flow", session, "policy_objective_fit"
        )
        self.assertIn("<h2>Policy Objective Fit</h2>", response.bot_message)
        self.assertIn("<strong>Supported</strong> (score: 7/10)", response.bot_message)
        self.assertIn("<strong>Objective:</strong> Electric mobility policy", response.bot_message)
        self.assertNotIn("Selected policy summary", response.bot_message)
        self.assertNotIn("Target actors/beneficiaries", response.bot_message)

    def test_grounding_pauses_for_each_objective_confirmation(self):
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(return_value="")
        service._custom_hazard_mechanism_suggestion_step = AsyncMock(return_value="mechanism stage")
        session = self._session()
        deferred = {key: _deferred_dimension() for key in DIMENSION_WEIGHTS}
        sector_result = {
            "dimension_scores": {
                **deferred,
                "sectoral_objective_fit": {"score": 8, "reason": "Fits sustainable mobility.", "needs_clarification": False},
            },
            "context_fit": {"country_region_fit": {"supported": True}},
            "next_action": "ask_clarification",
        }
        policy_result = {
            "dimension_scores": {
                **sector_result["dimension_scores"],
                "policy_objective_fit": {"score": 8, "reason": "Fits the selected electric mobility policy.", "needs_clarification": False},
            },
            "context_fit": {"country_region_fit": {"supported": True}},
            "next_action": "ask_clarification",
        }
        with patch(
            "app.services.chat_custom_hazard_grounding.validate_custom_hazard_dimensions",
            AsyncMock(side_effect=[sector_result, policy_result]),
        ) as validate:
            first = asyncio.run(service._run_custom_hazard_dimension_check("objective-flow", session))
            self.assertIn("Sectoral Objective Fit", first.bot_message)
            self.assertEqual(validate.await_args.kwargs["dimensions_to_validate"], ("sectoral_objective_fit",))
            second = asyncio.run(service._handle_custom_hazard_objective_confirmation(
                "objective-flow", session, "Yes, confirm fit"
            ))
            self.assertIn("Policy Objective Fit", second.bot_message)
            self.assertEqual(validate.await_args.kwargs["dimensions_to_validate"], ("policy_objective_fit",))
            third = asyncio.run(service._handle_custom_hazard_objective_confirmation(
                "objective-flow", session, "Yes, confirm fit"
            ))
            self.assertEqual(third, "mechanism stage")
            service._custom_hazard_mechanism_suggestion_step.assert_awaited_once()

    def test_confirmed_sectoral_fit_advances_to_policy_check(self):
        service = ChatService.__new__(ChatService)
        service._run_custom_hazard_dimension_check = AsyncMock(return_value="next stage")
        session = self._session(ChatPhase.CUSTOM_HAZARD_OBJECTIVE_CONFIRMATION)
        session.custom_hazard["objective_confirmation_dimension"] = "sectoral_objective_fit"
        result = asyncio.run(service._handle_custom_hazard_objective_confirmation(
            "objective-flow", session, "Yes, confirm fit"
        ))
        self.assertEqual(result, "next stage")
        self.assertTrue(session.custom_hazard["sectoral_objective_confirmed"])
        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)

    def test_rejected_policy_fit_requests_targeted_clarification(self):
        service = ChatService.__new__(ChatService)
        service._custom_hazard_clarification_step = Mock(return_value="clarify")
        session = self._session(ChatPhase.CUSTOM_HAZARD_OBJECTIVE_CONFIRMATION)
        session.custom_hazard["objective_confirmation_dimension"] = "policy_objective_fit"
        session.custom_hazard["dimension_scores"] = {
            "policy_objective_fit": {"score": 8, "needs_clarification": False},
        }
        result = asyncio.run(service._handle_custom_hazard_objective_confirmation(
            "objective-flow", session, "No, clarify fit"
        ))
        self.assertEqual(result, "clarify")
        item = session.custom_hazard["dimension_scores"]["policy_objective_fit"]
        self.assertTrue(item["needs_clarification"])
        self.assertIn("selected policy objective", item["clarification_question"])

    def test_confirmed_linkage_moves_to_group_grounding_without_extra_evidence_gate(self):
        service = ChatService.__new__(ChatService)
        service._run_custom_hazard_dimension_check = AsyncMock(return_value="group grounding")
        service._start_hazard_evidence_flow = AsyncMock()
        session = self._session(ChatPhase.CUSTOM_HAZARD_CAUSAL_LINKAGE_CONFIRMATION)
        session.custom_hazard.update({
            "selected_mechanism": "Charging mandates raise driver costs",
            "mechanism_causal_linkage": "mandate -> cost -> burden",
            "mechanism_provenance": "inferred",
        })
        result = asyncio.run(service._handle_custom_hazard_causal_linkage_confirmation(
            "objective-flow", session, "Yes"
        ))
        self.assertEqual(result, "group grounding")
        self.assertTrue(session.custom_hazard["evidence_decision_asked"])
        service._start_hazard_evidence_flow.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
