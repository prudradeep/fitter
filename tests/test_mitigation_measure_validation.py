import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas import ChatResponse
from app.services.chat_mitigation_creation import ChatMitigationCreationMixin
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_service import ChatService
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

    def _historical_evaluation_series(self, session):
        return []

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


class _MitigationClarificationEngine(
    ChatMitigationCreationMixin,
    ChatMitigationStepsMixin,
    ChatValidationServiceMixin,
):
    invalid_message = "Invalid"

    def _is_invalid_user_text(self, value):
        return False

    @staticmethod
    def _strip_wrapping_quotes(message):
        return message.strip("\"'")

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


class _MitigationEvidenceEngine(ChatMitigationCreationMixin):
    invalid_message = "Invalid"

    @staticmethod
    def _has_readable_evidence_content(evidence):
        return bool(evidence)


class _MitigationValidationAbstainEngine(
    ChatMitigationCreationMixin,
    ChatValidationServiceMixin,
    ChatMitigationStepsMixin,
):
    mitigation_critical_grounding_dimensions = (
        "hazard_fit",
        "justification_soundness",
    )
    mitigation_support_label_user_evidence = "USER_EVIDENCE"
    mitigation_support_label_curated_knowledge_base = "CURATED_KNOWLEDGE_BASE"

    def _is_invalid_user_text(self, value):
        return False

    async def _validate_mitigation_against_stats(
        self,
        session,
        mitigation_measure,
        reason,
        evidence="",
    ):
        return {
            "valid": False,
            "outcome": "ABSTAIN",
            "reason": "Insufficiently supported dimensions: hazard fit, justification soundness.",
            "dimensions": {
                "hazard_fit": {
                    "status": "INSUFFICIENT_INFO",
                    "explanation": "No explanation was provided.",
                },
                "justification_soundness": {
                    "status": "INSUFFICIENT_INFO",
                    "explanation": "No explanation was provided.",
                },
            },
        }

    def _grounding_validation_details(self, session, validation=None):
        return {}

    def _clear_mitigation_clarity_state(self, session):
        return None

    @staticmethod
    def _has_evidence_url_reference(evidence):
        return False

    @staticmethod
    def _has_user_supplied_evidence(evidence):
        return False


class _MitigationValidationUnavailableEngine(_MitigationValidationAbstainEngine):
    @staticmethod
    def _has_user_supplied_evidence(evidence):
        return bool(str(evidence or "").strip())

    async def _validate_mitigation_against_stats(
        self,
        session,
        mitigation_measure,
        reason,
        evidence="",
    ):
        return None


