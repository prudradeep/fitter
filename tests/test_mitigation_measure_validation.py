import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_mitigation_creation import ChatMitigationCreationMixin
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_session import ChatSession
from app.services.validation_service import ChatValidationServiceMixin


class _MitigationMeasureEngine(ChatMitigationStepsMixin, ChatValidationServiceMixin):
    invalid_message = "Invalid"

    def _clear_mitigation_clarity_state(self, session):
        return None

    def _clear_mitigation_validation_state(self, session):
        return None

    def _is_invalid_user_text(self, value):
        return False

    def _local_mitigation_measure_error(self, mitigation_measure):
        return None

    def _local_mitigation_duplicate_check(self, session, mitigation_measure):
        return None

    async def _semantic_mitigation_duplicate_check(self, session, mitigation_measure):
        return None


class _MitigationReviewEngine(ChatMitigationCreationMixin):
    def _is_invalid_user_text(self, value):
        return False

    def _scope_instruction(self, session):
        return "Stay in the selected context."

    async def _sector_prompt_rag_context(self, *args, **kwargs):
        return "- Sector context excerpt."

    async def _mitigation_knowledge_context(self, *args, **kwargs):
        return "- Generic mitigation KB excerpt."

    def _mitigation_measure_examples(self, sector_id):
        return "- Example mitigation measure."

    def _update_mitigation_review_details(self, *args, **kwargs):
        return None

    def _mitigation_target_affected_groups_json(self, session):
        return "[]"

    def _affected_profile_target_population_labels(self, session):
        return []

    def _normalize_population_group_labels(self, labels):
        return list(labels or [])

    def _group_target_population_labels(self, labels):
        return list(labels or [])

    def _grounding_validation_details(self, session):
        return {}

    def _promote_temporary_evidence(self, session):
        return None

    def _evaluation_questions(self):
        return [
            {
                "id": "q1",
                "category": "Feasibility and Implementation",
                "question": "How feasible is the implementation plan?",
            }
        ]

    def _current_evaluation_question(self, session):
        questions = session.evaluation_questions or []
        if session.evaluation_index < 0 or session.evaluation_index >= len(questions):
            return None
        return questions[session.evaluation_index]


class _MitigationClarificationEngine(ChatMitigationCreationMixin, ChatMitigationStepsMixin):
    invalid_message = "Invalid"

    def _is_invalid_user_text(self, value):
        return False

    async def _validate_clarification_answer_quality(self, session, message):
        return {"valid": True, "reason": "Clear."}

    async def _assess_mitigation_clarity(
        self,
        session,
        mitigation_measure,
        reason,
        evidence,
        clarification_answer=None,
    ):
        return {
            "clear": True,
            "dimensions": {
                "specificity": "CLEAR",
                "justification_clarity": "CLEAR",
                "evidence_identifiability": "CLEAR",
            },
            "follow_up_questions": [],
            "frozen_inputs": {
                "measure_description": mitigation_measure,
                "justification": reason,
                "evidence": evidence,
            },
            "reason": "All inputs are clear.",
        }

    async def _validate_frozen_mitigation_inputs(self, *args, **kwargs):
        raise AssertionError("Evidence should be requested before validation.")


