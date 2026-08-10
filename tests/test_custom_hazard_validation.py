import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import custom_hazard_validation as validator
from app.schemas import ChatResponse
from app.services.chat_formatters import format_hazards
from app.services.chat_options import HAZARD_POPULATION_REVIEW_OPTIONS
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.message_renderer import render_message


def _run(coro):
    return asyncio.run(coro)


async def _unavailable(*args, **kwargs):
    return "LLM unavailable"


class CustomHazardValidationTests(unittest.TestCase):
    def test_generic_groups_are_not_extracted(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Communities face higher costs from a vague transition risk.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(result["affected_groups"], [])
        self.assertTrue(
            result["dimension_scores"]["affected_groups_fit"]["needs_clarification"]
        )

    def test_policy_specific_group_is_extracted(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Renewable energy communities in Saxony face higher grid fees from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(
            [group["group"].casefold() for group in result["affected_groups"]],
            ["renewable energy communities"],
        )

    def test_duplicate_warning_does_not_force_rejection(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "High energy bills",
                    "Energy",
                    "Germany",
                    "Saxony",
                    ["High energy bills"],
                    None,
                )
            )

        self.assertTrue(result["duplicate_candidates"])
        self.assertEqual(result["next_action"], "ask_duplicate_confirmation")
        self.assertEqual(result["status"], "needs_duplicate_confirmation")

    def test_easy_validation_mode_accepts_borderline_dimension_scores(self):
        dimensions = {
            key: {"score": 4}
            for key in [
                "hazard_definition_fit",
                "twin_transition_policy_fit",
                "selected_sector_fit",
                "country_region_fit",
                "affected_groups_fit",
            ]
        }
        result = {
            "overall_score": 40,
            "dimension_scores": dimensions,
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        strict_action = validator._recommended_action(result, {}, "strict")
        easy_action = validator._recommended_action(result, {}, "easy")

        self.assertEqual(strict_action.value, "ask_clarification")
        self.assertEqual(easy_action.value, "validate")

    def test_easy_mode_accepts_soft_input_quality_rejection(self):
        service = ChatService.__new__(ChatService)
        service._scope_instruction = lambda session: "Stay in scope."
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            selected_hazard="Energy price shock",
            validation_mode="easy",
        )

        with patch(
            "app.services.validation_service.ask_llm_chat",
            AsyncMock(return_value='{"valid": false, "reason": "Please clarify the policy link."}'),
        ):
            result = _run(
                service._validate_input_quality(
                    session=session,
                    purpose="a reason explaining the selected hazard",
                    fields={"Reason": "Coal phase-out increases local household heating costs."},
                )
            )

        self.assertTrue(result["valid"])
        self.assertIn("Easy validation accepted", result["reason"])

    def test_easy_mode_relaxes_mitigation_grounding_required_dimensions(self):
        service = ChatService.__new__(ChatService)
        service.settings = SimpleNamespace(mitigation_support_score_floor=0.2)
        parsed = {
            "dimensions": {
                "hazard_fit": {
                    "status": "SUPPORTED",
                    "citation_ids": ["S1"],
                    "explanation": "The measure targets the selected hazard.",
                },
                "justification_soundness": {
                    "status": "INSUFFICIENT_INFO",
                    "citation_ids": [],
                    "explanation": "The justification is only partly grounded.",
                },
            },
            "verdict_stability": 1.0,
            "sample_count": 1,
        }

        strict = service._score_mitigation_grounding(
            parsed,
            support_context="- [S1] Source, score 0.11: Evidence text.",
            support_label=service.mitigation_support_label_curated_knowledge_base,
            validation_mode="strict",
        )
        easy = service._score_mitigation_grounding(
            parsed,
            support_context="- [S1] Source, score 0.11: Evidence text.",
            support_label=service.mitigation_support_label_curated_knowledge_base,
            validation_mode="easy",
        )

        self.assertFalse(strict["valid"])
        self.assertTrue(easy["valid"])

    def test_same_sector_duplicate_universe_includes_system_additional_custom_and_user_hazards(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["System employment shock"]),
                    SimpleNamespace(all=lambda: ["Additional tariff shock"]),
                    SimpleNamespace(all=lambda: ["Shared custom affordability shock"]),
                    SimpleNamespace(all=lambda: ["User energy poverty shock"]),
                ]
            )
        )
        session = ChatSession(
            sector_id=1,
            hazards=["In-session system hazard"],
            custom_hazards=["In-session custom hazard"],
            additional_hazards=["In-session additional hazard"],
        )

        names = service._same_sector_hazard_names_for_duplicate_check(session)

        self.assertIn("System employment shock", names)
        self.assertIn("Additional tariff shock", names)
        self.assertIn("Shared custom affordability shock", names)
        self.assertIn("User energy poverty shock", names)
        self.assertIn("In-session system hazard", names)

    def test_custom_hazard_duplicate_status_updates_from_same_sector_system_hazard(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Regional employment shock and left-behind energy regions"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_dimension_check",
            custom_hazard={
                "raw_text": "Regional employment shock and left-behind energy regions",
                "reason": "Coal phase-out causes job losses in fossil-dependent regions.",
                "evidence": "",
                "affected_groups": [],
                "duplicate_candidates": [],
            },
        )

        with patch.object(validator, "ask_llm_chat", _unavailable):
            response = _run(
                service._run_custom_hazard_dimension_check("session-1", session)
            )

        self.assertEqual(response.step, "custom_hazard_duplicate_confirmation")
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Regional employment shock", duplicate_card["reason"])

    def test_valid_initial_custom_hazard_updates_duplicate_status_from_user_hazards(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(
                        all=lambda: [
                            'Regional employment shock and "left-behind" energy regions'
                        ]
                    ),
                ]
            )
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                'Regional employment shock and "left-behind" energy regions',
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_duplicate_confirmation")
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Regional employment shock", duplicate_card["reason"])

    def test_continue_with_custom_hazard_duplicate_asks_clarification_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_duplicate_confirmation",
            pending_hazard="Regional employment shock",
            suggested_duplicate_hazard="Existing regional employment shock",
            custom_hazard={
                "raw_text": "Regional employment shock",
                "affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses."}
                ],
            },
        )

        response = _run(
            service._handle_hazard_duplicate_suggestion(
                "session-1",
                session,
                "Continue with custom hazard",
            )
        )

        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "custom_hazard_clarification")
        self.assertTrue(session.custom_hazard["duplicate_override_confirmed"])
        self.assertEqual(session.pending_hazard, "Regional employment shock")

    def test_hazard_clarification_reason_asks_evidence_decision(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_reason",
            pending_hazard="Regional employment shock",
            custom_hazard={"raw_text": "Regional employment shock"},
        )

        response = service._capture_hazard_reason(
            "session-1",
            session,
            "Reason: Coal phase-out policy can cause job losses in coal-dependent regions.",
        )

        self.assertEqual(response.step, "custom_hazard_evidence_decision")
        self.assertEqual([option.label for option in response.options], ["Yes", "No"])
        self.assertEqual(session.phase, "add_hazard_evidence_decision")
        self.assertIn("Coal phase-out policy", session.pending_hazard_reason)

    def test_unclear_hazard_definition_asks_clarification_before_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
            pending_hazard="Digital energy problems",
            custom_hazard={
                "raw_text": "Digital energy problems",
                "resolved_hazard_text": "Digital energy problems",
                "title_validation_reason": "The transition link is implied but the harm is unclear.",
            },
        )
        grounding_result = {
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 3,
                    "reason": "The negative impact is not defined.",
                    "needs_clarification": True,
                    "clarification_question": "What specific harm or negative impact does this create?",
                },
                "twin_transition_policy_fit": {
                    "score": 7,
                    "reason": "Digital transition policy is implied.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 7,
                    "reason": "Energy services are implied.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 7,
                    "reason": "The selected place is acceptable.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 7,
                    "reason": "Affected groups can be reviewed later.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
            "overall_score": 64,
            "next_action": "ask_clarification",
            "status": "needs_clarification",
        }

        with patch(
            "app.services.chat_hazard_creation.validate_custom_hazard_dimensions",
            AsyncMock(return_value=grounding_result),
        ):
            response = _run(
                service._start_custom_hazard_grounding_check(
                    "session-1",
                    session,
                    "Digital energy problems",
                )
            )

        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "custom_hazard_clarification")
        self.assertIn("What specific harm", response.bot_message)
        self.assertNotEqual(session.phase, "add_hazard_reason")

    def test_clear_hazard_context_asks_reason_before_evidence_decision(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
            pending_hazard="Coal workers face job losses due to coal phase-out policy.",
            custom_hazard={
                "raw_text": "Coal workers face job losses due to coal phase-out policy.",
                "resolved_hazard_text": "Coal workers face job losses due to coal phase-out policy.",
                "title_validation_reason": "Coal phase-out policy can cause job losses in coal-dependent regions.",
            },
        )
        grounding_result = {
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 8,
                    "reason": "The harm is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 8,
                    "reason": "The policy link is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 8,
                    "reason": "The sector fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 8,
                    "reason": "The place fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 8,
                    "reason": "Coal workers are an affected group.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [{"group": "Coal workers", "reason": "Job losses.", "source": "title"}],
            "duplicate_candidates": [],
            "overall_score": 80,
            "next_action": "validate",
            "status": "ready",
        }

        with patch(
            "app.services.chat_hazard_creation.validate_custom_hazard_dimensions",
            AsyncMock(return_value=grounding_result),
        ):
            response = _run(
                service._start_custom_hazard_grounding_check(
                    "session-1",
                    session,
                    "Coal workers face job losses due to coal phase-out policy.",
                )
            )

        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "add_hazard_reason")
        self.assertIsNone(session.pending_hazard_reason)
        self.assertFalse(session.custom_hazard.get("evidence_decision_asked"))
        self.assertIn("reason or justification", response.bot_message)

    def test_generic_extracted_affected_group_asks_clarification(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_dimension_check",
            pending_hazard="People face higher bills due to renewable grid upgrade tariffs.",
            custom_hazard={
                "raw_text": "People face higher bills due to renewable grid upgrade tariffs.",
                "reason": "Renewable grid upgrade tariffs raise bills.",
                "evidence_decision_asked": True,
            },
        )
        grounding_result = {
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 8,
                    "reason": "The harm is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 8,
                    "reason": "The policy link is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 8,
                    "reason": "The sector fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 8,
                    "reason": "The place fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 8,
                    "reason": "A group is named.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [{"group": "People", "reason": "Higher bills.", "source": "llm"}],
            "duplicate_candidates": [],
            "overall_score": 80,
            "next_action": "review_groups",
            "status": "ready",
        }

        with patch(
            "app.services.chat_hazard_creation.validate_custom_hazard_dimensions",
            AsyncMock(return_value=grounding_result),
        ):
            response = _run(service._run_custom_hazard_dimension_check("session-1", session))

        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(session.phase, "custom_hazard_clarification")
        self.assertIn("too broad", response.bot_message)
        self.assertIn("Which specific group", response.bot_message)

    def test_hazard_evidence_decision_yes_asks_for_evidence(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )

        response = _run(
            service._handle_hazard_evidence_decision("session-1", session, "Yes")
        )

        self.assertEqual(response.step, "custom_hazard_evidence")
        self.assertEqual(response.input_mode, "evidence_only")
        self.assertEqual(
            [option.label for option in response.options],
            ["Go back to list of hazards", "Skip"],
        )
        self.assertEqual(session.phase, "add_hazard_evidence_input")

    def test_hazard_evidence_input_skip_validates_with_stored_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_input",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )
        service._validate_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="validated",
                options=[],
                session=session.summary(),
            )
        )

        response = _run(service._capture_hazard_evidence("session-1", session, "Skip"))

        self.assertEqual(response.bot_message, "validated")
        service._validate_custom_hazard.assert_awaited_once()
        self.assertEqual(
            service._validate_custom_hazard.await_args.args[2],
            "Reason: Coal phase-out policy can cause job losses.",
        )

    def test_hazard_evidence_decision_no_validates_with_stored_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )
        service._validate_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="validated",
                options=[],
                session=session.summary(),
            )
        )

        response = _run(
            service._handle_hazard_evidence_decision("session-1", session, "No")
        )

        self.assertEqual(response.bot_message, "validated")
        service._validate_custom_hazard.assert_awaited_once()
        self.assertEqual(
            service._validate_custom_hazard.await_args.args[2],
            "Reason: Coal phase-out policy can cause job losses.",
        )

    def test_hazard_evidence_decision_open_text_yes_asks_for_evidence(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )

        response = _run(
            service._handle_hazard_evidence_decision(
                "session-1",
                session,
                "I want to add evidence",
            )
        )

        self.assertEqual(response.step, "custom_hazard_evidence")
        self.assertEqual(response.input_mode, "evidence_only")
        self.assertEqual(session.phase, "add_hazard_evidence_input")

    def test_hazard_evidence_decision_open_text_no_validates(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )
        service._validate_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="validated",
                options=[],
                session=session.summary(),
            )
        )

        response = _run(
            service._handle_hazard_evidence_decision(
                "session-1",
                session,
                "continue without evidence",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            service._validate_custom_hazard.await_args.args[2],
            "Reason: Coal phase-out policy can cause job losses.",
        )

    def test_hazard_evidence_decision_open_text_no_i_dont_have_validates(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )
        service._validate_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="validated",
                options=[],
                session=session.summary(),
            )
        )

        response = _run(
            service._handle_hazard_evidence_decision(
                "session-1",
                session,
                "no i don't have",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            service._validate_custom_hazard.await_args.args[2],
            "Reason: Coal phase-out policy can cause job losses.",
        )

    def test_hazard_evidence_decision_no_i_dont_have_skips_common_quality_gate(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
        )

        self.assertFalse(
            service._should_check_common_user_input_quality(
                session,
                "no i don't have",
            )
        )

    def test_hazard_evidence_decision_accepts_url_in_open_text(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="add_hazard_evidence_decision",
            pending_hazard="Regional employment shock",
            pending_hazard_reason="Coal phase-out policy can cause job losses.",
            custom_hazard={"raw_text": "Regional employment shock"},
        )
        service._validate_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="validated",
                options=[],
                session=session.summary(),
            )
        )

        response = _run(
            service._handle_hazard_evidence_decision(
                "session-1",
                session,
                "Use this evidence https://example.org/report.pdf please",
            )
        )

        self.assertEqual(response.bot_message, "validated")
        self.assertEqual(
            service._validate_custom_hazard.await_args.args[2],
            (
                "Reason: Coal phase-out policy can cause job losses.\n"
                "Evidence: Evidence URL: https://example.org/report.pdf"
            ),
        )

    def test_hazard_creation_input_is_not_routed_to_grounded_questions(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._is_invalid_user_text = MagicMock(return_value=False)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._handle_anytime_grounded_question = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_input",
                bot_message="Grounded answer",
                options=[],
                session={},
                error=False,
            )
        )
        service._capture_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="Hazard validation",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Transport",
            phase="custom_hazard_input",
        )

        response = _run(
            service._chat_response(
                "session-1",
                session,
                "EV home-charging disadvantage for renters and apartment dwellers",
            )
        )

        self.assertEqual(response.bot_message, "Hazard validation")
        service._capture_custom_hazard.assert_awaited_once()
        service._handle_anytime_grounded_question.assert_not_awaited()

    def test_hazard_selection_is_not_routed_to_grounded_questions(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._is_invalid_user_text = MagicMock(return_value=False)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._handle_anytime_grounded_question = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="hazard_profile_selection",
                bot_message="Grounded answer",
                options=[],
                session={},
                error=False,
            )
        )
        service._is_saved_custom_hazard = MagicMock(return_value=False)
        service._record_activity = MagicMock()
        service._hazard_profiles_response = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="socio_demographic_review",
                bot_message="### Socio-demographic profiles most affected by Heat stress",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            phase="hazard_profile_selection",
            hazards=["Heat stress", "Energy poverty"],
            hazard_profiles={"Heat stress": [{"name": "Older adults"}]},
        )

        response = _run(
            service._chat_response("session-1", session, "Heat stress")
        )

        self.assertEqual(response.step, "socio_demographic_review")
        self.assertEqual(session.selected_hazard, "Heat stress")
        service._hazard_profiles_response.assert_awaited_once()
        service._handle_anytime_grounded_question.assert_not_awaited()

    def test_fuzzy_current_option_match_blocks_grounded_question(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            phase="hazard_profile_selection",
            hazards=["Heat stress", "Energy poverty"],
            hazard_profiles={"Heat stress": [{"name": "Older adults"}]},
        )

        self.assertTrue(
            service._matches_current_step_option(session, "Heat stres")
        )

    def test_selected_hazard_profiles_show_when_population_context_is_missing(self):
        service = ChatService.__new__(ChatService)
        service._stored_hazard_profiles = MagicMock(
            return_value=[
                {
                    "name": "Countries with higher Electricity consumption",
                    "explanation": "Higher consumption increases concern.",
                }
            ]
        )
        service._stored_user_hazard_profiles = MagicMock(return_value=[])
        service._is_additional_hazard = MagicMock(return_value=False)
        service._is_saved_custom_hazard = MagicMock(return_value=False)
        service._profiles_with_population_context = AsyncMock(return_value=[])
        service._get_hazard_profiles_from_llm = AsyncMock(return_value=[])
        service._ensure_system_hazard = MagicMock(return_value=None)

        session = ChatSession(
            selected_hazard="Heating and cooling costs increase",
            hazard_profiles={
                "Heating and cooling costs increase": [
                    {
                        "name": "Countries with higher Electricity consumption",
                        "explanation": "Higher consumption increases concern.",
                    }
                ]
            },
        )

        response = _run(
            service._hazard_profiles_response(
                "session-1",
                session,
                "Heating and cooling costs increase",
            )
        )

        self.assertIn("Countries with higher Electricity consumption", response.bot_message)
        self.assertNotIn(
            "No clearly supported socio-demographic profiles were returned",
            response.bot_message,
        )

    def test_early_invalid_custom_hazard_still_updates_duplicate_status(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Heating and cooling costs increase"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": (
                    "This input does not discuss a hazard arising from Green and Digital "
                    "Transition policies in Europe for the selected sector of Energy."
                ),
            }
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Heating and cooling costs increase",
            )
        )

        self.assertTrue(response.error)
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        policy_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Twin transition policy fit"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Heating and cooling costs increase", duplicate_card["reason"])
        self.assertEqual(policy_card["status"], "REJECTED")

    def test_reason_step_failure_still_updates_duplicate_status(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Heating and cooling costs increase"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        service._validate_input_quality = AsyncMock(
            return_value={
                "valid": False,
                "reason": "The reason does not connect the hazard to twin-transition policy impacts.",
            }
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_validation",
            pending_hazard="Heating and cooling costs increase",
            custom_hazard={
                "raw_text": "Heating and cooling costs increase",
                "duplicate_candidates": [],
                "dimension_scores": {},
            },
        )

        response = _run(
            service._validate_custom_hazard(
                "session-1",
                session,
                "Reason: because it is bad",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_validation")
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        policy_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Twin transition policy fit"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Heating and cooling costs increase", duplicate_card["reason"])
        self.assertEqual(policy_card["status"], "REJECTED")

    def test_score_threshold_moves_to_validation(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Low-income households in Saxony face higher renewable energy grid costs from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {
                        "duplicate_override_confirmed": True,
                        "confirmed_affected_groups": [
                            {"group": "Low-income households", "reason": "Confirmed."}
                        ],
                    },
                )
            )

        self.assertGreaterEqual(result["overall_score"], 75)
        self.assertEqual(result["next_action"], "validate")

    def test_high_score_with_unconfirmed_groups_moves_to_group_review(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Low-income households in Saxony face higher renewable energy grid costs from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"duplicate_override_confirmed": True},
                )
            )

        self.assertGreaterEqual(result["overall_score"], 75)
        self.assertEqual(result["next_action"], "review_groups")

    def test_core_dimension_gap_asks_clarification_before_group_review(self):
        llm_payload = {
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 8,
                    "reason": "A harm is described.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 3,
                    "reason": "The transition policy link is unclear.",
                    "needs_clarification": True,
                    "clarification_question": "Which green or digital transition policy causes this harm?",
                },
                "selected_sector_fit": {
                    "score": 8,
                    "reason": "Sector fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 8,
                    "reason": "Place fit is clear.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 8,
                    "reason": "Affected groups are named.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [{"group": "Coal workers", "reason": "Job losses."}],
            "duplicate_candidates": [],
        }

        with patch.object(validator, "_llm_dimension_validation", AsyncMock(return_value=llm_payload)):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Coal workers face job losses.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(result["next_action"], "ask_clarification")
        self.assertTrue(
            result["dimension_scores"]["twin_transition_policy_fit"]["needs_clarification"]
        )

    def test_duplicate_override_survives_grounding_text_with_reason(self):
        hazard = (
            "Low-income households in Saxony face higher renewable energy grid costs "
            "from green transition policy."
        )
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    (
                        f"{hazard}\n"
                        "Reason: Grid upgrade tariff pass-through raises bills for low-income households."
                    ),
                    "Energy",
                    "Germany",
                    "Saxony",
                    [hazard],
                    {
                        "raw_text": hazard,
                        "duplicate_override_confirmed": True,
                    },
                )
            )

        self.assertTrue(result["duplicate_candidates"])
        self.assertNotEqual(result["next_action"], "ask_duplicate_confirmation")

    def test_score_ten_overrides_llm_needs_clarification_flag(self):
        llm_payload = {
            "dimension_scores": {
                "twin_transition_policy_fit": {
                    "score": 10,
                    "reason": "Strong policy fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain policy fit?",
                },
                "selected_sector_fit": {
                    "score": 10,
                    "reason": "Strong sector fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain sector fit?",
                },
                "country_region_fit": {
                    "score": 10,
                    "reason": "Strong place fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain place fit?",
                },
                "affected_groups_fit": {
                    "score": 10,
                    "reason": "Strong group fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain groups?",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        with patch.object(validator, "_llm_dimension_validation", AsyncMock(return_value=llm_payload)):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Retrofit cost burdens from green transition policy.",
                    "Housing",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["next_action"], "validate")
        for dimension in result["dimension_scores"].values():
            self.assertFalse(dimension["needs_clarification"])
            self.assertEqual(dimension["clarification_question"], "")

        cards = validator.build_custom_hazard_grounding_status(
            {
                "dimension_scores": result["dimension_scores"],
                "overall_score": result["overall_score"],
                "status": result["status"],
            }
        )
        dimension_cards = cards[:3]
        self.assertTrue(all(card["status"] == "SUPPORTED" for card in dimension_cards))
        self.assertTrue(all(card["score"] == 100 for card in dimension_cards))

    def test_flat_score_after_round_two_moves_to_validation_when_required_dimensions_resolved(self):
        llm_payload = {
            "dimension_scores": {
                "twin_transition_policy_fit": {
                    "score": 6,
                    "reason": "Enough policy fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 6,
                    "reason": "Enough sector fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 6,
                    "reason": "Enough place fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 6,
                    "reason": "Enough group fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        with patch.object(validator, "_llm_dimension_validation", AsyncMock(return_value=llm_payload)):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 2, "scores": [42, 58]},
                )
            )

        self.assertLess(result["overall_score"] - 58, 3)
        self.assertEqual(result["next_action"], "validate")

    def test_flat_score_with_unresolved_required_gap_rejects_instead_of_validating(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 2, "scores": [42, 55]},
                )
            )

        self.assertLess(result["overall_score"] - 55, 3)
        self.assertEqual(result["next_action"], "reject")

    def test_clarification_can_continue_beyond_round_three_when_score_is_improving(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 4, "scores": [35, 42, 48, 51]},
                )
            )

        self.assertGreaterEqual(result["overall_score"] - 51, 3)
        self.assertEqual(result["next_action"], "ask_clarification")

    def test_invalid_hazard_does_not_continue_to_reason_step(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": "This is not a hazard related to the selected transition context.",
            }
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "I like sunny weather",
            )
        )

        self.assertTrue(response.error)
        self.assertNotEqual(response.input_mode, "reason_evidence")
        self.assertIn("personal preference", response.bot_message)
        self.assertIn("not a policy hazard", response.bot_message)
        self.assertNotIn(
            "This does not describe a clear hazard, risk, or negative impact",
            response.bot_message,
        )
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(
            response.custom_hazard["dimension_scores"]["twin_transition_policy_fit"]["status"],
            "REJECTED",
        )
        status_cards = {
            card["title"]: card
            for card in response.custom_hazard_grounding_status
        }
        self.assertEqual(
            status_cards["Twin transition policy fit"]["status"],
            "REJECTED",
        )

    def test_custom_hazard_classifier_parses_valid_json_response(self):
        response = ChatService._parse_custom_hazard_classifier_response(
            """
            {
              "status": "valid",
              "is_valid": true,
              "validation_code": "valid_hazard",
              "confidence": 0.97,
              "normalized_hazard": "Renters face higher housing costs when landlords pass renovation expenses through rent increases.",
              "reason": "Residential energy-performance renovation requirements can increase housing costs for renters, which fits the Housing sector.",
              "transition_link": {"is_present": true, "type": "green", "intervention": "Residential energy-performance renovation requirements"},
              "sector_validation": {"selected_sector": "Housing", "detected_sector": "Housing", "fits_selected_sector": true, "reason": "The mechanism is residential renovation."},
              "context_validation": {"country": "Germany", "country_fit": true, "region": null, "region_fit": true, "reason": "Plausible without a region-specific claim."},
              "affected_group": {"is_clear": true, "groups": ["Renters"]},
              "negative_consequence": "Higher housing costs",
              "suggested_rewrite": null,
              "clarification_question": null,
              "duplicate": {"is_duplicate": false, "matched_hazard_id": null, "matched_hazard_name": null}
            }
            """
        )

        self.assertIsNotNone(response)
        self.assertTrue(response["valid"])
        self.assertEqual(response["validation_code"], "valid_hazard")
        self.assertIn("renovation requirements", response["reason"])

    def test_custom_hazard_classifier_rejects_invalid_json(self):
        response = ChatService._parse_custom_hazard_classifier_response("{not json")

        self.assertIsNone(response)

    def test_custom_hazard_classifier_rejects_missing_required_json_fields(self):
        response = ChatService._parse_custom_hazard_classifier_response(
            '{"status":"valid","is_valid":true,"confidence":0.9}'
        )

        self.assertIsNone(response)

    def test_custom_hazard_classifier_low_confidence_needs_clarification(self):
        response = ChatService._parse_custom_hazard_classifier_response(
            """
            {
              "status": "valid",
              "is_valid": true,
              "validation_code": "valid_hazard",
              "confidence": 0.40,
              "normalized_hazard": "Digital energy problems",
              "reason": "The transition link is implied but unclear.",
              "transition_link": {"is_present": true, "type": "digital", "intervention": "Digital energy services"},
              "sector_validation": {"selected_sector": "Energy", "detected_sector": "Energy", "fits_selected_sector": true, "reason": "Digital energy service mechanism."},
              "context_validation": {"country": "Germany", "country_fit": true, "region": null, "region_fit": true, "reason": "Broadly plausible."},
              "affected_group": {"is_clear": false, "groups": []},
              "negative_consequence": null,
              "suggested_rewrite": null,
              "clarification_question": null,
              "duplicate": {"is_duplicate": false, "matched_hazard_id": null, "matched_hazard_name": null}
            }
            """
        )

        self.assertIsNotNone(response)
        self.assertFalse(response["valid"])
        self.assertEqual(response["status"], "Ambiguous")

    def test_sector_mismatch_updates_sector_fit_dimension(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Electric vehicle charging costs for taxi drivers",
            )
        )

        self.assertTrue(response.error)
        self.assertNotEqual(response.input_mode, "reason_evidence")
        self.assertEqual(
            response.custom_hazard["dimension_scores"]["selected_sector_fit"]["status"],
            "REJECTED",
        )
        status_cards = {
            card["title"]: card
            for card in response.custom_hazard_grounding_status
        }
        self.assertEqual(status_cards["Sector fit"]["status"], "REJECTED")

    def test_sector_mismatch_suggests_rewrite_for_selected_sector(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Transport",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Households lose access to affordable clean heating",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("Suggested rewrite direction", response.bot_message)
        self.assertIn("For Transport", response.bot_message)
        self.assertIn("Keep the affected group and harm", response.bot_message)
        self.assertIn("selected-sector transition policy", response.bot_message)

    def test_transition_policy_rejection_maps_to_policy_fit_dimension(self):
        reason = (
            "This input does not discuss a hazard arising from the Green and "
            "Digital Transition policies in Europe for the selected sector of Energy."
        )

        self.assertEqual(
            ChatService._custom_hazard_rejection_dimension(reason),
            "twin_transition_policy_fit",
        )

    def test_profile_impact_reason_card_is_not_displayed(self):
        cards = validator.build_custom_hazard_grounding_status(
            {
                "added_affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses affect income."}
                ]
            }
        )

        self.assertNotIn("Custom profile impact reason", [card["title"] for card in cards])

    def test_valid_hazard_after_invalid_hazard_clears_stale_dimension_status(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": "This is not a hazard related to the selected transition context.",
            }
        )

        invalid_response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "I like sunny weather",
            )
        )
        self.assertTrue(invalid_response.error)
        self.assertEqual(
            invalid_response.custom_hazard["dimension_scores"]["twin_transition_policy_fit"]["status"],
            "REJECTED",
        )

        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )
        valid_response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Renovation cost burden from green retrofit mandates for low-income tenants",
            )
        )

        self.assertFalse(valid_response.error)
        self.assertEqual(valid_response.step, "custom_hazard_clarification")
        self.assertEqual(valid_response.input_mode, "textarea")
        self.assertEqual(session.phase, "add_hazard_reason")
        self.assertNotEqual(
            session.custom_hazard["dimension_scores"]["twin_transition_policy_fit"].get("status"),
            "REJECTED",
        )

    def test_ambiguous_custom_hazard_title_asks_for_clarification_before_reason(self):
        service = ChatService.__new__(ChatService)
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "status": "needs_clarification",
                "valid": False,
                "is_valid": False,
                "reason": "The affected group and concrete consequence are unclear.",
                "validation_code": "unclear_hazard",
                "confidence": 0.84,
                "normalized_hazard": "Digital energy services leave people behind",
                "clarification_question": (
                    "Which digital energy service causes the harm, who is affected, "
                    "and what consequence do they experience?"
                ),
            }
        )
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            phase="custom_hazard_input",
        )

        with patch(
            "app.services.chat_hazard_creation.deterministic_custom_hazard_input_review",
            return_value=None,
        ):
            response = _run(
                service._capture_custom_hazard(
                    "session-1",
                    session,
                    "Digital energy services leave people behind",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_title_clarification")
        self.assertEqual(response.input_mode, "text")
        self.assertEqual(session.phase, "custom_hazard_title_clarification")
        self.assertEqual(session.custom_hazard["title_validation_status"], "needs_clarification")
        self.assertEqual(session.custom_hazard["title_clarification_round"], 1)
        self.assertIn("Which digital energy service", response.bot_message)

    def test_deterministic_unclear_custom_hazard_title_asks_for_clarification(self):
        service = ChatService.__new__(ChatService)
        service._review_custom_hazard_input = AsyncMock()
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            phase="custom_hazard_input",
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Digital energy services leave people behind",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_title_clarification")
        self.assertEqual(response.input_mode, "text")
        self.assertEqual(session.phase, "custom_hazard_title_clarification")
        self.assertEqual(session.custom_hazard["title_validation_status"], "needs_clarification")
        service._review_custom_hazard_input.assert_not_called()

    def test_reason_context_review_clarification_stays_unsaved(self):
        service = ChatService.__new__(ChatService)
        service._validate_input_quality = AsyncMock(return_value={"valid": True, "reason": "Clear."})
        service._validate_hazard_against_stats = AsyncMock(
            return_value={"valid": True, "reason": "Compatible with sector context."}
        )
        service._review_custom_hazard_context = AsyncMock(
            return_value={
                "status": "clarification",
                "valid": False,
                "question": "Which Bavarian tariff or grid-upgrade pathway creates this burden?",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            phase="custom_hazard_validation",
            pending_hazard="Low-income households face higher electricity bills from renewable grid upgrade tariffs",
            custom_hazard=validator.default_custom_hazard_state(),
        )

        response = _run(
            service._validate_custom_hazard(
                "session-1",
                session,
                (
                    "Reason: Renewable grid upgrades can push tariff costs onto low-income households.\n"
                    "Evidence: Not provided"
                ),
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazards")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "add_hazard_clarification")
        self.assertIsNone(session.accepted_custom_hazard)
        self.assertEqual(
            session.pending_hazard_reason,
            "Renewable grid upgrades can push tariff costs onto low-income households.",
        )
        self.assertIn("Which Bavarian tariff", response.bot_message)

    def test_dimension_grounding_clarification_lists_pending_questions(self):
        service = ChatService.__new__(ChatService)
        service._same_sector_hazard_names_for_duplicate_check = MagicMock(return_value=[])
        service._same_scope_custom_hazard_names_for_duplicate_check = MagicMock(return_value=[])
        service._dedupe_hazard_names = MagicMock(return_value=[])
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Transport",
            phase="custom_hazard_dimension_check",
            pending_hazard="Clean mobility access problems",
            custom_hazard={
                **validator.default_custom_hazard_state(),
                "raw_text": "Clean mobility access problems",
                "reason": (
                    "Low-income commuters may face access barriers when clean mobility policies "
                    "change car and public transport costs."
                ),
                "evidence": "",
            },
        )
        dimension_result = {
            "overall_score": 44,
            "status": "needs_clarification",
            "next_action": "ask_clarification",
            "affected_groups": [],
            "duplicate_candidates": [],
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 5,
                    "reason": "A negative access problem is described.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "twin_transition_policy_fit": {
                    "score": 3,
                    "reason": "The policy pathway is too broad.",
                    "needs_clarification": True,
                    "clarification_question": "Which clean mobility policy creates the access problem?",
                },
                "selected_sector_fit": {
                    "score": 4,
                    "reason": "The transport mechanism needs detail.",
                    "needs_clarification": True,
                    "clarification_question": "How is this connected to the selected Transport sector?",
                },
                "country_region_fit": {
                    "score": 6,
                    "reason": "Bavaria is named.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 3,
                    "reason": "Affected groups need clearer grounding.",
                    "needs_clarification": True,
                    "clarification_question": "Which population groups are affected by this hazard, and why?",
                },
            },
        }

        with patch(
            "app.services.chat_hazard_creation.validate_custom_hazard_dimensions",
            AsyncMock(return_value=dimension_result),
        ):
            response = _run(service._run_custom_hazard_dimension_check("session-1", session))

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "custom_hazard_clarification")
        self.assertEqual(
            session.custom_hazard["pending_clarification_questions"],
            [
                "Which clean mobility policy creates the access problem?",
                "How is this connected to the selected Transport sector?",
            ],
        )
        self.assertIn("I need a little more detail", response.bot_message)

    def test_custom_hazard_title_clarification_can_resolve_to_justification_clarification(self):
        service = ChatService.__new__(ChatService)
        resolved = (
            "Older adults face exclusion from electricity account services "
            "when billing moves online"
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "status": "valid",
                "valid": True,
                "is_valid": True,
                "reason": "The clarification identifies a concrete affected group and consequence.",
                "validation_code": "valid_hazard",
                "confidence": 0.9,
                "normalized_hazard": resolved,
                "transition_link": {
                    "measure_or_policy": "Digital electricity billing",
                    "causal_mechanism": "Offline support is withdrawn.",
                },
                "sector_validation": {"detected_sector": "Energy"},
                "affected_group": {"groups": ["Older adults"]},
                "negative_consequence": "Service exclusion",
            }
        )
        service._match_hazard = MagicMock(return_value=None)
        service._same_sector_hazard_names_for_duplicate_check = MagicMock(return_value=[])
        service._local_similar_hazards = MagicMock(return_value=[])
        service._semantic_hazard_duplicate_check = AsyncMock(return_value={"duplicate": False})
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            phase="custom_hazard_title_clarification",
            pending_hazard="Digital energy services leave people behind",
        )
        service._initialize_custom_hazard_title_state(
            session,
            "Digital energy services leave people behind",
        )
        question = "Which digital energy service causes the harm, who is affected, and what happens?"
        session.phase = "custom_hazard_title_clarification"
        session.pending_hazard = "Digital energy services leave people behind"
        session.pending_hazard_title_clarification_question = question
        session.custom_hazard["title_validation_status"] = "needs_clarification"
        session.custom_hazard["title_clarification_round"] = 1
        session.custom_hazard["title_clarification_questions"] = [question]

        response = _run(
            service._handle_custom_hazard_title_clarification(
                "session-1",
                session,
                "Older adults cannot use online-only electricity billing.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_clarification")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, "custom_hazard_clarification")
        self.assertEqual(session.pending_hazard, resolved)
        self.assertIsNone(session.pending_hazard_title_clarification_question)
        self.assertEqual(session.custom_hazard["resolved_hazard_text"], resolved)
        self.assertEqual(session.custom_hazard["title_validation_status"], "valid")
        service._review_custom_hazard_input.assert_awaited_once()
        self.assertIn(
            "clarification_context",
            service._review_custom_hazard_input.await_args.kwargs,
        )

    def test_custom_hazard_title_clarification_rejects_non_answer(self):
        service = ChatService.__new__(ChatService)
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "status": "valid",
                "valid": True,
                "is_valid": True,
                "reason": "Should not be used.",
                "validation_code": "valid_hazard",
                "confidence": 0.9,
                "normalized_hazard": "Invalidly accepted hazard",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            phase="custom_hazard_title_clarification",
            pending_hazard="Digital energy services leave people behind",
        )
        service._initialize_custom_hazard_title_state(
            session,
            "Digital energy services leave people behind",
        )
        question = "Which specific population group is affected, and what concrete consequence do they experience?"
        session.phase = "custom_hazard_title_clarification"
        session.pending_hazard = "Digital energy services leave people behind"
        session.pending_hazard_title_clarification_question = question
        session.custom_hazard["title_validation_status"] = "needs_clarification"
        session.custom_hazard["title_clarification_round"] = 1
        session.custom_hazard["title_clarification_questions"] = [question]

        response = _run(
            service._handle_custom_hazard_title_clarification(
                "session-1",
                session,
                "I don't know",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_title_clarification")
        self.assertEqual(response.input_mode, "text")
        self.assertEqual(session.phase, "custom_hazard_title_clarification")
        self.assertEqual(session.pending_hazard, "Digital energy services leave people behind")
        self.assertEqual(session.pending_hazard_title_clarification_answers, [])
        self.assertIn("does not clarify the hazard", response.bot_message)
        service._review_custom_hazard_input.assert_not_called()

    def test_custom_hazard_title_clarification_rejects_short_ambiguous_invalid_answers(self):
        ambiguous_answers = ["ok", "yes", "users", "abcdf"]
        for answer in ambiguous_answers:
            with self.subTest(answer=answer):
                service = ChatService.__new__(ChatService)
                service._review_custom_hazard_input = AsyncMock(
                    return_value={
                        "status": "valid",
                        "valid": True,
                        "is_valid": True,
                        "reason": "Should not be used.",
                        "validation_code": "valid_hazard",
                        "confidence": 0.9,
                        "normalized_hazard": "Invalidly accepted hazard",
                    }
                )
                session = ChatSession(
                    country="Germany",
                    region="Saxony",
                    sector="Energy",
                    phase="custom_hazard_title_clarification",
                    pending_hazard="Digital energy services leave people behind",
                )
                service._initialize_custom_hazard_title_state(
                    session,
                    "Digital energy services leave people behind",
                )
                question = "Which specific population group is affected, and what concrete consequence do they experience?"
                session.phase = "custom_hazard_title_clarification"
                session.pending_hazard = "Digital energy services leave people behind"
                session.pending_hazard_title_clarification_question = question
                session.custom_hazard["title_validation_status"] = "needs_clarification"
                session.custom_hazard["title_clarification_round"] = 1
                session.custom_hazard["title_clarification_questions"] = [question]

                response = _run(
                    service._handle_custom_hazard_title_clarification(
                        "session-1",
                        session,
                        answer,
                    )
                )

                self.assertTrue(response.error)
                self.assertEqual(response.step, "custom_hazard_title_clarification")
                self.assertEqual(session.pending_hazard_title_clarification_answers, [])
                service._review_custom_hazard_input.assert_not_called()

    def test_custom_hazard_title_second_clarification_rejects_question_reply(self):
        service = ChatService.__new__(ChatService)
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "status": "valid",
                "valid": True,
                "is_valid": True,
                "reason": "Should not be used.",
                "validation_code": "valid_hazard",
                "confidence": 0.9,
                "normalized_hazard": "Invalidly accepted hazard",
            }
        )
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            phase="custom_hazard_title_clarification",
            pending_hazard="Digital energy services leave people behind",
            pending_hazard_title_clarification_answers=[
                "Older adults are affected, but I am not sure how."
            ],
        )
        service._initialize_custom_hazard_title_state(
            session,
            "Digital energy services leave people behind",
        )
        first_question = "Which specific population group is affected?"
        second_question = "What concrete consequence do they experience?"
        session.phase = "custom_hazard_title_clarification"
        session.pending_hazard = "Digital energy services leave people behind"
        session.pending_hazard_title_clarification_answers = [
            "Older adults are affected, but I am not sure how."
        ]
        session.pending_hazard_title_clarification_question = second_question
        session.custom_hazard["title_validation_status"] = "needs_clarification"
        session.custom_hazard["title_clarification_round"] = 2
        session.custom_hazard["title_clarification_questions"] = [
            first_question,
            second_question,
        ]
        session.custom_hazard["title_clarification_answers"] = [
            "Older adults are affected, but I am not sure how."
        ]

        response = _run(
            service._handle_custom_hazard_title_clarification(
                "session-1",
                session,
                "what to do",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_title_clarification")
        self.assertEqual(response.input_mode, "text")
        self.assertEqual(session.phase, "custom_hazard_title_clarification")
        self.assertEqual(
            session.pending_hazard_title_clarification_answers,
            ["Older adults are affected, but I am not sure how."],
        )
        self.assertIn("question or request", response.bot_message)
        service._review_custom_hazard_input.assert_not_called()

    def test_fuzzy_confirmation_does_not_show_mitigation_adoption_option(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(phase="custom_hazard_input")

        response = service._fuzzy_confirmation_step(
            "session-1",
            session,
            "Go back to list of hazards",
        )

        labels = [option.label for option in response.options]
        self.assertEqual(labels, ["Yes", "No"])
        self.assertNotIn("Adopt mitigation proposal suggested above", labels)

    def test_custom_hazard_added_message_shows_only_added_hazard_details(self):
        message = render_message(
            "hazard_added.md",
            hazard="Regional employment shock",
            reason="Coal phase-out causes job losses.",
            evidence="Not provided",
            affected_population_groups="- **Coal workers**",
        )

        self.assertIn("Custom hazard added successfully", message)
        self.assertIn("Regional employment shock", message)
        self.assertIn("Coal workers", message)
        self.assertNotIn("Updated Hazard List", message)
        self.assertNotIn("following hazards", message)

    def test_additional_hazards_methodology_cta_survives_message_sanitizer(self):
        session = ChatSession(
            sector="Energy",
            region="Saxony",
            additional_hazards=["Regional employment shock"],
            hazard_profiles={
                "Regional employment shock": [{"name": "Coal workers"}],
            },
        )

        message = render_message(
            "hazards_overview.md",
            sector="Energy",
            region="Saxony",
            hazards=format_hazards(session),
        )

        self.assertIn("<button", message)
        self.assertIn("additional-hazards-methodology-cta", message)
        self.assertIn('data-open-methodology="true"', message)
        self.assertIn("Show methodologies", message)

    def test_custom_hazard_added_summary_keeps_left_panel_hazard_context(self):
        session = ChatSession(
            phase="hazards",
            accepted_custom_hazard="New custom hazard",
            hazards=["Top hazard 1", "Top hazard 2", "Top hazard 3", "Top hazard 4"],
            additional_hazards=["Expert-added hazard"],
            custom_hazards=["New custom hazard"],
            hazard_profiles={
                "Top hazard 1": [{"name": "Workers"}],
                "Top hazard 2": [{"name": "Residents"}],
                "Top hazard 3": [{"name": "Households"}],
                "Top hazard 4": [{"name": "Firms"}],
                "Expert-added hazard": [{"name": "Tenants"}],
                "New custom hazard": [
                    {
                        "name": "Coal workers",
                        "regional_population_pct": 12,
                        "national_population_pct": 9,
                    }
                ],
            },
            hazard_rankings={
                "Top hazard 1": {"regional_population_pct": 10, "national_population_pct": 8},
                "Top hazard 2": {"regional_population_pct": 7, "national_population_pct": 5},
                "Top hazard 3": {"regional_population_pct": 6, "national_population_pct": 4},
                "Expert-added hazard": {"regional_population_pct": 3, "national_population_pct": 2},
            },
        )

        summary = session.summary()

        self.assertEqual(
            [row["hazard"] for row in summary.top_hazards],
            ["Top hazard 1", "Top hazard 2", "Top hazard 3"],
        )
        self.assertEqual(summary.additional_hazards, ["Expert-added hazard"])
        self.assertEqual(summary.custom_hazards, ["New custom hazard"])
        self.assertEqual(
            [row["hazard"] for row in summary.additional_hazard_population],
            ["Expert-added hazard", "New custom hazard"],
        )
        self.assertEqual(
            summary.additional_hazard_population[1]["regional_population_pct"],
            12.0,
        )
        self.assertEqual(
            summary.additional_hazard_population[1]["national_population_pct"],
            9.0,
        )

    def test_population_review_options_use_open_conversation_for_add_remove(self):
        labels = [option.label for option in HAZARD_POPULATION_REVIEW_OPTIONS]

        self.assertEqual(labels, ["Confirm affected groups"])
        self.assertNotIn("Add affected group", labels)
        self.assertNotIn("Remove affected group", labels)
        self.assertNotIn("Edit group reason", labels)

    def test_removing_last_custom_affected_group_keeps_review_open_without_required_error(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses.", "source": "user_added"}
                ],
                "added_affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove affected group: Coal workers",
            )
        )

        self.assertEqual(session.custom_hazard["affected_groups"], [])
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertNotIn("At least one affected population group is required", response.bot_message)

    def test_typing_existing_custom_affected_group_name_removes_that_group_only(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "Coal-dependent communities", "reason": "Local dependence."},
                    {
                        "group": "Coal miners, power plant workers, and related supply chain jobs",
                        "reason": "Job losses.",
                    },
                    {
                        "group": "households with utility arrears: Add households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    },
                ],
                "added_affected_groups": [
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    }
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "households with utility arrears",
            )
        )

        remaining = [
            group["group"]
            for group in session.custom_hazard["affected_groups"]
        ]
        self.assertEqual(
            remaining,
            [
                "Coal-dependent communities",
                "Coal miners, power plant workers, and related supply chain jobs",
            ],
        )
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertIn("Coal-dependent communities", response.bot_message)

    def test_extracted_custom_affected_group_cannot_be_removed(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "Coal miners",
                        "reason": "Explicitly named in the hazard or clarification text.",
                    },
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    },
                ],
                "added_affected_groups": [
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    }
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove Coal miners",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("can't be removed because it was found by the system", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["Coal miners", "households with utility arrears"],
        )

    def test_custom_hazard_review_shows_strict_crowd_sourcing_notice(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            validation_mode="strict",
            crowd_sourcing_enabled=True,
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "Low-income workers",
                        "reason": "Job loss risk.",
                    },
                ],
            },
        )

        response = service._custom_hazard_population_review_step("session-1", session)

        self.assertIn("Hazard to be co-created:", response.bot_message)
        self.assertNotIn("New hazard:", response.bot_message)
        self.assertIn(
            "Once saved, this hazard will be visible to other platform users",
            response.bot_message,
        )
        self.assertIn("Bavaria, Germany", response.bot_message)

    def test_custom_hazard_added_shows_strict_crowd_sourcing_notice(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            validation_mode="strict",
            crowd_sourcing_enabled=True,
            accepted_custom_hazard="Coal phase-out job shock",
            accepted_custom_hazard_reason="Coal phase-out can reduce local mining jobs.",
            accepted_custom_hazard_evidence="Not provided",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "Low-income workers",
                        "reason": "Job loss risk.",
                    },
                ],
            },
        )
        service._prepare_custom_hazard_added_profiles = MagicMock(
            return_value="Coal phase-out job shock"
        )
        service._stored_hazard_profiles = MagicMock(
            return_value=[
                {
                    "group": "Low-income workers",
                    "reason": "Job loss risk.",
                }
            ]
        )

        response = service._custom_hazard_added_step_sync("session-1", session)

        self.assertIn("You have successfully co-created a hazard.", response.bot_message)
        self.assertIn(
            "This hazard is now visible to other platform users interested",
            response.bot_message,
        )
        self.assertIn("Bavaria, Germany", response.bot_message)

    def test_custom_affected_group_review_cleans_add_echo_from_label(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "households with utility arrears: Add households with utility arrears",
                        "reason": "Energy costs.",
                    },
                ],
            },
        )

        response = service._custom_hazard_population_review_step("session-1", session)

        self.assertIn("households with utility arrears", response.bot_message)
        self.assertNotIn(
            "households with utility arrears: Add households with utility arrears",
            response.bot_message,
        )

    def test_confirming_empty_custom_affected_groups_asks_to_add_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Confirm affected groups",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("No affected groups are selected", response.bot_message)
        self.assertEqual(response.step, "custom_hazard_group_review")

    def test_open_text_add_generic_affected_group_asks_for_specific_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [{"group": "Coal workers", "reason": "Job losses."}],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Add people",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertIn("too broad", response.bot_message)

    def test_user_added_affected_group_reason_is_validated_before_storage(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "Coal workers",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "bad",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(session.custom_hazard["added_affected_groups"], [])
        self.assertIn("meaningful impact reason", response.bot_message)

    def test_user_added_affected_group_reason_is_saved_when_valid(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "Coal workers",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )
        service._validate_input_quality = AsyncMock(
            return_value={"valid": True, "reason": "Reason is meaningful."}
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Coal workers face job losses and lower income when coal plants close.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(
            session.custom_hazard["added_affected_groups"][0]["reason"],
            "Coal workers face job losses and lower income when coal plants close.",
        )
        self.assertEqual(
            session.custom_hazard["affected_groups"][0]["group"],
            "Coal workers",
        )

    def test_open_text_add_affected_groups_prompts_for_first_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Add low-income renters, older adults",
            )
        )

        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("low-income renters", response.bot_message)
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "low-income renters",
        )
        self.assertEqual(session.custom_hazard["pending_profile_reason_queue"], ["older adults"])
        self.assertEqual(session.custom_hazard["affected_groups"], [])

    def test_open_text_add_rejects_non_population_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Add electricity price",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertIn("does not look like an affected population group", response.bot_message)
        self.assertNotIn("pending_profile_reason_group", session.custom_hazard)
        self.assertEqual(session.custom_hazard["affected_groups"], [])

    def test_valid_reason_for_queued_group_asks_next_group_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "low-income renters",
                "pending_profile_reason_queue": ["older adults"],
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )
        service._validate_input_quality = AsyncMock(
            return_value={"valid": True, "reason": "Reason is meaningful."}
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Renters face higher utility arrears when coal phase-out raises local energy costs.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("older adults", response.bot_message)
        self.assertEqual(
            session.custom_hazard["affected_groups"][0]["group"],
            "low-income renters",
        )
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "older adults",
        )

    def test_open_text_remove_and_add_updates_groups_then_prompts_for_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"},
                    {"group": "Coal workers", "reason": "Job losses."},
                ],
                "added_affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove tenants and add households with utility arrears",
            )
        )

        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("households with utility arrears", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["Coal workers"],
        )
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "households with utility arrears",
        )

    def test_remove_and_invalid_add_does_not_remove_existing_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"},
                ],
                "added_affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove tenants and add coal plant closure",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("does not look like an affected population group", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["tenants"],
        )

    def test_extracted_custom_groups_are_mapped_to_target_population_options(self):
        service = ChatService.__new__(ChatService)
        service._target_population_option_rows = lambda: [
            SimpleNamespace(id=1, question="Level of income", option="Low income"),
            SimpleNamespace(id=2, question="Tenancy status", option="Tenant"),
            SimpleNamespace(id=3, question="Age range", option="65+"),
        ]
        service._ensure_custom_hazard = lambda *args, **kwargs: SimpleNamespace(id=123)
        service._record_activity = lambda *args, **kwargs: None
        service._custom_hazard_added_step = AsyncMock(return_value="done")
        session = ChatSession(
            session_key="session-1",
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "low-income renters",
                        "reason": "Higher energy bills increase arrears risk.",
                        "source_text": "low-income renters",
                    },
                    {
                        "group": "older adults",
                        "reason": "Older adults face greater exposure to heating cost shocks.",
                        "source_text": "older adults",
                    },
                ],
                "confirmed_affected_groups": [],
            },
        )

        response = _run(
            service._finalize_custom_hazard_from_grounding("session-1", session)
        )

        self.assertEqual(response, "done")
        profiles = session.hazard_profiles["Coal phase-out job shock"]
        self.assertEqual(profiles[0]["target_population_option_ids"], ["1", "2"])
        self.assertEqual(profiles[1]["target_population_option_ids"], ["3"])
        self.assertIn("Level of income: Low income", profiles[0]["target_population_labels"])
        self.assertIn("Age range: 65+", profiles[1]["target_population_labels"])

    def test_user_added_utility_arrears_group_maps_to_low_income_population(self):
        service = ChatService.__new__(ChatService)
        service._target_population_option_rows = lambda: [
            SimpleNamespace(id=1, question="Level of income", option="Low income"),
            SimpleNamespace(id=2, question="Tenancy status", option="Tenant"),
        ]

        profiles = service._attach_target_population_matches_to_profiles(
            [
                {
                    "name": "households with utility arrears",
                    "profile": "households with utility arrears",
                    "explanation": "Energy cost increases worsen arrears risk.",
                    "source": "user_added",
                }
            ]
        )

        self.assertEqual(profiles[0]["target_population_option_ids"], ["1"])
        self.assertIn("Level of income: Low income", profiles[0]["target_population_labels"])

    def test_cached_additional_and_custom_profiles_are_population_enriched(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            additional_hazards=["Expert-added hazard"],
            custom_hazards=["Co-created hazard"],
            hazard_profiles={
                "Expert-added hazard": [{"name": "Tenants"}],
                "Co-created hazard": [{"name": "Coal workers"}],
            },
        )
        service._stored_hazard_profiles = MagicMock(
            side_effect=lambda current_session, hazard: current_session.hazard_profiles[hazard]
        )

        async def enrich(_session, hazard, profiles):
            if hazard == "Expert-added hazard":
                return [
                    {
                        **profiles[0],
                        "regional_population_pct": 3,
                        "national_population_pct": 2,
                    }
                ]
            return [
                {
                    **profiles[0],
                    "regional_population_pct": 12,
                    "national_population_pct": 9,
                }
            ]

        service._additional_profiles_with_population_context = AsyncMock(side_effect=enrich)

        _run(service._enrich_additional_and_custom_hazard_profiles_with_population_context(session))

        self.assertEqual(
            session.hazard_profiles["Expert-added hazard"][0]["regional_population_pct"],
            3,
        )
        self.assertEqual(
            session.hazard_profiles["Co-created hazard"][0]["national_population_pct"],
            9,
        )

    def test_new_policy_suggestions_fall_back_beyond_selected_country(self):
        service = ChatService.__new__(ChatService)
        service._selected_system_hazard_id = MagicMock(return_value="hazard-1")
        service._selected_system_hazard_target_option_ids = MagicMock(return_value={"option-1"})
        service._new_policy_suggestion_policy_rows = MagicMock(
            side_effect=[
                [],
                [{"id": "policy-1", "policy_title": "Regional energy support"}],
            ]
        )
        service._new_policy_suggestion_candidates_from_rows = MagicMock(
            return_value=[
                {
                    "policy_id": "policy-1",
                    "policy_title": "Regional energy support",
                    "score": 60,
                }
            ]
        )
        session = ChatSession(country_id="country-1", sector_id="sector-1")

        candidates = service._ranked_new_policy_suggestions(session)

        self.assertEqual(candidates[0]["policy_title"], "Regional energy support")
        self.assertEqual(
            [
                call.kwargs["require_selected_country"]
                for call in service._new_policy_suggestion_policy_rows.call_args_list
            ],
            [True, False],
        )

    def test_evaluation_answer_accepts_uuid_question_id(self):
        service = ChatService.__new__(ChatService)
        service._store_question_response = MagicMock()
        service._record_activity = MagicMock()
        service._evaluation_complete_step = MagicMock(return_value="done")
        session = ChatSession(
            evaluation_index=0,
            evaluation_questions=[
                {
                    "id": "44a1d52f-85aa-11f1-9282-cc28aa4b96ed",
                    "category": "Impact",
                    "question": "Does the mitigation improve fairness?",
                }
            ],
            evaluation_answers=[],
        )

        response = _run(service._handle_evaluation_answer("session-1", session, "Score: 8"))

        self.assertEqual(response, "done")
        self.assertEqual(
            service._store_question_response.call_args.kwargs["question_id"],
            "44a1d52f-85aa-11f1-9282-cc28aa4b96ed",
        )


if __name__ == "__main__":
    unittest.main()
