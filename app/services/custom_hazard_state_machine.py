from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.schemas import ChatResponse
from app.services.chat_session import ChatSession
from app.services.enums import ChatPhase


AsyncMessageHandler = Callable[[str, ChatSession, str], Awaitable[ChatResponse]]
AsyncSessionHandler = Callable[[str, ChatSession], Awaitable[ChatResponse]]
SyncMessageHandler = Callable[[str, ChatSession, str], ChatResponse]
QualityHandler = Callable[[str, ChatSession, str], Awaitable[ChatResponse | None]]


class HandlerKind(StrEnum):
    ASYNC_MESSAGE = "async_message"
    ASYNC_SESSION = "async_session"
    SYNC_MESSAGE = "sync_message"


class CustomHazardHandler(StrEnum):
    CAPTURE_HAZARD = "capture_hazard"
    CLARIFY_TITLE = "clarify_title"
    CLARIFY_HAZARD = "clarify_hazard"
    CHECK_DIMENSIONS = "check_dimensions"
    CAPTURE_REASON = "capture_reason"
    DECIDE_EVIDENCE = "decide_evidence"
    CAPTURE_EVIDENCE = "capture_evidence"
    VALIDATE_HAZARD = "validate_hazard"
    REVIEW_POPULATION = "review_population"
    REVIEW_SUMMARY = "review_summary"
    RESOLVE_DUPLICATE = "resolve_duplicate"


@dataclass(frozen=True, slots=True)
class CustomHazardState:
    phase: ChatPhase
    handler: CustomHazardHandler
    handler_kind: HandlerKind = HandlerKind.ASYNC_MESSAGE
    quality_gate: bool = True


@dataclass(frozen=True, slots=True)
class CustomHazardTransition:
    source: ChatPhase
    target: str
    response: ChatResponse


class InvalidCustomHazardTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CustomHazardHandlers:
    capture_hazard: AsyncMessageHandler
    clarify_title: AsyncMessageHandler
    clarify_hazard: AsyncMessageHandler
    check_dimensions: AsyncSessionHandler
    capture_reason: SyncMessageHandler
    decide_evidence: AsyncMessageHandler
    capture_evidence: AsyncMessageHandler
    validate_hazard: AsyncMessageHandler
    review_population: AsyncMessageHandler
    review_summary: AsyncMessageHandler
    resolve_duplicate: AsyncMessageHandler


CUSTOM_HAZARD_STATES: Mapping[ChatPhase, CustomHazardState] = MappingProxyType({
    ChatPhase.ADD_HAZARD: CustomHazardState(
        ChatPhase.ADD_HAZARD,
        CustomHazardHandler.CAPTURE_HAZARD,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_INPUT: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_INPUT,
        CustomHazardHandler.CAPTURE_HAZARD,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION,
        CustomHazardHandler.CLARIFY_TITLE,
    ),
    ChatPhase.ADD_HAZARD_CLARIFICATION: CustomHazardState(
        ChatPhase.ADD_HAZARD_CLARIFICATION,
        CustomHazardHandler.CLARIFY_HAZARD,
    ),
    ChatPhase.CUSTOM_HAZARD_CLARIFICATION: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
        CustomHazardHandler.CLARIFY_HAZARD,
    ),
    ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
        CustomHazardHandler.CHECK_DIMENSIONS,
        HandlerKind.ASYNC_SESSION,
    ),
    ChatPhase.ADD_HAZARD_REASON: CustomHazardState(
        ChatPhase.ADD_HAZARD_REASON,
        CustomHazardHandler.CAPTURE_REASON,
        HandlerKind.SYNC_MESSAGE,
    ),
    ChatPhase.ADD_HAZARD_EVIDENCE_DECISION: CustomHazardState(
        ChatPhase.ADD_HAZARD_EVIDENCE_DECISION,
        CustomHazardHandler.DECIDE_EVIDENCE,
    ),
    ChatPhase.ADD_HAZARD_EVIDENCE_INPUT: CustomHazardState(
        ChatPhase.ADD_HAZARD_EVIDENCE_INPUT,
        CustomHazardHandler.CAPTURE_EVIDENCE,
    ),
    ChatPhase.ADD_HAZARD_EVIDENCE: CustomHazardState(
        ChatPhase.ADD_HAZARD_EVIDENCE,
        CustomHazardHandler.VALIDATE_HAZARD,
    ),
    ChatPhase.CUSTOM_HAZARD_VALIDATION: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_VALIDATION,
        CustomHazardHandler.VALIDATE_HAZARD,
    ),
    ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
        CustomHazardHandler.REVIEW_POPULATION,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
        CustomHazardHandler.REVIEW_POPULATION,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_PROFILE_REASON: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_PROFILE_REASON,
        CustomHazardHandler.REVIEW_POPULATION,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
        CustomHazardHandler.REVIEW_SUMMARY,
        quality_gate=False,
    ),
    ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION: CustomHazardState(
        ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
        CustomHazardHandler.RESOLVE_DUPLICATE,
        quality_gate=False,
    ),
    ChatPhase.HAZARD_DUPLICATE_SUGGESTION: CustomHazardState(
        ChatPhase.HAZARD_DUPLICATE_SUGGESTION,
        CustomHazardHandler.RESOLVE_DUPLICATE,
        quality_gate=False,
    ),
})


