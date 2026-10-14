import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import ChatResponse
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_options import (
    ADOPT_INSPIRED_MITIGATION,
    INSPIRED_MITIGATION_CONFIRMATION_OPTIONS,
    INSPIRED_MITIGATION_REVISION_OPTIONS,
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

    def test_existing_policy_shows_d42_adaptations_without_factsheet_context(self):
        engine = _InspiredMeasureEngine()
        engine._hazard_with_mitigation_factsheet_reference = lambda session, **kwargs: (
            "## Factsheet content that should not be shown"
        )
        engine._existing_policy_adaptation_reference = AsyncMock(
            return_value=(
                "## Challenges to be addressed\n\n"
                "- Upfront costs exclude low-income tenants. <button>Source</button>\n\n"
                "## Proposed adaptations for the policy\n\n"
                "- Add targeted grants. <button>Source</button>"
            )
        )
        engine._existing_policy_mechanisms_reference = AsyncMock(
            return_value=(
                "## Policy mechanisms to be considered for mitigation\n\n"
                "- Upfront costs exclude renters. <button>Source</button>"
            )
        )
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        policy_position = response.bot_message.index("Selected policy:")
        hazard_position = response.bot_message.index("Selected hazard:")
        context_position = response.bot_message.index("Selected context:")
        mechanisms_position = response.bot_message.index("Policy mechanisms to be considered for mitigation")
        self.assertLess(policy_position, hazard_position)
        self.assertLess(hazard_position, context_position)
        self.assertLess(context_position, mechanisms_position)
        self.assertIn("Clean Energy Access Act", response.bot_message)
        self.assertIn("Rising electricity costs", response.bot_message)
        self.assertIn("Germany, Berlin, Energy", response.bot_message)
        self.assertIn("Proposed adaptations for the policy", response.bot_message)
        self.assertIn("Add targeted grants.", response.bot_message)
        self.assertIn("Policy mechanisms to be considered for mitigation", response.bot_message)
        self.assertIn("Upfront costs exclude renters.", response.bot_message)
        self.assertIn("Source", response.bot_message)
        self.assertNotIn("Factsheet content", response.bot_message)
        self.assertEqual([option.label for option in response.options], [ADOPT_INSPIRED_MITIGATION])
        self.assertEqual(session.new_policy_inspiration["challenge"], ["Upfront costs exclude low-income tenants."])
        self.assertEqual(session.new_policy_inspiration["approach"], ["Add targeted grants."])

    def test_policy_objectives_precede_mechanisms_in_both_flows(self):
        engine = _InspiredMeasureEngine()
        engine._policy_summary_display = ChatService._policy_summary_display
        engine._hazard_with_mitigation_factsheet_reference = lambda session, **kwargs: (
            "# Inspiration for New policy proposal\n\n"
            "## Policy mechanisms to be considered for mitigation\n\n- Energy costs"
        )
        engine._existing_policy_mechanisms_reference = AsyncMock(return_value=(
            "## Policy mechanisms to be considered for mitigation\n\n- Energy costs"
        ))
        engine._existing_policy_adaptation_reference = AsyncMock(return_value="")

        for proposal_type in ("new_policy", "existing_policy"):
            session = self._session()
            session.mitigation_proposal_type = proposal_type
            session.selected_context_policy_summary = (
                "### Policy Objectives\n\n- Reduce household energy costs.\n\n"
                "### Intended benefits\n\n- Affordable power."
            )
            session.selected_context_policy_sources = {
                "policy objectives": [[
                    {"field": "Document", "value": "Selected policy document"},
                    {"field": "Source", "value": "https://example.org/policy.pdf"},
                    {"field": "Referenced excerpt", "value": "Reduce household energy costs."},
                ]]
            }
            response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))
            self.assertLess(
                response.bot_message.index("Policy Objectives"),
                response.bot_message.index("Policy mechanisms to be considered for mitigation"),
            )
            self.assertIn("Reduce household energy costs.", response.bot_message)
            self.assertIn("data-source-table=", response.bot_message)
            self.assertIn("https://example.org/policy.pdf", response.bot_message)
            self.assertEqual(response.bot_message.count("Policy Objectives"), 1)

    def test_existing_policy_adoption_uses_displayed_concepts(self):
        engine = _InspiredMeasureEngine()
        engine._existing_policy_adaptation_reference = AsyncMock(return_value=(
            "## Challenges to be addressed\n\n- Upfront costs exclude low-income tenants.\n\n"
            "## Possible ways to address the challenges\n\n- Add targeted grants.\n\n"
            "## Disadvantage groups to consider for this mitigation measure\n\n"
            "- Low-income tenants"
        ))
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"
        asyncio.run(engine._create_mitigation_measure_step("session-1", session))
        mocked_llm = AsyncMock(side_effect=[
            '{"measure":"Offer targeted grants to low-income tenants.",'
            '"reason":"Grants reduce the upfront cost barrier.",'
            '"policy_about":"The Clean Energy Access Act supports renewable power.",'
            '"hazard_pathway":"Targeted grants reduce cost barriers under the selected hazard.",'
            '"group_pathways":[{"group":"Low-income tenants","pathway":"Grants reduce upfront costs."}]}',
            '{"coverage":[{"group":"Low-income tenants","covered":true,'
            '"reason":"The grants reduce their upfront costs."}]}',
        ])

        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(
                engine._capture_mitigation_measure("session-1", session, ADOPT_INSPIRED_MITIGATION)
            )

        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertIn("Offer targeted grants", response.bot_message)
        self.assertIn("Clean Energy Access Act", response.bot_message)
        self.assertIn("Targeted grants reduce cost barriers", response.bot_message)
        self.assertIn("Low-income tenants", response.bot_message)
        llm_input = json.loads(mocked_llm.await_args_list[0].kwargs["messages"][0]["content"])
        self.assertEqual(llm_input["important_concepts"]["approach"], ["Add targeted grants."])

    def test_existing_policy_with_an_approach_can_adopt_without_a_challenge(self):
        engine = _InspiredMeasureEngine()
        engine._existing_policy_adaptation_reference = AsyncMock(return_value=(
            "## Challenges to be addressed\n\nNo supported challenge was found.\n\n"
            "## Possible ways to address the challenges\n\n- Add targeted grants."
        ))
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        self.assertEqual([option.label for option in response.options], [ADOPT_INSPIRED_MITIGATION])
        self.assertEqual(session.new_policy_inspiration["approach"], ["Add targeted grants."])

    def test_existing_policy_with_mechanisms_can_adopt_without_d42_concepts(self):
        engine = _InspiredMeasureEngine()
        engine._existing_policy_mechanisms_reference = AsyncMock(return_value=(
            "## Policy mechanisms to be considered for mitigation\n\n"
            "- **Levy funds are reserved for community projects:** "
            "Households facing energy bills receive no direct support. <button>Source</button>"
        ))
        engine._existing_policy_adaptation_reference = AsyncMock(return_value=(
            "## Challenges to be addressed\n\nNo challenge is stated in the matched D4.2 record.\n\n"
            "## Possible ways to address the challenges\n\n"
            "No approach is stated in the matched D4.2 record."
        ))
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"
        session.selected_context_policy_summary = None

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        self.assertEqual([option.label for option in response.options], [ADOPT_INSPIRED_MITIGATION])
        self.assertEqual(session.new_policy_inspiration["challenge"], [])
        self.assertEqual(session.new_policy_inspiration["approach"], [])
        self.assertIn("Households facing energy bills", session.new_policy_inspiration["policy_mechanisms"][0])
        mocked_llm = AsyncMock(return_value=(
            '{"measure":"Use levy funds to reduce household energy costs.",'
            '"reason":"Direct support addresses the bill burden."}'
        ))
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            adopted = asyncio.run(
                engine._capture_mitigation_measure("session-1", session, ADOPT_INSPIRED_MITIGATION)
            )
        self.assertEqual(adopted.step, "inspired_mitigation_confirmation")

    def test_existing_policy_without_evidence_has_no_adopt_option(self):
        engine = _InspiredMeasureEngine()
        engine._existing_policy_adaptation_reference = AsyncMock(return_value=(
            "## Challenges to be addressed\n\nNo supported challenge was found.\n\n"
            "## Possible ways to address the challenges\n\nNo supported adaptation was found."
        ))
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"
        session.selected_context_policy_summary = None

        response = asyncio.run(engine._create_mitigation_measure_step("session-1", session))

        self.assertEqual(response.options, [])
        self.assertIsNone(session.new_policy_inspiration)

    def test_click_shows_measure_from_shown_concepts_for_confirmation(self):
        engine = _InspiredMeasureEngine()
        engine._hazard_with_mitigation_factsheet_reference = lambda session, **kwargs: (
            "# Important concepts for your new proposal\n\n"
            "## Disadvantage groups to consider for this mitigation measure\n\n"
            "- Low-income tenants\n- Older renters"
        )
        session = self._session()
        asyncio.run(engine._create_mitigation_measure_step("session-1", session))
        mocked_llm = AsyncMock(
            side_effect=[
                '{"measure":"Create a Berlin tenant energy advice service with municipal '
                'offices and tenant groups.","reason":"Clear supplier and rights guidance '
                'can help tenants respond to rising electricity costs.",'
                '"policy_about":"The Clean Energy Access Act expands renewable power.",'
                '"hazard_pathway":"Tenant advice helps households manage rising electricity costs.",'
                '"group_pathways":[{"group":"Low-income tenants","pathway":"Advice helps with costs."},'
                '{"group":"Older renters","pathway":"Advice helps renters compare suppliers."},'
                '{"group":"Unlisted group","pathway":"Unrelated."}]}',
                '{"coverage":[{"group":"Low-income tenants","covered":true,"reason":"Clear advice."},'
                '{"group":"Older renters","covered":true,"reason":"Supplier guidance."}]}',
            ]
        )
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(
                engine._capture_mitigation_measure("session-1", session, ADOPT_INSPIRED_MITIGATION)
            )

        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertEqual(session.phase, "inspired_mitigation_confirmation")
        self.assertIn("tenant energy advice service", response.bot_message)
        self.assertIn("Clean Energy Access Act", response.bot_message)
        self.assertIn("Tenant advice helps households", response.bot_message)
        self.assertIn("Low-income tenants", response.bot_message)
        self.assertIn("Older renters", response.bot_message)
        self.assertNotIn("Unlisted group", response.bot_message)
        self.assertEqual(session.pending_inspired_mitigation_groups, ["Low-income tenants", "Older renters"])
        self.assertEqual(
            [option.label for option in response.options],
            [option.label for option in INSPIRED_MITIGATION_CONFIRMATION_OPTIONS],
        )
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("tenant energy advice service", session.pending_inspired_mitigation_measure)
        self.assertIn("rising electricity costs", session.pending_inspired_mitigation_reason)
        llm_call = mocked_llm.await_args_list[0].kwargs
        llm_input = json.loads(llm_call["messages"][0]["content"])
        self.assertEqual(llm_input["country"], "Germany")
        self.assertEqual(llm_input["region"], "Berlin")
        self.assertEqual(llm_input["sector"], "Energy")
        self.assertEqual(llm_input["selected_policy"], "Clean Energy Access Act")
        self.assertIn("renewable power", llm_input["selected_policy_summary"])
        self.assertEqual(llm_input["selected_hazard"], "Rising electricity costs")
        self.assertEqual(llm_input["affected_groups"], ["Low-income tenants"])
        self.assertEqual(llm_input["additional_affected_groups"], ["Older renters"])
        self.assertEqual(llm_input["considered_groups"], ["Low-income tenants", "Older renters"])
        self.assertIn("Local advice could help tenants", llm_input["important_concepts"]["approach"][0])
        self.assertIn("selected twin-transition policy", llm_call["context"])
        self.assertIn("policy's implementation", llm_call["context"])

    def test_uncovered_factsheet_group_blocks_adoption_until_revision(self):
        engine = _InspiredMeasureEngine()
        engine._hazard_with_mitigation_factsheet_reference = lambda session, **kwargs: (
            "# Important concepts for your new proposal\n\n"
            "## Disadvantage groups to consider for this mitigation measure\n\n"
            "- Low-income tenants\n- Factsheet-only households"
        )
        session = self._session()
        asyncio.run(engine._create_mitigation_measure_step("session-1", session))
        self.assertEqual(session.inspired_mitigation_considered_groups, [
            "Low-income tenants", "Factsheet-only households",
        ])
        mocked_llm = AsyncMock(side_effect=[
            json.dumps({
                "measure": "Offer tenant advice.", "reason": "Advice addresses energy costs.",
                "policy_about": "The policy expands renewable power.",
                "hazard_pathway": "Advice helps households manage costs.",
                "group_pathways": [{"group": "Low-income tenants", "pathway": "Advice reduces costs."}],
            }),
            json.dumps({"coverage": [{
                "group": "Low-income tenants", "covered": True, "reason": "Specific advice is provided.",
            }]}),
            json.dumps({
                "measure": "Offer targeted advice and bill support to both groups.",
                "reason": "Support addresses energy costs for both groups.",
                "policy_about": "The policy expands renewable power.",
                "hazard_pathway": "Advice and bill support reduce household costs.",
                "group_pathways": [
                    {"group": "Low-income tenants", "pathway": "Bill support reduces costs."},
                    {"group": "Factsheet-only households", "pathway": "Bill support reduces costs."},
                ],
            }),
            json.dumps({"coverage": [
                {"group": "Low-income tenants", "covered": True, "reason": "Bill support lowers costs."},
                {"group": "Factsheet-only households", "covered": True, "reason": "Bill support lowers costs."},
            ]}),
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            draft = asyncio.run(engine._adopt_inspired_mitigation_response("session-1", session))
            self.assertIn("Needs revision", draft.bot_message)
            self.assertIn("Factsheet-only households", draft.bot_message)
            self.assertEqual(draft.options, INSPIRED_MITIGATION_REVISION_OPTIONS)
            session = ChatSessionStore().put("session-1", vars(session).copy())
            restored = engine._inspired_mitigation_confirmation_response("session-1", session)
            self.assertEqual(restored.options, INSPIRED_MITIGATION_REVISION_OPTIONS)
            blocked = asyncio.run(engine._handle_inspired_mitigation_confirmation(
                "session-1", session, "Yes, use this measure"
            ))
            self.assertTrue(blocked.error)
            self.assertEqual(blocked.options, INSPIRED_MITIGATION_REVISION_OPTIONS)
            revised = asyncio.run(engine._handle_inspired_mitigation_confirmation(
                "session-1", session, "Revise measure to cover all groups"
            ))

        self.assertEqual(revised.options, INSPIRED_MITIGATION_CONFIRMATION_OPTIONS)
        self.assertIn("Confirm the mitigation measure", revised.bot_message)
        self.assertEqual(session.pending_inspired_mitigation_groups, [
            "Low-income tenants", "Factsheet-only households",
        ])
        revision_input = json.loads(mocked_llm.await_args_list[2].kwargs["messages"][0]["content"])
        self.assertEqual(revision_input["previous_uncovered_groups"], ["Factsheet-only households"])

    def test_yes_continues_with_the_confirmed_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        session.pending_inspired_mitigation_description = "Advice helps tenants with electricity costs."
        session.pending_inspired_mitigation_groups = ["Low-income tenants"]

        response = asyncio.run(
            engine._handle_inspired_mitigation_confirmation("session-1", session, "Yes")
        )

        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Create a tenant energy advice service.")
        self.assertEqual(session.pending_mitigation_reason, "Help tenants compare suppliers.")
        self.assertIsNone(session.pending_inspired_mitigation_measure)
        self.assertIsNone(session.pending_inspired_mitigation_description)
        self.assertIsNone(session.pending_inspired_mitigation_groups)

    def test_no_requests_user_written_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        session.pending_inspired_mitigation_description = "Advice helps tenants with electricity costs."
        session.pending_inspired_mitigation_groups = ["Low-income tenants"]
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
        self.assertIsNone(session.pending_inspired_mitigation_description)
        self.assertIsNone(session.pending_inspired_mitigation_groups)
        self.assertIsNone(session.new_policy_inspiration)

    def test_confirmation_survives_session_restore(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        session.pending_inspired_mitigation_description = "The policy expands renewable power; advice helps tenants with rising costs."
        session.pending_inspired_mitigation_groups = ["Low-income tenants"]
        restored = ChatSessionStore().put("session-1", vars(session).copy())

        response = engine._inspired_mitigation_confirmation_response("session-1", restored)

        self.assertEqual(restored.phase, "inspired_mitigation_confirmation")
        self.assertIn("Create a tenant energy advice service.", response.bot_message)
        self.assertIn("advice helps tenants with rising costs", response.bot_message)
        self.assertIn("Low-income tenants", response.bot_message)

        service = ChatService.__new__(ChatService)
        resumed_response = service._repeat_current_options("session-1", restored, "", False)
        self.assertEqual(resumed_response.step, "inspired_mitigation_confirmation")
        self.assertIn("Create a tenant energy advice service.", resumed_response.bot_message)
        self.assertIn("Low-income tenants", resumed_response.bot_message)

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