class MitigationMeasureValidationTests(unittest.TestCase):
    def test_weak_measure_requests_clarification_before_reason_step(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._capture_mitigation_measure(
                "test-session",
                session,
                "Government should help",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("too vague", response.bot_message)

    def test_restatement_of_hazard_is_invalid(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._capture_mitigation_measure(
                "test-session",
                session,
                "Heat stress",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("restates the hazard", response.bot_message)

    def test_concrete_measure_uses_measure_only_prompt_and_moves_to_clarification(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )
        llm_payload = {
            "status": "VALID",
            "summary": "The measure is concrete and relevant.",
            "checks": {
                "hazard_fit": True,
                "sector_fit": True,
                "country_region_fit": True,
                "twin_transition_fit": True,
                "policy_quality": True,
            },
            "clarification_question": "",
            "suggested_improvement": "",
        }

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(return_value=json.dumps(llm_payload)),
        ) as ask_mock:
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    "Introduce targeted grants for low-income households to install heat pumps.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(
            session.pending_mitigation_measure,
            "Introduce targeted grants for low-income households to install heat pumps.",
        )
        call = ask_mock.await_args.kwargs
        self.assertIn("validate ONLY the mitigation measure itself", call["context"])
        self.assertIn("Be reasonably permissive", call["context"])
        self.assertIn('"country_region_fit": true', call["context"])
        user_content = call["messages"][0]["content"]
        self.assertIn("Mitigation Measure:", user_content)
        self.assertIn("Do NOT evaluate or request the implementation reason or justification.", user_content)
        self.assertNotIn("Reason:", user_content)
        self.assertNotIn("Justification:", user_content)

    def test_short_meaningful_measure_is_sent_to_validator_not_locally_rejected(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
        )
        llm_payload = {
            "status": "VALID",
            "summary": "The measure is meaningful for the context.",
            "checks": {
                "hazard_fit": True,
                "sector_fit": True,
                "country_region_fit": True,
                "twin_transition_fit": True,
                "policy_quality": True,
            },
            "clarification_question": "",
            "suggested_improvement": "",
        }

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(return_value=json.dumps(llm_payload)),
        ) as ask_mock:
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    "Introduce grants",
                )
            )

        self.assertTrue(ask_mock.await_args)
        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_measure, "Introduce grants")

    def test_clarification_completion_moves_to_evidence_question_before_validation(self):
        engine = _MitigationClarificationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="",
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="justification_clarity",
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                "It lowers upfront costs for low-income households affected by energy poverty.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_evidence_decision")
        self.assertEqual(session.phase, "mitigation_evidence_decision")
        self.assertIn("Do you have evidence", response.bot_message)
        self.assertEqual(
            session.pending_mitigation_reason,
            "Clarification: It lowers upfront costs for low-income households affected by energy poverty.",
        )

    def test_practical_considerations_ignore_schema_placeholder_heading(self):
        payload = {
            "title": "# Practical Considerations",
            "themes": [
                {
                    "heading": "## <Dynamic Theme Heading>",
                    "summary": "Placeholder text should not become a checklist item.",
                    "concerns": ["- Placeholder concern."],
                },
                {
                    "heading": "## Targeted Mobility Access",
                    "summary": "Real theme summary.",
                    "concerns": ["- Real implementation concern."],
                },
            ],
        }

        markdown, panel_items = ChatMitigationCreationMixin._practical_considerations_json_to_markdown(
            json.dumps(payload)
        )

        self.assertEqual(panel_items, ["Targeted Mobility Access"])
        self.assertNotIn("Dynamic Theme Heading", markdown)

    def test_extract_suggested_policy_reason_from_why_this_helps(self):
        markdown = (
            "### Regional support package\n"
            "- **Proposal:** Provide targeted retrofit grants.\n"
            "- **Why this helps:** It lowers upfront costs for affected households."
        )

        self.assertEqual(
            ChatMitigationCreationMixin._extract_suggested_policy_reason(markdown),
            "It lowers upfront costs for affected households.",
        )

    def test_mitigation_review_starts_open_discussion_before_evaluation(self):
        engine = _MitigationReviewEngine()
        engine._mitigation_review_response = AsyncMock(
            return_value=(
                "### What is covered\n"
                "- The measure targets affordability.\n\n"
                "### Pros\n"
                "- It is targeted before evaluation."
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            mitigation_validation={},
            mitigation_grounded_synthesis=(
                "Existing grounded synthesis should be context, not the final review."
            ),
        )

        response = asyncio.run(engine._mitigation_review_step("test-session", session))

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_review")
        self.assertEqual(session.phase, "mitigation_review")
        self.assertIn("Concept Comparision", response.bot_message)
        self.assertIn("What is covered", response.bot_message)
        engine._mitigation_review_response.assert_awaited_once()
        prompt = engine._mitigation_review_response.await_args.args[1]
        self.assertIn("Compare the conceptual design", prompt)
        self.assertIn("Do not ask evaluation questions yet", prompt)
        self.assertIn("do not name the source document", prompt)

    def test_mitigation_review_next_step_starts_highest_priority_challenge(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            mitigation_review_analysis=(
                "Pros: targeted. Cons: high administrative burden. Risks: funding gaps."
            ),
        )

        challenge_json = json.dumps(
            [
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "importance": 5,
                    "implementation_impact": 5,
                },
                {
                    "title": "Administrative burden",
                    "category": "Operational",
                    "why_important": "Eligibility checks may delay delivery.",
                    "importance": 3,
                    "implementation_impact": 4,
                },
            ]
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(return_value=challenge_json),
        ):
            response = asyncio.run(
                engine._handle_mitigation_review(
                    "test-session",
                    session,
                    "Move to next step",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_challenge_discussion")
        self.assertEqual(session.phase, "implementation_challenge_discussion")
        self.assertIn("Funding sustainability", response.bot_message)
        self.assertNotIn("Administrative burden</strong>", response.bot_message)

    def test_review_cons_are_consolidated_when_generated_list_is_sparse(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            mitigation_review_analysis=(
                "### Pros\n"
                "- Targeted support.\n\n"
                "### Cons\n"
                "- Administrative burden may delay applications.\n\n"
                "### Risks\n"
                "- Funding may expire before households receive support."
            )
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(return_value="[]"),
        ):
            challenges = asyncio.run(engine._ranked_implementation_challenges(session))

        titles = [challenge["title"] for challenge in challenges]
        self.assertIn("Administrative burden may delay applications", titles)
        self.assertIn("Funding may expire before households receive support", titles)

    def test_implementation_challenge_discussion_resolves_then_assesses(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            implementation_challenges=[
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "importance": 5,
                    "implementation_impact": 5,
                    "status": "unresolved",
                }
            ],
            implementation_challenge_index=0,
            implementation_mitigation_strategy=[],
        )

        responses = [
            json.dumps(
                {
                    "status": "resolved",
                    "evaluation": "The funding plan is concrete enough.",
                    "follow_up_question": "",
                    "mitigation_strategy": (
                        "Use a two-year municipal budget line with quarterly monitoring."
                    ),
                }
            ),
            (
                "## Implementation Readiness Assessment\n\n"
                "### Resolved challenges\n"
                "- Funding sustainability\n\n"
                "### Partially resolved challenges\n"
                "- None\n\n"
                "### Remaining unresolved risks\n"
                "- None\n\n"
                "### Residual implementation concerns\n"
                "- Monitor spending.\n\n"
                "### Recommended improvements\n"
                "- Keep quarterly review.\n\n"
                "### Overall implementation confidence/readiness score\n"
                "- 90/100"
            ),
        ]

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(side_effect=responses),
        ):
            response = asyncio.run(
                engine._handle_implementation_challenge_response(
                    "test-session",
                    session,
                    "Use a two-year municipal budget line with quarterly monitoring.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_readiness_assessment")
        self.assertEqual(session.phase, "implementation_readiness_assessment")
        self.assertEqual(session.implementation_challenges[0]["status"], "resolved")
        self.assertIn("Implementation Readiness Assessment", response.bot_message)

    def test_partial_challenge_can_move_forward_when_enough_information_exists(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            implementation_challenges=[
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "importance": 5,
                    "implementation_impact": 5,
                    "status": "unresolved",
                },
                {
                    "title": "Administrative burden",
                    "category": "Operational",
                    "why_important": "Eligibility checks may delay delivery.",
                    "importance": 4,
                    "implementation_impact": 4,
                    "status": "unresolved",
                },
            ],
            implementation_challenge_index=0,
            implementation_mitigation_strategy=[],
        )

        evaluation_json = json.dumps(
            {
                "status": "partial",
                "ready_to_continue": True,
                "evaluation": "The funding source is named, but long-term renewal remains risky.",
                "follow_up_question": "",
                "mitigation_strategy": "Use existing grant funds for year one.",
            }
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(return_value=evaluation_json),
        ):
            response = asyncio.run(
                engine._handle_implementation_challenge_response(
                    "test-session",
                    session,
                    "Use existing grant funds for year one.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_challenge_discussion")
        self.assertEqual(session.implementation_challenge_index, 1)
        self.assertEqual(session.implementation_challenges[0]["status"], "partial")
        self.assertIn("Administrative burden", response.bot_message)

    def test_string_false_ready_to_continue_keeps_follow_up_on_same_challenge(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            implementation_challenges=[
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "importance": 5,
                    "implementation_impact": 5,
                    "status": "unresolved",
                },
                {
                    "title": "Administrative burden",
                    "category": "Operational",
                    "why_important": "Eligibility checks may delay delivery.",
                    "importance": 4,
                    "implementation_impact": 4,
                    "status": "unresolved",
                },
            ],
            implementation_challenge_index=0,
            implementation_mitigation_strategy=[],
        )

        evaluation_json = json.dumps(
            {
                "status": "partial",
                "ready_to_continue": "false",
                "evaluation": "The answer names funding but not ownership or renewal.",
                "follow_up_question": "Who owns the budget renewal decision?",
                "mitigation_strategy": "Use existing grant funds.",
            }
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(return_value=evaluation_json),
        ):
            response = asyncio.run(
                engine._handle_implementation_challenge_response(
                    "test-session",
                    session,
                    "Use existing grant funds.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_challenge_discussion")
        self.assertEqual(session.implementation_challenge_index, 0)
        self.assertIn("Who owns the budget renewal decision?", response.bot_message)
        self.assertNotIn("Administrative burden", response.bot_message)

    def test_readiness_assessment_can_continue_to_evaluation(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            phase="implementation_readiness_assessment",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
        )

        response = asyncio.run(
            engine._handle_implementation_readiness_action(
                "test-session",
                session,
                "Continue to evaluation",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "evaluation_question")
        self.assertEqual(session.phase, "evaluation_question")
        self.assertIn("How feasible is the implementation plan?", response.bot_message)

    def test_readiness_assessment_can_review_partial_and_unresolved_again(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            phase="implementation_readiness_assessment",
            implementation_challenges=[
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "status": "resolved",
                },
                {
                    "title": "Administrative burden",
                    "category": "Operational",
                    "why_important": "Eligibility checks may delay delivery.",
                    "status": "partial",
                    "mitigation_strategy": "Use existing staff capacity.",
                },
                {
                    "title": "Legal eligibility",
                    "category": "Legal",
                    "why_important": "Eligibility rules may exclude intended groups.",
                    "status": "unresolved",
                },
            ],
            implementation_challenge_index=3,
            implementation_mitigation_strategy=[
                {
                    "challenge": "Administrative burden",
                    "status": "partial",
                    "strategy": "Use existing staff capacity.",
                }
            ],
        )

        response = asyncio.run(
            engine._handle_implementation_readiness_action(
                "test-session",
                session,
                "Review unresolved and partially resolved challenges again",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_challenge_discussion")
        self.assertEqual(session.phase, "implementation_challenge_discussion")
        self.assertEqual(session.implementation_challenge_index, 1)
        self.assertEqual(
            session.implementation_mitigation_strategy[0]["challenge"],
            "Administrative burden",
        )
        self.assertIn("Administrative burden", response.bot_message)
        self.assertNotIn("Funding sustainability</strong>", response.bot_message)

    def test_review_again_uses_visible_readiness_assessment_remaining_concerns(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            phase="implementation_readiness_assessment",
            implementation_challenges=[
                {
                    "title": "Funding sustainability",
                    "category": "Cost",
                    "why_important": "The support may be unaffordable over time.",
                    "status": "resolved",
                }
            ],
            implementation_challenge_index=1,
            implementation_readiness_assessment=(
                "## Implementation Readiness Assessment\n\n"
                "### Resolved challenges\n"
                "- Funding sustainability\n\n"
                "### Partially resolved challenges\n"
                "- **Administrative burden**: Staffing capacity remains unclear.\n\n"
                "### Remaining unresolved risks\n"
                "- **Legal eligibility**: Eligibility rules may exclude intended groups."
            ),
        )

        response = asyncio.run(
            engine._handle_implementation_readiness_action(
                "test-session",
                session,
                "Review unresolved and partially resolved challenges again",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "implementation_challenge_discussion")
        self.assertEqual(session.phase, "implementation_challenge_discussion")
        self.assertEqual(session.implementation_challenge_index, 1)
        self.assertEqual(session.implementation_challenges[1]["status"], "partial")
        self.assertEqual(session.implementation_challenges[2]["status"], "unresolved")
        self.assertIn("Administrative burden", response.bot_message)
        self.assertNotIn("All implementation challenges", response.bot_message)

    def test_mitigation_review_prompt_includes_d23_page_range_context(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            mitigation_grounded_synthesis=(
                "The previous grounding result supported affordability targeting."
            ),
        )

        with patch(
            "app.services.chat_mitigation_creation._d23_conceptual_review_page_texts",
            MagicMock(
                return_value=(
                    (25, "This page is outside the requested range."),
                    (
                        26,
                        "Energy poverty concepts include affordability, distributional fairness, "
                        "and social vulnerability in transition policy design.",
                    ),
                    (
                        91,
                        "Policy design should consider targeting, trade-offs, feasibility, "
                        "and vulnerable household coverage.",
                    ),
                    (92, "This page is outside the requested range."),
                )
            ),
        ):
            context, messages = asyncio.run(
                engine._build_mitigation_review_messages(
                    session,
                    "Open the conversational discussion before evaluation.",
                )
            )

        self.assertIn("Conceptual source excerpts", context)
        self.assertIn("[Source p. 26]", context)
        self.assertIn("[Source p. 91]", context)
        self.assertNotIn("[Source p. 25]", context)
        self.assertNotIn("[Source p. 92]", context)
        self.assertNotIn("FITTER D2.3", context)
        self.assertIn("Grounded validation synthesis", context)
        self.assertIn("supported affordability targeting", context)
        self.assertIn("Generic mitigation KB excerpt", context)
        self.assertIn("What the mitigation measure covers well", messages[0]["content"])
        self.assertIn("Pros, strengths", messages[0]["content"])
        self.assertIn("Do not name the source document", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