CUSTOM_HAZARD_TRANSITIONS: Mapping[ChatPhase, frozenset[ChatPhase]] = MappingProxyType(
    {
        ChatPhase.ADD_HAZARD: frozenset(
            {
                ChatPhase.ADD_HAZARD,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_CLARIFICATION,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_INPUT: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.HAZARD_DUPLICATE_SUGGESTION,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_TITLE_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.ADD_HAZARD_CLARIFICATION: frozenset(
            {
                ChatPhase.ADD_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_REASON,
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_CLARIFICATION: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_REASON,
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.ADD_HAZARD_REASON,
                ChatPhase.ADD_HAZARD_EVIDENCE_DECISION,
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.ADD_HAZARD_REASON: frozenset(
            {
                ChatPhase.ADD_HAZARD_REASON,
                ChatPhase.ADD_HAZARD_EVIDENCE_DECISION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.ADD_HAZARD_EVIDENCE_DECISION: frozenset(
            {
                ChatPhase.ADD_HAZARD_EVIDENCE_DECISION,
                ChatPhase.ADD_HAZARD_EVIDENCE_INPUT,
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.ADD_HAZARD_EVIDENCE_INPUT: frozenset(
            {
                ChatPhase.ADD_HAZARD_EVIDENCE_INPUT,
                ChatPhase.ADD_HAZARD_EVIDENCE_DECISION,
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.ADD_HAZARD_EVIDENCE: frozenset(
            {
                ChatPhase.ADD_HAZARD_EVIDENCE,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_VALIDATION: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_VALIDATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.ADD_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_CLARIFICATION,
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_DUPLICATE_CONFIRMATION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.SOCIO_DEMOGRAPHIC_REVIEW,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.HAZARD_DUPLICATE_SUGGESTION: frozenset(
            {
                ChatPhase.HAZARD_DUPLICATE_SUGGESTION,
                ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.SOCIO_DEMOGRAPHIC_REVIEW,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_PROFILE_REASON,
                ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_PROFILE_REASON,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_PROFILE_REASON: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_PROFILE_REASON,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
        ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW: frozenset(
            {
                ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW,
                ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW,
                ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
                ChatPhase.CUSTOM_HAZARD_INPUT,
                ChatPhase.HAZARDS,
            }
        ),
    }
)


def transition_custom_hazard(session: ChatSession, target: ChatPhase) -> ChatPhase:
    """Move a session through the declared custom-hazard transition graph."""
    try:
        source = ChatPhase.coerce(session.phase)
    except ValueError:
        source = None

    if source not in CUSTOM_HAZARD_STATES:
        if target not in CUSTOM_HAZARD_STATES:
            raise InvalidCustomHazardTransition(
                f"Cannot enter custom-hazard workflow at {target.value!r} from {session.phase!r}."
            )
    elif target not in CUSTOM_HAZARD_TRANSITIONS[source]:
        raise InvalidCustomHazardTransition(
            f"Illegal custom-hazard transition: {source.value!r} -> {target.value!r}."
        )

    session.phase = target.value
    return target


class CustomHazardStateMachine:
    def state_for(self, phase: object) -> CustomHazardState | None:
        try:
            normalized_phase = ChatPhase.coerce(phase)
        except ValueError:
            return None
        return CUSTOM_HAZARD_STATES.get(normalized_phase)

    async def dispatch(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
        *,
        handlers: CustomHazardHandlers,
        quality_handler: QualityHandler,
    ) -> CustomHazardTransition | None:
        state = self.state_for(session.phase)
        if state is None:
            return None

        if state.quality_gate:
            quality_response = await quality_handler(session_id, session, message)
            if quality_response is not None:
                return CustomHazardTransition(
                    source=state.phase,
                    target=str(session.phase),
                    response=quality_response,
                )

        handler = getattr(handlers, state.handler.value)
        if state.handler_kind == HandlerKind.ASYNC_SESSION:
            response = await handler(session_id, session)
        elif state.handler_kind == HandlerKind.SYNC_MESSAGE:
            response = handler(session_id, session, message)
        else:
            response = await handler(session_id, session, message)

        return CustomHazardTransition(
            source=state.phase,
            target=str(session.phase),
            response=response,
        )


custom_hazard_state_machine = CustomHazardStateMachine()