class _MitigationSynthesisUnavailableEngine(_MitigationValidationAbstainEngine):
    async def _validate_mitigation_against_stats(
        self,
        session,
        mitigation_measure,
        reason,
        evidence="",
    ):
        return {
            "valid": True,
            "outcome": "PASS",
            "reason": "Supported.",
            "dimensions": {},
        }

    async def _grounded_mitigation_synthesis(
        self,
        session,
        mitigation_measure,
        reason,
        validation,
    ):
        return None


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

    def test_concrete_contextual_measure_moves_to_clarification_without_llm_gate(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(side_effect=AssertionError("LLM should not be needed.")),
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
        self.assertIsNone(ask_mock.await_args)

    def test_measure_only_prompt_excludes_reason_when_llm_gate_is_needed(self):
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
                    "Introduce heat pump grants.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        call = ask_mock.await_args.kwargs
        self.assertIn("validate ONLY the mitigation measure itself", call["context"])
        self.assertIn("Be reasonably permissive", call["context"])
        self.assertIn('"country_region_fit": true', call["context"])
        user_content = call["messages"][0]["content"]
        self.assertIn("Mitigation Measure:", user_content)
        self.assertIn("Do NOT evaluate or request the implementation reason or justification.", user_content)
        self.assertNotIn("Reason:", user_content)
        self.assertNotIn("Justification:", user_content)

    def test_home_energy_affordability_grant_is_accepted_with_session_context(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
        )

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(side_effect=AssertionError("LLM should not be needed.")),
        ) as ask_mock:
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    (
                        "Targeted Home Energy Affordability Grant: Provide income-based "
                        "grants for insulation, efficient windows, heat pumps, and "
                        "heating-system upgrades for households experiencing repeated "
                        "utility arrears."
                    ),
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.phase, "mitigation_clarity")
        self.assertIsNone(ask_mock.await_args)
        self.assertIn("Targeted Home Energy Affordability Grant", session.pending_mitigation_measure)

    def test_bill_credit_and_energy_assessment_measure_moves_to_clarification(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
        )

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(side_effect=AssertionError("LLM should not be needed.")),
        ):
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    (
                        "Provide temporary bill credits combined with free "
                        "energy-efficiency assessments for low-income households "
                        "until longer-term building upgrades are completed."
                    ),
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.phase, "mitigation_clarity")
        self.assertIn("temporary bill credits", session.pending_mitigation_measure)

    def test_missing_context_llm_rejection_is_routed_to_clarification(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="Energy poverty",
        )
        llm_payload = {
            "status": "INVALID",
            "summary": (
                "The mitigation measure is provided but lacks specific details "
                "regarding the hazard type, sector relevance, and alignment with "
                "European Twin Transition objectives."
            ),
            "checks": {
                "hazard_fit": False,
                "sector_fit": False,
                "country_region_fit": True,
                "twin_transition_fit": False,
                "policy_quality": True,
            },
            "clarification_question": "How does it relate to the selected hazard?",
            "suggested_improvement": "State the hazard and sector fit.",
        }

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(return_value=json.dumps(llm_payload)),
        ):
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    "Provide temporary bill credits.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(session.phase, "mitigation_clarity")

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

    def test_clarification_after_declining_evidence_does_not_ask_again(self):
        engine = _MitigationClarificationEngine()
        engine._validate_frozen_mitigation_inputs = AsyncMock(
            return_value=ChatResponse(
                session_id="test-session",
                step="mitigation_target_population_review",
                bot_message="validated without evidence",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="",
            pending_mitigation_evidence="",
            mitigation_evidence_declined=True,
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
        self.assertEqual(response.step, "mitigation_target_population_review")
        self.assertEqual(response.bot_message, "validated without evidence")
        engine._validate_frozen_mitigation_inputs.assert_awaited_once()
        self.assertEqual(engine._validate_frozen_mitigation_inputs.await_args.args[4], "")

    def test_repeated_mitigation_clarification_question_returns_error(self):
        engine = ChatService.__new__(ChatService)
        engine._is_invalid_user_text = MagicMock(return_value=False)
        engine._validate_clarification_answer_quality = AsyncMock(
            return_value={"valid": True, "reason": "Clear."}
        )
        question = "How is the proposed measure expected to reduce the selected hazard's impact?"
        engine._assess_mitigation_clarity = AsyncMock(
            return_value={
                "clear": False,
                "dimensions": {
                    "specificity": "NEEDS_CLARIFICATION",
                    "justification_clarity": "NEEDS_CLARIFICATION",
                    "evidence_identifiability": "CLEAR",
                },
                "follow_up_questions": [question],
                "frozen_inputs": {},
                "reason": "The mechanism is still unclear.",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            phase="mitigation_clarity",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="Targeted grants reduce exposure to energy poverty.",
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="justification_clarity",
            mitigation_clarification_history=[
                {
                    "role": "assistant",
                    "content": (
                        "Currently clarifying: Justification clarity\n\n"
                        f"Please answer these questions in one response:\n\n1. {question}"
                    ),
                }
            ],
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                "It pays part of the upfront bill so low-income households can afford the upgrade.",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(response.input_mode, "textarea")
        self.assertIn("did not resolve", response.bot_message)
        self.assertIn(question, response.bot_message)
        self.assertEqual(session.phase, "mitigation_clarity")

    def test_concrete_repeated_mitigation_specificity_answer_moves_to_evidence(self):
        engine = ChatService.__new__(ChatService)
        engine._is_invalid_user_text = MagicMock(return_value=False)
        engine._validate_clarification_answer_quality = AsyncMock(
            return_value={"valid": True, "reason": "Clear."}
        )
        questions = [
            "What specific financial instruments or funding mechanisms will be used for the direct assistance to vulnerable households?",
            "Which specific building retrofit technologies or energy-efficiency measures are prioritized for rural and suburban households?",
        ]
        engine._assess_mitigation_clarity = AsyncMock(
            return_value={
                "clear": False,
                "dimensions": {
                    "specificity": "NEEDS_CLARIFICATION",
                    "justification_clarity": "NEEDS_CLARIFICATION",
                    "evidence_identifiability": "CLEAR",
                },
                "follow_up_questions": questions,
                "frozen_inputs": {},
                "reason": "The measure still needs concrete instruments and retrofit details.",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            phase="mitigation_clarity",
            pending_mitigation_measure="Affordable Heating Shield for Baden-Württemberg",
            pending_mitigation_reason=(
                "The measure reduces rising heating and cooling costs for vulnerable "
                "households through affordability support and building upgrades."
            ),
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="specificity",
            mitigation_clarification_history=[
                {
                    "role": "assistant",
                    "content": (
                        "Currently clarifying: Specificity\n\n"
                        "Please answer these questions in one response:\n\n"
                        f"1. {questions[0]}\n"
                        f"2. {questions[1]}"
                    ),
                }
            ],
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                (
                    "Financial instruments and funding mechanisms: Use upfront, income-tested grants "
                    "for vulnerable households, supplemented by energy-bill credits for households in "
                    "utility arrears and low-interest or zero-interest loans for costs not fully covered "
                    "by grants. Funding can be structured through Germany's allocation under the EU "
                    "Social Climate Fund, complemented by Baden-Württemberg energy-efficiency support "
                    "programmes.\n"
                    "Prioritized retrofit and energy-efficiency measures: For rural and suburban "
                    "households, prioritize roof and attic insulation, external-wall insulation, "
                    "high-performance windows and doors, heat pumps, heating controls and thermostats, "
                    "heating-system optimization, and hot-water efficiency improvements."
                ),
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_evidence_decision")
        self.assertEqual(session.phase, "mitigation_evidence_decision")
        self.assertIn("Do you have evidence", response.bot_message)

    def test_partial_repeated_mitigation_specificity_answer_still_errors(self):
        engine = ChatService.__new__(ChatService)
        engine._is_invalid_user_text = MagicMock(return_value=False)
        engine._validate_clarification_answer_quality = AsyncMock(
            return_value={"valid": True, "reason": "Clear."}
        )
        questions = [
            "What specific financial instruments or funding mechanisms will be used for the direct assistance to vulnerable households?",
            "Which specific building retrofit technologies or energy-efficiency measures are prioritized for rural and suburban households?",
        ]
        engine._assess_mitigation_clarity = AsyncMock(
            return_value={
                "clear": False,
                "dimensions": {
                    "specificity": "NEEDS_CLARIFICATION",
                    "justification_clarity": "NEEDS_CLARIFICATION",
                    "evidence_identifiability": "CLEAR",
                },
                "follow_up_questions": questions,
                "frozen_inputs": {},
                "reason": "The answer does not cover retrofit technologies.",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            phase="mitigation_clarity",
            pending_mitigation_measure="Affordable Heating Shield for Baden-Württemberg",
            pending_mitigation_reason="The measure reduces energy affordability pressure.",
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="specificity",
            mitigation_clarification_history=[
                {
                    "role": "assistant",
                    "content": (
                        "Currently clarifying: Specificity\n\n"
                        "Please answer these questions in one response:\n\n"
                        f"1. {questions[0]}\n"
                        f"2. {questions[1]}"
                    ),
                }
            ],
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                (
                    "Use upfront income-tested grants, energy-bill credits, and zero-interest loans "
                    "for vulnerable households in utility arrears."
                ),
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertIn(questions[1], response.bot_message)

    def test_repeated_mechanism_clarification_answer_moves_to_evidence(self):
        engine = ChatService.__new__(ChatService)
        engine._is_invalid_user_text = MagicMock(return_value=False)
        engine._validate_clarification_answer_quality = AsyncMock(
            return_value={"valid": True, "reason": "Clear."}
        )
        question = (
            "How will this mitigation measure reduce the negative impact of the selected hazard "
            "for the affected profiles, and why is it appropriate for this context?"
        )
        engine._assess_mitigation_clarity = AsyncMock(
            return_value={
                "clear": False,
                "dimensions": {
                    "specificity": "NEEDS_CLARIFICATION",
                    "justification_clarity": "NEEDS_CLARIFICATION",
                    "evidence_identifiability": "CLEAR",
                },
                "follow_up_questions": [question],
                "frozen_inputs": {},
                "reason": "The mitigation mechanism is still unclear.",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Baden-Württemberg",
            sector="Energy",
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            phase="mitigation_clarity",
            pending_mitigation_measure="Affordable Heating Shield for Baden-Württemberg",
            pending_mitigation_reason="",
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="justification_clarity",
            mitigation_clarification_history=[
                {
                    "role": "assistant",
                    "content": (
                        "Please answer this clarification question before evidence is collected:\n\n"
                        f"1. {question}"
                    ),
                }
            ],
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                (
                    "The measure will reduce heating and cooling cost pressure by combining direct "
                    "bill credits and grants with insulation, clean heating, heat pump support, "
                    "consumer protection, and energy advice. It protects vulnerable households from "
                    "energy bill shocks while lowering demand in Baden-Württemberg's Energy sector."
                ),
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_evidence_decision")
        self.assertEqual(session.phase, "mitigation_evidence_decision")

    def test_mitigation_follow_up_resolution_helper_requires_all_question_categories(self):
        questions = [
            "What specific financial instruments or funding mechanisms will be used for the direct assistance to vulnerable households?",
            "Which specific building retrofit technologies or energy-efficiency measures are prioritized for rural and suburban households?",
        ]
        complete_answer = (
            "Use income-tested grants, bill credits, and zero-interest loans for vulnerable households "
            "in utility arrears. Prioritize roof insulation, external-wall insulation, high-performance "
            "windows, heat pumps, thermostats, and heating-system optimization."
        )
        partial_answer = (
            "Use income-tested grants, bill credits, and zero-interest loans for vulnerable households "
            "in utility arrears."
        )

        self.assertTrue(
            ChatService._mitigation_answer_resolves_follow_up_questions(
                complete_answer,
                questions,
                selected_hazard="HEATING AND COOLING COSTS INCREASE",
            )
        )
        self.assertFalse(
            ChatService._mitigation_answer_resolves_follow_up_questions(
                partial_answer,
                questions,
                selected_hazard="HEATING AND COOLING COSTS INCREASE",
            )
        )

    def test_mitigation_reason_same_as_measure_is_rejected(self):
        engine = _MitigationClarificationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            phase="mitigation_reason",
            pending_mitigation_measure="Introduce targeted grants for low-income households",
        )

        response = asyncio.run(
            engine._validate_mitigation_reason(
                "test-session",
                session,
                "Introduce targeted grants for low-income households",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_reason")
        self.assertIn("reason repeats the mitigation measure", response.bot_message)

    def test_mitigation_clarification_same_as_measure_is_rejected(self):
        engine = _MitigationClarificationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            pending_mitigation_measure="Introduce targeted grants for low-income households",
            pending_mitigation_reason="",
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="justification_clarity",
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                "Introduce targeted grants for low-income households",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertIn("clarification repeats information already provided", response.bot_message)

    def test_mitigation_clarification_same_as_reason_is_rejected(self):
        engine = _MitigationClarificationEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason=(
                "It lowers upfront costs for low-income households affected by energy poverty."
            ),
            pending_mitigation_evidence="",
            pending_mitigation_clarity_dimension="specificity",
        )

        response = asyncio.run(
            engine._handle_mitigation_clarity_answer(
                "test-session",
                session,
                "It lowers upfront costs for low-income households affected by energy poverty.",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertIn("clarification repeats information already provided", response.bot_message)

    def test_mitigation_abstain_returns_clarification_textarea_not_evidence_controls(self):
        engine = _MitigationValidationAbstainEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
        )

        response = asyncio.run(
            engine._validate_frozen_mitigation_inputs(
                "test-session",
                session,
                "Introduce targeted grants",
                "It lowers upfront costs.",
                "",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "mitigation_clarity")
        self.assertEqual(session.pending_mitigation_clarity_dimension, "justification_clarity")
        self.assertIn("Clarification needed", response.bot_message)
        option_labels = [option.label for option in response.options]
        self.assertNotIn("Skip", option_labels)
        self.assertNotIn("Back to evidence question", option_labels)

    def test_mitigation_validation_unavailable_without_evidence_uses_local_fallback(self):
        engine = _MitigationValidationUnavailableEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            mitigation_target_population=["Low-income households"],
        )

        response = asyncio.run(
            engine._validate_frozen_mitigation_inputs(
                "test-session",
                session,
                "Introduce targeted grants",
                "It lowers upfront costs.",
                "",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_target_population_review")
        self.assertEqual(session.phase, "mitigation_target_population_review")
        self.assertTrue(session.mitigation_validation["valid"])
        self.assertTrue(session.mitigation_validation["local_llm_unavailable_fallback"])
        self.assertEqual(
            session.mitigation_validation["support_label"],
            "LOCAL_FALLBACK_NO_LLM",
        )
        self.assertIn("Local fallback conclusion", session.mitigation_grounded_synthesis)

    def test_mitigation_validation_unavailable_with_evidence_returns_clarification(self):
        engine = _MitigationValidationUnavailableEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            mitigation_target_population=["Low-income households"],
        )

        response = asyncio.run(
            engine._validate_frozen_mitigation_inputs(
                "test-session",
                session,
                "Introduce targeted grants",
                "It lowers upfront costs.",
                "Evidence URL: https://example.org/report.pdf",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_clarity")
        self.assertEqual(response.input_mode, "textarea")
        option_labels = [option.label for option in response.options]
        self.assertNotIn("Skip", option_labels)
        self.assertNotIn("Back to evidence question", option_labels)

    def test_mitigation_synthesis_unavailable_without_evidence_uses_local_fallback(self):
        engine = _MitigationSynthesisUnavailableEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
            mitigation_target_population=["Low-income households"],
        )

        response = asyncio.run(
            engine._validate_frozen_mitigation_inputs(
                "test-session",
                session,
                "Introduce targeted grants",
                "It lowers upfront costs.",
                "",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_target_population_review")
        self.assertEqual(session.phase, "mitigation_target_population_review")
        self.assertIn("Local fallback conclusion", session.mitigation_grounded_synthesis)

    def test_mitigation_evidence_decision_open_text_yes_asks_for_evidence(self):
        engine = _MitigationEvidenceEngine()
        session = ChatSession(
            phase="mitigation_evidence_decision",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="It lowers upfront costs.",
        )

        response = asyncio.run(
            engine._handle_mitigation_evidence_decision(
                "test-session",
                session,
                "I have evidence",
            )
        )

        self.assertEqual(response.step, "mitigation_evidence")
        self.assertEqual(response.input_mode, "evidence_only")
        self.assertEqual(session.phase, "mitigation_evidence_input")

    def test_mitigation_evidence_decision_open_text_no_validates(self):
        engine = _MitigationEvidenceEngine()
        engine._validate_frozen_mitigation_inputs = AsyncMock(
            return_value=ChatResponse(
                session_id="test-session",
                step="mitigation_target_population_review",
                bot_message="validated",
                options=[],
                session={},
            )
        )
        session = ChatSession(
            phase="mitigation_evidence_decision",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="It lowers upfront costs.",
        )

        response = asyncio.run(
            engine._handle_mitigation_evidence_decision(
                "test-session",
                session,
                "skip evidence",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            engine._validate_frozen_mitigation_inputs.await_args.args[4],
            "",
        )
        self.assertTrue(session.mitigation_evidence_declined)

    def test_mitigation_evidence_decision_open_text_no_i_dont_know_validates(self):
        engine = _MitigationEvidenceEngine()
        engine._validate_frozen_mitigation_inputs = AsyncMock(
            return_value=ChatResponse(
                session_id="test-session",
                step="mitigation_target_population_review",
                bot_message="validated",
                options=[],
                session={},
            )
        )
        session = ChatSession(
            phase="mitigation_evidence_decision",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="It lowers upfront costs.",
        )

        response = asyncio.run(
            engine._handle_mitigation_evidence_decision(
                "test-session",
                session,
                "no, i don't know",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            engine._validate_frozen_mitigation_inputs.await_args.args[4],
            "",
        )
        self.assertTrue(session.mitigation_evidence_declined)

    def test_mitigation_evidence_decision_accepts_url_in_open_text(self):
        engine = _MitigationEvidenceEngine()
        engine._validate_frozen_mitigation_inputs = AsyncMock(
            return_value=ChatResponse(
                session_id="test-session",
                step="mitigation_target_population_review",
                bot_message="validated",
                options=[],
                session={},
            )
        )
        session = ChatSession(
            phase="mitigation_evidence_decision",
            pending_mitigation_measure="Introduce targeted grants",
            pending_mitigation_reason="It lowers upfront costs.",
        )

        response = asyncio.run(
            engine._handle_mitigation_evidence_decision(
                "test-session",
                session,
                "Evidence is at https://example.org/retrofit-study.pdf.",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            engine._validate_frozen_mitigation_inputs.await_args.args[4],
            "Evidence URL: https://example.org/retrofit-study.pdf",
        )
        self.assertFalse(session.mitigation_evidence_declined)

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

    def test_extract_suggested_policy_target_group_mechanisms(self):
        markdown = (
            "### Regional support package\n"
            "- **Proposal:** Provide targeted retrofit grants.\n"
            "- **Target-group mechanisms:**\n"
            "  - **Low-income households:** Higher grant coverage reduces upfront costs.\n"
            "  - **Tenants:** Landlord participation rules reduce split incentives.\n"
            "- **Why this helps:** It lowers upfront costs for affected households."
        )

        self.assertEqual(
            ChatMitigationCreationMixin._extract_suggested_policy_target_group_mechanisms(
                markdown
            ),
            (
                "Low-income households: Higher grant coverage reduces upfront costs; "
                "Tenants: Landlord participation rules reduce split incentives"
            ),
        )

    def test_target_population_inference_uses_suggested_mechanisms(self):
        engine = _MitigationReviewEngine()
        engine._match_mitigation_target_population_answer = AsyncMock(
            return_value=["Level of income: Low income", "Tenancy status: Tenant"]
        )
        session = ChatSession(
            suggested_new_policy_target_group_mechanisms=(
                "Low-income households receive higher grant coverage; "
                "tenants receive landlord participation safeguards."
            )
        )

        inferred = asyncio.run(
            engine._infer_mitigation_target_population_from_inputs(
                session,
                "Provide targeted retrofit grants.",
                "It lowers upfront costs.",
            )
        )

        self.assertEqual(
            inferred,
            ["Level of income: Low income", "Tenancy status: Tenant"],
        )
        matched_text = engine._match_mitigation_target_population_answer.await_args.args[0]
        self.assertIn("Target-group mechanisms", matched_text)
        self.assertIn("tenants receive landlord participation safeguards", matched_text)

    def test_target_population_inference_keeps_all_explicit_mechanism_groups(self):
        engine = _MitigationReviewEngine()
        engine._match_mitigation_target_population_answer = AsyncMock(
            return_value=["Utility arrears households (twice or more)"]
        )
        session = ChatSession(
            suggested_new_policy_target_group_mechanisms=(
                "Utility arrears households (twice or more): Provide direct financial "
                "support to cover heating costs; Religious minorities: Ensure equal "
                "access through community outreach and language assistance"
            )
        )

        inferred = asyncio.run(
            engine._infer_mitigation_target_population_from_inputs(
                session,
                "Expand the EU Social Climate Fund for vulnerable households.",
                "It targets clean heating upgrades and energy advice.",
            )
        )

        self.assertEqual(
            inferred,
            [
                "Utility arrears households (twice or more)",
                "Religious minorities",
            ],
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

    def test_mitigation_review_shows_strict_crowd_sourcing_notice(self):
        engine = _MitigationReviewEngine()
        engine._mitigation_review_response = AsyncMock(return_value="### Review\nSupported.")
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            validation_mode="strict",
            crowd_sourcing_enabled=True,
            selected_hazard="Higher electricity bills",
            mitigation_measure="Targeted bill support.",
            mitigation_reason="It offsets affordability pressure.",
            mitigation_target_population=["Low-income households"],
            mitigation_validation={},
        )

        response = asyncio.run(engine._mitigation_review_step("test-session", session))

        self.assertIn(
            "Once saved, this mitigation measure will be visible to other platform users",
            response.bot_message,
        )
        self.assertIn("Bavaria, Germany", response.bot_message)

    def test_mitigation_review_next_step_starts_evaluation(self):
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

        response = asyncio.run(
            engine._handle_mitigation_review(
                "test-session",
                session,
                "Move to next step",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "evaluation_question")
        self.assertEqual(session.phase, "evaluation_question")
        self.assertIn("How feasible is the implementation plan?", response.bot_message)

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

    def test_review_again_parses_bold_readiness_remaining_unresolved_risks(self):
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
                "**Resolved Challenges:**\n"
                "- Funding sustainability\n\n"
                "**Remaining Unresolved Risks:**\n"
                "**Legal eligibility:** Eligibility rules may exclude intended groups.\n\n"
                "**Recommended Improvements:**\n"
                "- Assign an accountable legal owner."
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
        self.assertEqual(session.implementation_challenges[1]["status"], "unresolved")
        self.assertIn("Legal eligibility", response.bot_message)
        self.assertNotIn("All implementation challenges", response.bot_message)

    def test_evaluation_complete_offers_system_inquiry(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            hazards=[
                "Higher electricity bills from renewable grid upgrade tariffs",
                "Power outages from grid congestion",
            ],
            mitigation_measure=(
                "A grant that offsets electricity bill increases for low-income households."
            ),
            mitigation_reason="It reduces bill pressure while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            evaluation_answers=[
                {
                    "category": "Systemic and structural",
                    "question": "How structural is this measure?",
                    "score": 8,
                }
            ],
        )

        response = engine._evaluation_complete_step("test-session", session)

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_intro")
        self.assertEqual(session.phase, "system_inquiry_intro")
        self.assertTrue(session.system_inquiry_observations)
        self.assertIn("Start system inquiry", [option.label for option in response.options])
        self.assertNotIn("Power outages from grid congestion", response.bot_message)
        self.assertNotIn("selected hazards have no mitigation measure", response.bot_message)

    def test_system_inquiry_uses_cap_and_reports_held_lenses(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            hazard_profiles={
                "Higher electricity bills from renewable grid upgrade tariffs": [
                    {"name": "Low-income households"},
                    {"name": "Tenants"},
                ]
            },
            mitigation_measure=(
                "A grant application that offsets electricity bill increases for "
                "low-income households over 18 months."
            ),
            mitigation_reason="It reduces bill pressure while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            evaluation_answers=[
                {
                    "category": "Systemic and structural",
                    "question": "How structural is this measure?",
                    "score": 8,
                }
            ],
        )

        response = engine._system_inquiry_intro_step("test-session", session)

        self.assertEqual(len(session.system_inquiry_observations or []), 2)
        self.assertIn("C1-P1", [
            item["probe_id"] for item in session.system_inquiry_observations or []
        ])
        self.assertIn("C2-P1", [
            item["probe_id"] for item in session.system_inquiry_observations or []
        ])
        self.assertTrue(session.system_inquiry_held_observations)
        self.assertIn("Boundary note", response.bot_message)
        self.assertIn("Procedural access", response.bot_message)
        first = (session.system_inquiry_observations or [])[0]
        self.assertEqual(first["candidate_status"], "selected")
        self.assertEqual(first["library_version"], "1.0")
        self.assertIn("required_anchors", first)
        self.assertIn("anchor_counts", first)
        self.assertIn("§5.3", first["source_refs"][0]["locator"])
        self.assertNotEqual(first["source_refs"][0]["locator"], "§4.4")
        observations = {
            item["probe_id"]: item
            for item in session.system_inquiry_observations or []
        }
        self.assertEqual(observations["C1-P1"]["corpus_label"], "evidenced")
        self.assertEqual(observations["C1-P1"]["anchor_counts"]["predictors"], 1)
        self.assertEqual(
            observations["C1-P1"]["citations"][0]["source"],
            "session_affected_population_profile",
        )
        self.assertEqual(observations["C2-P1"]["corpus_label"], "evidenced")
        self.assertEqual(observations["C2-P1"]["anchor_counts"]["predictors"], 1)
        held = session.system_inquiry_held_observations or []
        self.assertTrue(all(item["candidate_status"] == "held_cap" for item in held))
        audit_statuses = {
            item["probe_id"]: item["candidate_status"]
            for item in session.system_inquiry_candidate_audit or []
        }
        self.assertEqual(audit_statuses["C1-P1"], "selected")
        self.assertEqual(audit_statuses["C2-P1"], "selected")
        self.assertEqual(audit_statuses["C3-P1"], "held_cap")

    def test_system_inquiry_async_intro_runs_constrained_llm_pipeline(self):
        async def fake_ask_llm_chat(context, messages, **kwargs):
            if "Extract MeasureAttributes" in context:
                return json.dumps(
                    {
                        "action_type": "grant",
                        "leverage_depth": "rules",
                        "delivery_channel": "application",
                        "cost_incidence": "upfront_user_cost",
                        "time_to_benefit": "months",
                        "eligibility_basis": ["income", "tenure"],
                        "named_sectors": ["housing"],
                        "requires_capacity": True,
                        "capacity_type": "installers",
                    }
                )
            payload = json.loads(messages[0]["content"])
            candidates = payload["candidates"]
            if "Screen system-inquiry" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "screen_result": True,
                            "reason": "Anchored in the dossier.",
                        }
                        for item in candidates
                    ]
                )
            if "Verify system-inquiry" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "verify_votes": 3,
                            "reason": "The anchors are real.",
                        }
                        for item in candidates
                    ]
                )
            if "Adjudicate corpus support" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "corpus_label": "unproven",
                            "reason": "No direct corpus evidence supplied.",
                        }
                        for item in candidates
                    ]
                )
            return "{}"

        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills",
            mitigation_measure=(
                "A housing retrofit grant application funds installer works for "
                "low-income tenants over 18 months."
            ),
            mitigation_reason="It reduces housing and energy costs.",
            mitigation_target_population=["Low-income tenants"],
            evaluation_answers=[
                {
                    "category": "Systemic and structural",
                    "question": "How structural is this measure?",
                    "score": 8,
                }
            ],
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(side_effect=fake_ask_llm_chat),
        ) as mocked_llm:
            response = asyncio.run(
                engine._system_inquiry_intro_step_with_llm("test-session", session)
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_intro")
        self.assertEqual(session.system_inquiry_attributes["extraction_method"], "llm_constrained_v1")
        self.assertEqual(session.system_inquiry_attributes["leverage_depth"], "rules")
        self.assertEqual(mocked_llm.await_count, 4)
        audit = session.system_inquiry_candidate_audit or []
        self.assertTrue(audit)
        self.assertTrue(all(item.get("screen_method") == "llm_constrained_v1" for item in audit))
        self.assertTrue(all(item.get("verify_method") == "llm_constrained_v1" for item in audit))
        self.assertTrue(
            all(
                item.get("corpus_adjudication_method") == "llm_constrained_v1"
                for item in audit
            )
        )

    def test_system_inquiry_llm_screen_can_discard_unstable_candidate(self):
        async def fake_ask_llm_chat(context, messages, **kwargs):
            if "Extract MeasureAttributes" in context:
                return json.dumps(
                    {
                        "action_type": "grant",
                        "leverage_depth": "parameter",
                        "delivery_channel": "application",
                        "cost_incidence": "upfront_user_cost",
                        "time_to_benefit": "months",
                        "eligibility_basis": ["income"],
                        "named_sectors": [],
                        "requires_capacity": False,
                        "capacity_type": "none",
                    }
                )
            payload = json.loads(messages[0]["content"])
            candidates = payload["candidates"]
            if "Screen system-inquiry" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "screen_result": item["probe_id"] != "C3-P1",
                            "reason": "Screened.",
                        }
                        for item in candidates
                    ]
                )
            if "Verify system-inquiry" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "verify_votes": 3,
                            "reason": "Verified.",
                        }
                        for item in candidates
                    ]
                )
            if "Adjudicate corpus support" in context:
                return json.dumps(
                    [
                        {
                            "candidate_id": item["candidate_id"],
                            "corpus_label": "unproven",
                            "reason": "No direct corpus evidence supplied.",
                        }
                        for item in candidates
                    ]
                )
            return "{}"

        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills",
            mitigation_measure="An application grant for low-income households.",
            mitigation_reason="It offsets household bills.",
            mitigation_target_population=["Low-income households"],
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(side_effect=fake_ask_llm_chat),
        ):
            asyncio.run(engine._system_inquiry_intro_step_with_llm("test-session", session))

        audit_statuses = {
            item["probe_id"]: item["candidate_status"]
            for item in session.system_inquiry_candidate_audit or []
        }
        self.assertEqual(audit_statuses["C3-P1"], "discarded_unstable")

    def test_system_inquiry_probe_metadata_uses_specific_source_refs(self):
        c1 = _MitigationReviewEngine._system_inquiry_probe_metadata("C1-P1")
        unknown = _MitigationReviewEngine._system_inquiry_probe_metadata("Z9-P1")

        self.assertEqual(
            c1["source_refs"][0]["locator"],
            "§5.3 C1-P1 — UPFRONT-COST-INCIDENCE",
        )
        self.assertEqual(unknown["source_refs"][0]["locator"], "§4.4")

    def test_system_inquiry_probe_library_is_versioned_static_asset(self):
        from app.services.system_inquiry_probe_library import system_inquiry_probe_library

        library = system_inquiry_probe_library()

        self.assertEqual(library["library_version"], "1.0")
        self.assertGreaterEqual(len(library["probes"]), 30)
        self.assertIn("C1-P1", library["records"])
        self.assertEqual(
            _MitigationReviewEngine._system_inquiry_library_version(),
            "1.0",
        )

    def test_system_inquiry_adds_portfolio_probe_for_shared_target_group(self):
        class _PriorMeasure:
            measure = "A previous electricity voucher for low-income households."
            reason = "It provides support through existing payment rules."
            target_population = json.dumps(["Low-income households"])

        engine = _MitigationReviewEngine()
        engine._system_inquiry_prior_measure_rows = lambda session: [_PriorMeasure()]
        session = ChatSession(
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure=(
                "A grant that offsets electricity bill increases for low-income households."
            ),
            mitigation_reason="It reduces bill pressure.",
            mitigation_target_population=["Low-income households"],
        )

        observations = engine._system_inquiry_observations(session)

        self.assertIn("D2-P1", [item["probe_id"] for item in observations])
        d2 = next(item for item in observations if item["probe_id"] == "D2-P1")
        self.assertIn("previous electricity voucher", d2["observation"])
        self.assertEqual(d2["tier"], "conditional")
        self.assertEqual(d2["anchor_counts"]["measures"], 2)

    def test_system_inquiry_adds_interaction_probe_for_policy_tension(self):
        class _PriorMeasure:
            measure = "A grant that helps low-income households buy efficient heaters."
            reason = "It provides financial support."
            target_population = json.dumps(["Low-income households"])

        engine = _MitigationReviewEngine()
        engine._system_inquiry_prior_measure_rows = lambda session: [_PriorMeasure()]
        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure=(
                "A regulation that requires households to replace inefficient heaters."
            ),
            mitigation_reason="It mandates higher efficiency standards.",
            mitigation_target_population=["Low-income households"],
        )

        observations = engine._system_inquiry_observations(session)

        self.assertIn("D1-P1", [item["probe_id"] for item in observations])
        d1 = next(item for item in observations if item["probe_id"] == "D1-P1")
        self.assertEqual(d1["tier"], "conditional")
        self.assertEqual(d1["anchor_counts"]["measures"], 2)
        self.assertIn("increases obligations", d1["observation"])
        self.assertIn("efficient heaters", d1["observation"])

    def test_system_inquiry_discards_candidate_missing_required_anchor(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="",
            mitigation_measure="A grant for households.",
            mitigation_reason="It offsets bills.",
            mitigation_target_population=["Low-income households"],
        )

        observations = engine._system_inquiry_observations(session)

        self.assertNotIn("C1-P1", [item["probe_id"] for item in observations])
        discarded = [
            item
            for item in (session.system_inquiry_held_observations or [])
            if item["probe_id"] == "C1-P1"
        ]
        self.assertEqual(discarded[0]["candidate_status"], "discarded_no_anchor")
        audit = {
            item["probe_id"]: item
            for item in session.system_inquiry_candidate_audit or []
        }
        self.assertEqual(audit["C1-P1"]["candidate_status"], "discarded_no_anchor")
        self.assertEqual(audit["C1-P1"]["anchor_counts"]["hazards"], 0)

    def test_system_inquiry_candidate_finalization_records_all_terminal_statuses(self):
        engine = _MitigationReviewEngine()

        def candidate(
            probe_id,
            *,
            candidate_id=None,
            family="C_justice",
            corpus_label="unproven",
            screen_result=True,
            verify_votes=None,
            salience=80,
            groups=1,
            hazards=1,
        ):
            return {
                "candidate_id": candidate_id or probe_id,
                "probe_id": probe_id,
                "family": family,
                "corpus_label": corpus_label,
                "screen_result": screen_result,
                "verify_votes": verify_votes,
                "salience": salience,
                "anchor_counts": {
                    "groups": groups,
                    "hazards": hazards,
                    "measures": 1,
                    "predictors": 0,
                },
                "required_anchors": {"hazards": 1},
            }

        candidates = [
            candidate("D2-P1", family="D_portfolio", salience=70),
            candidate("C1-P1", candidate_id="C1-P1-a", salience=95),
            candidate("C1-P1", candidate_id="C1-P1-b", salience=90),
            candidate("A4-P1", corpus_label="refuted"),
            candidate("B1-P1", verify_votes=1),
            candidate("C3-P1", hazards=0),
            candidate("A5-P1", corpus_label="evidenced", salience=75),
        ]

        selected = engine._system_inquiry_finalize_candidates(candidates, cap=3)

        statuses = {
            item["candidate_id"]: item["candidate_status"]
            for item in candidates
        }
        self.assertEqual(statuses["C1-P1-a"], "selected")
        self.assertEqual(statuses["D2-P1"], "selected")
        self.assertEqual(statuses["A5-P1"], "selected")
        self.assertEqual(statuses["C1-P1-b"], "discarded_dedupe")
        self.assertEqual(statuses["A4-P1"], "discarded_refuted")
        self.assertEqual(statuses["B1-P1"], "discarded_unstable")
        self.assertEqual(statuses["C3-P1"], "discarded_no_anchor")
        self.assertEqual([item["probe_id"] for item in selected][0], "A5-P1")

    def test_system_inquiry_candidate_dedupe_allows_distinct_anchor_sets(self):
        engine = _MitigationReviewEngine()

        def candidate(group):
            return {
                "candidate_id": f"C3-P1-{group}",
                "probe_id": "C3-P1",
                "family": "C_justice",
                "corpus_label": "unproven",
                "screen_result": True,
                "verify_votes": None,
                "salience": 90,
                "salience_score": 0.9,
                "anchors": {"measure": "Measure", "groups": [group]},
                "anchor_counts": {
                    "groups": 1,
                    "hazards": 0,
                    "measures": 1,
                    "predictors": 0,
                },
                "required_anchors": {"measures": 1, "groups": 1},
            }

        candidates = [candidate("Tenants"), candidate("Homeowners")]

        selected = engine._system_inquiry_finalize_candidates(candidates, cap=2)

        self.assertEqual([item["candidate_status"] for item in candidates], ["selected", "selected"])
        self.assertEqual(len(selected), 2)

    def test_system_inquiry_prior_anchor_match_reuses_earlier_response(self):
        class _PriorMeasure:
            measure = "Earlier application grant."
            reason = "It used an application route."
            target_population = json.dumps(["Low-income households"])
            system_inquiry_json = json.dumps(
                {
                    "annotations": [
                        {
                            "annotation_id": "si-001",
                            "probe_id": "C3-P1",
                            "status": "current",
                            "resolution_state": "addressed",
                            "anchors": {
                                "hazard": "Heating and cooling costs increase",
                                "groups": ["Low-income households"],
                            },
                            "user_response": (
                                "Local advisors will complete the form with households."
                            ),
                        }
                    ]
                }
            )

        engine = _MitigationReviewEngine()
        engine._system_inquiry_prior_measure_rows = lambda session: [_PriorMeasure()]
        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure=(
                "A means-tested application grant for low-income households."
            ),
            mitigation_reason="Households apply through the local office.",
            mitigation_target_population=["Low-income households"],
        )

        engine._system_inquiry_observations(session)
        c3 = next(
            item
            for item in session.system_inquiry_candidate_audit or []
            if item["probe_id"] == "C3-P1"
        )

        self.assertEqual(c3["dedupe_basis"], "probe_id_anchor_set_prior_response")
        self.assertIn("Earlier you said", c3["observation"])
        self.assertIn("Local advisors", c3["observation"])
        self.assertIn("Does your earlier answer still apply", c3["question"])

    def test_system_inquiry_candidate_finalization_prefers_portfolio_after_first_measure(self):
        engine = _MitigationReviewEngine()

        def candidate(probe_id, family, salience):
            return {
                "candidate_id": probe_id,
                "probe_id": probe_id,
                "family": family,
                "corpus_label": "unproven",
                "screen_result": True,
                "verify_votes": None,
                "salience": salience,
                "salience_score": salience / 100,
                "anchor_counts": {
                    "groups": 1,
                    "hazards": 1,
                    "measures": 1,
                    "predictors": 0,
                },
                "required_anchors": {"hazards": 1},
            }

        candidates = [
            candidate("C1-P1", "C_justice", 99),
            candidate("C2-P1", "C_justice", 98),
            candidate("A4-P1", "A_structure", 97),
            candidate("D2-P1", "D_portfolio", 70),
        ]

        selected = engine._system_inquiry_finalize_candidates(
            candidates,
            cap=3,
            require_portfolio=True,
        )

        self.assertIn("D2-P1", [item["probe_id"] for item in selected])
        self.assertEqual(
            next(item for item in candidates if item["probe_id"] == "A4-P1")[
                "candidate_status"
            ],
            "held_cap",
        )

    def test_system_inquiry_candidate_finalization_applies_cumulative_cap(self):
        engine = _MitigationReviewEngine()

        def candidate(probe_id, salience):
            return {
                "candidate_id": probe_id,
                "probe_id": probe_id,
                "family": "C_justice",
                "corpus_label": "unproven",
                "screen_result": True,
                "verify_votes": None,
                "salience": salience,
                "salience_score": salience / 100,
                "anchor_counts": {
                    "groups": 1,
                    "hazards": 1,
                    "measures": 1,
                    "predictors": 0,
                },
                "required_anchors": {"hazards": 1},
            }

        candidates = [
            candidate("C1-P1", 95),
            candidate("C2-P1", 89),
            candidate("A4-P1", 70),
        ]

        selected = engine._system_inquiry_finalize_candidates(
            candidates,
            cap=3,
            prior_surface_count=10,
        )

        self.assertEqual([item["probe_id"] for item in selected], ["C1-P1"])
        self.assertEqual(
            next(item for item in candidates if item["probe_id"] == "C2-P1")[
                "candidate_status"
            ],
            "held_cap",
        )

    def test_system_inquiry_intro_offers_rerun_for_stale_reflections(self):
        class _Row:
            user_session_id = None
            system_inquiry_json = json.dumps(
                {
                    "context_fingerprint": "old-context",
                    "annotations": [{"annotation_id": "si-001"}],
                }
            )

        class _Db:
            def scalar(self, statement):
                return _Row()

        engine = _MitigationReviewEngine()
        engine.db = _Db()
        session = ChatSession(
            selected_hazard="Higher electricity bills",
            mitigation_measure="Updated targeted bill support.",
            mitigation_reason="It adds automatic eligibility.",
            mitigation_record_id="measure-1",
            mitigation_target_population=["Low-income households"],
        )

        response = engine._system_inquiry_intro_step("test-session", session)

        self.assertIn("Re-run note", response.bot_message)
        self.assertIn("1 reflection response", response.bot_message)
        self.assertIn("Start system inquiry", [option.label for option in response.options])

    def test_system_inquiry_intro_hides_rerun_note_for_current_reflections(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="Higher electricity bills",
            mitigation_measure="Targeted bill support.",
            mitigation_reason="It adds automatic eligibility.",
            mitigation_record_id="measure-1",
            mitigation_target_population=["Low-income households"],
        )
        fingerprint = engine._system_inquiry_context_fingerprint(session)

        class _Row:
            user_session_id = None
            system_inquiry_json = json.dumps(
                {
                    "context_fingerprint": fingerprint,
                    "annotations": [{"annotation_id": "si-001"}],
                }
            )

        class _Db:
            def scalar(self, statement):
                return _Row()

        engine.db = _Db()

        response = engine._system_inquiry_intro_step("test-session", session)

        self.assertNotIn("Re-run note", response.bot_message)

    def test_system_inquiry_records_response_and_completes(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure=(
                "A grant that offsets electricity bill increases for low-income households."
            ),
            mitigation_reason="It reduces bill pressure while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            evaluation_answers=[
                {
                    "category": "Systemic and structural",
                    "question": "How structural is this measure?",
                    "score": 8,
                }
            ],
        )
        engine._system_inquiry_intro_step("test-session", session)

        first = asyncio.run(
            engine._handle_system_inquiry_intro(
                "test-session",
                session,
                "Start system inquiry",
            )
        )
        self.assertEqual(first.step, "system_inquiry_observation")
        self.assertEqual(session.phase, "system_inquiry_observation")

        while session.phase == "system_inquiry_observation":
            response = asyncio.run(
                engine._handle_system_inquiry_observation(
                    "test-session",
                    session,
                    "The measure should add automatic eligibility checks and emergency support.",
                )
            )

        self.assertEqual(response.step, "system_inquiry_complete")
        self.assertTrue(session.system_inquiry_annotations)
        self.assertTrue(
            all(
                item["resolution_state"] == "addressed"
                for item in session.system_inquiry_annotations or []
            )
        )
        annotation = (session.system_inquiry_annotations or [])[0]
        self.assertEqual(annotation["version"], 1)
        self.assertTrue(str(annotation["created_at"]).endswith("Z"))
        self.assertTrue(annotation["screen_result"])
        self.assertIn("source_refs", annotation)
        self.assertEqual(annotation["source_refs"][0]["document"], "System enquiry.md")
        self.assertIn("salience_score", annotation)

    def test_system_inquiry_partial_response_gets_one_followup(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure=(
                "A grant that offsets electricity bill increases for low-income households."
            ),
            mitigation_reason="It reduces bill pressure while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
        )
        engine._system_inquiry_intro_step("test-session", session)
        asyncio.run(
            engine._handle_system_inquiry_intro(
                "test-session",
                session,
                "Start system inquiry",
            )
        )

        followup = asyncio.run(
            engine._handle_system_inquiry_observation(
                "test-session",
                session,
                "Looks okay.",
            )
        )

        self.assertFalse(followup.error)
        self.assertEqual(followup.step, "system_inquiry_followup")
        self.assertEqual(session.phase, "system_inquiry_followup")
        self.assertIn("Specify the mechanism", followup.bot_message)
        pending = session.system_inquiry_pending_followup or {}
        adjudication = pending.get("adjudication") or {}
        self.assertEqual(adjudication["followup_type"], "specify_mechanism")

        response = asyncio.run(
            engine._handle_system_inquiry_followup(
                "test-session",
                session,
                (
                    "The measure should include automatic eligibility checks and "
                    "a local support owner for households without digital access."
                ),
            )
        )

        self.assertEqual(session.system_inquiry_index, 1)
        self.assertEqual(response.step, "system_inquiry_observation")
        annotation = (session.system_inquiry_annotations or [])[0]
        self.assertEqual(annotation["resolution_state"], "addressed")
        self.assertEqual(annotation["followup_type"], "specify_mechanism")
        self.assertIn("specify_mechanism", annotation["followup_types"])
        self.assertEqual(annotation["candidate_status"], "selected")
        self.assertIn("trigger_basis", annotation)
        self.assertIn("automatic eligibility", annotation["followup_response"])

    def test_system_inquiry_response_adjudication_uses_constrained_llm(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills",
            mitigation_measure="An application grant for low-income households.",
            mitigation_reason="It offsets bills.",
            mitigation_target_population=["Low-income households"],
            system_inquiry_attributes={"extraction_method": "llm_constrained_v1"},
            system_inquiry_observations=[
                {
                    "probe_id": "C3-P1",
                    "title": "Procedural access",
                    "observation": "The measure requires an application.",
                    "question": "Who helps applicants complete it?",
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": "An application grant",
                        "hazard": "Higher electricity bills",
                        "groups": ["Low-income households"],
                    },
                }
            ],
            system_inquiry_index=0,
            system_inquiry_annotations=[],
            phase="system_inquiry_observation",
        )

        with patch(
            "app.services.chat_mitigation_creation.ask_llm_chat",
            AsyncMock(
                return_value=json.dumps(
                    {
                        "resolution_state": "partially_addressed",
                        "evaluation": "The response is relevant but needs a mechanism.",
                        "needs_followup": True,
                        "followup_type": "specify_mechanism",
                    }
                )
            ),
        ) as mocked_llm:
            response = asyncio.run(
                engine._handle_system_inquiry_observation(
                    "test-session",
                    session,
                    "Local advice centres can help.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_followup")
        self.assertEqual(mocked_llm.await_count, 1)
        pending = session.system_inquiry_pending_followup or {}
        adjudication = pending.get("adjudication") or {}
        self.assertEqual(adjudication["adjudication_method"], "llm_constrained_v1")
        self.assertIn("Specify the mechanism", adjudication["followup_question"])

    def test_system_inquiry_followup_uses_probe_specific_group_prompt(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            hazard_profiles={
                "Higher electricity bills from renewable grid upgrade tariffs": [
                    {"name": "Low-income households"},
                    {"name": "Tenants"},
                ]
            },
            mitigation_measure="Targeted electricity bill support.",
            mitigation_reason="It offsets tariff increases.",
            mitigation_target_population=["Low-income households"],
            system_inquiry_observations=[],
            system_inquiry_index=0,
            system_inquiry_annotations=[],
        )
        observations = engine._system_inquiry_observations(session)
        c2 = next(item for item in observations if item["probe_id"] == "C2-P1")
        session.system_inquiry_observations = [c2]

        response = asyncio.run(
            engine._handle_system_inquiry_observation(
                "test-session",
                session,
                "Maybe.",
            )
        )

        self.assertEqual(response.step, "system_inquiry_followup")
        self.assertIn("Optional coverage check", response.bot_message)
        self.assertIn("keep the current target population as is", response.bot_message)
        pending = session.system_inquiry_pending_followup or {}
        adjudication = pending.get("adjudication") or {}
        self.assertEqual(adjudication["followup_type"], "name_group")

    def test_system_inquiry_followup_can_be_skipped(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure=(
                "A grant that offsets electricity bill increases for low-income households."
            ),
            mitigation_reason="It reduces bill pressure while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
        )
        engine._system_inquiry_intro_step("test-session", session)
        asyncio.run(
            engine._handle_system_inquiry_intro(
                "test-session",
                session,
                "Start system inquiry",
            )
        )
        asyncio.run(
            engine._handle_system_inquiry_observation(
                "test-session",
                session,
                "Not relevant.",
            )
        )

        response = asyncio.run(
            engine._handle_system_inquiry_followup(
                "test-session",
                session,
                "Skip follow-up",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_observation")
        annotation = (session.system_inquiry_annotations or [])[0]
        self.assertEqual(annotation["resolution_state"], "open")
        self.assertEqual(annotation["followup_response"], "")

    def test_system_inquiry_reasoned_not_applicable_completes_without_followup(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support.",
            mitigation_reason="It offsets tariff increases.",
            mitigation_target_population=["Low-income households"],
            system_inquiry_observations=[
                {
                    "probe_id": "B1-P1",
                    "lens_id": "B1",
                    "family": "B_framing",
                    "title": "Problem framing",
                    "corpus_label": "unproven",
                    "observation": "The measure frames the response around bill support.",
                    "why_it_matters": "Boundaries shape who is included.",
                    "question": "What sits outside that boundary?",
                }
            ],
            system_inquiry_index=0,
            system_inquiry_annotations=[],
            phase="system_inquiry_observation",
        )

        response = asyncio.run(
            engine._handle_system_inquiry_observation(
                "test-session",
                session,
                (
                    "This is not applicable because the adjacent tenant group is already "
                    "covered by a separate housing support measure."
                ),
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_complete")
        annotation = (session.system_inquiry_annotations or [])[0]
        self.assertEqual(annotation["resolution_state"], "not_applicable_reasoned")
        self.assertFalse(session.system_inquiry_pending_followup)

    def test_system_inquiry_complete_builds_profile_and_persists_payload(self):
        class _Row:
            system_inquiry_json = None

        class _Db:
            def __init__(self):
                self.row = _Row()
                self.committed = False
                self.rolled_back = False

            def scalar(self, statement):
                return self.row

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

        engine = _MitigationReviewEngine()
        engine.db = _Db()
        session = ChatSession(
            selected_hazard="Higher electricity bills",
            mitigation_measure="Targeted electricity bill support.",
            mitigation_record_id="measure-1",
            system_inquiry_coverage_summary={"uncovered_hazards": []},
            system_inquiry_held_observations=[
                {
                    "probe_id": "A4-P1",
                    "family": "A_structure",
                    "candidate_status": "held_cap",
                },
                {
                    "probe_id": "C3-P1",
                    "family": "C_justice",
                    "candidate_status": "discarded_no_anchor",
                },
            ],
            system_inquiry_annotations=[
                {
                    "probe_id": "C1-P1",
                    "family": "C_justice",
                    "resolution_state": "addressed",
                    "followup_response": "",
                },
                {
                    "probe_id": "B1-P1",
                    "family": "B_framing",
                    "resolution_state": "partially_addressed",
                    "followup_response": "Add tenant outreach through local advice centres.",
                },
            ],
        )

        response = engine._system_inquiry_complete_step("test-session", session)

        self.assertFalse(response.error)
        self.assertTrue(engine.db.committed)
        self.assertFalse(engine.db.rolled_back)
        self.assertEqual(session.system_inquiry_profile["annotation_count"], 2)
        self.assertEqual(session.system_inquiry_profile["completion_score"], 0.75)
        self.assertTrue(session.system_inquiry_profile["followup_used"])
        payload = json.loads(engine.db.row.system_inquiry_json)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["library_version"], "1.0")
        self.assertIn("context_fingerprint", payload)
        self.assertIn("context_snapshot", payload)
        self.assertEqual(payload["attributes"]["action_type"], "service")
        self.assertEqual(payload["attributes"]["leverage_depth"], "parameter")
        self.assertEqual(payload["profile"]["library_version"], "1.0")
        self.assertEqual(len(payload["profile"]["session_id_anon"]), 16)
        self.assertEqual(payload["profile"]["state_counts"]["addressed"], 1)
        self.assertEqual(payload["profile"]["status_counts"]["current"], 2)
        self.assertEqual(payload["profile"]["leverage_distribution"]["parameter"], 1)
        self.assertEqual(payload["candidate_audit"], [])
        self.assertEqual(payload["telemetry"]["library_version"], "1.0")
        self.assertEqual(len(payload["telemetry"]["session_id_anon"]), 16)
        self.assertEqual(payload["telemetry"]["measure_ordinal"], 1)
        self.assertFalse(payload["telemetry"]["skip_event"])
        self.assertEqual(payload["telemetry"]["family_coverage"]["C_justice"], 1.0)
        self.assertEqual(payload["telemetry"]["leverage_distribution"]["parameter"], 1)
        telemetry_probe = next(
            item
            for item in payload["telemetry"]["probes"]
            if item["probe_id"] == "B1-P1"
        )
        self.assertTrue(telemetry_probe["surfaced"])
        self.assertEqual(telemetry_probe["resolution_state"], "partially_addressed")
        self.assertEqual(telemetry_probe["response_length_bucket"], "empty")
        self.assertTrue(telemetry_probe["followup_used"])
        self.assertNotIn(
            "Add tenant outreach",
            json.dumps(payload["telemetry"], ensure_ascii=False),
        )
        self.assertEqual(payload["profile"]["per_family"]["C_justice"]["surfaced"], 1)
        self.assertEqual(payload["profile"]["per_family"]["C_justice"]["coverage"], 1.0)
        self.assertEqual(payload["profile"]["per_family"]["B_framing"]["partially"], 1)
        self.assertEqual(payload["profile"]["per_family"]["B_framing"]["coverage"], 0.5)
        self.assertEqual(
            payload["profile"]["per_family"]["A_structure"]["unexamined_held_by_cap"],
            1,
        )
        self.assertEqual(
            payload["profile"]["per_family"]["C_justice"]["unexamined_held_by_cap"],
            0,
        )
        self.assertEqual(payload["profile"]["trajectory"][0]["ordinal"], 1)
        self.assertEqual(
            payload["profile"]["trajectory"][0]["coverage"]["C_justice"],
            1.0,
        )
        self.assertEqual(len(payload["annotations"]), 2)
        self.assertEqual(payload["annotations"][0]["annotation_id"], "si-001")
        self.assertEqual(payload["annotations"][0]["version"], 1)
        self.assertTrue(str(payload["annotations"][0]["created_at"]).endswith("Z"))
        self.assertEqual(payload["annotations"][0]["citations"], [])
        self.assertEqual(payload["annotations"][0]["source_refs"], [])
        self.assertEqual(payload["annotations"][0]["status"], "current")
        self.assertIsNone(payload["annotations"][0]["superseded_by"])
        self.assertEqual(
            payload["annotations"][0]["context_fingerprint"],
            payload["context_fingerprint"],
        )
        self.assertIn("held_observations", payload)

    def test_system_inquiry_coverage_excludes_macro_profiles_from_affected_groups(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure="Targeted retrofit support.",
            mitigation_target_population=["Tenants"],
            hazard_profiles={
                "Heating and cooling costs increase": [
                    {
                        "name": "Countries with higher Electricity consumption",
                        "variable_name": "macro_electricity_consumption",
                        "variable_type": "macro",
                    },
                    {
                        "name": "Utility arrears: Yes, twice or more",
                        "variable_name": "utility_arrears",
                        "variable_type": "individual",
                    },
                ]
            },
        )

        summary = engine._system_inquiry_coverage_summary(session)
        formatted = engine._format_system_inquiry_coverage_summary(summary)

        self.assertEqual(summary["affected_group_count"], 1)
        self.assertEqual(summary["untargeted_groups"], ["Utility arrears: Yes, twice or more"])
        self.assertNotIn("Countries with higher Electricity consumption", formatted)
        self.assertIn("Utility arrears: Yes, twice or more", formatted)
        self.assertIn("Affected groups not yet covered", formatted)
        self.assertNotIn("Optional coverage suggestion", formatted)
        self.assertNotIn("not named in the mitigation target population", formatted)

    def test_system_inquiry_complete_prompts_for_optional_group_completeness(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure="Targeted retrofit support.",
            mitigation_target_population=["Tenants"],
            system_inquiry_observations=[],
            system_inquiry_annotations=[],
            system_inquiry_coverage_summary={
                "untargeted_groups": [
                    "Higher Home problems count",
                    "Utility arrears: Yes, twice or more",
                ]
            },
        )

        response = engine._system_inquiry_complete_step("test-session", session)

        self.assertEqual(response.step, "system_inquiry_followup")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual([option.label for option in response.options], ["Skip", "End system inquiry"])
        self.assertIn("Affected groups not yet covered", response.bot_message)
        self.assertIn("Higher Home problems count", response.bot_message)
        self.assertIn("Utility arrears: Yes, twice or more", response.bot_message)
        self.assertNotIn("Optional coverage suggestion", response.bot_message)

        completed = asyncio.run(
            engine._handle_system_inquiry_followup(
                "test-session",
                session,
                "Skip",
            )
        )

        self.assertEqual(completed.step, "system_inquiry_complete")
        self.assertTrue(session.system_inquiry_coverage_completion_done)
        self.assertIsNone(session.system_inquiry_pending_followup)

    def test_system_inquiry_group_completeness_note_is_recorded(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure="Targeted retrofit support.",
            mitigation_target_population=["Tenants"],
            system_inquiry_observations=[],
            system_inquiry_annotations=[],
            system_inquiry_coverage_summary={
                "untargeted_groups": ["Utility arrears: Yes, twice or more"]
            },
        )
        engine._system_inquiry_complete_step("test-session", session)

        completed = asyncio.run(
            engine._handle_system_inquiry_followup(
                "test-session",
                session,
                "Add a bill-arrears outreach route through energy advisors.",
            )
        )

        self.assertEqual(completed.step, "system_inquiry_complete")
        annotation = (session.system_inquiry_annotations or [])[0]
        self.assertEqual(annotation["probe_id"], "D5-COVERAGE")
        self.assertEqual(annotation["followup_type"], "coverage_completion")
        self.assertEqual(annotation["resolution_state"], "addressed")
        self.assertIn("bill-arrears outreach", annotation["user_response"])

    def test_system_inquiry_complete_ask_another_question_prompts_for_input(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._is_invalid_user_text = MagicMock(return_value=False)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._common_user_input_quality_response = AsyncMock(return_value=None)
        service._handle_anytime_grounded_question = AsyncMock(return_value=None)
        service._deep_dive = AsyncMock(
            return_value=ChatResponse(
                session_id="test-session",
                step="complete",
                bot_message="Deep dive answer",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            phase="system_inquiry_complete",
        )

        response = asyncio.run(
            service._chat_response(
                "test-session",
                session,
                "Ask another question",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "complete")
        self.assertEqual(response.input_mode, "text")
        self.assertEqual(session.phase, "complete")
        self.assertIn("What would you like to ask", response.bot_message)
        service._deep_dive.assert_not_awaited()

    def test_system_inquiry_payload_persists_candidate_audit(self):
        class _Row:
            system_inquiry_json = None

        class _Db:
            def __init__(self):
                self.row = _Row()

            def scalar(self, statement):
                return self.row

            def commit(self):
                return None

            def rollback(self):
                return None

        engine = _MitigationReviewEngine()
        engine.db = _Db()
        session = ChatSession(
            selected_hazard="",
            mitigation_measure="A grant for households.",
            mitigation_reason="It offsets bills.",
            mitigation_record_id="measure-1",
            mitigation_target_population=["Low-income households"],
        )
        engine._system_inquiry_intro_step("test-session", session)

        engine._system_inquiry_complete_step("test-session", session)

        payload = json.loads(engine.db.row.system_inquiry_json)
        audit = {
            item["probe_id"]: item
            for item in payload["candidate_audit"]
        }
        self.assertEqual(audit["C1-P1"]["status"], "discarded_no_anchor")
        self.assertEqual(audit["C1-P1"]["anchor_counts"]["hazards"], 0)
        self.assertEqual(audit["B1-P1"]["status"], "discarded_no_anchor")
        self.assertIn("anchors", audit["C1-P1"])
        self.assertNotIn("observation", audit["C1-P1"])
        telemetry = {
            item["probe_id"]: item
            for item in payload["telemetry"]["probes"]
        }
        self.assertFalse(telemetry["C1-P1"]["anchor_valid"])

        session = ChatSession(
            selected_hazard="Higher electricity bills",
            mitigation_measure="A grant for households.",
            mitigation_reason="It offsets bills.",
            mitigation_record_id="measure-2",
            mitigation_target_population=["Low-income households"],
        )
        engine._system_inquiry_intro_step("test-session", session)
        engine._system_inquiry_complete_step("test-session", session)
        payload = json.loads(engine.db.row.system_inquiry_json)
        selected_audit = {
            item["probe_id"]: item
            for item in payload["candidate_audit"]
        }
        self.assertIn(selected_audit["B1-P1"]["status"], {"selected", "held_cap"})
        self.assertIn("anchors", selected_audit["B1-P1"])
        self.assertEqual(
            selected_audit["B1-P1"]["source_refs"][0]["document"],
            "System enquiry.md",
        )
        self.assertNotIn("observation", selected_audit["B1-P1"])
        self.assertTrue(
            any(item["status"] == "selected" for item in selected_audit.values())
        )

    def test_system_inquiry_profile_includes_prior_measure_trajectory(self):
        class _PriorMeasure:
            id = "measure-0"
            measure = "Prior bill rebate."
            reason = "It offers bill support."
            target_population = json.dumps(["Low-income households"])
            system_inquiry_json = json.dumps(
                {
                    "profile": {
                        "per_family": {
                            "C_justice": {"coverage": 1.0},
                            "B_framing": {"coverage": 0.5},
                        }
                    }
                }
            )

        engine = _MitigationReviewEngine()
        engine._system_inquiry_prior_measure_rows = lambda session: [_PriorMeasure()]
        session = ChatSession(
            mitigation_record_id="measure-1",
            mitigation_measure="Current bill support.",
            mitigation_reason="It adds automatic support.",
            system_inquiry_annotations=[
                {
                    "probe_id": "B1-P1",
                    "family": "B_framing",
                    "resolution_state": "addressed",
                }
            ],
        )

        profile = engine._system_inquiry_profile(session)

        self.assertEqual(len(profile["trajectory"]), 2)
        self.assertEqual(profile["trajectory"][0]["measure_id"], "measure-0")
        self.assertEqual(profile["trajectory"][0]["coverage"]["B_framing"], 0.5)
        self.assertEqual(profile["trajectory"][1]["measure_id"], "measure-1")
        self.assertEqual(profile["trajectory"][1]["coverage"]["B_framing"], 1.0)

    def test_system_inquiry_attributes_capture_delivery_cost_time_and_capacity(self):
        engine = _MitigationReviewEngine()

        attributes = engine._system_inquiry_measure_attributes(
            (
                "A means-tested grant application funds insulation retrofit works "
                "for tenants over 18 months using approved installers."
            ),
            "It reduces housing and energy costs for low income households.",
            ["Low-income households", "Tenants"],
        )

        self.assertEqual(attributes["action_type"], "grant")
        self.assertEqual(attributes["delivery_channel"], "means_tested")
        self.assertEqual(attributes["cost_incidence"], "upfront_user_cost")
        self.assertEqual(attributes["time_to_benefit"], "months")
        self.assertEqual(attributes["capacity_type"], "installers")
        self.assertIn("income", attributes["eligibility_basis"])
        self.assertIn("tenure", attributes["eligibility_basis"])

    def test_system_inquiry_adds_cross_sector_capacity_rebound_and_long_term_probes(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Italy",
            sector="Energy",
            selected_hazard="Heating and cooling costs increase",
            mitigation_measure=(
                "A grant funds housing retrofit works for low-income households "
                "over two years using approved installers."
            ),
            mitigation_reason=(
                "The housing retrofit lowers energy demand and reduces exposure "
                "to heating bills."
            ),
            mitigation_target_population=["Low-income households"],
        )

        observations = engine._system_inquiry_observations(session)
        by_probe = {
            item["probe_id"]: item
            for item in (session.system_inquiry_candidate_audit or observations)
        }

        self.assertIn("A2-P1", by_probe)
        self.assertIn("A6-P1", by_probe)
        self.assertIn("A7-P1", by_probe)
        self.assertIn("C4-P1", by_probe)
        self.assertEqual(by_probe["A2-P1"]["tier"], "conditional")
        self.assertEqual(by_probe["A2-P1"]["anchor_counts"]["sectors"], 1)
        self.assertEqual(by_probe["A7-P1"]["anchor_counts"]["groups"], 1)
        self.assertEqual(by_probe["C4-P1"]["corpus_label"], "unproven")
        self.assertIn("§5.3", by_probe["A6-P1"]["source_refs"][0]["locator"])

    def test_system_inquiry_adds_missing_probe_catalogue_lenses(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="France",
            sector="Housing",
            selected_hazard="Unsafe and unaffordable homes",
            mitigation_measure=(
                "A means-tested grant routed through advisors supports low-income tenants "
                "to retrofit homes."
            ),
            mitigation_reason=(
                "If uptake changes demand and participation, the eligibility route should "
                "adapt over time."
            ),
            mitigation_target_population=["Low-income tenants"],
            mitigation_validation={
                "dimensions": {
                    "hazard_fit": {
                        "status": "SUPPORTED",
                        "explanation": "Covered.",
                    },
                    "justification_soundness": {
                        "status": "INSUFFICIENT_INFO",
                        "explanation": "The justification does not show how the route stays fair.",
                    },
                }
            },
        )

        observations = engine._system_inquiry_observations(session)
        by_probe = {
            item["probe_id"]: item
            for item in (session.system_inquiry_candidate_audit or observations)
        }

        self.assertIn("A1-P1", by_probe)
        self.assertIn("A3-P1", by_probe)
        self.assertIn("B2-P1", by_probe)
        self.assertIn("B3-P1", by_probe)
        self.assertIn("B4-P1", by_probe)
        self.assertEqual(by_probe["B2-P1"]["corpus_label"], "evidenced")
        self.assertIn("criteria", by_probe["B3-P1"]["observation"].casefold())
        self.assertIn("intermediary", by_probe["B4-P1"]["observation"].casefold())

    def test_system_inquiry_anonymous_session_id_is_stable(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(session_key="session-a")

        first = engine._system_inquiry_session_id_anon(session)
        second = engine._system_inquiry_session_id_anon(session)
        other = engine._system_inquiry_session_id_anon(
            ChatSession(session_key="session-b")
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertNotEqual(first, other)

    def test_system_inquiry_persistence_supersedes_changed_context(self):
        class _Row:
            system_inquiry_json = json.dumps(
                {
                    "context_fingerprint": "old-context",
                    "annotations": [
                        {
                            "annotation_id": "si-001",
                            "probe_id": "C1-P1",
                            "status": "current",
                            "resolution_state": "addressed",
                        }
                    ],
                    "superseded_annotations": [
                        {
                            "annotation_id": "si-previous",
                            "probe_id": "B1-P1",
                            "status": "superseded",
                        }
                    ],
                }
            )

        class _Db:
            def __init__(self):
                self.row = _Row()

            def scalar(self, statement):
                return self.row

            def commit(self):
                return None

            def rollback(self):
                return None

        engine = _MitigationReviewEngine()
        engine.db = _Db()
        session = ChatSession(
            selected_hazard="Higher electricity bills",
            mitigation_measure="Updated targeted electricity bill support.",
            mitigation_reason="It adds a new eligibility route.",
            mitigation_record_id="measure-1",
            system_inquiry_annotations=[
                {
                    "probe_id": "C3-P1",
                    "family": "C_justice",
                    "resolution_state": "addressed",
                }
            ],
        )

        engine._system_inquiry_complete_step("test-session", session)

        payload = json.loads(engine.db.row.system_inquiry_json)
        superseded = payload["superseded_annotations"]
        self.assertEqual(len(superseded), 2)
        self.assertEqual(superseded[0]["annotation_id"], "si-previous")
        self.assertEqual(superseded[1]["annotation_id"], "si-001")
        self.assertEqual(superseded[1]["status"], "superseded")
        self.assertEqual(superseded[1]["superseded_by"], payload["context_fingerprint"])

    def test_suggested_mitigation_report_includes_systemic_reflections(self):
        class _Row:
            system_inquiry_json = json.dumps(
                {
                    "profile": {"completion_score": 0.75},
                    "coverage_summary": {
                        "uncovered_hazards": ["Heat stress"],
                        "untargeted_groups": ["Outdoor workers"],
                    },
                    "annotations": [
                        {
                            "title": "Distributional incidence",
                            "corpus_label": "unproven",
                            "resolution_state": "addressed",
                            "status": "current",
                            "observation_text": "The measure relies on reimbursement.",
                            "question_text": "What happens if households cannot pay?",
                            "user_response": "Add an upfront grant route.",
                            "followup_question": "Specify the mechanism.",
                            "followup_response": "The agency pays approved installers directly.",
                        }
                    ],
                    "superseded_annotations": [
                        {"annotation_id": "si-old", "status": "superseded"}
                    ],
                }
            )

        engine = _MitigationReviewEngine()
        engine._suggested_mitigation_record = lambda session: _Row()
        session = ChatSession(suggested_mitigation_measure_id="measure-1")

        report = engine._suggested_mitigation_system_inquiry_report(session)

        self.assertIn("Completion score", report)
        self.assertIn("D4 hazard coverage", report)
        self.assertIn("D5 group coverage", report)
        self.assertIn("consider adding mitigation plans", report)
        self.assertIn("not mandatory", report)
        self.assertIn("Distributional incidence", report)
        self.assertIn("Add an upfront grant route", report)
        self.assertIn("approved installers directly", report)
        self.assertIn("Superseded reflection responses retained", report)

    def test_system_inquiry_can_be_skipped(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            phase="system_inquiry_intro",
            mitigation_measure="Targeted electricity bill support.",
            mitigation_reason="It offsets tariff increases.",
            mitigation_target_population=["Low-income households"],
        )
        engine._system_inquiry_intro_step("test-session", session)

        response = asyncio.run(
            engine._handle_system_inquiry_intro(
                "test-session",
                session,
                "Skip system inquiry",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "system_inquiry_complete")
        self.assertTrue(session.system_inquiry_skipped)
        self.assertIn("skipped", response.bot_message.casefold())

    def test_system_inquiry_skip_payload_records_telemetry_skip_event(self):
        class _Row:
            system_inquiry_json = None

        class _Db:
            def __init__(self):
                self.row = _Row()

            def scalar(self, statement):
                return self.row

            def commit(self):
                return None

            def rollback(self):
                return None

        engine = _MitigationReviewEngine()
        engine.db = _Db()
        session = ChatSession(
            country="Italy",
            sector="Energy",
            phase="system_inquiry_intro",
            mitigation_measure="Targeted electricity bill support.",
            mitigation_record_id="measure-1",
        )
        engine._system_inquiry_intro_step("test-session", session)

        asyncio.run(
            engine._handle_system_inquiry_intro(
                "test-session",
                session,
                "Skip system inquiry",
            )
        )

        payload = json.loads(engine.db.row.system_inquiry_json)
        self.assertTrue(payload["telemetry"]["skip_event"])
        self.assertEqual(payload["telemetry"]["country"], "Italy")
        self.assertEqual(payload["telemetry"]["sector"], "Energy")
        self.assertEqual(payload["telemetry"]["probes"], [])

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
