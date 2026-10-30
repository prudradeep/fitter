import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    _custom_hazard_context_fit,
    default_custom_hazard_state,
    validate_custom_hazard_dimensions,
)
from app.services.enums import ChatPhase


class RequestedCustomHazardFlowTests(unittest.TestCase):
    def _session(self, phase: ChatPhase = ChatPhase.CUSTOM_HAZARD_MECHANISM_INPUT) -> ChatSession:
        state = default_custom_hazard_state()
        state["raw_text"] = "Charging rules increase costs for rural drivers"
        state["resolved_hazard_text"] = state["raw_text"]
        return ChatSession(
            session_key="flow-test", phase=phase,
            country="Italy", region="Calabria", sector="Transport",
            selected_context_policy="Electric mobility policy",
            custom_hazard=state,
        )

    def test_user_mechanism_asks_for_optional_evidence_before_source_lookup(self) -> None:
        service = ChatService.__new__(ChatService)
        expected = object()
        service._hazard_evidence_decision_step = Mock(return_value=expected)
        service._validate_current_custom_hazard_mechanism_source = AsyncMock()
        session = self._session()
        with patch(
            "app.services.chat_custom_hazard_mechanism.assess_custom_hazard_mechanism_clarity",
            AsyncMock(return_value={"clear": True}),
        ):
            response = asyncio.run(service._handle_custom_hazard_mechanism_input(
                "flow-test", session, "Charging mandates pass infrastructure costs to drivers"
            ))
        self.assertIs(response, expected)
        self.assertTrue(session.custom_hazard["user_mechanism_evidence_pending"])
        service._validate_current_custom_hazard_mechanism_source.assert_not_awaited()

    def test_evidence_decision_is_allowed_from_mechanism_input(self) -> None:
        service = ChatService.__new__(ChatService)
        service._custom_hazard_response = Mock(return_value=object())
        session = self._session()
        session.custom_hazard["user_mechanism_evidence_pending"] = True
        service._hazard_evidence_decision_step("flow-test", session)
        self.assertEqual(session.phase, ChatPhase.ADD_HAZARD_EVIDENCE_DECISION)

    def test_user_evidence_is_the_only_mechanism_linkage_source(self) -> None:
        service = ChatService.__new__(ChatService)
        service._user_evidence_context_for_contradiction_check = AsyncMock(
            return_value="The supplied report documents charging costs passed to rural drivers."
        )
        service._custom_hazard_ai_reflection = AsyncMock(return_value="Policy and charging costs are linked in Calabria.")
        expected = object()
        service._custom_hazard_causal_linkage_step = Mock(return_value=expected)
        session = self._session(ChatPhase.ADD_HAZARD_EVIDENCE_INPUT)
        session.custom_hazard.update({
            "selected_mechanism": "Charging mandates pass infrastructure costs to drivers",
            "evidence": "Evidence file: user-report.pdf",
        })
        linkage = {"supported": True, "causal_linkage": "report finding -> higher charging costs -> driver burden"}
        with patch(
            "app.services.chat_custom_hazard_mechanism.validate_custom_hazard_mechanism_linkage",
            AsyncMock(return_value=linkage),
        ) as check:
            response = asyncio.run(service._validate_user_mechanism_evidence("flow-test", session))
        self.assertIs(response, expected)
        self.assertEqual(check.await_args.args[2], "The supplied report documents charging costs passed to rural drivers.")
        self.assertEqual(session.custom_hazard["mechanism_provenance"], "documented")
        self.assertEqual(session.custom_hazard["mechanism_source"], "user_evidence")

    def test_proposed_mechanism_shows_a_verified_policy_source(self) -> None:
        service = ChatService.__new__(ChatService)
        service._stored_context_policy_document_ids = Mock(return_value=["policy-doc"])
        service._context_policy_document_context = Mock(return_value="The policy requires charging infrastructure.")
        service._custom_hazard_response = Mock(return_value=object())
        session = self._session()
        session.selected_context_policy_id = "policy-1"
        session.custom_hazard["selected_mechanism"] = "Charging infrastructure costs are passed to drivers"
        with patch(
            "app.services.chat_custom_hazard_mechanism.summarize_custom_hazard_supporting_policy",
            AsyncMock(return_value={
                "supported": True,
                "causal_linkage": "charging requirement -> higher cost -> driver burden",
                "policy_details": "Charging infrastructure is required.",
            }),
        ):
            asyncio.run(service._preview_custom_hazard_mechanism_source(
                session, session.custom_hazard["raw_text"],
                session.custom_hazard["selected_mechanism"],
            ))
        service._custom_hazard_mechanism_confirmation_step("flow-test", session)
        message = service._custom_hazard_response.call_args.kwargs["bot_message"]
        self.assertIn("[Documented]", message)
        self.assertIn("Electric mobility policy", message)
        self.assertIn("Charging infrastructure is required", message)

    def test_unsupported_user_evidence_requests_replacement_or_revision(self) -> None:
        service = ChatService.__new__(ChatService)
        service._user_evidence_context_for_contradiction_check = AsyncMock(return_value="Unrelated report")
        expected = object()
        service._hazard_evidence_input_step = Mock(return_value=expected)
        session = self._session(ChatPhase.ADD_HAZARD_EVIDENCE_INPUT)
        session.custom_hazard.update({"selected_mechanism": "Charging costs rise", "evidence": "Evidence file: other.pdf"})
        with patch(
            "app.services.chat_custom_hazard_mechanism.validate_custom_hazard_mechanism_linkage",
            AsyncMock(return_value={"supported": False, "reason": "No matching finding."}),
        ):
            response = asyncio.run(service._validate_user_mechanism_evidence("flow-test", session))
        self.assertIs(response, expected)
        self.assertTrue(session.custom_hazard["user_mechanism_evidence_pending"])
        self.assertTrue(service._hazard_evidence_input_step.call_args.kwargs["retry"])

    def test_skipped_sources_can_produce_a_confirmable_inferred_linkage(self) -> None:
        service = ChatService.__new__(ChatService)
        service._custom_hazard_ai_reflection = AsyncMock(return_value="This is a possible policy effect.")
        expected = object()
        service._custom_hazard_causal_linkage_step = Mock(return_value=expected)
        session = self._session(ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        session.custom_hazard["selected_mechanism"] = "Charging mandates raise operator costs"
        response_json = json.dumps({
            "plausible": True,
            "causal_linkage": "mobility objective -> charging mandates -> higher operator costs",
        })
        with patch("app.services.chat_custom_hazard_mechanism.ask_llm_chat", AsyncMock(return_value=response_json)):
            response = asyncio.run(service._infer_custom_hazard_mechanism_linkage("flow-test", session))
        self.assertIs(response, expected)
        self.assertEqual(session.custom_hazard["mechanism_provenance"], "inferred")

    def test_user_evidence_supports_group_and_is_labelled_documented(self) -> None:
        service = ChatService.__new__(ChatService)
        service._user_evidence_context_for_contradiction_check = AsyncMock(
            return_value="The report finds rural drivers pay higher charging costs."
        )
        session = self._session()
        session.custom_hazard["evidence"] = "Evidence file: user-report.pdf"
        reply = json.dumps({"supported": True, "finding": "Rural drivers pay higher charging costs.", "reason": "Matched."})
        with patch("app.services.validation_service.ask_llm_chat", AsyncMock(return_value=reply)):
            result = asyncio.run(service._custom_affected_group_evidence_support(
                session, "Rural drivers", "Charging costs increase their travel expenses"
            ))
        self.assertTrue(result["valid"])
        support = session.custom_hazard["affected_group_support"]["rural drivers"]
        self.assertEqual(support["basis"], "documented")
        self.assertIn("user-report.pdf", support["source"])

    def test_group_review_shows_basis_and_source(self) -> None:
        message = ChatService._format_population_profiles_for_review([{
            "name": "Rural drivers", "reflection": "They face higher travel costs.",
            "basis": "documented", "source_label": "user-report.pdf",
        }])
        self.assertIn("[Documented]", message)
        self.assertIn("user-report.pdf", message)

    def test_generated_group_can_be_confirmed_after_review(self) -> None:
        service = ChatService.__new__(ChatService)
        expected = object()
        service._route_custom_hazard_next_action = AsyncMock(return_value=expected)
        session = self._session(ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)
        group = {
            "group": "Transport sector workers/employers",
            "reason": "Temporary grants interrupt road safety training.",
            "reflection": "Funding gaps interrupt training delivery.",
            "needs_review": True,
        }
        session.custom_hazard["affected_groups"] = [group]

        response = asyncio.run(service._handle_custom_hazard_population_review(
            "flow-test", session, "Confirm affected groups"
        ))

        self.assertIs(response, expected)
        self.assertTrue(session.custom_hazard["affected_groups_reviewed"])
        self.assertEqual(session.custom_hazard["confirmed_affected_groups"], [group])

    def test_unsupported_group_evidence_still_blocks_confirmation(self) -> None:
        service = ChatService.__new__(ChatService)
        expected = object()
        service._custom_hazard_population_review_step = Mock(return_value=expected)
        session = self._session(ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)
        session.custom_hazard["affected_groups"] = [{
            "group": "Rural drivers",
            "reason": "Higher travel costs.",
            "needs_review": True,
            "evidence_error": "The supplied report does not support this impact.",
        }]

        response = asyncio.run(service._handle_custom_hazard_population_review(
            "flow-test", session, "Confirm affected groups"
        ))

        self.assertIs(response, expected)
        self.assertEqual(
            service._custom_hazard_population_review_step.call_args.kwargs["error_reason"],
            "The supplied report does not support this impact.",
        )
        self.assertFalse(session.custom_hazard.get("affected_groups_reviewed"))

    def test_supported_generated_group_clears_review_flag(self) -> None:
        service = ChatService.__new__(ChatService)
        service._custom_affected_group_evidence_support = AsyncMock(return_value={"valid": True})
        session = self._session(ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)
        group = {
            "group": "Rural drivers", "reason": "Higher travel costs.",
            "needs_review": True,
        }
        session.custom_hazard["affected_groups"] = [group]

        asyncio.run(service._ensure_affected_population_provenance(session))

        self.assertFalse(group["needs_review"])
        self.assertNotIn("evidence_error", group)

    def test_context_fit_checks_place_without_title_name_matching(self) -> None:
        checks = _custom_hazard_context_fit(
            {"context_fit": {
                "country_region_fit": {"supported": True},
            }},
            "Charging costs increase for rural drivers",
            "Transport", "Italy", "Calabria", default_custom_hazard_state(),
        )
        self.assertNotIn("selected_sector_fit", checks)
        self.assertTrue(checks["country_region_fit"]["supported"])

    def test_explicit_location_mismatch_requests_clarification(self) -> None:
        checks = _custom_hazard_context_fit(
            {"context_fit": {"country_region_fit": {
                "supported": False,
                "reason": "The proposal specifies a different country.",
                "clarification_question": "Which country does this concern?",
            }}},
            "Charging costs increase for rural drivers",
            "Transport", "Italy", "Calabria", default_custom_hazard_state(),
        )
        self.assertFalse(checks["country_region_fit"]["supported"])
        self.assertIn("Which country", checks["country_region_fit"]["clarification_question"])

    def test_context_mismatch_blocks_objective_fit_without_new_score_dimensions(self) -> None:
        model_response = json.dumps({
            "dimension_scores": {"policy_objective_fit": {
                "score": 8, "reason": "The policy objective is relevant.",
                "needs_clarification": False,
            }},
            "context_fit": {"country_region_fit": {
                "supported": False, "reason": "This refers to another country.",
                "clarification_question": "How does this apply in Italy?",
            }},
            "affected_groups": [], "duplicate_candidates": [],
        })
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            AsyncMock(return_value=model_response),
        ):
            result = asyncio.run(validate_custom_hazard_dimensions(
                "Charging costs increase for rural drivers", "Transport", "Italy", "Calabria",
                [], default_custom_hazard_state(), dimensions_to_validate=("policy_objective_fit",),
            ))
        self.assertLess(result["dimension_scores"]["policy_objective_fit"]["score"], 7)
        self.assertNotIn("country_region_fit", result["dimension_scores"])
        self.assertFalse(result["context_fit"]["country_region_fit"]["supported"])


if __name__ == "__main__":
    unittest.main()
