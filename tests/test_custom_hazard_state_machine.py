import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from app.schemas import ChatResponse
from app.services.chat_session import ChatSession
from app.services.custom_hazard_state_machine import (
    CUSTOM_HAZARD_STATES,
    CUSTOM_HAZARD_TRANSITIONS,
    CustomHazardHandler,
    CustomHazardHandlers,
    CustomHazardStateMachine,
    HandlerKind,
    InvalidCustomHazardTransition,
    transition_custom_hazard,
)
from app.services.enums import ChatPhase


def _response(session_id: str, session: ChatSession, message: str) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        step=session.phase,
        bot_message=message,
        session=session.summary(),
    )


def _handlers(
    *,
    async_message=None,
    async_session=None,
    sync_message=None,
) -> CustomHazardHandlers:
    async_message = async_message or AsyncMock()
    async_session = async_session or AsyncMock()
    sync_message = sync_message or Mock()
    return CustomHazardHandlers(
        capture_hazard=async_message,
        clarify_title=async_message,
        clarify_hazard=async_message,
        check_dimensions=async_session,
        capture_reason=sync_message,
        decide_evidence=async_message,
        capture_evidence=async_message,
        validate_hazard=async_message,
        review_population=async_message,
        review_summary=async_message,
        resolve_duplicate=async_message,
    )


class CustomHazardStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = CustomHazardStateMachine()

    def test_registry_has_one_definition_for_every_custom_hazard_phase(self) -> None:
        expected_handlers = {
            ChatPhase.ADD_HAZARD: CustomHazardHandler.CAPTURE_HAZARD,
            ChatPhase.CUSTOM_HAZARD_INPUT: CustomHazardHandler.CAPTURE_HAZARD,
            ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION: CustomHazardHandler.CLARIFY_TITLE,
            ChatPhase.ADD_HAZARD_CLARIFICATION: CustomHazardHandler.CLARIFY_HAZARD,
            ChatPhase.CUSTOM_HAZARD_CLARIFICATION: CustomHazardHandler.CLARIFY_HAZARD,
            ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK: CustomHazardHandler.CHECK_DIMENSIONS,
            ChatPhase.ADD_HAZARD_REASON: CustomHazardHandler.CAPTURE_REASON,
            ChatPhase.ADD_HAZARD_EVIDENCE_DECISION: CustomHazardHandler.DECIDE_EVIDENCE,
            ChatPhase.ADD_HAZARD_EVIDENCE_INPUT: CustomHazardHandler.CAPTURE_EVIDENCE,
            ChatPhase.ADD_HAZARD_EVIDENCE: CustomHazardHandler.VALIDATE_HAZARD,
            ChatPhase.CUSTOM_HAZARD_VALIDATION: CustomHazardHandler.VALIDATE_HAZARD,
            ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW: CustomHazardHandler.REVIEW_POPULATION,
            ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW: CustomHazardHandler.REVIEW_POPULATION,
            ChatPhase.CUSTOM_HAZARD_PROFILE_REASON: CustomHazardHandler.REVIEW_POPULATION,
            ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW: CustomHazardHandler.REVIEW_SUMMARY,
            ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION: CustomHazardHandler.RESOLVE_DUPLICATE,
            ChatPhase.HAZARD_DUPLICATE_SUGGESTION: CustomHazardHandler.RESOLVE_DUPLICATE,
        }
        self.assertEqual(
            {phase: state.handler for phase, state in CUSTOM_HAZARD_STATES.items()},
            expected_handlers,
        )
        self.assertEqual(
            CUSTOM_HAZARD_STATES[ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK].handler_kind,
            HandlerKind.ASYNC_SESSION,
        )
        self.assertEqual(
            CUSTOM_HAZARD_STATES[ChatPhase.ADD_HAZARD_REASON].handler_kind,
            HandlerKind.SYNC_MESSAGE,
        )
        ungated_phases = {
            ChatPhase.ADD_HAZARD,
            ChatPhase.CUSTOM_HAZARD_INPUT,
            ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
            ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
            ChatPhase.CUSTOM_HAZARD_PROFILE_REASON,
            ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
            ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
            ChatPhase.HAZARD_DUPLICATE_SUGGESTION,
        }
        self.assertEqual(
            {
                phase
                for phase, state in CUSTOM_HAZARD_STATES.items()
                if not state.quality_gate
            },
            ungated_phases,
        )

    def test_non_custom_phase_is_not_dispatched(self) -> None:
        self.assertIsNone(self.machine.state_for(ChatPhase.HAZARDS))
        self.assertIsNone(self.machine.state_for("unknown-phase"))

    def test_declared_transition_moves_session_to_target(self) -> None:
        session = ChatSession(phase=ChatPhase.CUSTOM_HAZARD_INPUT)

        target = transition_custom_hazard(
            session,
            ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION,
        )

        self.assertEqual(target, ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION)
        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION)

    def test_persisted_workflow_can_resume_at_any_declared_state(self) -> None:
        session = ChatSession(phase=ChatPhase.HAZARDS)

        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)

        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)

    def test_illegal_transition_is_rejected_without_mutating_session(self) -> None:
        session = ChatSession(phase=ChatPhase.CUSTOM_HAZARD_INPUT)

        with self.assertRaisesRegex(InvalidCustomHazardTransition, "Illegal custom-hazard"):
            transition_custom_hazard(session, ChatPhase.MITIGATION_REVIEW)

        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_INPUT)

    def test_every_state_has_a_declared_transition_set(self) -> None:
        self.assertEqual(set(CUSTOM_HAZARD_TRANSITIONS), set(CUSTOM_HAZARD_STATES))
        self.assertTrue(all(targets for targets in CUSTOM_HAZARD_TRANSITIONS.values()))

    def test_every_state_can_restart_with_a_new_custom_hazard(self) -> None:
        self.assertTrue(
            all(
                ChatPhase.CUSTOM_HAZARD_INPUT in targets
                for targets in CUSTOM_HAZARD_TRANSITIONS.values()
            )
        )

    def test_input_state_bypasses_shared_quality_gate(self) -> None:
        session = ChatSession(phase=ChatPhase.CUSTOM_HAZARD_INPUT)
        expected = _response("session-1", session, "captured")
        capture = AsyncMock(return_value=expected)
        quality = AsyncMock(return_value=_response("session-1", session, "rejected"))

        transition = asyncio.run(
            self.machine.dispatch(
                "session-1",
                session,
                "A specific hazard",
                handlers=_handlers(async_message=capture),
                quality_handler=quality,
            )
        )

        self.assertIsNotNone(transition)
        self.assertIs(transition.response, expected)
        capture.assert_awaited_once_with("session-1", session, "A specific hazard")
        quality.assert_not_awaited()

    def test_quality_gate_can_stop_reason_transition(self) -> None:
        session = ChatSession(phase=ChatPhase.ADD_HAZARD_REASON)
        rejected = _response("session-1", session, "rejected")
        quality = AsyncMock(return_value=rejected)
        capture_reason = Mock()

        transition = asyncio.run(
            self.machine.dispatch(
                "session-1",
                session,
                "unclear",
                handlers=_handlers(sync_message=capture_reason),
                quality_handler=quality,
            )
        )

        self.assertIsNotNone(transition)
        self.assertIs(transition.response, rejected)
        capture_reason.assert_not_called()

    def test_dimension_state_uses_session_handler_and_records_target(self) -> None:
        session = ChatSession(phase=ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)

        async def check_dimensions(session_id: str, current: ChatSession) -> ChatResponse:
            transition_custom_hazard(current, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
            return _response(session_id, current, "clarify")

        transition = asyncio.run(
            self.machine.dispatch(
                "session-1",
                session,
                "ignored",
                handlers=_handlers(async_session=check_dimensions),
                quality_handler=AsyncMock(return_value=None),
            )
        )

        self.assertIsNotNone(transition)
        self.assertEqual(transition.source, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
        self.assertEqual(transition.target, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        self.assertEqual(transition.response.bot_message, "clarify")


if __name__ == "__main__":
    unittest.main()
