import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import ChatResponse
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_options import (
    ADOPT_INSPIRED_MITIGATION,
    INSPIRED_MITIGATION_CONFIRMATION_OPTIONS,
    INSPIRED_MITIGATION_PARTIAL_OPTIONS,
    INSPIRED_MITIGATION_REVISION_OPTIONS,
    POLICY_ADAPTATION_SKIP_OPTIONS,
)
from app.services.chat_session import ChatSession, ChatSessionStore
from app.services.chat_service import ChatService
from tests.test_policy_summary_references import POLICY, _db_with_d42


CONCEPTS = {
    "challenge": ["Tenants lack clear information about energy choices."],
    "approach": ["Local advice could help tenants compare suppliers and understand their rights."],
    "stakeholders": ["Municipal offices and tenant groups could deliver the advice."],
}

POLICY_ALIGNED = {
    "policy_relevance": "aligned",
    "policy_link": "The policy's access rules affect the selected hazard.",
    "policy_basis": "summary",
    "policy_source_ids": [],
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
    def test_explicit_new_access_condition_is_detected(self):
        self.assertIn("required payment", ChatMitigationStepsMixin._obvious_inspired_access_barrier(
            "Deduct a mandatory, pre-approved participation fee before households can access the scheme."
        ))
        self.assertEqual(ChatMitigationStepsMixin._obvious_inspired_access_barrier(
            "Waive the mandatory participation fee before households apply."
        ), "")

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

    def test_selected_policy_sources_include_d42_linked_document_and_knowledge_base(self):
        db, _ = _db_with_d42()
        try:
            document = KnowledgeDocument(
                title="Selected transport policy", source_type="pdf",
                source_uri="kb/selected-transport-policy.pdf", scope="main",
            )
            db.add(document)
            db.flush()
            db.add(KnowledgeChunk(
                document_id=document.id, chunk_index=0, page_number=2,
                content="Annual passes require an upfront payment from riders.",
                source_type="pdf", source_uri=document.source_uri,
            ))
            db.commit()
            service = ChatService.__new__(ChatService)
            service.db = db
            service.user_id = None
            service._shared_knowledge_results = AsyncMock(return_value=[{
                "title": "Transport affordability study",
                "content": "Low-income riders struggle with upfront fares.",
            }])
            session = ChatSession(
                country="Italy", sector="Transport", selected_context_policy=POLICY,
                selected_hazard="Unaffordable travel",
                pending_context_policy_document_ids=[document.id],
            )
            sources = asyncio.run(service._policy_adaptation_sources(
                session, "Low-income riders and upfront fares"
            ))
            kinds = {source["kind"] for source in sources}
            self.assertIn("D4.2", kinds)
            self.assertIn("D4.2_proposal", kinds)
            self.assertIn("policy_document", kinds)
            self.assertIn("knowledge_base", kinds)
            self.assertTrue(any("Annual passes require" in source["content"]
                                for source in sources))
            self.assertTrue(any("Improve access to public transport" in source["content"]
                                for source in sources))
        finally:
            db.close()

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
            '"pathway":"The grants reduce their upfront costs."}]}',
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

    def test_generation_failure_stays_on_measure_step_after_retry(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.new_policy_inspiration = CONCEPTS
        session.inspired_mitigation_considered_groups = ["Low-income tenants"]
        mocked_llm = AsyncMock(return_value='{"measure":"","reason":""}')
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(response.input_mode, "mitigation_measure")
        self.assertEqual(response.options[0].label, ADOPT_INSPIRED_MITIGATION)
        self.assertIn("try generating it again", response.bot_message)
        self.assertEqual(mocked_llm.await_count, 2)
        self.assertEqual(session.selected_context_policy, "Clean Energy Access Act")
        self.assertEqual(session.selected_hazard, "Rising electricity costs")
        self.assertEqual(session.new_policy_inspiration, CONCEPTS)

    def test_missing_existing_policy_concepts_stays_on_measure_step(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.mitigation_proposal_type = "existing_policy"
        session.selected_context_policy_summary = None
        session.new_policy_inspiration = None
        response = asyncio.run(engine._adopt_inspired_mitigation_response(
            "session-1", session
        ))
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIn("not find enough policy context", response.bot_message)
        self.assertEqual(response.options, [])

    def test_generated_measure_without_reason_still_shows_summary(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.new_policy_inspiration = CONCEPTS
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            return_value=json.dumps({
                "measure": "Provide free municipal energy advice to tenants.",
                "reason": "",
                "hazard_pathway": "Advice helps tenants compare lower-cost options.",
            })
        )):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertIn("Provide free municipal energy advice", response.bot_message)
        self.assertEqual(session.pending_inspired_mitigation_reason,
                         "Advice helps tenants compare lower-cost options.")

    def test_short_retry_recovers_draft_and_shows_summary(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.new_policy_inspiration = CONCEPTS
        mocked_llm = AsyncMock(side_effect=[
            '{"measure":""}',
            json.dumps({
                "measure": "Provide free municipal energy advice to tenants.",
                "reason": "Advice helps tenants address rising electricity costs.",
            }),
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertIn("Provide free municipal energy advice", response.bot_message)
        self.assertEqual(mocked_llm.await_count, 2)

    def test_failed_revision_keeps_existing_draft_for_review(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.new_policy_inspiration = CONCEPTS
        session.pending_inspired_mitigation_measure = "Offer targeted bill support."
        session.pending_inspired_mitigation_reason = "Support lowers energy costs."
        session.pending_inspired_mitigation_uncovered_groups = ["Older renters"]
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            return_value='{"measure":"","reason":""}'
        )):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session, revision=True
            ))
        self.assertEqual(response.step, "inspired_mitigation_confirmation")
        self.assertIn("Offer targeted bill support", response.bot_message)
        self.assertEqual(session.pending_inspired_mitigation_measure,
                         "Offer targeted bill support.")

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
                '{"coverage":[{"group":"Low-income tenants","covered":true,'
                '"pathway":"Advice helps tenants compare bills and reduce costs."},'
                '{"group":"Older renters","covered":true,'
                '"pathway":"Advice helps renters compare suppliers."}]}',
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
        self.assertIn("Advice helps tenants compare bills", response.bot_message)
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

    def test_uncovered_factsheet_group_does_not_block_adoption(self):
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
                "group": "Low-income tenants", "covered": True,
                "pathway": "Advice helps tenants manage energy costs.",
            }]}),
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            draft = asyncio.run(engine._adopt_inspired_mitigation_response("session-1", session))
        self.assertIn("Confirm the mitigation measure", draft.bot_message)
        self.assertIn("Factsheet-only households", draft.bot_message)
        self.assertEqual(draft.options, INSPIRED_MITIGATION_PARTIAL_OPTIONS)
        session = ChatSessionStore().put("session-1", vars(session).copy())
        restored = engine._inspired_mitigation_confirmation_response("session-1", session)
        self.assertEqual(restored.options, INSPIRED_MITIGATION_PARTIAL_OPTIONS)
        adopted = asyncio.run(engine._handle_inspired_mitigation_confirmation(
            "session-1", session, "Yes, use this measure"
        ))
        self.assertEqual(adopted.step, "mitigation_clarity")
        self.assertEqual(session.accepted_inspired_mitigation_group_pathways, {
            "Low-income tenants": "Advice helps tenants manage energy costs.",
        })
        self.assertEqual(mocked_llm.await_count, 2)

    def test_generated_group_claim_does_not_override_measure_only_review(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.inspired_mitigation_considered_groups = [
            "Households with unpaid utility bills", "Households with Higher Home problems count",
        ]
        session.new_policy_inspiration = CONCEPTS
        generator = {
            "measure": "Require digital and financial literacy training before participation.",
            "reason": "Training helps households understand costs.",
            "group_pathways": [
                {"group": group, "pathway": "Training benefits this group."}
                for group in session.inspired_mitigation_considered_groups
            ],
        }
        review = {"coverage": [
            {"group": "Households with unpaid utility bills", "covered": True,
             "pathway": "Training helps households understand utility costs."},
            {"group": "Households with Higher Home problems count", "covered": False,
             "pathway": ""},
        ]}
        mocked_llm = AsyncMock(side_effect=[json.dumps(generator), json.dumps(review)])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertIn("Confirm the mitigation measure", response.bot_message)
        self.assertIn("Higher Home problems count", response.bot_message)
        self.assertEqual(response.options, INSPIRED_MITIGATION_PARTIAL_OPTIONS)
        self.assertEqual(session.pending_inspired_mitigation_groups, [
            "Households with unpaid utility bills"
        ])
        review_input = json.loads(mocked_llm.await_args_list[1].kwargs["messages"][0]["content"])
        self.assertNotIn("proposed_pathways", review_input)
        self.assertNotIn("reason", review_input)

    def test_source_grounded_generation_revises_missing_groups_and_new_barrier(self):
        engine = _InspiredMeasureEngine()
        source_rows = [
            {"kind": "D4.2", "title": "D4.2", "content": "Existing Gaps: participation costs."},
            {"kind": "policy_document", "title": "Selected policy",
             "content": "Participation rules for renewable projects."},
            {"kind": "knowledge_base", "title": "Household study",
             "content": "Unpaid bills and home repair needs can exclude households."},
        ]
        engine._policy_adaptation_sources = AsyncMock(return_value=source_rows)
        session = self._session()
        session.inspired_mitigation_considered_groups = [
            "High-consumption households", "Households with home problems",
            "Households with unpaid bills",
        ]
        session.new_policy_inspiration = CONCEPTS
        first = {
            "measure": "Require an upfront fee before participation.",
            "reason": "The fee funds participation.",
        }
        first_review = {
            "coverage": [{"group": "High-consumption households", "covered": False,
                          "pathway": "", "source_ids": []}],
            "introduces_barrier": True,
            "barrier_reason": "The required fee increases the upfront cost barrier.",
        }
        revised = {
            "measure": "Waive entry costs, provide bill relief, and fund home repairs for eligible households.",
            "reason": "Cost relief and repairs make participation more accessible.",
            "policy_about": "The policy sets participation rules.",
            "hazard_pathway": "Support reduces exclusion from renewable benefits.",
        }
        pathways = [
            "Waived entry costs reduce participation costs.",
            "Funded home repairs address housing barriers.",
            "Bill relief addresses unpaid utility bills.",
        ]
        final_review = {
            "coverage": [
                {"group": group, "covered": True, "pathway": pathway,
                 "source_ids": [3]}
                for group, pathway in zip(session.inspired_mitigation_considered_groups, pathways)
            ],
            "introduces_barrier": False,
        }
        mocked_llm = AsyncMock(side_effect=[
            json.dumps(item) for item in (first, first_review, revised, final_review)
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertIn("Confirm the mitigation measure", response.bot_message)
        self.assertIn("Waive entry costs", response.bot_message)
        self.assertEqual(response.options, INSPIRED_MITIGATION_CONFIRMATION_OPTIONS)
        self.assertEqual(session.pending_inspired_mitigation_groups,
                         session.inspired_mitigation_considered_groups)
        first_input = json.loads(mocked_llm.await_args_list[0].kwargs["messages"][0]["content"])
        revision_input = json.loads(mocked_llm.await_args_list[2].kwargs["messages"][0]["content"])
        self.assertEqual({item["kind"] for item in first_input["sources"]},
                         {"D4.2", "policy_document", "knowledge_base"})
        self.assertIn("required fee", revision_input["revision_feedback"])
        self.assertEqual(revision_input["previous_uncovered_groups"],
                         session.inspired_mitigation_considered_groups)
        engine._policy_adaptation_sources.assert_awaited_once()

    def test_source_grounded_generation_stops_after_bounded_revisions(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[
            {"kind": "D4.2", "title": "D4.2", "content": "Policy objective: participation."}
        ])
        session = self._session()
        session.inspired_mitigation_considered_groups = ["Households with home problems"]
        session.new_policy_inspiration = CONCEPTS
        draft = {"measure": "Provide literacy training.",
                 "reason": "Training explains participation."}
        review = {"coverage": [{"group": "Households with home problems",
                               "covered": False, "pathway": "", "source_ids": []}],
                  "introduces_barrier": False}
        mocked_llm = AsyncMock(side_effect=[
            json.dumps(item) for item in (draft, review) * 3
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._adopt_inspired_mitigation_response(
                "session-1", session
            ))
        self.assertIn("Confirm the mitigation measure", response.bot_message)
        self.assertIn("Households with home problems", response.bot_message)
        self.assertIn("could not verify a concrete benefit pathway", response.bot_message)
        self.assertEqual(response.options, INSPIRED_MITIGATION_PARTIAL_OPTIONS)
        self.assertEqual(mocked_llm.await_count, 2)
        accepted = asyncio.run(engine._handle_inspired_mitigation_confirmation(
            "session-1", session, "Yes, use this measure"
        ))
        self.assertEqual(accepted.step, "mitigation_clarity")
        self.assertEqual(session.accepted_inspired_mitigation_group_pathways, {})

    def test_partial_draft_can_still_be_revised(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.new_policy_inspiration = CONCEPTS
        session.inspired_mitigation_considered_groups = ["Low-income tenants", "Older renters"]
        session.pending_inspired_mitigation_measure = "Offer tenant advice."
        session.pending_inspired_mitigation_uncovered_groups = ["Older renters"]
        generator = {"measure": "Offer advice and home visits to tenants and older renters.",
                     "reason": "The service improves access to energy advice."}
        review = {"coverage": [
            {"group": "Low-income tenants", "covered": True,
             "pathway": "Advice helps tenants compare energy options."},
            {"group": "Older renters", "covered": True,
             "pathway": "Home visits help older renters access advice."},
        ]}
        mocked_llm = AsyncMock(side_effect=[json.dumps(generator), json.dumps(review)])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._handle_inspired_mitigation_confirmation(
                "session-1", session, "Revise measure to cover more groups"
            ))
        self.assertEqual(response.options, INSPIRED_MITIGATION_CONFIRMATION_OPTIONS)
        revision_input = json.loads(mocked_llm.await_args_list[0].kwargs["messages"][0]["content"])
        self.assertEqual(revision_input["previous_uncovered_groups"], ["Older renters"])

    def test_adopted_draft_with_no_verified_groups_skips_required_pathways(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        measure = "Offer a free municipal energy advice service."
        session.pending_mitigation_measure = measure
        session.accepted_inspired_mitigation_measure = measure
        session.accepted_inspired_mitigation_group_pathways = {}
        service._assess_measure_policy_and_mechanisms = AsyncMock(return_value=({}, []))
        service._continue_with_knowledge_base_evidence_or_request_user_evidence = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1", step="mitigation_evidence_decision",
                bot_message="Evidence", session=session.summary(),
            )
        )
        response = asyncio.run(service._start_guided_mitigation_flow(
            "session-1", session, measure, "Advice improves access."
        ))
        self.assertEqual(response.step, "mitigation_evidence_decision")
        self.assertEqual(session.mitigation_target_population, [])
        service._guided_dg_evidence_decision_step = AsyncMock(return_value=ChatResponse(
            session_id="session-1", step="mitigation_final_summary_review",
            bot_message="Summary", session=session.summary(),
        ))
        later = asyncio.run(service._guided_dg_suggestion_step("session-1", session))
        self.assertEqual(later.step, "mitigation_final_summary_review")

    def test_guided_flow_reuses_reviewed_pathways_for_unchanged_adopted_measure(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        measure = "Offer targeted bill support to low-income tenants."
        session.accepted_inspired_mitigation_measure = measure
        session.accepted_inspired_mitigation_group_pathways = {
            "Low-income tenants": "Bill support reduces their energy costs."
        }
        service._assess_measure_policy_and_mechanisms = AsyncMock(return_value=({}, []))
        service._continue_with_knowledge_base_evidence_or_request_user_evidence = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1", step="mitigation_evidence_decision",
                bot_message="Evidence", session=session.summary(),
            )
        )
        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat", new=AsyncMock(
            side_effect=AssertionError("Reviewed pathways should be carried forward")
        )):
            response = asyncio.run(service._start_guided_mitigation_flow(
                "session-1", session, measure, "Bill support lowers costs."
            ))
        self.assertEqual(response.step, "mitigation_evidence_decision")
        self.assertEqual(session.mitigation_target_population, ["Low-income tenants"])
        self.assertEqual(session.mitigation_dg_benefit_explanations, {
            "Low-income tenants": "Bill support reduces their energy costs."
        })

    def test_guided_flow_discards_reviewed_pathways_when_measure_changes(self):
        service = ChatService.__new__(ChatService)
        session = self._session()
        session.accepted_inspired_mitigation_measure = "Offer targeted bill support."
        session.accepted_inspired_mitigation_group_pathways = {
            "Low-income tenants": "Bill support reduces their energy costs."
        }
        service._assess_measure_policy_and_mechanisms = AsyncMock(return_value=({}, []))
        service._mitigation_target_population_labels = lambda current: ["Low-income tenants"]
        service._mitigation_dg_benefit_reasons = AsyncMock(return_value={})
        response = asyncio.run(service._start_guided_mitigation_flow(
            "session-1", session, "Offer training only.", "Training explains costs."
        ))
        self.assertEqual(response.step, "mitigation_dg_input")
        self.assertIn("Describe the pathway", response.bot_message)
        self.assertIsNone(session.accepted_inspired_mitigation_group_pathways)
        self.assertIsNone(session.mitigation_dg_benefit_explanations)

    def test_yes_continues_with_the_confirmed_measure(self):
        engine = _InspiredMeasureEngine()
        session = self._session()
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Create a tenant energy advice service."
        session.pending_inspired_mitigation_reason = "Help tenants compare suppliers."
        session.pending_inspired_mitigation_description = "Advice helps tenants with electricity costs."
        session.pending_inspired_mitigation_groups = ["Low-income tenants"]
        session.pending_inspired_mitigation_group_pathways = {
            "Low-income tenants": "Advice helps tenants compare suppliers."
        }

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

        self.assertEqual(response.step, "policy_adaptation_question")
        self.assertEqual(response.input_mode, "textarea")
        self.assertIn("Question 1 of 3", response.bot_message)
        self.assertIn("limitations or gaps", response.bot_message)
        self.assertEqual(response.options, POLICY_ADAPTATION_SKIP_OPTIONS)
        self.assertEqual(session.phase, "policy_adaptation_question")
        self.assertIsNone(session.pending_inspired_mitigation_measure)
        self.assertIsNone(session.pending_inspired_mitigation_description)
        self.assertIsNone(session.pending_inspired_mitigation_groups)
        self.assertIsNone(session.new_policy_inspiration)

    def test_no_branch_asks_three_questions_and_hands_off_answers(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[
            {"kind": "D4.2", "title": "Policy factsheet", "content": "Access gap."}
        ])
        session = self._session()
        engine._capture_mitigation_measure = AsyncMock(return_value=ChatResponse(
            session_id="session-1", step="mitigation_clarity", bot_message="Review",
            session=session.summary(),
        ))
        session.phase = "inspired_mitigation_confirmation"
        session.pending_inspired_mitigation_measure = "Suggested measure"
        asyncio.run(engine._handle_inspired_mitigation_confirmation("session-1", session, "No"))
        responses = [
            {**POLICY_ALIGNED, "status": "clear", "support_source_ids": [1]},
            {**POLICY_ALIGNED, "status": "clear", "support_source_ids": [1]},
            {**POLICY_ALIGNED, "status": "clear", "support_source_ids": [1],
             "target_groups": ["Low-income tenants"]},
        ]
        answers = [
            "The eligibility rule excludes renters and raises their electricity costs.",
            "Expand eligibility to renters and provide targeted bill support.",
            "Low-income tenants should be included because they cannot apply directly.",
        ]
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            side_effect=[json.dumps(item) for item in responses]
        )):
            first = asyncio.run(engine._handle_policy_adaptation_answer("session-1", session, answers[0]))
            second = asyncio.run(engine._handle_policy_adaptation_answer("session-1", session, answers[1]))
            final = asyncio.run(engine._handle_policy_adaptation_answer("session-1", session, answers[2]))
        self.assertIn("Question 2 of 3", first.bot_message)
        self.assertIn("Question 3 of 3", second.bot_message)
        self.assertEqual(final.step, "mitigation_clarity")
        self.assertEqual(session.policy_adaptation_answers, answers)
        self.assertEqual(session.mitigation_target_population, ["Low-income tenants"])
        self.assertIn(answers[1], engine._capture_mitigation_measure.await_args.args[2])
        self.assertIn(answers[0], engine._capture_mitigation_measure.await_args.args[2])

    def test_skip_all_questions_returns_to_measure_input_from_question_or_evidence(self):
        engine = _InspiredMeasureEngine()
        for phase in ("policy_adaptation_question", "policy_adaptation_evidence"):
            with self.subTest(phase=phase):
                session = self._session()
                session.phase = phase
                session.policy_adaptation_index = 1
                session.policy_adaptation_answers = ["An earlier answer"]
                session.policy_adaptation_evidence = ["An earlier source"]
                session.policy_adaptation_pending_answer = "A pending answer"
                shown = engine._policy_adaptation_question_response(
                    "session-1", session, evidence=phase == "policy_adaptation_evidence"
                )
                self.assertEqual(shown.options, POLICY_ADAPTATION_SKIP_OPTIONS)
                with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
                    side_effect=AssertionError("Skip must not assess the answer")
                )):
                    response = asyncio.run(engine._handle_policy_adaptation_answer(
                        "session-1", session, POLICY_ADAPTATION_SKIP_OPTIONS[0].label
                    ))
                self.assertEqual(response.step, "mitigation_measure")
                self.assertEqual(response.input_mode, "mitigation_measure")
                self.assertEqual(response.options, [])
                self.assertIn("Questions skipped", response.bot_message)
                self.assertEqual(session.policy_adaptation_answers, [])
                self.assertEqual(session.policy_adaptation_evidence, [])
                self.assertIsNone(session.policy_adaptation_pending_answer)
                self.assertEqual(session.selected_hazard, "Rising electricity costs")

    def test_no_branch_clarifies_and_requests_missing_evidence(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.phase = "policy_adaptation_question"
        unclear = {**POLICY_ALIGNED, "status": "clarify", "clarification": "Which eligibility rule excludes tenants?",
                   "support_source_ids": []}
        clear = {**POLICY_ALIGNED, "status": "clear", "support_source_ids": []}
        evidence_review = {**POLICY_ALIGNED, "status": "clear", "support_source_ids": [],
                           "user_evidence_relevant": True}
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            side_effect=[json.dumps(item) for item in (unclear, clear, evidence_review)]
        )):
            clarification = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session, "The policy is unfair to tenants."
            ))
            evidence = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session,
                "The policy excludes renters from grants, leaving energy bills high."
            ))
            next_question = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session,
                "Source: eligibility report. Finding: renters cannot apply. This supports the exclusion gap."
            ))
        self.assertIn("Which eligibility rule", clarification.bot_message)
        self.assertEqual(session.policy_adaptation_answers, [
            "The policy excludes renters from grants, leaving energy bills high."
        ])
        self.assertIn("knowledge-base evidence", evidence.bot_message)
        self.assertIn("Question 2 of 3", next_question.bot_message)
        self.assertIn("eligibility report", session.policy_adaptation_evidence[0])

    def test_no_branch_rejects_irrelevant_user_evidence(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.phase = "policy_adaptation_question"
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            side_effect=[
                json.dumps({**POLICY_ALIGNED, "status": "clear", "support_source_ids": []}),
                json.dumps({**POLICY_ALIGNED, "status": "clear", "support_source_ids": [],
                            "user_evidence_relevant": False,
                            "clarification": "This source does not describe the policy gap."}),
            ]
        )):
            asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session, "The eligibility rule excludes tenants from grants."
            ))
            response = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session, "Source: a report on unrelated road traffic."
            ))
        self.assertEqual(response.step, "policy_adaptation_evidence")
        self.assertIn("does not describe the policy gap", response.bot_message)
        self.assertFalse(session.policy_adaptation_answers)
        self.assertEqual(session.policy_adaptation_index, 0)

    def test_policy_relevance_failure_asks_for_specific_policy_link(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.phase = "policy_adaptation_question"
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            return_value=json.dumps({
                "status": "clear", "policy_relevance": "unclear",
                "policy_basis": "none", "policy_link": "", "policy_source_ids": [],
                "support_source_ids": [],
            })
        )):
            response = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session,
                "The funding condition makes energy costs higher for renters."
            ))
        self.assertIn("Clean Energy Access Act", response.bot_message)
        self.assertIn("provision, eligibility rule, or implementation gap", response.bot_message)
        self.assertEqual(session.phase, "policy_adaptation_question")
        self.assertFalse(session.policy_adaptation_answers)

    def test_policy_relevance_uses_supported_policy_source_without_title_in_answer(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[
            {"kind": "D4.2", "title": "Policy factsheet",
             "content": "Existing Gaps: renters cannot access grants."}
        ])
        session = self._session()
        session.phase = "policy_adaptation_question"
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=AsyncMock(
            return_value=json.dumps({
                "status": "clear", "policy_relevance": "aligned",
                "policy_basis": "source", "policy_link": "Renters are excluded from grants.",
                "policy_source_ids": [1], "support_source_ids": [1],
            })
        )):
            response = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session,
                "Renters cannot access the grants and face higher electricity costs."
            ))
        self.assertIn("Question 2 of 3", response.bot_message)
        self.assertEqual(session.policy_adaptation_index, 1)

    def test_policy_answer_retries_unreadable_assessment_without_losing_answer(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[
            {"kind": "D4.2", "title": "Policy factsheet",
             "content": "Existing Gaps: renters cannot access grants."}
        ])
        session = self._session()
        session.phase = "policy_adaptation_question"
        answer = "Renters cannot access the grants and face higher electricity costs."
        mocked_llm = AsyncMock(side_effect=[
            "This is not JSON",
            json.dumps({
                "status": "clear", "policy_relevance": "aligned",
                "policy_basis": "source", "policy_link": "Renters are excluded from grants.",
                "policy_source_ids": [1], "support_source_ids": [1],
            }),
        ])
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session, answer
            ))
        self.assertIn("Question 2 of 3", response.bot_message)
        self.assertEqual(session.policy_adaptation_answers, [answer])
        self.assertEqual(mocked_llm.await_count, 2)

    def test_policy_answer_remains_pending_when_assessment_is_unreadable(self):
        engine = _InspiredMeasureEngine()
        engine._policy_adaptation_sources = AsyncMock(return_value=[])
        session = self._session()
        session.phase = "policy_adaptation_question"
        mocked_llm = AsyncMock(return_value="This is not JSON")
        with patch("app.services.chat_mitigation_steps.ask_llm_chat", new=mocked_llm):
            response = asyncio.run(engine._handle_policy_adaptation_answer(
                "session-1", session,
                "Renters cannot access the grants and face higher electricity costs."
            ))
        self.assertEqual(response.step, "policy_adaptation_question")
        self.assertIn("Your answer was not rejected", response.bot_message)
        self.assertEqual(session.policy_adaptation_index, 0)
        self.assertFalse(session.policy_adaptation_answers)
        self.assertEqual(mocked_llm.await_count, 2)

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
