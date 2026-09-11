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

        response = run(
            self.service._handle_mitigation_mechanism_confirmation(
                "session-1", self.session, "Confirm mechanisms"
            )
        )
        self.assertEqual(response.step, "mitigation_evidence_decision")

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

        self.service._infer_mitigation_target_population_from_inputs = AsyncMock(
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
