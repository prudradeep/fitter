import asyncio
import unittest
from pathlib import Path

from app.services.chat_session import ChatSession
from app.services.chat_service import ChatService
from tests.run_open_conversation_selection_cases import (
    _OpenConversationSelectionEngine,
    infer_actual_action,
)


def _run(coro):
    return asyncio.run(coro)


class OpenConversationFlowActionTests(unittest.TestCase):
    @staticmethod
    def _active_custom_hazard_session(phase: str) -> ChatSession:
        return ChatSession(
            phase=phase,
            sector="Energy",
            selected_hazard="Previous hazard",
            custom_hazards=["Previous hazard"],
            accepted_custom_hazard="Previous hazard",
            accepted_custom_hazard_reason="Previous reason",
            accepted_custom_hazard_evidence="Previous evidence",
            custom_hazard={
                "raw_text": "Previous hazard",
                "affected_groups": [{"group": "Previous group"}],
            },
            custom_hazard_input_history=["Previous hazard"],
            suggested_duplicate_hazard="Existing hazard",
            suggested_duplicate_hazard_record_id=42,
            pending_affected_population_profiles=[{"name": "Previous group"}],
        )

    def test_add_new_hazard_restarts_an_active_group_review(self):
        service = ChatService.__new__(ChatService)
        session = self._active_custom_hazard_session("custom_hazard_group_review")

        response = _run(
            service._handle_other_nav_action(
                "test-session",
                session,
                "Add a new hazard",
            )
        )

        self.assertIsNotNone(response)
        self.assertFalse(response.error)
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(session.custom_hazard.get("raw_text"), "")
        self.assertEqual(session.custom_hazard_input_history, [])
        self.assertIsNone(session.accepted_custom_hazard)
        self.assertIsNone(session.pending_affected_population_profiles)
        self.assertEqual(session.custom_hazards, ["Previous hazard"])

    def test_write_hazard_again_replaces_an_active_population_review(self):
        service = ChatService.__new__(ChatService)
        session = self._active_custom_hazard_session("custom_hazard_profile_reason")

        response = _run(
            service._handle_other_nav_action(
                "test-session",
                session,
                "Write hazard again",
            )
        )

        self.assertIsNotNone(response)
        self.assertFalse(response.error)
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(session.custom_hazard.get("raw_text"), "")
        self.assertEqual(session.custom_hazard_input_history, [])
        self.assertIsNone(session.suggested_duplicate_hazard)
        self.assertIsNone(session.pending_affected_population_profiles)
        self.assertEqual(session.custom_hazards, [])

    def test_post_sector_add_hazard_text_enters_hazard_creation_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Post-sector",
                    "Initial State": (
                        "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
                    ),
                    "User Message": "None of these hazards fit. I want to add one.",
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazards")
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(infer_actual_action(response, session), "ADD_NEW_HAZARD")

    def test_post_sector_start_mitigation_text_enters_mitigation_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Post-sector",
                    "Initial State": (
                        "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
                    ),
                    "User Message": "Start mitigation planning",
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazard_profile_selection")
        self.assertEqual(session.phase, "hazard_profile_selection")
        self.assertEqual(
            infer_actual_action(response, session),
            "START_MITIGATION_PLANNING",
        )

    def test_hazard_selection_other_options_include_go_back_to_hazard_list(self):
        engine = _OpenConversationSelectionEngine()
        session = engine._session_from_state(
            "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
        )
        session.phase = "hazard_profile_selection"

        labels = engine._other_nav_options(session, "hazard_profile_selection")

        self.assertIn("Go back to list of hazards", labels)

    def test_go_back_to_hazard_list_from_hazard_selection_returns_hazards_step(self):
        engine = _OpenConversationSelectionEngine()
        session = engine._session_from_state(
            "Country=Germany; Region=Baden-Württemberg; Sector=Energy; "
            "Hazard=Heat stress"
        )
        session.phase = "hazard_profile_selection"

        response = _run(
            engine._handle_other_nav_action(
                "test-session",
                session,
                "Go back to list of hazards",
            )
        )

        self.assertIsNotNone(response)
        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazards")
        self.assertIsNone(session.selected_hazard)

    def test_reason_confirmation_add_mitigation_text_enters_measure_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Reason confirmation",
                    "Initial State": (
                        "Country=Germany; Region=Bavaria; Sector=Energy; "
                        "Hazard=Heat stress"
                    ),
                    "User Message": (
                        "None of these mitigation measures fit. I want to add one."
                    ),
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(session.phase, "mitigation_measure")
        self.assertEqual(
            infer_actual_action(response, session),
            "WRITE_MITIGATION_MANUALLY",
        )

    def test_hazard_listing_workflow_help_context_explains_add_hazard(self):
        engine = _OpenConversationSelectionEngine()
        session = engine._session_from_state(
            "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
        )
        session.phase = "hazards"

        service = ChatService.__new__(ChatService)
        context = service._workflow_help_context(session)
        file_context = Path("app/prompts/workflow/hazards.txt").read_text(encoding="utf-8").strip()

        self.assertEqual(context, file_context)
        self.assertIn("Add a new Hazard", context)
        self.assertIn("listed hazards do not capture the risk", context)
        self.assertIn("does not immediately save a hazard", context)
        self.assertIn("Hazards can be added later", context)
        self.assertIn("keep the user on the same hazard listing step", context)
        self.assertIn("answer yes", context)

    def test_can_add_my_own_hazard_is_workflow_help_question(self):
        engine = _OpenConversationSelectionEngine()
        session = engine._session_from_state(
            "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
        )
        session.phase = "hazards"
        service = ChatService.__new__(ChatService)

        self.assertTrue(
            service._is_workflow_help_question(session, "Can I add my own hazard?")
        )

    def test_workflow_answer_prompt_is_loaded_from_workflow_file(self):
        context = Path("app/prompts/workflow/answer.txt").read_text(encoding="utf-8")

        self.assertIn("workflow help assistant", context)
        self.assertIn("Do not use Knowledge Base or Sector Statistical Context", context)


if __name__ == "__main__":
    unittest.main()
