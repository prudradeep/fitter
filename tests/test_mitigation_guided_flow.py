import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas import ChatResponse
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


def run(coro):
    return asyncio.run(coro)


class MitigationGuidedFlowTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(ChatService)
        self.service.invalid_message = "Invalid selection"
        self.session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Low-income households lose access to affordable heat",
        )

    def test_mechanism_first_step_shows_general_guidance_and_selectable_mechanisms(self):
        self.service._practical_policy_recommendations = AsyncMock(
            return_value="## General considerations\n\n- Protect affordability."
        )
        self.service._mitigation_mechanism_planning_overview = AsyncMock(
            return_value={
                "general_suggestions": ["Phase support before costs are passed on."],
                "mechanisms": [
                    {
                        "mechanism": "Up-front retrofit costs exclude low-income households",
                        "policy_title": "Minimum energy performance standards",
                        "causal_linkage": "Compliance costs are passed through before support arrives.",
                        "considerations": ["Coordinate grants with compliance dates."],
                        "mitigation_suggestions": ["Provide advance means-tested grants."],
                    }
                ],
            }
        )

        response = run(
            self.service._mitigation_mechanism_selection_step(
                "session-1", self.session
            )
        )

        self.assertEqual(response.step, "mitigation_mechanism_selection")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(
            [option.label for option in response.options],
            ["Up-front retrofit costs exclude low-income households"],
        )
        self.assertIn("General considerations", response.bot_message)
        self.assertIn("Mechanisms leading to the hazard", response.bot_message)
        self.assertIn("General suggestions for mitigation", response.bot_message)
        self.assertIn("Which mechanism", response.bot_message)

    def test_selecting_offered_mechanism_checks_policy_before_guidance(self):
        mechanism = "Up-front retrofit costs exclude low-income households"
        self.session.phase = "mitigation_mechanism_selection"
        self.session.mitigation_mechanism_candidates = [
            {
                "mechanism": mechanism,
                "policy_title": "Minimum energy performance standards",
                "causal_linkage": "Compliance costs reach households before financial support.",
                "considerations": ["Align support and compliance timing."],
                "mitigation_suggestions": ["Offer advance grants."],
            }
        ]
        expected = ChatResponse(
            session_id="session-1",
            step="mitigation_policy_confirmation",
            bot_message="Confirm policy",
            options=[],
            session=self.session.summary(),
        )
        self.service._start_mitigation_policy_gate = AsyncMock(return_value=expected)

        response = run(
            self.service._handle_mitigation_mechanism_selection(
                "session-1", self.session, mechanism
            )
        )

        self.assertEqual(response.step, "mitigation_policy_confirmation")
        self.assertEqual(self.session.selected_mitigation_mechanism, mechanism)
        self.assertIsNone(self.session.selected_mitigation_policy)
        self.service._start_mitigation_policy_gate.assert_awaited_once_with(
            "session-1", self.session
        )

    def test_free_text_mechanism_is_normalized_then_checks_policy(self):
        self.session.phase = "mitigation_mechanism_selection"
        self.session.mitigation_mechanism_candidates = []
        self.service._guided_text_review = AsyncMock(
            return_value={
                "clear": True,
                "normalized_text": "Landlords pass retrofit costs through higher rents",
            }
        )
        self.service._mitigation_specific_mechanism_guidance = AsyncMock(
            return_value={
                "mechanism": "Landlords pass retrofit costs through higher rents",
                "policy_title": "Building renovation requirement",
                "causal_linkage": "Required renovation can create rent pass-through pressure.",
                "considerations": ["Monitor post-renovation rent increases."],
                "mitigation_suggestions": ["Add rent and anti-displacement protections."],
            }
        )
        self.service._start_mitigation_policy_gate = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="mitigation_policy_reference",
                bot_message="Provide policy",
                options=[],
                session=self.session.summary(),
            )
        )

        response = run(
            self.service._handle_mitigation_mechanism_selection(
                "session-1", self.session, "owners make tenants pay for renovation"
            )
        )

        self.assertEqual(response.step, "mitigation_policy_reference")
        self.assertEqual(
            self.session.selected_mitigation_mechanism,
            "Landlords pass retrofit costs through higher rents",
        )
        self.service._mitigation_specific_mechanism_guidance.assert_awaited_once()

    def test_kb_policy_is_shown_for_confirmation_before_guidance(self):
        mechanism = "Up-front retrofit costs exclude low-income households"
        self.session.selected_mitigation_mechanism = mechanism
        self.session.mitigation_mechanism_guidance = {
            "policy_title": "Building Renovation Policy",
            "considerations": ["Coordinate support timing."],
            "mitigation_suggestions": ["Provide advance grants."],
        }
        result = {
            "title": "Building Renovation Policy",
            "source_uri": "https://example.test/policy",
            "page_number": 4,
            "content": "The policy requires renovation standards and financial support.",
        }
        self.service._shared_knowledge_results = AsyncMock(return_value=[result])
        self.service._mitigation_policy_reference_results = MagicMock(return_value=[])
        self.service.grounding_models = MagicMock()
        self.service.grounding_models.ground_results = AsyncMock(return_value=[result])

        with (
            patch(
                "app.services.chat_mitigation_creation_guided.validate_policy_reference_twin_transition",
                new=AsyncMock(return_value={"related": True, "reason": "Relevant policy."}),
            ),
            patch(
                "app.services.chat_mitigation_creation_guided.summarize_custom_hazard_supporting_policy",
                new=AsyncMock(
                    return_value={
                        "supported": True,
                        "summary": "The policy establishes renovation requirements.",
                        "policy_details": "Financial support accompanies the requirements.",
                        "causal_linkage": "Renovation duty -> upfront costs -> exclusion risk",
                        "reason": "Supported.",
                    }
                ),
            ),
        ):
            response = run(
                self.service._start_mitigation_policy_gate("session-1", self.session)
            )

        self.assertEqual(response.step, "mitigation_policy_confirmation")
        self.assertIn("Building Renovation Policy", response.bot_message)
        self.assertIn("Policy-to-mechanism-to-hazard linkage", response.bot_message)
        self.assertIsNone(self.session.selected_mitigation_policy)

    def test_missing_kb_policy_requests_policy_document(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.service._shared_knowledge_results = AsyncMock(return_value=[])
        self.service._mitigation_policy_reference_results = MagicMock(return_value=[])
        self.service.grounding_models = MagicMock()
        self.service.grounding_models.ground_results = AsyncMock(return_value=[])

        response = run(
            self.service._start_mitigation_policy_gate("session-1", self.session)
        )

        self.assertEqual(response.step, "mitigation_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertIn("could not find", response.bot_message)

    def test_kb_result_without_supported_causal_link_is_treated_as_not_found(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        result = {
            "title": "General Energy Report",
            "content": "General discussion of energy efficiency.",
        }
        self.service._shared_knowledge_results = AsyncMock(return_value=[result])
        self.service._mitigation_policy_reference_results = MagicMock(return_value=[])
        self.service.grounding_models = MagicMock()
        self.service.grounding_models.ground_results = AsyncMock(return_value=[result])

        with (
            patch(
                "app.services.chat_mitigation_creation_guided.validate_policy_reference_twin_transition",
                new=AsyncMock(return_value={"related": True, "reason": "Broadly relevant."}),
            ),
            patch(
                "app.services.chat_mitigation_creation_guided.summarize_custom_hazard_supporting_policy",
                new=AsyncMock(
                    return_value={
                        "supported": False,
                        "causal_linkage": "",
                        "reason": "No supporting policy provision was found.",
                    }
                ),
            ),
        ):
            response = run(
                self.service._start_mitigation_policy_gate("session-1", self.session)
            )

        self.assertEqual(response.step, "mitigation_policy_reference")
        self.assertNotEqual(response.step, "mitigation_policy_retry")

    def test_confirmed_policy_then_shows_mechanism_guidance(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.session.pending_mitigation_policy = {
            "title": "Building Renovation Policy",
            "causal_linkage": "Renovation duty -> higher costs -> arrears",
        }
        self.session.mitigation_mechanism_guidance = {
            "considerations": ["Coordinate support timing."],
            "mitigation_suggestions": ["Provide advance grants."],
        }

        response = run(
            self.service._handle_mitigation_policy_confirmation(
                "session-1", self.session, "Yes, use this policy"
            )
        )

        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(response.input_mode, "mitigation_measure")
        self.assertEqual(
            self.session.selected_mitigation_policy, "Building Renovation Policy"
        )
        self.assertIn("Guidance for the selected mechanism", response.bot_message)

    def test_rejected_kb_policy_requests_another_policy(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.session.pending_mitigation_policy = {
            "title": "Building Renovation Policy",
            "causal_linkage": "Renovation duty -> higher costs -> arrears",
        }

        response = run(
            self.service._handle_mitigation_policy_confirmation(
                "session-1", self.session, "No, provide another policy"
            )
        )

        self.assertEqual(response.step, "mitigation_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertIsNone(self.session.selected_mitigation_policy)

    def test_uploaded_policy_without_linkage_offers_clarify_or_upload_again(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.service._policy_reference_context = AsyncMock(return_value="Readable policy text")

        with (
            patch(
                "app.services.chat_mitigation_creation_guided.validate_policy_reference_twin_transition",
                new=AsyncMock(return_value={"related": True, "reason": "Relevant."}),
            ),
            patch(
                "app.services.chat_mitigation_creation_guided.summarize_custom_hazard_supporting_policy",
                new=AsyncMock(
                    return_value={
                        "supported": False,
                        "causal_linkage": "",
                        "reason": "No provision connects the policy to this mechanism.",
                    }
                ),
            ),
        ):
            response = run(
                self.service._handle_mitigation_policy_reference(
                    "session-1",
                    self.session,
                    "Policy reference file: policy.pdf\nPolicy reference document ID: doc-1",
                )
            )

        self.assertEqual(response.step, "mitigation_policy_retry")
        self.assertTrue(response.error)
        self.assertEqual(
            [option.label for option in response.options],
            ["Clarify the relevance", "Provide policy again"],
        )

    def test_uploaded_supported_policy_is_shown_for_confirmation(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.service._policy_reference_context = AsyncMock(return_value="Readable policy text")

        with (
            patch(
                "app.services.chat_mitigation_creation_guided.validate_policy_reference_twin_transition",
                new=AsyncMock(return_value={"related": True, "reason": "Relevant."}),
            ),
            patch(
                "app.services.chat_mitigation_creation_guided.summarize_custom_hazard_supporting_policy",
                new=AsyncMock(
                    return_value={
                        "supported": True,
                        "summary": "The policy requires home renovation.",
                        "policy_details": "Article 4 establishes minimum standards.",
                        "causal_linkage": "Article 4 -> retrofit costs -> arrears risk",
                        "reason": "Supported.",
                    }
                ),
            ),
        ):
            response = run(
                self.service._handle_mitigation_policy_reference(
                    "session-1",
                    self.session,
                    "Policy reference URL: https://example.test/policy\n"
                    "Policy reference document ID: doc-1",
                )
            )

        self.assertEqual(response.step, "mitigation_policy_confirmation")
        self.assertIn("Article 4", response.bot_message)

    def test_policy_text_clarification_is_revalidated_before_confirmation(self):
        self.session.selected_mitigation_mechanism = "Retrofit costs increase arrears"
        self.session.pending_mitigation_policy_context = "Readable policy text"
        self.session.pending_mitigation_policy = {
            "title": "Building Renovation Policy",
            "sources": [],
            "origin": "user-provided policy",
        }
        policy_validator = AsyncMock(
            return_value={
                "supported": True,
                "summary": "The policy requires home renovation.",
                "policy_details": "Article 4 establishes minimum standards.",
                "causal_linkage": "Article 4 -> retrofit costs -> arrears risk",
                "reason": "Supported after clarification.",
            }
        )

        with (
            patch(
                "app.services.chat_mitigation_creation_guided.validate_policy_reference_twin_transition",
                new=AsyncMock(return_value={"related": True, "reason": "Relevant."}),
            ),
            patch(
                "app.services.chat_mitigation_creation_guided.summarize_custom_hazard_supporting_policy",
                new=policy_validator,
            ),
        ):
            response = run(
                self.service._handle_mitigation_policy_clarification(
                    "session-1",
                    self.session,
                    "Article 4 makes landlords pay upfront before grants arrive.",
                )
            )

        self.assertEqual(response.step, "mitigation_policy_confirmation")
        self.assertEqual(
            policy_validator.await_args.kwargs["relevance_clarification"],
            "Article 4 makes landlords pay upfront before grants arrive.",
        )

    def test_selected_mechanism_is_preserved_when_measure_is_validated(self):
        self.session.selected_mitigation_mechanism = (
            "Up-front retrofit costs exclude low-income households"
        )
        self.session.mitigation_mechanisms = [self.session.selected_mitigation_mechanism]
        self.session.mitigation_mechanism_guidance = {
            "causal_linkage": "Compliance costs arrive before household support."
        }
        self.service._assess_measure_policy_and_mechanisms = AsyncMock(
            return_value=({"relevant": True, "reason": "Relevant"}, [])
        )

        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"reflection":"Advance grants remove the up-front cost barrier."}'
                )
            ),
        ):
            response = run(
                self.service._start_guided_mitigation_flow(
                    "session-1",
                    self.session,
                    "Provide advance means-tested retrofit grants",
                )
            )

        self.assertEqual(response.step, "mitigation_mechanism_reflection_review")
        self.assertEqual(
            self.session.mitigation_mechanisms,
            ["Up-front retrofit costs exclude low-income households"],
        )
        self.assertEqual(
            self.session.pending_mitigation_reason,
            "Compliance costs arrive before household support.",
        )

    def test_confirmed_flow_reaches_self_evaluation_after_equity(self):
        mechanism_result = (
            '{"relevant": true, "reason": "Relevant", '
            '"mechanisms": ["Up-front retrofit costs exclude low-income households"]}'
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value=mechanism_result),
        ):
            response = run(
                self.service._start_guided_mitigation_flow(
                    "session-1",
                    self.session,
                    "Provide means-tested grants for home insulation",
                )
            )
        self.assertEqual(response.step, "mitigation_mechanism_confirmation")

        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"reflection":"Means-tested grants remove the cost barrier."}'
                )
            ),
        ):
            response = run(
                self.service._handle_mitigation_mechanism_confirmation(
                    "session-1", self.session, "Confirm mechanisms"
                )
            )
        self.assertEqual(response.step, "mitigation_mechanism_reflection_review")

        response = run(
            self.service._handle_mitigation_mechanism_reflection_review(
                "session-1", self.session, "Confirm reflection"
            )
        )
        self.assertEqual(response.step, "mitigation_evidence_decision")

        self.service._identify_mitigation_policy_effects = AsyncMock(return_value=[])
        response = run(
            self.service._handle_mitigation_evidence_decision("session-1", self.session, "No")
        )
        self.assertEqual(response.step, "mitigation_summary_review")
        self.assertIn("Up-front retrofit costs", response.bot_message)

        response = run(
            self.service._handle_mitigation_summary_review(
                "session-1", self.session, "Confirm summary"
            )
        )
        self.assertEqual(response.step, "mitigation_inspiration_review")

        self.service._mitigation_target_population_labels = MagicMock(
            return_value=["Low-income households"]
        )
        response = run(
            self.service._handle_mitigation_inspiration_review(
                "session-1", self.session, "Discard inspirations"
            )
        )
        self.assertEqual(response.step, "mitigation_dg_review")

        response = run(
            self.service._handle_mitigation_dg_review(
                "session-1", self.session, "Confirm disadvantaged groups"
            )
        )
        self.assertEqual(response.step, "mitigation_dg_evidence_decision")

        response = run(
            self.service._handle_mitigation_dg_evidence_decision(
                "session-1", self.session, "No DG evidence"
            )
        )
        self.assertEqual(response.step, "mitigation_dg_summary_review")

        response = run(
            self.service._handle_mitigation_dg_summary_review(
                "session-1", self.session, "Confirm DG summary"
            )
        )
        self.assertEqual(response.step, "mitigation_equity")
        self.assertIn(
            "How is the proposed mitigation measure equitable for the different disadvantaged groups?",
            response.bot_message,
        )
        self.assertEqual([option.label for option in response.options], ["Skip this"])

        clarity_result = (
            '{"clear": true, "normalized_text": '
            '"Means testing removes up-front costs for low-income households", '
            '"clarification_question": ""}'
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value=clarity_result),
        ):
            response = run(
                self.service._handle_mitigation_equity(
                    "session-1",
                    self.session,
                    "Means testing removes up-front costs for low-income households",
                )
            )
        self.assertEqual(response.step, "mitigation_final_summary_review")

        expected = ChatResponse(
            session_id="session-1",
            step="evaluation_question",
            bot_message="Self evaluation",
            options=[],
            session=self.session.summary(),
            error=False,
        )
        self.service._validate_frozen_mitigation_inputs = AsyncMock(return_value=expected)
        response = run(
            self.service._handle_mitigation_final_summary_review(
                "session-1", self.session, "Confirm final summary"
            )
        )
        self.assertEqual(response.step, "evaluation_question")
        self.service._validate_frozen_mitigation_inputs.assert_awaited_once()

    def test_disadvantaged_groups_come_from_selected_hazard_profiles(self):
        self.session.pending_mitigation_measure = "Upgrade local grid infrastructure"
        self.session.mitigation_mechanisms = ["Dependence on external providers"]
        self.service._mitigation_target_population_labels = MagicMock(
            return_value=["Low-income households", "Rural residents"]
        )
        self.service._infer_mitigation_target_population_from_inputs = AsyncMock()

        response = run(
            self.service._guided_dg_suggestion_step("session-1", self.session)
        )

        self.assertEqual(response.step, "mitigation_dg_review")
        self.assertEqual(
            self.session.mitigation_target_population,
            ["Low-income households", "Rural residents"],
        )
        self.assertIn("Low-income households", response.bot_message)
        self.assertIn("Rural residents", response.bot_message)
        self.service._mitigation_target_population_labels.assert_called_once_with(
            self.session
        )
        self.service._infer_mitigation_target_population_from_inputs.assert_not_awaited()

    def test_stored_custom_hazard_profiles_supply_disadvantaged_groups(self):
        hazard = "Regional energy affordability shock"
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard=hazard,
            hazard_profiles={
                hazard: [
                    {"name": "Low-income households"},
                    {"name": "Tenant households"},
                ]
            },
        )

        groups = self.service._mitigation_target_population_labels(session)

        self.assertEqual(groups, ["Low-income households", "Tenant households"])

    def test_equity_question_can_be_skipped_without_storing_button_text(self):
        self.session.phase = "mitigation_equity"
        self.session.pending_mitigation_measure = "Provide means-tested retrofit grants"
        self.session.mitigation_target_population = ["Low-income households"]
        self.service._guided_text_review = AsyncMock()
        self.assertTrue(self.service._matches_current_step_option(self.session, "Skip this"))

        response = run(
            self.service._handle_mitigation_equity(
                "session-1", self.session, "Skip this"
            )
        )

        self.assertEqual(response.step, "mitigation_final_summary_review")
        self.assertIsNone(self.session.mitigation_equity)
        self.assertTrue(self.session.mitigation_equity_skipped)
        self.assertIn("Not provided (skipped)", response.bot_message)
        self.service._guided_text_review.assert_not_awaited()

    def test_irrelevant_evidence_requests_clarification_and_remains_optional(self):
        self.session.pending_mitigation_measure = "Provide advance retrofit grants"
        self.session.pending_mitigation_reason = "Reduce the up-front cost barrier"
        self.session.mitigation_mechanisms = ["Up-front retrofit cost barrier"]
        self.service._guided_evidence_relevance = AsyncMock(
            return_value={
                "outcome": "ambiguous",
                "relevant": False,
                "reason": "Only general background is provided.",
                "clarification_question": "How does this source support the grant mechanism?",
            }
        )

        response = run(
            self.service._guided_after_evidence(
                "session-1", self.session, "https://example.test/evidence"
            )
        )

        self.assertEqual(response.step, "mitigation_evidence")
        self.assertTrue(response.error)
        self.assertIn("How does this source", response.bot_message)
        self.assertIn("Skip", [option.label for option in response.options])

    def test_measure_evidence_relevance_does_not_use_confirmed_mechanisms(self):
        measure = "Provide advance retrofit grants"
        mechanism = "An unrelated confirmed mechanism"
        self.session.pending_mitigation_measure = measure
        self.session.mitigation_mechanisms = [mechanism]
        self.service._mitigation_evidence_context = AsyncMock(
            return_value="The source documents advance retrofit grants."
        )

        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"outcome":"relevant","relevant":true,'
                    '"reason":"The source supports the proposed grant.",'
                    '"clarification_question":""}'
                )
            ),
        ) as llm:
            result = run(
                self.service._guided_evidence_relevance(
                    self.session, "https://example.test/grant-evidence"
                )
            )

        self.assertTrue(result["relevant"])
        context_call = self.service._mitigation_evidence_context.await_args
        self.assertEqual(context_call.args[2], "")
        self.assertEqual(context_call.kwargs["retrieval_query"], measure)
        validation_message = llm.await_args.kwargs["messages"][0]["content"]
        self.assertIn(f"Measure: {measure}", validation_message)
        self.assertNotIn("Mechanisms:", validation_message)
        self.assertNotIn(mechanism, validation_message)

    def test_agreed_policy_effect_requires_additional_mitigation_then_summarizes(self):
        self.session.mitigation_policy_effects = [
            {
                "policy_aspect": "Rent regulation",
                "problem": "Renovation costs could be passed through as rent increases",
                "causal_pathway": "Grant gaps leave residual costs for landlords",
                "basis": "The mapped policy links renovation obligations and affordability",
                "user_position": "pending",
                "additional_mitigation": "",
                "disagreement_reason": "",
            }
        ]
        self.session.mitigation_policy_effect_index = 0

        response = run(
            self.service._handle_mitigation_policy_effect_review(
                "session-1", self.session, "Yes"
            )
        )
        self.assertEqual(response.step, "mitigation_policy_effect_mitigation")

        self.service._guided_text_review = AsyncMock(
            return_value={
                "clear": True,
                "normalized_text": "Cap post-renovation rent increases for five years",
            }
        )
        response = run(
            self.service._handle_mitigation_policy_effect_mitigation(
                "session-1", self.session, "protect tenants"
            )
        )

        self.assertEqual(response.step, "mitigation_summary_review")
        self.assertEqual(
            self.session.mitigation_policy_effects[0]["additional_mitigation"],
            "Cap post-renovation rent increases for five years",
        )
        self.assertIn("Other policy effects reviewed", response.bot_message)

    def test_disagreed_policy_effect_requires_specific_reason_with_unlimited_clarification(self):
        self.session.mitigation_policy_effects = [
            {
                "problem": "Benefits could bypass rural households",
                "user_position": "pending",
                "additional_mitigation": "",
                "disagreement_reason": "",
            }
        ]
        self.session.mitigation_policy_effect_index = 0
        response = run(
            self.service._handle_mitigation_policy_effect_review(
                "session-1", self.session, "No"
            )
        )
        self.assertEqual(response.step, "mitigation_policy_effect_disagreement")

        self.service._guided_text_review = AsyncMock(
            return_value={
                "clear": False,
                "clarification_question": "Which delivery safeguard reaches rural households?",
            }
        )
        for _ in range(6):
            response = run(
                self.service._handle_mitigation_policy_effect_disagreement(
                    "session-1", self.session, "It will be fine"
                )
            )
            self.assertEqual(response.step, "mitigation_policy_effect_disagreement")
        self.assertEqual(self.session.mitigation_policy_effect_index, 0)

    def test_ambiguous_mechanism_input_repeats_without_a_turn_cap(self):
        self.session.phase = "mitigation_mechanism_input"
        unclear = (
            '{"clear": false, "normalized_text": "", '
            '"clarification_question": "What specific causal process does it change?"}'
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value=unclear),
        ):
            for _ in range(7):
                response = run(
                    self.service._handle_mitigation_mechanism_input(
                        "session-1", self.session, "It generally helps"
                    )
                )
                self.assertEqual(response.step, "mitigation_mechanism_input")
                self.assertFalse(response.error)
        self.assertEqual(self.session.phase, "mitigation_mechanism_input")

    def test_open_labs_candidates_include_proposals_and_adjustments(self):
        self.service._ranked_new_policy_suggestions = lambda session, limit: [
            {"policy_title": "New A", "policy_type": "New policy proposal"},
            {"policy_title": "New B", "policy_type": "New policy proposal"},
            {"policy_title": "Adjust A", "policy_type": "Adjustment to existing policy"},
        ]

        candidates = self.service._guided_open_labs_candidates(self.session)

        self.assertEqual(
            [candidate["policy_title"] for candidate in candidates],
            ["New A", "New B", "Adjust A"],
        )

    def test_invalid_confirmation_stays_on_guided_step(self):
        self.session.phase = "mitigation_mechanism_confirmation"
        self.session.mitigation_mechanisms = ["A concrete cost pass-through mechanism"]

        response = run(
            self.service._handle_mitigation_mechanism_confirmation(
                "session-1", self.session, "Unrelated response"
            )
        )

        self.assertEqual(response.step, "mitigation_mechanism_confirmation")
        self.assertTrue(response.error)

    def test_confirmed_equity_bypasses_legacy_review_and_starts_self_evaluation(self):
        self.session.mitigation_measure = "Means-tested insulation grants"
        self.session.mitigation_reason = "Reduces up-front retrofit cost exclusion"
        self.session.mitigation_target_population = ["Low-income households"]
        self.session.mitigation_equity = "The grant removes up-front costs."
        self.service._selected_hazard_reference = MagicMock(
            return_value={
                "user_session_id": "user-session-1",
                "user_hazard_id": None,
                "custom_hazard_id": None,
                "system_hazard_id": "hazard-1",
                "additional_hazard_id": None,
            }
        )
        self.service._store_mitigation_measure = MagicMock(return_value="measure-1")
        self.service._update_mitigation_creation_details = MagicMock()
        self.service._record_activity = MagicMock()
        expected = ChatResponse(
            session_id="session-1",
            step="evaluation_question",
            bot_message="Self evaluation",
            options=[],
            session=self.session.summary(),
            error=False,
        )
        self.service._start_evaluation_questions = MagicMock(return_value=expected)
        self.service._mitigation_review_step = AsyncMock()

        response = run(self.service._finalize_validated_mitigation("session-1", self.session))

        self.assertEqual(response.step, "evaluation_question")
        self.service._start_evaluation_questions.assert_called_once()
        self.service._mitigation_review_step.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
