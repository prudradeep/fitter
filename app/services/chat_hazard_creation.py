import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select

from app.llm import ask_llm_chat
from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    Country,
    CustomHazard,
    CustomHazardProfile,
    EurostatPopulationCache,
    EvaluationQuestion,
    QuestionOption,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
    SystemHazardSocioDemographicTargetPopulation,
    UserHazard,
    UserHazardSocioDemographic,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)
from app.schemas import ChatResponse, Option
from app.services.chat_formatters import (
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_json import (
    parse_json_array,
    parse_json_object,
)
from app.services.chat_options import (
    HAZARD_ENTRY_OPTIONS,
    EVALUATION_CATEGORIES,
    HAZARD_DUPLICATE_OPTIONS,
    best_fuzzy_label,
    compact_for_match,
    exact_option_label,
    fuzzy_score,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_llm_hazard_list,
)
from app.services.chat_hazard_duplicates import (
    dedupe_hazard_names,
    hazard_duplicate_payloads,
    hazard_similarity_words,
    local_similar_hazards,
    same_scope_custom_hazard_names,
    same_sector_hazard_names,
)
from app.services.hazard_profile_parsing import (
    clean_hazard_profile_item,
    extract_socio_demographic_profiles,
    humanize_predictor_label,
    parse_hazard_profile_items,
    profile_from_predictor_entry,
)
from app.services.chat_population_edits import (
    clean_affected_group_label,
    parse_custom_affected_group_edit_message,
    split_affected_group_labels,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    build_custom_hazard_grounding_status,
    custom_hazard_validation_details,
    default_custom_hazard_state,
    frontend_custom_hazard_payload,
    validate_custom_hazard_dimensions,
)
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.knowledge_base import TEMPORARY_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, KnowledgeBaseService
from app.services.message_renderer import markdown_to_html, render_message
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger(__name__)

class ChatHazardCreationMixin:
    def _custom_hazard_state(self, session: ChatSession) -> dict[str, object]:
        if not isinstance(session.custom_hazard, dict):
            session.custom_hazard = default_custom_hazard_state()
        return session.custom_hazard

    def _custom_hazard_response(
        self,
        *,
        session_id: str,
        session: ChatSession,
        step: str,
        bot_message: str,
        options: list[Option],
        input_mode: str = "text",
        error: bool = False,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        return ChatResponse(
            session_id=session_id,
            step=step,
            bot_message=bot_message,
            options=options,
            session=session.summary(),
            input_mode=input_mode,
            error=error,
            validation_details=custom_hazard_validation_details(state),
            custom_hazard=frontend_custom_hazard_payload(state),
            custom_hazard_grounding_status=build_custom_hazard_grounding_status(state),
        )

    async def _run_custom_hazard_dimension_check(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("raw_text") or session.pending_hazard or "").strip()
        if not hazard:
            session.phase = "custom_hazard_input"
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_input",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )
        result = await validate_custom_hazard_dimensions(
            self._custom_hazard_grounding_text(state, hazard),
            session.sector or "",
            session.country or "",
            session.region or "",
            self._dedupe_hazard_names(
                [
                    *self._same_sector_hazard_names_for_duplicate_check(session),
                    *self._same_scope_custom_hazard_names_for_duplicate_check(session),
                ]
            ),
            state,
            session.validation_mode,
        )
        self._store_custom_hazard_validation_result(session, hazard, result)
        return await self._route_custom_hazard_next_action(session_id, session)

    def _same_sector_hazard_names_for_duplicate_check(self, session: ChatSession) -> list[str]:
        return same_sector_hazard_names(getattr(self, "db", None), session)

    def _same_scope_custom_hazard_names_for_duplicate_check(
        self, session: ChatSession
    ) -> list[str]:
        return same_scope_custom_hazard_names(
            getattr(self, "db", None),
            session,
            getattr(self, "user_id", None),
        )

    @staticmethod
    def _dedupe_hazard_names(names: list[object]) -> list[str]:
        return dedupe_hazard_names(names)

    def _custom_hazard_grounding_text(
        self, state: dict[str, object], hazard: str
    ) -> str:
        parts = [hazard]
        reason = str(state.get("reason") or "").strip()
        evidence = str(state.get("evidence") or "").strip()
        if reason:
            parts.append(f"Reason: {reason}")
        if evidence:
            parts.append(f"Evidence: {evidence}")
        return "\n".join(parts)

    def _store_custom_hazard_validation_result(
        self, session: ChatSession, hazard: str, result: dict[str, object]
    ) -> None:
        state = self._custom_hazard_state(session)
        state["raw_text"] = hazard
        state["normalized_text"] = normalize_for_match(hazard)
        state["selected_country"] = session.country or ""
        state["selected_region"] = session.region or ""
        state["selected_sector"] = session.sector or ""
        state["validation_round"] = int(state.get("validation_round") or 0) + 1
        state["overall_score"] = int(result.get("overall_score") or 0)
        scores = list(state.get("scores") or [])
        scores.append(state["overall_score"])
        state["scores"] = scores[-4:]
        state["dimension_scores"] = result.get("dimension_scores") or {}
        state["affected_groups"] = result.get("affected_groups") or []
        state["duplicate_candidates"] = result.get("duplicate_candidates") or []
        state["next_action"] = CustomHazardAction.coerce(
            result.get("next_action"),
            CustomHazardAction.ASK_CLARIFICATION,
        ).value
        state["status"] = CustomHazardStatus.coerce(
            result.get("status"),
            CustomHazardStatus.NEEDS_CLARIFICATION,
        ).value

    async def _route_custom_hazard_next_action(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        action = CustomHazardAction.coerce(
            state.get("next_action"),
            CustomHazardAction.ASK_CLARIFICATION,
        )
        hazard = str(state.get("raw_text") or session.pending_hazard or "this hazard").strip()
        if action == CustomHazardAction.ASK_DUPLICATE_CONFIRMATION:
            candidate = (state.get("duplicate_candidates") or [{}])[0]
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                str(candidate.get("existing_hazard") or "the suggested existing hazard"),
                str(candidate.get("reason") or "The proposed hazard appears similar to an existing hazard."),
            )
        if action == CustomHazardAction.REVIEW_GROUPS:
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_reason = self._custom_hazard_dimension_reason(state)
            session.accepted_custom_hazard_evidence = "Not provided"
            return self._custom_hazard_population_review_step(session_id, session)
        if action == CustomHazardAction.VALIDATE:
            return await self._finalize_custom_hazard_from_grounding(session_id, session)
        if action == CustomHazardAction.REJECT:
            session.phase = ChatPhase.CUSTOM_HAZARD_VALIDATION.value
            state["status"] = CustomHazardStatus.REJECTED.value
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_validation",
                bot_message=(
                    "This custom hazard is insufficiently supported after the available "
                    "clarification rounds. Please edit the custom hazard or use an existing hazard."
                ),
                options=HAZARD_DUPLICATE_OPTIONS,
                input_mode="text",
                error=True,
            )
        return self._custom_hazard_clarification_step(session_id, session)

    def _custom_hazard_clarification_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        questions: list[str] = []
        dimensions = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
        for value in dimensions.values():
            if not isinstance(value, dict) or not value.get("needs_clarification"):
                continue
            question = str(value.get("clarification_question") or "").strip()
            if question:
                questions.append(question)
            if len(questions) >= 2:
                break
        if not questions:
            questions = ["Can you clarify how this hazard fits the selected sector, place, and twin-transition policy context?"]
        session.phase = ChatPhase.CUSTOM_HAZARD_CLARIFICATION.value
        state["pending_clarification_questions"] = questions
        state["message"] = "Clarification is needed before validation."
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(
                "I need a little more detail before validating this custom hazard:\n\n"
                + "\n".join(f"- {question}" for question in questions)
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=False,
        )

    def _custom_hazard_dimension_reason(self, state: dict[str, object]) -> str:
        dimensions = state.get("dimension_scores") if isinstance(state.get("dimension_scores"), dict) else {}
        reasons = [
            str(value.get("reason") or "").strip()
            for value in dimensions.values()
            if isinstance(value, dict) and str(value.get("reason") or "").strip()
        ]
        return " ".join(reasons)[:1000] or "Validated through custom hazard grounding checks."

    @staticmethod
    def _clean_affected_group_label(value: str) -> str:
        return clean_affected_group_label(value)

    @classmethod
    def _split_affected_group_labels(cls, value: str) -> list[str]:
        return split_affected_group_labels(value)

    @classmethod
    def _parse_custom_affected_group_edit_message(cls, message: str) -> dict[str, list[str]]:
        return parse_custom_affected_group_edit_message(message)

    def _custom_affected_group_matches(self, state: dict[str, object], target: str) -> bool:
        target_label = self._clean_affected_group_label(target)
        return any(
            isinstance(group, dict)
            and self._profiles_are_similar(
                self._clean_affected_group_label(str(group.get("group") or "")),
                target_label,
            )
            for group in state.get("affected_groups") or []
        )

    def _custom_affected_group_label_error(self, group: str) -> str | None:
        label = self._clean_affected_group_label(group)
        normalized = normalize_for_match(label)
        compact = compact_for_match(label)
        if not label:
            return "Please name the affected population group to add."
        if self._is_invalid_user_text(label) or len(compact) < 4:
            return "Please add a meaningful affected population group."

        population_terms = {
            "adults",
            "businesses",
            "citizens",
            "communities",
            "community",
            "consumers",
            "drivers",
            "employees",
            "families",
            "farmers",
            "firms",
            "households",
            "landlords",
            "miners",
            "owners",
            "patients",
            "people",
            "population",
            "renters",
            "residents",
            "students",
            "suppliers",
            "tenants",
            "users",
            "workers",
        }
        non_population_terms = {
            "bill",
            "bills",
            "closure",
            "cost",
            "costs",
            "decarbonization",
            "electricity",
            "emission",
            "emissions",
            "energy",
            "fee",
            "fees",
            "grid",
            "hazard",
            "inflation",
            "infrastructure",
            "policy",
            "price",
            "prices",
            "plant",
            "plants",
            "poverty",
            "regulation",
            "shock",
            "tariff",
            "tariffs",
            "transition",
            "unemployment",
        }
        words = set(normalized.split())
        if words & population_terms:
            return None
        if words & non_population_terms:
            return (
                f"'{label}' does not look like an affected population group. "
                "Please add a group of people, households, workers, communities, firms, or residents affected by the hazard."
            )
        return (
            f"'{label}' does not look like an affected population group. "
            "Please add a group of people, households, workers, communities, firms, or residents affected by the hazard."
        )

    def _is_user_added_custom_affected_group(
        self,
        state: dict[str, object],
        group: dict[str, object],
    ) -> bool:
        if str(group.get("source") or "").strip() == "user_added":
            return True
        group_label = self._clean_affected_group_label(str(group.get("group") or ""))
        return any(
            isinstance(added_group, dict)
            and self._profiles_are_similar(
                self._clean_affected_group_label(str(added_group.get("group") or "")),
                group_label,
            )
            for added_group in state.get("added_affected_groups") or []
        )

    def _remove_custom_affected_group(self, state: dict[str, object], target: str) -> str | None:
        target_label = self._clean_affected_group_label(target)
        removed: list[dict[str, object]] = []
        kept: list[dict[str, object]] = []
        blocked_labels: list[str] = []
        for group in state.get("affected_groups") or []:
            if isinstance(group, dict) and self._profiles_are_similar(
                self._clean_affected_group_label(str(group.get("group") or "")),
                target_label,
            ):
                if self._is_user_added_custom_affected_group(state, group):
                    removed.append(group)
                else:
                    kept.append(group)
                    blocked_labels.append(
                        self._clean_affected_group_label(str(group.get("group") or "this affected group"))
                    )
            else:
                kept.append(group)
        state["affected_groups"] = kept
        state["removed_affected_groups"] = [
            *list(state.get("removed_affected_groups") or []),
            *removed,
        ]
        if blocked_labels:
            label = blocked_labels[0]
            return (
                f"'{label}' can't be removed because it was found by the system. "
                "You can add another affected group, or edit the group reason if the impact needs correction."
            )
        return None



    def _mark_custom_hazard_dimension(
        self,
        session: ChatSession,
        dimension: str,
        *,
        status: str,
        score: int,
        reason: str,
        clarification_question: str = "",
    ) -> None:
        state = self._custom_hazard_state(session)
        dimensions = dict(state.get("dimension_scores") or {})
        dimensions[dimension] = {
            "score": max(0, min(10, score)),
            "status": status,
            "reason": reason,
            "needs_clarification": status.upper() in {"NEEDS CLARIFICATION", "INSUFFICIENT INFO"},
            "clarification_question": clarification_question,
        }
        state["dimension_scores"] = dimensions
        state["overall_score"] = 0
        state["status"] = (
            CustomHazardStatus.REJECTED.value
            if status.upper() == "REJECTED"
            else CustomHazardStatus.NEEDS_CLARIFICATION.value
        )
        state["message"] = reason

    def _refresh_custom_hazard_duplicate_candidates(
        self,
        session: ChatSession,
        hazard: str,
    ) -> None:
        state = self._custom_hazard_state(session)
        known_hazards = self._dedupe_hazard_names(
            [
                *self._same_sector_hazard_names_for_duplicate_check(session),
                *self._same_scope_custom_hazard_names_for_duplicate_check(session),
            ]
        )
        matches = self._local_similar_hazards(
            hazard,
            known_hazards,
        )
        state["duplicate_candidates"] = hazard_duplicate_payloads(hazard, matches)

    def _custom_hazard_validation_failed_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        reason: str,
        dimension: str | None = None,
        evidence: str = "",
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        state["raw_text"] = hazard
        state["normalized_text"] = normalize_for_match(hazard)
        if evidence:
            state["evidence"] = evidence
        rejected_dimension = dimension or self._custom_hazard_rejection_dimension(reason)
        self._mark_custom_hazard_dimension(
            session,
            rejected_dimension,
            status="REJECTED",
            score=0,
            reason=reason,
        )
        self._refresh_custom_hazard_duplicate_candidates(session, hazard)
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_validation",
            bot_message=render_message(
                "hazard_validation_failed.md",
                sector=session.sector,
                reason=reason,
                rewrite_suggestion=self._custom_hazard_sector_rewrite_suggestion(
                    session,
                    hazard,
                    evidence=evidence,
                )
                if rejected_dimension == "selected_sector_fit"
                else "",
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="reason_evidence",
            error=True,
        )


    async def _finalize_custom_hazard_from_grounding(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("raw_text") or session.pending_hazard or "New hazard").strip()
        groups = state.get("confirmed_affected_groups") or state.get("affected_groups") or []
        profiles = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            name = str(group.get("group") or group.get("name") or "").strip()
            if not name:
                continue
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "variable_name": "custom_hazard_grounding",
                    "explanation": str(group.get("reason") or "Affected group identified during custom hazard grounding.").strip(),
                    "statistical_basis": str(group.get("source_text") or "Custom hazard grounding review.").strip(),
                    "source": str(group.get("source") or "custom_hazard_grounding").strip(),
                }
            )
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        if profiles:
            profiles = self._attach_target_population_matches_to_profiles(profiles)
            session.hazard_profiles[hazard] = profiles
            session.socio_demographic_profiles = [str(profile["name"]) for profile in profiles]
        state["status"] = CustomHazardStatus.READY.value
        if session.custom_hazards is None:
            session.custom_hazards = []
        if hazard and not any(normalize(item) == normalize(hazard) for item in session.custom_hazards):
            session.custom_hazards.append(hazard)
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = (
            str(state.get("reason") or "").strip()
            or self._custom_hazard_dimension_reason(state)
        )
        session.accepted_custom_hazard_evidence = (
            str(state.get("evidence") or "").strip() or "Not provided"
        )
        shared_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=None
            if session.accepted_custom_hazard_evidence == "Not provided"
            else session.accepted_custom_hazard_evidence,
        )
        session.accepted_custom_hazard_id = shared_hazard.id if shared_hazard else None
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None
        self._record_activity(session_id, session, "custom_hazard_added", hazard)
        if session.accepted_custom_hazard_evidence != "Not provided":
            self._promote_temporary_evidence(
                session,
                target_scope=VALIDATED_EVIDENCE_SCOPE,
                provenance="validated_user_evidence",
            )
        return await self._custom_hazard_added_step(session_id, session)

    async def _capture_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.custom_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        hazard = message.strip()
        if not hazard:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        session.custom_hazard = default_custom_hazard_state()
        state = self._custom_hazard_state(session)
        state.update(
            {
                "raw_text": hazard,
                "normalized_text": normalize_for_match(hazard),
                "selected_country": session.country or "",
                "selected_region": session.region or "",
                "selected_sector": session.sector or "",
                "status": CustomHazardStatus.DRAFT.value,
            }
        )

        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            hazard,
        )
        if plain_rejection_reason:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                self._custom_hazard_rejection_dimension(plain_rejection_reason),
                status="REJECTED",
                score=0,
                reason=plain_rejection_reason,
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=plain_rejection_reason,
                    rewrite_suggestion="",
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        hazard_review = await self._review_custom_hazard_input(session, hazard)
        if hazard_review is None:
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=(
                    "I could not review this hazard for clarity and policy fit because "
                    "the local LLM is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )
        if not hazard_review["valid"]:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                self._custom_hazard_rejection_dimension(str(hazard_review["reason"])),
                status="REJECTED",
                score=0,
                reason=str(hazard_review["reason"]),
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=hazard_review["reason"],
                    rewrite_suggestion="",
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            hazard,
        )
        if sector_mismatch_reason:
            session.pending_hazard = None
            self._mark_custom_hazard_dimension(
                session,
                "selected_sector_fit",
                status="REJECTED",
                score=0,
                reason=sector_mismatch_reason,
            )
            self._refresh_custom_hazard_duplicate_candidates(session, hazard)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=sector_mismatch_reason,
                    rewrite_suggestion=self._custom_hazard_sector_rewrite_suggestion(
                        session,
                        hazard,
                    ),
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                error=True,
            )

        self._refresh_custom_hazard_duplicate_candidates(session, hazard)
        return self._hazard_reason_evidence_step(session_id, session, hazard)

    async def _handle_custom_hazard_clarification(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.pending_hazard_reason = None
            session.pending_hazard_evidence = None
            session.pending_hazard_clarification_question = None
            session.pending_hazard_clarification_answer = None
            session.custom_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        answer = message.strip()
        if session.phase == "custom_hazard_clarification":
            state = self._custom_hazard_state(session)
            if not answer:
                return self._custom_hazard_clarification_step(session_id, session)
            clarifications = list(state.get("clarifications") or [])
            clarifications.append(
                {
                    "questions": list(state.get("pending_clarification_questions") or []),
                    "answer": answer,
                }
            )
            state["clarifications"] = clarifications
            state["message"] = "Scores were recalculated after clarification."
            session.phase = "custom_hazard_dimension_check"
            return await self._run_custom_hazard_dimension_check(session_id, session)

        hazard = session.pending_hazard or ""
        if not hazard:
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )
        if not answer:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                session.pending_hazard_clarification_question
                or "Could you clarify how this hazard relates to the selected transition policy context?",
            )

        pending_reason = session.pending_hazard_reason
        if pending_reason:
            reason = pending_reason
            evidence = session.pending_hazard_evidence or ""
            context_review = await self._review_custom_hazard_context(
                session,
                hazard,
                reason,
                evidence,
                clarification=answer,
            )
            if context_review is None:
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message("hazard_validation_unavailable.md"),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    input_mode="textarea",
                    error=True,
                )
            if context_review["status"] == "clarification":
                return self._hazard_clarification_step(
                    session_id,
                    session,
                    hazard,
                    str(context_review["question"]),
                )
            if not context_review["valid"]:
                session.pending_hazard_reason = None
                session.pending_hazard_evidence = None
                session.pending_hazard_clarification_question = None
                session.pending_hazard_clarification_answer = None
                session.phase = "add_hazard_evidence"
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message(
                        "hazard_validation_failed.md",
                        sector=session.sector,
                        reason=str(context_review["reason"]),
                        rewrite_suggestion="",
                    ),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )
            session.pending_hazard_clarification_answer = answer
            if isinstance(session.custom_hazard, dict):
                state = self._custom_hazard_state(session)
                clarifications = list(state.get("clarifications") or [])
                clarifications.append(
                    {
                        "questions": [session.pending_hazard_clarification_question or ""],
                        "answer": answer,
                    }
                )
                state["clarifications"] = clarifications
                state["reason"] = reason
                state["evidence"] = evidence
                state["message"] = "Reason, evidence, and clarification were accepted for grounding validation."
                session.accepted_custom_hazard_reason = reason
                session.accepted_custom_hazard_evidence = evidence or "Not provided"
                session.phase = "custom_hazard_dimension_check"
                return await self._run_custom_hazard_dimension_check(session_id, session)
            return await self._finalize_valid_custom_hazard(
                session_id,
                session,
                hazard,
                reason,
                evidence,
                clarification=answer,
            )

        clarified_hazard = self._clarified_hazard_text(session, hazard, answer)
        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            clarified_hazard,
        )
        if plain_rejection_reason:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                plain_rejection_reason,
            )

        hazard_review = await self._review_custom_hazard_input(session, clarified_hazard)
        if hazard_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=(
                    "I could not review this clarification because the local LLM is "
                    "unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )
        if not hazard_review["valid"]:
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                str(hazard_review["reason"]),
            )

        session.pending_hazard_clarification_answer = answer
        return await self._continue_valid_custom_hazard(
            session_id,
            session,
            hazard,
            clarification=answer,
        )

    def _hazard_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        question: str,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.pending_hazard_clarification_question = question
        session.phase = "add_hazard_clarification"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazard_clarification.md",
                hazard=hazard,
                question=question,
            ),
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    async def _continue_valid_custom_hazard(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        *,
        clarification: str | None = None,
    ) -> ChatResponse:
        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            hazard,
        )
        if sector_mismatch_reason:
            session.pending_hazard = None
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_rewrite_required.md",
                    hazard=hazard,
                    reason=sector_mismatch_reason,
                    rewrite_suggestion=self._custom_hazard_sector_rewrite_suggestion(
                        session,
                        hazard,
                    ),
                    suggestions="",
                    has_suggestions=False,
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )

        existing_hazard = self._match_hazard(hazard, session)
        if existing_hazard is not None:
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                existing_hazard,
                "This appears to already be covered by an existing hazard.",
            )

        local_matches = self._local_similar_hazards(
            hazard,
            self._same_sector_hazard_names_for_duplicate_check(session),
        )
        if local_matches:
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                local_matches[0],
                "This appears to have the same or similar meaning as an existing hazard.",
            )

        duplicate_check = await self._semantic_hazard_duplicate_check(session, hazard)
        if duplicate_check is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=(
                    "I could not check whether this hazard is already covered because "
                    "the local LLM is unavailable. Please try again."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=True,
            )
        if duplicate_check.get("duplicate"):
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                str(duplicate_check.get("match") or ""),
                str(duplicate_check.get("reason") or ""),
            )

        return self._hazard_reason_evidence_step(session_id, session, hazard)

    def _hazard_reason_evidence_step(
        self, session_id: str, session: ChatSession, hazard: str
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.phase = "add_hazard_evidence"
        session.suggested_duplicate_hazard = None
        session.suggested_duplicate_hazard_record_id = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = None
        message = render_message(
            "hazard_reason_evidence.md",
            hazard=hazard,
            matching_hazards="",
            has_matching_hazards=False,
        )
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["raw_text"] = hazard
            state["status"] = CustomHazardStatus.DRAFT.value
            state["message"] = "Reason and optional evidence are required before grounding validation."
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_validation",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="reason_evidence",
                error=False,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=message,
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="reason_evidence",
            error=False,
        )

    def _hazard_duplicate_suggestion_step(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        suggested_hazard: str,
        reason: str,
    ) -> ChatResponse:
        session.pending_hazard = hazard
        session.suggested_duplicate_hazard = suggested_hazard.strip() or None
        session.phase = (
            "custom_hazard_duplicate_confirmation"
            if isinstance(session.custom_hazard, dict)
            else "hazard_duplicate_suggestion"
        )
        message = render_message(
            "hazard_duplicate.md",
            hazard=hazard,
            suggested_hazard=suggested_hazard or "the suggested existing hazard",
            reason=reason or "The proposed hazard appears similar to an existing hazard.",
        )
        if session.phase == "custom_hazard_duplicate_confirmation":
            state = self._custom_hazard_state(session)
            state["message"] = (
                f"This hazard appears similar to an existing hazard: "
                f"'{suggested_hazard or 'the suggested existing hazard'}'."
            )
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_duplicate_confirmation",
                bot_message=message,
                options=HAZARD_DUPLICATE_OPTIONS,
                error=False,
            )
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=message,
            options=HAZARD_DUPLICATE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_hazard_duplicate_suggestion(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_DUPLICATE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_DUPLICATE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action in {normalize("Continue with this hazard"), normalize("Continue with custom hazard")}:
            hazard = session.pending_hazard or ""
            if not hazard:
                session.phase = "custom_hazard_input"
                session.custom_hazard = default_custom_hazard_state()
                return ChatResponse(
                    session_id=session_id,
                    step="hazards",
                    bot_message=render_message("add_hazard.md", sector=session.sector),
                    options=HAZARD_ENTRY_OPTIONS,
                    session=session.summary(),
                    error=True,
                )
            if session.phase == "custom_hazard_duplicate_confirmation":
                state = self._custom_hazard_state(session)
                state["duplicate_override_confirmed"] = True
                if state.get("affected_groups"):
                    state["next_action"] = CustomHazardAction.REVIEW_GROUPS.value
                    state["status"] = CustomHazardStatus.NEEDS_GROUP_REVIEW.value
                else:
                    state["next_action"] = CustomHazardAction.ASK_CLARIFICATION.value
                    state["status"] = CustomHazardStatus.NEEDS_CLARIFICATION.value
                return await self._route_custom_hazard_next_action(session_id, session)
            return self._hazard_reason_evidence_step(session_id, session, hazard)

        if action in {normalize("Explore suggested hazard"), normalize("Use existing hazard")}:
            suggested_hazard = session.suggested_duplicate_hazard or ""
            hazard = self._match_hazard(suggested_hazard, session) or self._fuzzy_hazard(
                suggested_hazard,
                session,
            )
            if hazard is None:
                session.phase = "hazards"
                return self._hazards_step(session_id, session)
            self._clear_selected_hazard_context(session)
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.selected_hazard = hazard
            session.phase = "socio_demographic_review"
            self._record_activity(session_id, session, "hazard_selected", hazard)
            return await self._hazard_profiles_response(session_id, session, hazard)

        if action in {normalize("Write hazard again"), normalize("Edit custom hazard")}:
            session.pending_hazard = None
            session.suggested_duplicate_hazard = None
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=self.invalid_message,
            options=HAZARD_DUPLICATE_OPTIONS,
            session=session.summary(),
            error=True,
        )


    async def _finalize_valid_custom_hazard(
        self,
        session_id: str,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> ChatResponse:
        if session.custom_hazards is None:
            session.custom_hazards = []
        if hazard and not any(normalize(item) == normalize(hazard) for item in session.custom_hazards):
            session.custom_hazards.append(hazard)

        session.pending_hazard = None
        session.pending_hazard_reason = None
        session.pending_hazard_evidence = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = clarification
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_reason = reason
        session.accepted_custom_hazard_evidence = evidence or "Not provided"
        shared_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=reason,
            evidence=evidence or None,
        )
        session.accepted_custom_hazard_id = shared_hazard.id if shared_hazard else None
        session.accepted_custom_hazard_record_id = None
        session.selected_hazard_record_id = None
        self._record_activity(session_id, session, "custom_hazard_added", hazard)
        if evidence:
            self._promote_temporary_evidence(
                session,
                target_scope=VALIDATED_EVIDENCE_SCOPE,
                provenance="validated_user_evidence",
            )

        profiles = await self._extract_custom_hazard_affected_population_profiles(
            session,
            hazard,
            reason,
            evidence,
            clarification=clarification,
        )
        if not profiles:
            profiles = self._additional_hazard_profiles_for_custom_hazard(session, hazard)
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = profiles
        if not profiles:
            session.socio_demographic_profiles = []
            target_population_step = self._start_target_population_questions(session_id, session)
            if target_population_step is not None:
                return target_population_step
        session.socio_demographic_profiles = [
            str(profile.get("name") or profile.get("profile") or "").strip()
            for profile in profiles
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        return self._custom_hazard_population_review_step(session_id, session)

    async def _synthesize_target_population_profile(self, session: ChatSession) -> None:
        hazard = session.accepted_custom_hazard
        answers = session.target_population_answers or []
        if not hazard or not answers:
            return
        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        answers_by_id = {
            int(answer.get("question_id") or 0): answer
            for answer in answers
            if int(answer.get("question_id") or 0) > 0
        }
        all_options_selected = bool(questions_by_id)
        structured_answers: list[dict[str, object]] = []
        for question_id, question in questions_by_id.items():
            answer = answers_by_id.get(question_id, {})
            available = [str(item) for item in question.get("options", [])]
            stored_selected = answer.get("selected")
            selected = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [
                    option
                    for option in available
                    if normalize(option)
                    in normalize(str(answer.get("answer") or ""))
                ]
            )
            if {normalize(item) for item in selected} != {normalize(item) for item in available}:
                all_options_selected = False
            structured_answers.append(
                {
                    "question": str(answer.get("question") or "").strip(),
                    "selected": selected,
                    "available": available,
                }
            )
        required_title = "General Population" if all_options_selected else ""
        context = load_nested_prompt_file("llm/target_population_summary.txt")
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Hazard: {hazard}\n"
                        f"Required title: {required_title or 'Choose a concise title'}\n"
                        f"All options selected: {all_options_selected}\n"
                        "Selections:\n"
                        + json.dumps(structured_answers, ensure_ascii=False)
                    ),
                }
            ],
            temperature=0,
            max_tokens=180,
        )
        title = required_title
        description = ""
        if not is_llm_unavailable_response(response):
            parsed = parse_json_object(response) or {}
            if isinstance(parsed, dict):
                if not required_title:
                    title = str(parsed.get("title") or "").strip()
                description = str(parsed.get("description") or "").strip()
        selected_labels = [
            label
            for answer in structured_answers
            for label in answer.get("selected", [])
            if str(label).strip()
        ]
        if not title:
            title = "Selected Target Population"
        if not description:
            description = (
                "The hazard is considered across the general population without restricting it "
                "to a particular socio-demographic group."
                if all_options_selected
                else "This profile summarizes the selected target groups: "
                + ", ".join(str(label) for label in selected_labels[:6])
                + "."
            )
        title = re.sub(r"\s+", " ", normalize_markdown_text(title)).strip("`*_ #.-")[:120]
        description = re.sub(
            r"\s+", " ", normalize_markdown_text(description)
        ).strip("`*_ #")
        first_sentence = re.match(r"^(.+?[.!?])(?:\s|$)", description)
        if first_sentence:
            description = first_sentence.group(1)
        description = description[:260]
        profile = {
            "name": title,
            "profile": title,
            "variable_name": "generalized_target_population",
            "explanation": description,
            "statistical_basis": "LLM synthesis of user-selected target-population responses.",
            "source": "target_population",
        }
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = [profile]
        session.socio_demographic_profiles = [title]

    def _set_custom_hazard_profiles_from_target_population(self, session: ChatSession) -> None:
        hazard = session.accepted_custom_hazard
        if not hazard:
            return
        existing_profiles = self._stored_hazard_profiles(session, hazard)
        if any(
            profile.get("variable_name") == "generalized_target_population"
            for profile in existing_profiles
        ):
            return
        profiles = self._target_population_profiles_from_answers(
            session.target_population_answers or [],
            hazard,
        )
        if not profiles:
            return
        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        session.hazard_profiles[hazard] = profiles
        session.socio_demographic_profiles = [profile["name"] for profile in profiles]

    def _hydrate_custom_hazard_profiles(self, session: ChatSession) -> None:
        for hazard in session.custom_hazards or []:
            existing_profiles = self._stored_hazard_profiles(session, hazard)
            stored_profiles = self._stored_user_hazard_profiles(session, hazard)
            if stored_profiles:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = self._merge_custom_hazard_profile_sources(
                    existing_profiles,
                    stored_profiles,
                )
                continue
            profiles = self._target_population_profiles_for_saved_hazard(session, hazard)
            if not profiles:
                if existing_profiles and session.hazard_profiles is not None:
                    session.hazard_profiles[hazard] = existing_profiles
                continue
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[hazard] = self._merge_hazard_profile_lists(
                existing_profiles,
                profiles,
            )

    @staticmethod
    def _custom_hazard_profile_name_sections(
        profiles: list[dict[str, object]],
    ) -> tuple[list[str], list[str]]:
        user_sources = {
            "target_population",
            "target_population_additional",
            "user_review",
            "user_validated",
        }
        assistant_names: list[str] = []
        user_names: list[str] = []
        assistant_keys: set[str] = set()
        user_keys: set[str] = set()

        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            key = normalize(name)
            if not key:
                continue
            source = str(profile.get("source") or "").strip()
            if source in user_sources:
                if key not in user_keys and key not in assistant_keys:
                    user_keys.add(key)
                    user_names.append(name)
                continue
            if key not in assistant_keys:
                assistant_keys.add(key)
                assistant_names.append(name)
            if key in user_keys:
                user_keys.remove(key)
                user_names = [value for value in user_names if normalize(value) != key]

        return assistant_names, user_names

    @staticmethod
    def _merge_hazard_profile_lists(
        *profile_groups: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for profiles in profile_groups:
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                name = str(profile.get("name") or profile.get("profile") or "").strip()
                if not name:
                    continue
                source = str(profile.get("source") or "").strip()
                key = normalize(f"{name}|{source}")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(profile)
        return merged

    @classmethod
    def _merge_custom_hazard_profile_sources(
        cls,
        *profile_groups: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[str] = set()
        by_name: dict[str, int] = {}
        for profiles in profile_groups:
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                name = str(profile.get("name") or profile.get("profile") or "").strip()
                if not name:
                    continue
                name_key = normalize(name)
                source = str(profile.get("source") or "").strip()
                label_set = cls._mapped_label_key_set(profile)
                if name_key in by_name:
                    cls._merge_profile_payload(merged[by_name[name_key]], profile)
                    continue
                is_answer_profile = source == "target_population"
                if is_answer_profile and label_set:
                    parent_index = cls._covered_profile_parent_index(merged, label_set)
                    if parent_index is not None:
                        covered_names = merged[parent_index].setdefault("covered_profile_names", [])
                        cls._append_unique_value(covered_names, name)
                        continue
                key = normalize(f"{name}|{source}|{'|'.join(sorted(label_set))}")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(profile))
                by_name[name_key] = len(merged) - 1
        return merged

    @classmethod
    def _merge_profile_payload(
        cls,
        existing: dict[str, object],
        incoming: dict[str, object],
    ) -> None:
        for key in (
            "target_population_option_ids",
            "target_population_labels",
            "population_lookup_labels",
            "population_context",
            "covered_profile_names",
        ):
            values = existing.setdefault(key, [])
            if not isinstance(values, list):
                values = []
                existing[key] = values
            incoming_values = incoming.get(key)
            if not isinstance(incoming_values, list):
                incoming_metadata = incoming.get("metadata")
                if isinstance(incoming_metadata, dict):
                    incoming_values = incoming_metadata.get(key)
            if isinstance(incoming_values, list):
                for value in incoming_values:
                    cls._append_unique_value(values, str(value))

        for key in (
            "explanation",
            "variable_name",
            "variable_type",
            "statistical_basis",
            "source",
            "regional_population_pct",
            "population_pct",
            "national_population_pct",
        ):
            if existing.get(key) in (None, "", []):
                value = incoming.get(key)
                if value not in (None, "", []):
                    existing[key] = value

        incoming_metadata = incoming.get("metadata")
        if isinstance(incoming_metadata, dict):
            metadata = existing.setdefault("metadata", {})
            if isinstance(metadata, dict):
                for key, value in incoming_metadata.items():
                    if key not in metadata or metadata.get(key) in (None, "", []):
                        metadata[key] = value

    @classmethod
    def _covered_profile_parent_index(
        cls,
        profiles: list[dict[str, object]],
        child_labels: set[str],
    ) -> int | None:
        if not child_labels:
            return None
        candidates: list[tuple[int, int]] = []
        for index, profile in enumerate(profiles):
            parent_labels = cls._mapped_label_key_set(profile)
            if parent_labels and child_labels <= parent_labels:
                candidates.append((len(parent_labels), index))
        if not candidates:
            return None
        _, index = max(candidates)
        return index

    @classmethod
    def _filter_session_hazards_without_profiles(cls, session: ChatSession) -> None:
        system_hazards = [
            hazard
            for hazard in (session.hazards or [])
            if cls._stored_hazard_profiles(session, hazard)
        ]
        custom_hazards = [
            hazard
            for hazard in (session.custom_hazards or [])
            if cls._stored_hazard_profiles(session, hazard)
        ]
        additional_hazards = [
            hazard
            for hazard in (session.additional_hazards or [])
            if cls._stored_hazard_profiles(session, hazard)
        ]
        session.hazards = system_hazards
        session.custom_hazards = custom_hazards
        session.additional_hazards = additional_hazards

        allowed = {
            normalize(hazard)
            for hazard in [*system_hazards, *custom_hazards, *additional_hazards]
        }
        session.hazard_profiles = {
            hazard: profiles
            for hazard, profiles in (session.hazard_profiles or {}).items()
            if normalize(str(hazard)) in allowed
        }
        session.hazard_rankings = {
            hazard: ranking
            for hazard, ranking in (session.hazard_rankings or {}).items()
            if normalize(str(hazard)) in allowed
        } or None

    def _target_population_profiles_for_saved_hazard(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is not None:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserSession, UserSession.id == UserQuestionResponse.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserQuestionResponse.custom_hazard_id == custom_hazard_id,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        else:
            rows = []
        if not rows:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserHazard, UserHazard.id == UserQuestionResponse.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazard.source == "custom",
                    UserHazard.name == hazard,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        answers = [
            {
                "question": normalize_markdown_text(question),
                "answer": str(response or "").strip(),
                "selected": [str(response or "").strip()],
            }
            for question, response in rows
            if str(response or "").strip()
        ]
        return self._target_population_profiles_from_answers(answers, hazard)

    @classmethod
    def _target_population_profiles_from_answers(
        cls,
        answers: list[dict[str, object]],
        hazard: str,
    ) -> list[dict[str, str]]:
        grouped: dict[str, dict[str, object]] = {}
        ordered_keys: list[str] = []
        for answer in answers:
            question = str(answer.get("question") or "").strip()
            answer_text = str(answer.get("answer") or "").strip()
            if not question or not answer_text:
                continue
            stored_selected = answer.get("selected")
            labels = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [item.strip() for item in answer_text.split(",") if item.strip()]
            )
            question_key = normalize_for_match(question)
            group = grouped.get(question_key)
            if group is None:
                display_question = cls._display_target_population_question(question)
                group = {
                    "question": question,
                    "name": display_question[:120],
                    "profile": display_question[:120],
                    "variable_name": question[:160],
                    "variable_type": cls._profile_variable_type(question),
                    "options": [],
                    "target_population_labels": [],
                    "population_lookup_labels": [],
                    "source": "target_population",
                }
                grouped[question_key] = group
                ordered_keys.append(question_key)
            for label in labels:
                cleaned_label = label.strip()
                if not cleaned_label:
                    continue
                cls._append_unique_value(group["options"], cleaned_label)
                mapped_label = f"{question.rstrip('.')}: {cleaned_label}"
                cls._append_unique_value(group["target_population_labels"], mapped_label)
                cls._append_unique_value(group["population_lookup_labels"], mapped_label)

        profiles: list[dict[str, str]] = []
        for key in ordered_keys:
            group = grouped[key]
            options = [
                str(option).strip()
                for option in group.get("options", [])
                if str(option).strip()
            ]
            if not options:
                continue
            explanation = (
                "Synthesized from user-selected target-population responses for "
                f"{hazard}: " + "; ".join(options)
            )
            profiles.append(
                {
                    "name": str(group.get("name") or "")[:120],
                    "profile": str(group.get("profile") or "")[:120],
                    "variable_name": str(group.get("variable_name") or "")[:160],
                    "variable_type": str(group.get("variable_type") or "individual"),
                    "explanation": explanation[:260],
                    "statistical_basis": "User-selected socio-demographic question response.",
                    "source": "target_population",
                    "target_population_labels": list(group.get("target_population_labels") or []),
                    "population_lookup_labels": list(group.get("population_lookup_labels") or []),
                    "metadata": {
                        "target_population_labels": list(group.get("target_population_labels") or []),
                        "population_lookup_labels": list(group.get("population_lookup_labels") or []),
                    },
                }
            )
        return profiles

    @staticmethod
    def _target_population_profile_name(question: str, label: str) -> str:
        normalized_question = question.strip().rstrip(".")
        normalized_label = label.strip()
        label_key = normalize(normalized_label)
        if label_key == normalize("Yes"):
            return normalized_question
        if label_key == normalize("No"):
            return f"Not {normalized_question[:1].lower()}{normalized_question[1:]}"
        if "age" in normalize_for_match(normalized_question):
            return f"Age {normalized_label}"
        if normalize_for_match(normalized_question) in {"gender", "sex"}:
            return normalized_label
        return f"{normalized_question}: {normalized_label}"

    def _current_evaluation_question(
        self, session: ChatSession
    ) -> dict[str, str | int] | None:
        questions = session.evaluation_questions or []
        if session.evaluation_index < 0 or session.evaluation_index >= len(questions):
            return None
        return questions[session.evaluation_index]

    def _evaluation_questions(self) -> list[dict[str, str | int]]:
        rows = self.db.scalars(
            select(EvaluationQuestion)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category.in_(EVALUATION_CATEGORIES),
            )
            .order_by(EvaluationQuestion.category.desc(), EvaluationQuestion.sort_order)
        ).all()
        category_order = {category: index for index, category in enumerate(EVALUATION_CATEGORIES)}
        sorted_rows = sorted(
            rows,
            key=lambda row: (category_order.get(row.category, 99), row.sort_order, row.id),
        )
        return [
            {
                "id": row.id,
                "category": row.category,
                "chart_title": row.chart_title or normalize_markdown_text(row.question),
                "question": normalize_markdown_text(row.question),
            }
            for row in sorted_rows
        ]

    @classmethod
    def _extend_unique_profiles(cls, existing: list[str], new_profiles: list[str]) -> None:
        for profile in new_profiles:
            if any(cls._profiles_are_similar(profile, existing_profile) for existing_profile in existing):
                continue
            existing.append(profile)

    @classmethod
    def _match_existing_dg(cls, session: ChatSession, new_profiles: list[str]) -> dict[str, object] | None:
        existing = cls._selected_hazard_profile_names(session)
        for profile in new_profiles:
            for existing_profile in existing:
                if cls._profiles_are_similar(profile, existing_profile):
                    return {
                        "duplicate": True,
                        "match": existing_profile,
                        "reason": "The proposed profile is the same as, or very similar to, an existing profile.",
                        "duplicates": [
                            {
                                "profile": profile,
                                "match": existing_profile,
                                "reason": "Similar profile already exists.",
                            }
                        ],
                    }
        return None

    @classmethod
    def _profiles_are_similar(cls, left: str, right: str) -> bool:
        left_key = normalize_for_match(left)
        right_key = normalize_for_match(right)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        left_compact = compact_for_match(left)
        right_compact = compact_for_match(right)
        if left_compact and right_compact and (
            left_compact in right_compact or right_compact in left_compact
        ):
            return True
        left_words = cls._profile_similarity_words(left_key)
        right_words = cls._profile_similarity_words(right_key)
        if not left_words or not right_words:
            return False
        overlap = len(left_words & right_words)
        smaller_overlap = overlap / max(1, min(len(left_words), len(right_words)))
        larger_overlap = overlap / max(1, max(len(left_words), len(right_words)))
        return smaller_overlap >= 0.85 or (smaller_overlap >= 0.7 and larger_overlap >= 0.5)

    @staticmethod
    def _profile_similarity_words(value: str) -> set[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "for",
            "in",
            "is",
            "of",
            "the",
            "to",
            "with",
        }
        words: set[str] = set()
        for word in value.split():
            if word in stop_words or len(word) <= 2:
                continue
            if len(word) > 4 and word.endswith("ies"):
                word = word[:-3] + "y"
            elif len(word) > 4 and word.endswith("s"):
                word = word[:-1]
            words.add(word)
        return words

    @classmethod
    def _selected_hazard_profile_names(cls, session: ChatSession) -> list[str]:
        profiles: list[str] = []
        if session.socio_demographic_profiles:
            profiles.extend(session.socio_demographic_profiles)
        elif session.socio_demographic_findings:
            profiles.extend(cls._extract_socio_demographic_profiles(session.socio_demographic_findings))

        selected_hazard = session.selected_hazard or session.accepted_custom_hazard
        if selected_hazard:
            stored_profiles = cls._stored_hazard_profiles(session, selected_hazard)
            profiles.extend(profile["name"] for profile in stored_profiles if profile.get("name"))

        profiles.extend(session.additional_dgs or [])

        deduped: list[str] = []
        seen: set[str] = set()
        for profile in profiles:
            key = normalize(profile)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(profile)
        return deduped

    @classmethod
    def _format_selected_hazard_profiles_for_duplicate_check(cls, session: ChatSession) -> str:
        profiles = cls._selected_hazard_profile_names(session)
        if not profiles:
            return "- No socio-demographic profiles have been identified for the selected hazard yet."
        return "\n".join(f"- {profile}" for profile in profiles)

    @staticmethod
    def _format_duplicate_dgs(duplicate_check: dict[str, object]) -> str:
        duplicates = duplicate_check.get("duplicates")
        if isinstance(duplicates, list) and duplicates:
            lines: list[str] = []
            for item in duplicates:
                if not isinstance(item, dict):
                    continue
                profile = str(item.get("profile") or "Proposed profile")
                match = str(item.get("match") or "an existing profile")
                reason = str(item.get("reason") or "").strip()
                line = f"- **{profile}** already appears to be covered by **{match}**"
                if reason:
                    line += f": {reason}"
                lines.append(line)
            if lines:
                return "\n".join(lines)

        match = str(duplicate_check.get("match") or "an existing profile")
        reason = str(duplicate_check.get("reason") or "").strip()
        line = f"- The proposed profile already appears to be covered by **{match}**"
        if reason:
            line += f": {reason}"
        return line

    @staticmethod
    def _format_hazard_suggestions(review: dict[str, object]) -> str:
        suggestions = review.get("suggestions")
        if isinstance(suggestions, list):
            lines = [f"- **{item}**" for item in suggestions if str(item).strip()]
            if lines:
                return "\n".join(lines)
        return ""


    @staticmethod
    def _evaluation_evidence_text(evidence: str | None) -> str | None:
        if not evidence or not evidence.strip():
            return None

        lines = [line.strip() for line in evidence.splitlines() if line.strip()]
        if not lines:
            return None

        content_lines = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.casefold().startswith("evidence content:")
            and line.split(":", 1)[1].strip()
        ]
        if content_lines:
            source_lines = [
                line
                for line in lines
                if line.casefold().startswith(("evidence url:", "evidence file:"))
            ]
            return "\n".join([*source_lines, *content_lines]).strip()

        return "\n".join(lines)

    @staticmethod
    def _temporary_evidence_document_ids(evidence: str | None) -> list[int]:
        if not evidence:
            return []
        return [
            int(match)
            for match in re.findall(
                r"Temporary evidence document ID:\s*(\d+)",
                evidence,
                flags=re.IGNORECASE,
            )
        ]

    def _discard_temporary_evidence(self, session: ChatSession, evidence: str | None) -> None:
        document_ids = self._temporary_evidence_document_ids(evidence)
        if not session.session_key or not self._has_user_supplied_evidence(evidence):
            return
        try:
            KnowledgeBaseService(
                self.db,
                self.user_id,
                scope=TEMPORARY_KB_SCOPE,
                session_key=session.session_key,
            ).delete_temporary_documents(document_ids)
        except Exception:
            logger.exception("Failed to discard rejected temporary evidence")

    def _promote_temporary_evidence(
        self,
        session: ChatSession,
        *,
        target_scope: str = VALIDATED_EVIDENCE_SCOPE,
        provenance: str | None = None,
    ) -> None:
        if not session.session_key:
            return
        try:
            KnowledgeBaseService(
                self.db,
                self.user_id,
                scope=TEMPORARY_KB_SCOPE,
                session_key=session.session_key,
            ).promote_temporary_documents(
                target_scope=target_scope,
                provenance=provenance,
                country_id=session.country_id,
                region_id=session.region_id,
                sector_id=session.sector_id,
            )
        except Exception:
            logger.exception("Failed to promote temporary evidence")

    @staticmethod
    def _has_hazard_suggestions(review: dict[str, object]) -> bool:
        suggestions = review.get("suggestions")
        return isinstance(suggestions, list) and any(str(item).strip() for item in suggestions)

    @classmethod
    def _local_similar_hazards(cls, hazard: str, existing_hazards: list[str]) -> list[str]:
        return local_similar_hazards(hazard, existing_hazards)

    @staticmethod
    def _hazard_similarity_words(value: str) -> set[str]:
        return hazard_similarity_words(value)

    @classmethod
    def _extract_socio_demographic_profiles(cls, markdown_text: str) -> list[str]:
        return extract_socio_demographic_profiles(markdown_text, cls._is_statistical_basis_line)

    @classmethod
    def _parse_hazard_profile_items(cls, response: str) -> list[dict[str, str]]:
        return parse_hazard_profile_items(response)

    async def _profiles_with_population_context(
        self,
        session: ChatSession,
        hazard: str,
        profiles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not profiles:
            return profiles
        population_profiles = self._ranking_population_profiles(session, hazard)
        if not population_profiles:
            return []
        cached_matches: dict[str, list[dict[str, object]]] = {}
        blocked_profiles: set[str] = set()
        profiles_needing_match: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name or self._profile_has_odds_ratio_below_one(profile):
                continue
            if self._profile_population_match_blocked(session, hazard, profile):
                blocked_profiles.add(normalize(name))
                continue
            cached_match = self._matched_population_profile_from_db(
                session,
                hazard,
                profile,
                population_profiles,
            )
            if cached_match:
                cached_matches[normalize(name)] = cached_match
            else:
                profiles_needing_match.append(profile)
        llm_matches = (
            await self._match_population_profiles_with_llm(
                profiles_needing_match,
                population_profiles,
            )
            if profiles_needing_match
            else {}
        )
        enriched: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name or self._profile_has_odds_ratio_below_one(profile):
                continue
            if normalize(name) in blocked_profiles:
                continue
            matches = self._merge_population_profile_matches(
                cached_matches.get(normalize(name), []),
                llm_matches.get(normalize(name), []),
                self._deterministic_population_profile_matches(
                    name,
                    population_profiles,
                ),
            )
            percentages = self._population_context_percentages(matches) if matches else None
            if percentages is None:
                self._record_profile_population_match_failure(session, hazard, profile)
                continue
            self._store_matched_profile_population_references(
                session,
                hazard,
                profile,
                matches,
            )
            updated = dict(profile)
            updated["regional_population_pct"] = percentages[0]
            updated["national_population_pct"] = percentages[1]
            updated.pop("population_context", None)
            enriched.append(updated)
        return enriched

    async def _additional_profiles_with_population_context(
        self,
        session: ChatSession,
        hazard: str,
        profiles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not profiles:
            return profiles
        country = self.db.get(Country, session.country_id) if session.country_id else None
        sector = self.db.get(Sector, session.sector_id) if session.sector_id else None
        region = self.db.get(Region, session.region_id) if session.region_id else None
        if country is None or sector is None:
            return profiles
        country_name = str(country.name or "").strip()
        region_name = str((region.name if region else country.name) or "").strip()
        sector_name = str(sector.name or "").strip()
        if not country_name or not region_name or not sector_name:
            return profiles

        enriched: list[dict[str, str]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                enriched.append(profile)
                continue
            lookup_labels = self._additional_profile_population_lookup_labels(profile)
            if not lookup_labels:
                enriched.append(profile)
                continue
            population_profiles: list[dict[str, object]] = []
            for label in lookup_labels:
                try:
                    prevalence = await self.eurostat.get_prevalence(
                        label,
                        country_code=country_name,
                        nuts_code=region_name,
                        sector=sector_name,
                        hazard=hazard,
                        confirmed_predictor_category=label,
                    )
                except Exception:
                    logger.exception(
                        "Failed to fetch Eurostat population for additional hazard profile"
                    )
                    continue
                if prevalence is None:
                    continue
                population_profiles.append(
                    {
                        "name": label,
                        "eurostat_population_cache_id": prevalence.get(
                            "eurostat_population_cache_id"
                        ),
                        "population_pct": prevalence.get("population_pct"),
                        "national_population_pct": prevalence.get("national_population_pct"),
                        "source": prevalence.get("source"),
                        "dataset": prevalence.get("dataset"),
                        "geo": prevalence.get("geo"),
                    }
                )
            percentages = (
                self._population_context_percentages(population_profiles)
                if population_profiles
                else None
            )
            if percentages is None:
                enriched.append(profile)
                continue
            updated = dict(profile)
            updated["regional_population_pct"] = percentages[0]
            updated["national_population_pct"] = percentages[1]
            updated["population_context"] = population_profiles
            updated["population_source"] = "Eurostat"
            updated["population_lookup_labels"] = lookup_labels
            enriched.append(updated)
        return enriched

    @classmethod
    def _additional_profile_population_lookup_labels(
        cls,
        profile: dict[str, object],
    ) -> list[str]:
        labels: list[str] = []
        raw_labels = cls._list_from_profile_or_metadata(
            profile,
            "target_population_labels",
        )
        labels.extend(str(label).strip() for label in raw_labels if str(label).strip())
        if not labels:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if name:
                labels.append(name)
        seen: set[str] = set()
        deduped: list[str] = []
        for label in labels:
            key = normalize(label)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(label)
        return deduped

    @staticmethod
    def _profile_has_odds_ratio_below_one(profile: dict[str, object]) -> bool:
        candidates: list[object] = [profile]
        metadata = profile.get("metadata")
        if isinstance(metadata, dict):
            candidates.append(metadata)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("odds_ratio", "or", "OR"):
                if candidate.get(key) is None:
                    continue
                try:
                    return float(candidate[key]) < 1
                except (TypeError, ValueError):
                    pass
        text_value = " ".join(
            str(profile.get(key) or "")
            for key in ("explanation", "statistical_basis", "basis")
        )
        ratio_matches = re.finditer(
            r"(?i)(?:odds\s+ratio|\bOR\b)\s*(?:=|:|is|<|>|<=|>=)?\s*(\d+(?:\.\d+)?)",
            text_value,
        )
        for ratio_match in ratio_matches:
            try:
                if float(ratio_match.group(1)) < 1:
                    return True
            except (TypeError, ValueError):
                continue
        normalized_text = normalize_for_match(text_value)
        basis_text = normalize_for_match(
            " ".join(
                str(profile.get(key) or "")
                for key in ("statistical_basis", "basis")
            )
        )
        if "decreases" in basis_text or "direction decreases" in basis_text:
            return True
        return any(
            marker in normalized_text
            for marker in ("protective", "lower odds", "lower concern")
        )

    async def _match_population_profiles_with_llm(
        self,
        profiles: list[dict[str, str]],
        population_profiles: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        profile_items = [
            {
                "name": str(profile.get("name") or profile.get("profile") or "").strip(),
                "explanation": str(profile.get("explanation") or "").strip(),
            }
            for profile in profiles
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        candidate_names = [
            str(profile.get("name") or "").strip()
            for profile in population_profiles
            if str(profile.get("name") or "").strip()
        ]
        if not profile_items or not candidate_names:
            return {}
        context = load_nested_prompt_file("llm/population_profile_matcher.txt")
        messages = [
            {
                "role": "user",
                "content": (
                    "Displayed profiles with explanations:\n"
                    + json.dumps(profile_items, ensure_ascii=False)
                    + "\n\nPopulation profiles:\n"
                    + json.dumps(candidate_names, ensure_ascii=False)
                    + "\n\nReturn ONLY a JSON array like:\n"
                    '[{"profile": "displayed profile", '
                    '"matched_profiles": ["population profile 1", "population profile 2"]}]'
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=500,
        )
        parsed = parse_json_array(response)
        if not isinstance(parsed, list):
            return {}
        population_by_name = {
            normalize(str(profile.get("name") or "")): profile
            for profile in population_profiles
            if str(profile.get("name") or "").strip()
        }
        matches: dict[str, list[dict[str, object]]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            profile_name = str(item.get("profile") or "").strip()
            raw_matched_names = item.get("matched_profiles")
            if not isinstance(raw_matched_names, list):
                legacy_name = item.get("matched_profile")
                raw_matched_names = [legacy_name] if legacy_name is not None else []
            if not profile_name:
                continue
            matched_profiles = [
                population_by_name[normalize(str(matched_name or "").strip())]
                for matched_name in raw_matched_names
                if normalize(str(matched_name or "").strip()) in population_by_name
            ]
            if matched_profiles:
                matches[normalize(profile_name)] = matched_profiles
        return matches

    @staticmethod
    def _ranking_population_profiles(
        session: ChatSession,
        hazard: str,
    ) -> list[dict[str, object]]:
        rankings = session.hazard_rankings or {}
        ranking = rankings.get(hazard)
        if ranking is None:
            hazard_key = normalize(hazard)
            for stored_hazard, stored_ranking in rankings.items():
                if normalize(str(stored_hazard)) == hazard_key:
                    ranking = stored_ranking
                    break
        if not isinstance(ranking, dict):
            return []
        profiles = ranking.get("profiles")
        if not isinstance(profiles, list):
            return []
        return [
            profile
            for profile in profiles
            if isinstance(profile, dict)
            and str(profile.get("name") or "").strip()
            and profile.get("population_pct") is not None
        ]

    def _matched_population_profile_from_db(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
        population_profiles: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if session.sector_id is None:
            return []
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return []
        cache_ids: set[int] = set()
        for population_profile in population_profiles:
            try:
                cache_id = int(population_profile.get("eurostat_population_cache_id") or 0)
            except (TypeError, ValueError):
                continue
            if cache_id > 0:
                cache_ids.add(cache_id)
        if not cache_ids:
            return []
        system_hazard = self.db.scalar(
            select(SystemHazard).where(
                SystemHazard.sector_id == session.sector_id,
                SystemHazard.name == hazard,
            )
        )
        if system_hazard is None:
            return []
        system_profile = self.db.scalar(
            select(SystemHazardSocioDemographic).where(
                SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
                func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
            )
        )
        if system_profile is None:
            return []
        matched_caches = self.db.scalars(
            select(EurostatPopulationCache)
            .join(
                SystemHazardSocioDemographicPopulationMatch,
                SystemHazardSocioDemographicPopulationMatch.eurostat_population_cache_id
                == EurostatPopulationCache.id,
            )
            .where(
                SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                == system_profile.id,
                SystemHazardSocioDemographicPopulationMatch.match_status == 1,
                EurostatPopulationCache.id.in_(cache_ids),
                EurostatPopulationCache.country_id == session.country_id,
                EurostatPopulationCache.region_id == session.region_id,
                EurostatPopulationCache.sector_id == session.sector_id,
                EurostatPopulationCache.system_hazard_id == system_hazard.id,
                EurostatPopulationCache.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
            )
        ).all()
        matched_cache_ids = {cache.id for cache in matched_caches}
        return [
            population_profile
            for population_profile in population_profiles
            if int(population_profile.get("eurostat_population_cache_id") or 0)
            in matched_cache_ids
        ]

    def _profile_population_match_blocked(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> bool:
        system_profile = self._system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return False
        blocked = self.db.scalar(
            select(SystemHazardSocioDemographicPopulationMatch).where(
                SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                == system_profile.id,
                SystemHazardSocioDemographicPopulationMatch.match_status == -1,
            )
        )
        return blocked is not None

    def _record_profile_population_match_failure(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> None:
        system_profile = self._ensure_system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographicPopulationMatch).where(
                    SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                    == system_profile.id,
                    SystemHazardSocioDemographicPopulationMatch.eurostat_population_cache_id.is_(None),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographicPopulationMatch(
                    system_hazard_socio_demographic_id=system_profile.id,
                    eurostat_population_cache_id=None,
                    match_status=0,
                    attempt_count=0,
                )
                self.db.add(row)
            row.attempt_count = int(row.attempt_count or 0) + 1
            if row.attempt_count >= 3:
                row.match_status = -1
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to record profile population match failure")

    @staticmethod
    def _deterministic_population_profile_matches(
        profile_name: str,
        population_profiles: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        profile_key = normalize(profile_name)
        return [
            population_profile
            for population_profile in population_profiles
            if (
                (candidate_key := normalize(str(population_profile.get("name") or "").strip()))
                and profile_key
                and (profile_key in candidate_key or candidate_key in profile_key)
            )
        ]

    @staticmethod
    def _merge_population_profile_matches(
        *match_groups: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        seen: set[tuple[int, str]] = set()
        for group in match_groups:
            for profile in group:
                try:
                    cache_id = int(profile.get("eurostat_population_cache_id") or 0)
                except (TypeError, ValueError):
                    cache_id = 0
                key = (cache_id, normalize(str(profile.get("name") or "")))
                if key in seen:
                    continue
                seen.add(key)
                matches.append(profile)
        return matches

    @staticmethod
    def _population_context_percentages(
        profiles: list[dict[str, object]],
    ) -> tuple[float, float] | None:
        regional_values: list[float] = []
        national_values: list[float] = []
        for profile in profiles:
            try:
                regional_values.append(float(profile.get("population_pct")))
                national_values.append(float(profile.get("national_population_pct")))
            except (TypeError, ValueError):
                continue
        if not regional_values or not national_values:
            return None
        regional_pct = sum(regional_values) / len(regional_values)
        national_pct = sum(national_values) / len(national_values)
        return round(regional_pct, 1), round(national_pct, 1)

    @staticmethod
    def _system_style_user_profile(profile: dict[str, str]) -> dict[str, str]:
        name = str(profile.get("name") or profile.get("profile") or "").strip()
        explanation = str(profile.get("explanation") or "").strip()
        if not explanation:
            metadata = profile.get("metadata")
            if isinstance(metadata, dict):
                explanation = str(
                    metadata.get("explanation")
                    or metadata.get("reason")
                    or metadata.get("description")
                    or ""
                ).strip()
        if not explanation:
            source = str(profile.get("source") or "").strip()
            if source == "target_population":
                explanation = "Added from the target-population selections for this hazard."
            else:
                explanation = (
                    "Added by the user as an additional socio-demographic profile "
                    "and validated for this hazard."
                )
        return {
            **profile,
            "name": name,
            "profile": name,
            "explanation": explanation,
        }

    @classmethod
    def _stored_hazard_profiles(cls, session: ChatSession, hazard: str) -> list[dict[str, str]]:
        stored_profiles = session.hazard_profiles or {}
        values = stored_profiles.get(hazard)
        if values is None:
            hazard_key = normalize(hazard)
            for stored_hazard, stored_value in stored_profiles.items():
                if normalize(str(stored_hazard)) == hazard_key:
                    values = stored_value
                    break
        if values is None:
            return []
        if isinstance(values, str):
            raw_items: list[dict[str, str] | str] = [values]
        else:
            raw_items = list(values)

        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("profile") or "").strip()
                explanation = str(item.get("explanation") or "").strip()
                variable_name = str(item.get("variable_name") or item.get("variable") or "").strip()
                variable_type = str(item.get("variable_type") or "").strip()
                statistical_basis = str(item.get("statistical_basis") or "").strip()
                source = str(item.get("source") or "").strip()
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                target_population_option_ids = item.get("target_population_option_ids")
                if not isinstance(target_population_option_ids, list) or not target_population_option_ids:
                    target_population_option_ids = metadata.get("target_population_option_ids")
                target_population_labels = item.get("target_population_labels")
                if not isinstance(target_population_labels, list) or not target_population_labels:
                    target_population_labels = metadata.get("target_population_labels")
                population_context = item.get("population_context")
                population_lookup_labels = item.get("population_lookup_labels")
                if not isinstance(population_lookup_labels, list) or not population_lookup_labels:
                    population_lookup_labels = metadata.get("population_lookup_labels")
                regional_population_pct = (
                    item.get("regional_population_pct") or item.get("population_pct")
                )
                national_population_pct = item.get("national_population_pct")
                population_source = item.get("population_source")
            else:
                name = str(item).strip()
                explanation = ""
                variable_name = ""
                variable_type = ""
                statistical_basis = ""
                source = ""
                metadata = {}
                target_population_option_ids = []
                target_population_labels = []
                population_context = []
                population_lookup_labels = []
                regional_population_pct = None
                national_population_pct = None
                population_source = None
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": explanation,
                    "variable_name": variable_name,
                    "variable_type": cls._profile_variable_type(variable_name, variable_type),
                    "statistical_basis": statistical_basis,
                    "source": source,
                    "metadata": metadata,
                    "target_population_option_ids": (
                        list(target_population_option_ids)
                        if isinstance(target_population_option_ids, list)
                        else []
                    ),
                    "target_population_labels": (
                        list(target_population_labels)
                        if isinstance(target_population_labels, list)
                        else []
                    ),
                    "regional_population_pct": regional_population_pct,
                    "population_pct": regional_population_pct,
                    "national_population_pct": national_population_pct,
                    "population_source": str(population_source or ""),
                    "population_context": (
                        list(population_context)
                        if isinstance(population_context, list)
                        else []
                    ),
                    "population_lookup_labels": (
                        list(population_lookup_labels)
                        if isinstance(population_lookup_labels, list)
                        else []
                    ),
                }
            )
        return profiles

    @staticmethod
    def _profile_variable_type(variable_name: object, variable_type: object = "") -> str:
        if str(variable_type or "").strip().casefold() == "macro":
            return "macro"
        if str(variable_name or "").strip().casefold().startswith("macro_"):
            return "macro"
        return "individual"

    def _stored_custom_hazard_profiles(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, object]]:
        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is None:
            return []
        try:
            rows = self.db.scalars(
                select(CustomHazardProfile)
                .where(CustomHazardProfile.custom_hazard_id == custom_hazard_id)
                .order_by(CustomHazardProfile.id)
            ).all()
        except Exception:
            logger.exception("Failed to load shared custom hazard profiles")
            return []

        profiles: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in rows:
            name = str(row.profile or "").strip()
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            metadata = self._metadata_from_json(row.metadata_json)
            target_population_option_ids = (
                metadata.get("target_population_option_ids")
                if isinstance(metadata.get("target_population_option_ids"), list)
                else []
            )
            target_population_labels = (
                metadata.get("target_population_labels")
                if isinstance(metadata.get("target_population_labels"), list)
                else []
            )
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.explanation or metadata.get("explanation") or ""),
                    "variable_name": str(
                        row.variable_name
                        or metadata.get("variable_name")
                        or metadata.get("variable")
                        or ""
                    ),
                    "variable_type": self._profile_variable_type(
                        row.variable_name or metadata.get("variable_name") or metadata.get("variable") or "",
                        metadata.get("variable_type") or "",
                    ),
                    "statistical_basis": str(
                        row.statistical_basis
                        or metadata.get("statistical_basis")
                        or metadata.get("basis")
                        or ""
                    ),
                    "source": str(row.source or metadata.get("source") or "custom_hazard_extraction"),
                    "metadata": metadata,
                    "target_population_option_ids": target_population_option_ids,
                    "target_population_labels": target_population_labels,
                    "population_context": metadata.get("population_context")
                    if isinstance(metadata.get("population_context"), list)
                    else [],
                    "population_lookup_labels": metadata.get("population_lookup_labels")
                    if isinstance(metadata.get("population_lookup_labels"), list)
                    else [],
                }
            )
        return profiles

    def _stored_user_hazard_profiles(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        is_custom_hazard = self._is_saved_custom_hazard(session, hazard) or normalize(hazard) == normalize(
            session.accepted_custom_hazard or ""
        )
        shared_profiles = self._stored_custom_hazard_profiles(session, hazard)
        try:
            allowed_sources = [
                "user_validated",
                "target_population",
                "custom_hazard_extraction",
                "user_review",
                "d4_2_pdf",
                "llm",
            ]
            base_filters = [
                UserSession.country_id == session.country_id,
                UserHazardSocioDemographic.country_id == session.country_id,
                UserHazardSocioDemographic.region_id.is_(None)
                if session.region_id is None
                else UserHazardSocioDemographic.region_id == session.region_id,
                UserHazardSocioDemographic.sector_id == session.sector_id,
                UserHazardSocioDemographic.source.in_(allowed_sources),
            ]
            if is_custom_hazard:
                custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
                if custom_hazard_id is not None:
                    query = (
                        select(UserHazardSocioDemographic)
                        .join(UserSession, UserSession.id == UserHazardSocioDemographic.user_session_id)
                        .where(
                            UserHazardSocioDemographic.custom_hazard_id == custom_hazard_id,
                            *base_filters,
                        )
                        .order_by(UserHazardSocioDemographic.id)
                    )
                else:
                    query = (
                        select(UserHazardSocioDemographic)
                        .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                        .join(UserSession, UserSession.id == UserHazard.user_session_id)
                        .where(
                            func.lower(UserHazard.name) == hazard.casefold(),
                            UserHazard.sector_id == session.sector_id,
                            UserHazard.region_id.is_(None)
                            if session.region_id is None
                            else UserHazard.region_id == session.region_id,
                            *base_filters,
                        )
                        .order_by(UserHazardSocioDemographic.id)
                    )
            else:
                system_hazard_id = None
                additional_hazard_id = None
                if self._is_additional_hazard(session, hazard):
                    additional_hazard_id = self._selected_additional_hazard_id(session, hazard)
                else:
                    system_hazard_id = self.db.scalar(
                        select(SystemHazard.id).where(
                            SystemHazard.sector_id == session.sector_id,
                            func.lower(SystemHazard.name) == hazard.casefold(),
                        )
                    )
                if system_hazard_id is None and additional_hazard_id is None:
                    return []
                query = (
                    select(UserHazardSocioDemographic)
                    .join(UserSession, UserSession.id == UserHazardSocioDemographic.user_session_id)
                    .where(
                        UserHazardSocioDemographic.system_hazard_id == system_hazard_id,
                        UserHazardSocioDemographic.additional_hazard_id == additional_hazard_id,
                        *base_filters,
                    )
                    .order_by(UserHazardSocioDemographic.id)
                )
            if self.user_id is not None:
                query = query.where(UserSession.user_id == self.user_id)
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load user-added socio-demographic profiles")
            return shared_profiles

        target_questions = self._target_population_questions()
        grouped: dict[str, dict[str, object]] = {}
        ungrouped: list[dict[str, str]] = []
        for row in rows:
            name = str(row.profile or "").strip()
            if not name:
                continue
            metadata = self._metadata_from_json(row.metadata_json)
            variable_name = str(
                row.variable_name
                or metadata.get("variable_name")
                or metadata.get("variable")
                or ""
            ).strip()
            if not variable_name:
                variable_name = self._infer_target_population_question(name, target_questions)
            source = str(row.source or "user_validated")
            if variable_name and source == "target_population":
                group = grouped.setdefault(
                    normalize(variable_name),
                    {
                        "question": variable_name,
                        "labels": [],
                        "source": source,
                        "statistical_basis": str(row.statistical_basis or ""),
                        "metadata": metadata,
                    },
                )
                labels = group.setdefault("labels", [])
                if isinstance(labels, list):
                    label = self._target_population_group_label(variable_name, name)
                    if label and normalize(label) not in {normalize(str(item)) for item in labels}:
                        labels.append(label)
                if not group.get("statistical_basis") and row.statistical_basis:
                    group["statistical_basis"] = str(row.statistical_basis)
                continue

            key = normalize(name)
            if key in {normalize(profile["name"]) for profile in ungrouped}:
                continue
            target_population_option_ids = (
                metadata.get("target_population_option_ids")
                if isinstance(metadata.get("target_population_option_ids"), list)
                else []
            )
            target_population_labels = (
                metadata.get("target_population_labels")
                if isinstance(metadata.get("target_population_labels"), list)
                else ([name] if source == "target_population" else [])
            )
            ungrouped.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.explanation or ""),
                    "variable_name": "",
                    "variable_type": self._profile_variable_type(""),
                    "statistical_basis": str(row.statistical_basis or ""),
                    "source": source,
                    "metadata": metadata,
                    "target_population_option_ids": target_population_option_ids,
                    "target_population_labels": target_population_labels,
                }
            )

        profiles: list[dict[str, str]] = []
        for group in grouped.values():
            question = str(group.get("question") or "").strip()
            labels = [
                str(label).strip()
                for label in group.get("labels", [])
                if str(label).strip()
            ]
            labels_text = ", ".join(labels)
            name = f"{question}: {labels_text}" if labels_text else question
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": "Selected target-population responses for this hazard.",
                    "variable_name": question,
                    "variable_type": self._profile_variable_type(question),
                    "statistical_basis": str(group.get("statistical_basis") or ""),
                    "source": str(group.get("source") or "user_validated"),
                    "target_population_labels": labels,
                    "metadata": group.get("metadata") if isinstance(group.get("metadata"), dict) else {},
                }
            )
        profiles.extend(ungrouped)
        answer_profiles = (
            self._target_population_profiles_for_saved_hazard(session, hazard)
            if is_custom_hazard
            else []
        )
        return self._merge_custom_hazard_profile_sources(
            shared_profiles,
            profiles,
            answer_profiles,
        )

    @staticmethod
    def _target_population_group_label(question: str, profile_name: str) -> str:
        question = question.strip().rstrip(".")
        profile_name = profile_name.strip()
        if normalize(profile_name) == normalize(question):
            return "Yes"
        not_prefix = f"Not {question[:1].lower()}{question[1:]}"
        if normalize(profile_name) == normalize(not_prefix):
            return "No"
        prefix = f"{question}:"
        if normalize(profile_name).startswith(normalize(prefix)):
            return profile_name.split(":", 1)[1].strip()
        if "age" in normalize_for_match(question) and normalize_for_match(profile_name).startswith("age "):
            return profile_name[4:].strip()
        return profile_name

    @staticmethod
    def _infer_target_population_question(
        profile_name: str, questions: list[dict[str, object]]
    ) -> str:
        profile_key = normalize_for_match(profile_name)
        for question in questions:
            question_text = str(question.get("question") or "").strip()
            question_key = normalize_for_match(question_text)
            if not question_key:
                continue
            if profile_key == question_key or profile_key == f"not {question_key}":
                return question_text
            if profile_key.startswith(f"{question_key} "):
                return question_text
            for option in question.get("options", []):
                option_key = normalize_for_match(str(option))
                if option_key and profile_key == option_key:
                    return question_text
        for question in questions:
            question_text = str(question.get("question") or "").strip()
            if "age" in normalize_for_match(question_text) and profile_key.startswith("age "):
                return question_text
        return ""

    @classmethod
    def _confirmed_predictor_hazard_block(cls, sector_prompt: str, hazard: str) -> str:
        target = normalize_for_match(hazard)
        hazard_pattern = re.compile(
            r"(?ms)^HAZARD\s+\d+\.\s+(.+?)\n(.*?)(?=^HAZARD\s+\d+\.|\Z)"
        )
        prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
        for match in hazard_pattern.finditer(prompt):
            heading = cls._clean_sector_hazard_name(match.group(1))
            if normalize_for_match(heading) == target:
                return strip_rule_lines(match.group(0))
        for match in hazard_pattern.finditer(prompt):
            heading = cls._clean_sector_hazard_name(match.group(1))
            if target in normalize_for_match(heading) or normalize_for_match(heading) in target:
                return strip_rule_lines(match.group(0))
        return ""

    @staticmethod
    def _is_statistical_basis_line(value: str) -> bool:
        normalized = normalize_markdown_text(value).strip().strip("*_` ").casefold()
        return normalized.startswith(
            (
                "statistical basis",
                "basis",
                "evidence",
                "reason",
                "why",
                "rationale",
                "note",
            )
        )

    @staticmethod
    def _strip_practical_sections(markdown_text: str) -> str:
        practical_headings = (
            "practical considerations",
            "general considerations to mitigate the negative effects",
            "practical policy recommendations",
            "current policy implementation",
        )
        kept_lines: list[str] = []
        skipping_heading_level: int | None = None

        for line in markdown_text.splitlines():
            stripped = line.strip()
            heading_marker = len(stripped) - len(stripped.lstrip("#"))
            is_heading = heading_marker > 0 and stripped[heading_marker:].startswith(" ")
            heading_text = stripped[heading_marker:].strip().strip(":").casefold()

            if is_heading:
                if any(heading in heading_text for heading in practical_headings):
                    skipping_heading_level = heading_marker
                    continue
                if skipping_heading_level is not None and heading_marker <= skipping_heading_level:
                    skipping_heading_level = None

            if skipping_heading_level is not None:
                continue

            lowered = stripped.casefold()
            if any(lowered.startswith(f"- {heading}") for heading in practical_headings):
                continue

            kept_lines.append(line)

        cleaned = "\n".join(kept_lines).strip()
        return cleaned or markdown_text.strip()

    def _hazard_options(self, session: ChatSession) -> list[Option]:
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(self._primary_hazard_names(session), start=1)
        ]
        if self._additional_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show hazards added by experts"))
        if self._custom_hazard_options(session):
            options.append(Option(id=len(options) + 1, label="Show co-created hazards"))
        return options

    def _additional_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._additional_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    def _custom_hazard_selection_options(self, session: ChatSession) -> list[Option]:
        labels = self._custom_hazard_options(session)
        options = [
            Option(id=index, label=hazard)
            for index, hazard in enumerate(labels, start=1)
        ]
        options.append(Option(id=len(options) + 1, label="Show listed hazards"))
        return options

    @staticmethod
    def _primary_hazard_names(session: ChatSession) -> list[str]:
        additional_keys = {normalize(hazard) for hazard in (session.additional_hazards or [])}
        custom_keys = {normalize(hazard) for hazard in (session.custom_hazards or [])}
        return [
            hazard
            for hazard in hazard_names(session)
            if normalize(hazard) not in additional_keys
            and normalize(hazard) not in custom_keys
        ]

    @classmethod
    def _additional_hazard_options(cls, session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.additional_hazards or [])
            if hazard and cls._stored_hazard_profiles(session, hazard)
        ]

    @classmethod
    def _custom_hazard_options(cls, session: ChatSession) -> list[str]:
        return [
            hazard
            for hazard in (session.custom_hazards or [])
            if hazard and cls._stored_hazard_profiles(session, hazard)
        ]

    def _saved_custom_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        shared_rows = self.db.scalars(
            select(CustomHazard.name)
            .where(
                CustomHazard.country_id == session.country_id,
                CustomHazard.sector_id == session.sector_id,
                CustomHazard.region_scope_key == (session.region_id or 0),
                or_(
                    CustomHazard.created_by_user_id == self.user_id,
                    and_(
                        CustomHazard.validation_mode == "strict",
                        CustomHazard.is_crowd_sourced.is_(True),
                    ),
                ),
            )
            .order_by(CustomHazard.name)
        ).all()
        legacy_rows = self.db.scalars(
            select(UserHazard.name)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.country_id == session.country_id,
                UserHazard.sector_id == session.sector_id,
                UserHazard.region_id.is_(None)
                if session.region_id is None
                else UserHazard.region_id == session.region_id,
                UserHazard.source == "custom",
                or_(
                    UserSession.user_id == self.user_id,
                    and_(
                        UserHazard.validation_mode == "strict",
                        UserHazard.is_crowd_sourced.is_(True),
                    ),
                ),
            )
            .order_by(UserHazard.name)
        ).all()

        seen: set[str] = set()
        hazards: list[str] = []
        system_names = {normalize(hazard) for hazard in (session.hazards or [])}
        for row in [*shared_rows, *legacy_rows]:
            key = normalize(row)
            if key in seen or key in system_names:
                continue
            seen.add(key)
            hazards.append(row)
        return hazards

    def _additional_hazards_for_context(self, session: ChatSession) -> list[str]:
        if session.country_id is None or session.sector_id is None:
            return []
        rows = self.db.execute(
            select(AdditionalHazard, AdditionalHazardProfile)
            .outerjoin(
                AdditionalHazardProfile,
                AdditionalHazardProfile.additional_hazard_id == AdditionalHazard.id,
            )
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(
                AdditionalHazard.csv_row_number,
                AdditionalHazard.name,
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [
            int(profile_row.id)
            for _, profile_row in rows
            if profile_row is not None and isinstance(profile_row.id, int)
        ]
        target_population_by_profile = (
            self._additional_hazard_profile_target_population_map(profile_ids)
            if profile_ids
            else {}
        )

        existing_names = {
            normalize(hazard)
            for hazard in [
                *(session.hazards or []),
                *(session.custom_hazards or []),
            ]
        }
        seen: set[str] = set()
        hazards: list[str] = []
        profiles_by_hazard: dict[str, list[dict[str, str]]] = {}
        for hazard_row, profile_row in rows:
            hazard = str(hazard_row.name or "").strip()
            key = normalize(hazard)
            if not hazard or key in existing_names:
                continue
            if key not in seen:
                seen.add(key)
                hazards.append(hazard)
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            mapped_targets = target_population_by_profile.get(int(profile_row.id), [])
            profiles_by_hazard.setdefault(hazard, []).append(
                {
                    "name": str(profile_row.profile).strip(),
                    "profile": str(profile_row.profile).strip(),
                    "explanation": str(profile_row.evidence or "").strip(),
                    "statistical_basis": str(profile_row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        int(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        if profiles_by_hazard:
            session.hazard_profiles = {
                **(session.hazard_profiles or {}),
                **profiles_by_hazard,
            }
        return hazards

    def _additional_hazard_profiles_for_custom_hazard(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        if session.country_id is None or session.sector_id is None:
            return []
        hazard_rows = self.db.execute(
            select(AdditionalHazard.id, AdditionalHazard.name)
            .where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
            )
            .order_by(AdditionalHazard.csv_row_number, AdditionalHazard.name)
        ).all()
        matched_names = {
            normalize(name)
            for name in self._local_similar_hazards(
                hazard,
                [str(row.name or "").strip() for row in hazard_rows],
            )
        }
        matched_hazard_ids = [
            int(row.id)
            for row in hazard_rows
            if normalize(str(row.name or "").strip()) in matched_names
        ]
        if not matched_hazard_ids:
            return []

        profile_rows = self.db.scalars(
            select(AdditionalHazardProfile)
            .where(AdditionalHazardProfile.additional_hazard_id.in_(matched_hazard_ids))
            .order_by(
                AdditionalHazardProfile.csv_row_number,
                AdditionalHazardProfile.profile,
            )
        ).all()
        profile_ids = [int(row.id) for row in profile_rows if isinstance(row.id, int)]
        target_population_by_profile = self._additional_hazard_profile_target_population_map(
            profile_ids
        )
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in profile_rows:
            name = str(row.profile or "").strip()
            key = normalize(name)
            if not name or key in seen:
                continue
            seen.add(key)
            mapped_targets = target_population_by_profile.get(int(row.id), [])
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "explanation": str(row.evidence or "").strip(),
                    "statistical_basis": str(row.reference or "").strip(),
                    "source": "d4_2_pdf",
                    "target_population_option_ids": [
                        int(item["option_id"]) for item in mapped_targets
                    ],
                    "target_population_labels": [
                        str(item["label"]) for item in mapped_targets
                    ],
                }
            )
        return profiles

    def _is_saved_custom_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.custom_hazards or []))

    def _is_additional_hazard(self, session: ChatSession, hazard: str) -> bool:
        return any(normalize(hazard) == normalize(item) for item in (session.additional_hazards or []))

    def _additional_hazard_profile_target_population_map(
        self, profile_ids: list[int]
    ) -> dict[int, list[dict[str, object]]]:
        if not profile_ids:
            return {}
        rows = self.db.execute(
            select(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id
                == AdditionalHazardProfileTargetPopulation.question_option_id,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id.in_(
                    profile_ids
                ),
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(
                AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id,
                EvaluationQuestion.sort_order,
                QuestionOption.id,
            )
        ).all()
        mapped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            profile_id = int(row.additional_hazard_profile_id)
            mapped.setdefault(profile_id, []).append(
                {
                    "option_id": int(row.id),
                    "label": f"{row.question}: {row.option}",
                }
            )
        return mapped

    def _target_population_answers_for_saved_hazard(
        self, session: ChatSession, hazard: str
    ) -> str:
        if session.country_id is None or session.sector_id is None:
            return ""

        custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
        if custom_hazard_id is not None:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserSession, UserSession.id == UserQuestionResponse.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserQuestionResponse.custom_hazard_id == custom_hazard_id,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()
        else:
            rows = []
        if not rows:
            rows = self.db.execute(
                select(
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                )
                .join(UserHazard, UserHazard.id == UserQuestionResponse.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .join(EvaluationQuestion, EvaluationQuestion.id == UserQuestionResponse.question_id)
                .where(
                    UserSession.country_id == session.country_id,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazard.source == "custom",
                    UserHazard.name == hazard,
                    EvaluationQuestion.category == "target_population",
                )
                .order_by(EvaluationQuestion.sort_order, UserQuestionResponse.created_at)
            ).all()

        if not rows:
            return ""

        lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        for question, response in rows:
            if not response:
                continue
            key = (question, response)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- **{normalize_markdown_text(question)}**: {response}")
        return "\n".join(lines)

    def _stored_hazard_items_for_context(
        self, session_id: str, session: ChatSession
    ) -> list[dict[str, object]]:
        if session.sector_id is None:
            return []
        try:
            query = (
                select(SystemHazard, SystemHazardSocioDemographic)
                .outerjoin(
                    SystemHazardSocioDemographic,
                    and_(
                        SystemHazardSocioDemographic.system_hazard_id == SystemHazard.id,
                        SystemHazardSocioDemographic.sector_id == session.sector_id,
                    ),
                )
                .where(SystemHazard.sector_id == session.sector_id)
                .order_by(SystemHazard.id, SystemHazardSocioDemographic.id)
            )
            rows = self.db.execute(query).all()
        except Exception:
            logger.exception("Failed to load stored hazards and profiles")
            return []

        items_by_hazard: dict[int, dict[str, object]] = {}
        seen_profiles: dict[int, set[str]] = {}
        for hazard, profile_row in rows:
            item = items_by_hazard.setdefault(
                hazard.id,
                {"hazard": hazard.name, "profiles": []},
            )
            if profile_row is None or not str(profile_row.profile or "").strip():
                continue
            seen = seen_profiles.setdefault(hazard.id, set())
            profile_name = str(profile_row.profile).strip()
            key = normalize(profile_name)
            if key in seen:
                continue
            seen.add(key)
            item_profiles = item.setdefault("profiles", [])
            if isinstance(item_profiles, list):
                item_profiles.append(
                    {
                        "name": profile_name,
                        "profile": profile_name,
                        "explanation": str(profile_row.explanation or ""),
                        "variable_name": str(profile_row.variable_name or ""),
                        "variable_type": self._profile_variable_type(profile_row.variable_name),
                        "statistical_basis": str(profile_row.statistical_basis or ""),
                        "source": str(profile_row.source or "sector_prompt"),
                    }
                )
        return [
            item
            for item in items_by_hazard.values()
            if self._hazard_item_has_profiles(item)
        ]

    @staticmethod
    def _hazard_item_has_profiles(item: dict[str, object]) -> bool:
        profiles = item.get("profiles")
        if not isinstance(profiles, list):
            return False
        return any(
            (
                isinstance(profile, dict)
                and bool(str(profile.get("name") or profile.get("profile") or "").strip())
            )
            or (isinstance(profile, str) and bool(profile.strip()))
            for profile in profiles
        )

    async def _refresh_hazards_and_profiles_from_llm(
        self,
        session_id: str,
        session: ChatSession,
        *,
        replace_sector_hazards: bool = False,
    ) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_from_llm(session)
        valid_hazard_items = [
            item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
            and normalize(str(item.get("hazard") or "")) != normalize("Analysis not available")
            and self._hazard_item_has_profiles(item)
        ]
        if replace_sector_hazards and not valid_hazard_items:
            logger.warning(
                "Keeping existing hazards because refresh returned no usable sector hazards"
            )
            return self._stored_hazard_items_for_context(session_id, session)
        if replace_sector_hazards:
            self._delete_sector_hazards_and_generated_dgs(session)
        await self._match_system_profiles_to_target_populations(valid_hazard_items)
        self._persist_hazard_items_for_context(
            session_id,
            session,
            valid_hazard_items,
            replace_generated_profiles=True,
        )
        if replace_sector_hazards:
            self._relink_user_system_hazards(session)
        return valid_hazard_items

    async def _match_system_profiles_to_target_populations(
        self, hazard_items: list[dict[str, object]]
    ) -> None:
        option_rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(
                EvaluationQuestion.active.is_(True),
                EvaluationQuestion.category == "target_population",
            )
            .order_by(EvaluationQuestion.sort_order, QuestionOption.id)
        ).all()
        if not option_rows:
            return

        profiles_by_key: dict[str, dict[str, object]] = {}
        for hazard_index, item in enumerate(hazard_items):
            for profile_index, profile in enumerate(item.get("profiles", [])):
                if not isinstance(profile, dict):
                    continue
                key = f"{hazard_index}:{profile_index}"
                profiles_by_key[key] = profile
        if not profiles_by_key:
            return

        for key, profile in profiles_by_key.items():
            profile["target_population_option_ids"] = sorted(
                self._deterministic_target_population_option_ids(profile, option_rows)
            )

    async def backfill_system_profile_target_populations(self) -> int:
        rows = self.db.execute(
            select(SystemHazardSocioDemographic, SystemHazard.name)
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .order_by(SystemHazardSocioDemographic.id)
        ).all()
        matched_rows = 0
        batch_size = 20
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            hazard_items: list[dict[str, object]] = []
            profiles_by_id: dict[int, dict[str, object]] = {}
            for row, hazard_name in batch:
                profile = {
                    "name": str(row.profile or ""),
                    "profile": str(row.profile or ""),
                    "variable_name": str(row.variable_name or ""),
                    "variable_type": self._profile_variable_type(row.variable_name),
                    "explanation": str(row.explanation or ""),
                    "statistical_basis": str(row.statistical_basis or ""),
                }
                profiles_by_id[int(row.id)] = profile
                hazard_items.append(
                    {"hazard": str(hazard_name or ""), "profiles": [profile]}
                )
            await self._match_system_profiles_to_target_populations(hazard_items)
            for profile_id, profile in profiles_by_id.items():
                option_ids = profile.get("target_population_option_ids")
                self._store_system_target_population_matches(profile_id, option_ids)
                if isinstance(option_ids, list) and option_ids:
                    matched_rows += 1
        return matched_rows

    @staticmethod
    def _deterministic_target_population_option_ids(
        profile: dict[str, object], option_rows: list[object]
    ) -> set[int]:
        identity_text = normalize_for_match(
            " ".join(
                str(profile.get(field) or "")
                for field in ("name", "profile", "variable_name")
            )
        )

        explanation_text = normalize_for_match(str(profile.get("explanation") or ""))
        statistical_text = normalize_for_match(str(profile.get("statistical_basis") or ""))
        profile_text = " ".join(
            value for value in (identity_text, explanation_text, statistical_text) if value
        )
        padded_profile = f" {profile_text} "
        padded_explanation = f" {explanation_text} "
        matched: set[int] = set()
        for row in option_rows:
            question = normalize_for_match(str(row.question))
            option = normalize_for_match(str(row.option))
            if not option or option in {"yes", "no", "other"}:
                continue
            if f" {option} " in padded_profile:
                matched.add(int(row.id))
                continue
            aliases = {
                ("gender", "woman"): ("women", "female"),
                ("gender", "male"): (" men ", " man "),
                ("age range", "65"): ("older", "older people", "older adults", "elderly", "senior"),
                ("level of income", "low income"): (
                    "poor households",
                    "income poor",
                    "utility arrears",
                    "utility bill arrears",
                    "energy arrears",
                    "households with utility arrears",
                ),
                ("tenancy status", "tenant"): ("renters", "rented housing"),
                ("tenancy status", "homeowner"): ("homeowners", "owner occupier"),
            }.get((question, option), ())
            if any(
                f" {normalize_for_match(alias)} " in padded_profile
                for alias in aliases
            ):
                matched.add(int(row.id))

        age_match = re.search(r"\bage(?: group)?\s+(\d{1,2})\s*(\+|and over)?", profile_text)
        if age_match:
            minimum_age = int(age_match.group(1))
            if minimum_age >= 65:
                allowed_age_options = {"65"}
            elif minimum_age >= 35:
                allowed_age_options = {"35 65", "65"}
            elif minimum_age >= 25:
                allowed_age_options = {"25 35", "35 65", "65"}
            else:
                allowed_age_options = {"18", "25 35", "35 65", "65"}
            for row in option_rows:
                if normalize_for_match(str(row.question)) != "age range":
                    continue
                option = normalize_for_match(str(row.option))
                if option in allowed_age_options:
                    matched.add(int(row.id))

        yes_question_markers = {
            "living in a house with low energy efficiency": (
                "low energy efficiency", "energy inefficient", "ber rating e g", "cold home"
            ),
            "need of a car to perform daily activities": (
                "car dependent", "need a car", "car reliance"
            ),
            "care responsibility as the main activity": (
                "care responsibility", "carer", "caregiver"
            ),
            "eu citizenship": ("eu citizen", "eu citizenship"),
            "disability of long term condition": (
                "disability", "disabled", "long term condition"
            ),
        }
        for row in option_rows:
            if normalize_for_match(str(row.option)) != "yes":
                continue
            markers = yes_question_markers.get(normalize_for_match(str(row.question)), ())
            if any(f" {normalize_for_match(marker)} " in padded_profile for marker in markers):
                matched.add(int(row.id))

        explanation_markers = {
            ("living in a house with low energy efficiency", "yes"): (
                "inability to keep home warm",
                "cannot keep home warm",
                "cold home",
                "home quality problem",
                "damp draughts mould",
                "pre 1945 housing",
            ),
            ("level of income", "low income"): (
                "struggling to pay bills",
                "utility bill arrears",
                "utility arrears",
                "cannot afford",
                "unable to afford",
                "often need help support",
            ),
            ("level of income", "high income"): (
                "higher income respondents",
                "high income respondents",
            ),
            ("tenancy status", "homeowner"): (
                "owns outright",
                "owner occupier",
            ),
            ("tenancy status", "tenant"): (
                "rented home",
                "rented housing",
                "private renter",
                "social renter",
            ),
            ("level of education", "further normal education"): (
                "further education or training after school",
            ),
        }
        for row in option_rows:
            key = (
                normalize_for_match(str(row.question)),
                normalize_for_match(str(row.option)),
            )
            if (
                key == ("level of income", "low income")
                and any(
                    marker in f" {identity_text} "
                    for marker in (" higher income ", " high income ")
                )
            ):
                continue
            markers = explanation_markers.get(key, ())
            if any(
                f" {normalize_for_match(marker)} " in padded_explanation
                for marker in markers
            ):
                matched.add(int(row.id))
        return matched

    def _historical_evaluation_series(
        self, session: ChatSession, limit: int = 4
    ) -> list[dict[str, object]]:
        if self.user_id is None or not session.evaluation_answers or session.sector_id is None:
            return []
        question_ids = [
            int(answer["question_id"])
            for answer in session.evaluation_answers
            if answer.get("question_id") is not None
        ]
        if not question_ids:
            return []
        query = (
            select(
                UserMitigationMeasure.id,
                UserMitigationMeasure.measure,
                EvaluationQuestion.id.label("question_id"),
                UserQuestionResponse.score,
                UserMitigationMeasure.created_at,
            )
            .join(
                UserQuestionResponse,
                UserQuestionResponse.mitigation_measure_id == UserMitigationMeasure.id,
            )
            .join(
                EvaluationQuestion,
                EvaluationQuestion.id == UserQuestionResponse.question_id,
            )
            .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
            .join(UserSession, UserSession.id == UserHazard.user_session_id)
            .where(
                UserSession.user_id == self.user_id,
                UserSession.sector_id == session.sector_id,
                UserSession.region_id.is_(None)
                if session.region_id is None
                else UserSession.region_id == session.region_id,
                EvaluationQuestion.id.in_(question_ids),
                UserQuestionResponse.score.is_not(None),
            )
            .order_by(UserMitigationMeasure.id.desc(), UserQuestionResponse.id)
        )
        if session.mitigation_record_id is not None:
            query = query.where(UserMitigationMeasure.id != session.mitigation_record_id)
        rows = self.db.execute(query).all()
        grouped: dict[int, dict[str, object]] = {}
        for row in rows:
            mitigation_id = int(row.id)
            if mitigation_id not in grouped and len(grouped) >= limit:
                continue
            group = grouped.setdefault(
                mitigation_id,
                {
                    "id": mitigation_id,
                    "measure": str(row.measure or "Prior mitigation"),
                    "scores": {},
                },
            )
            scores = group["scores"]
            if isinstance(scores, dict):
                scores[int(row.question_id)] = int(row.score)

        series: list[dict[str, object]] = []
        for group in grouped.values():
            scores = group.get("scores")
            if not isinstance(scores, dict) or not scores:
                continue
            measure = normalize_markdown_text(str(group.get("measure") or "Prior mitigation"))
            series.append(
                {
                    "name": f"#{group['id']} — {measure[:64]}",
                    "values": [scores.get(question_id) for question_id in question_ids],
                    "current": False,
                }
            )
        return series

    def _persist_hazard_items_for_context(
        self,
        session_id: str,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        *,
        replace_generated_profiles: bool = False,
    ) -> None:
        for item in hazard_items:
            hazard = str(item.get("hazard") or "").strip()
            if not hazard:
                continue
            system_hazard = self._ensure_system_hazard(session, hazard)
            if system_hazard is None:
                continue
            self._delete_user_linked_system_generated_socio_demographics(session, hazard)
            if replace_generated_profiles:
                self._delete_generated_system_socio_demographics(system_hazard.id, session)
            for profile in item.get("profiles", []):
                if isinstance(profile, dict):
                    profile_data = profile
                else:
                    profile_data = {"name": str(profile or "").strip()}
                profile_name = str(
                    profile_data.get("name") or profile_data.get("profile") or ""
                ).strip()
                if not profile_name:
                    continue
                self._store_system_socio_demographic(
                    session,
                    system_hazard.id,
                    profile_data,
                )
        self._normalize_stored_sdp_variable_names(session)

    def _delete_sector_hazards_and_generated_dgs(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            generated_user_dgs = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                    UserHazardSocioDemographic.source == "llm",
                )
            ).all()
            for row in generated_user_dgs:
                self.db.delete(row)

            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            )
            self.db.execute(
                delete(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear old sector hazards and generated DGs")
            raise

    def _relink_user_system_hazards(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_hazards = {
                normalize(hazard.name): hazard.id
                for hazard in self.db.scalars(
                    select(SystemHazard).where(SystemHazard.sector_id == session.sector_id)
                ).all()
            }
            user_hazards = self.db.scalars(
                select(UserHazard).where(
                    UserHazard.source == "system",
                    UserHazard.sector_id == session.sector_id,
                )
            ).all()
            for hazard in user_hazards:
                hazard.system_hazard_id = system_hazards.get(normalize(hazard.name))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to relink refreshed system hazards")

    def _normalize_stored_sdp_variable_names(self, session: ChatSession) -> None:
        if session.sector_id is None:
            return
        try:
            system_rows = self.db.scalars(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            user_rows = self.db.scalars(
                select(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.sector_id == session.sector_id
                )
            ).all()
            changed = False
            for row in [*system_rows, *user_rows]:
                normalized = self._valid_sdp_variable_name(session, row.variable_name)
                if normalized and normalized != row.variable_name:
                    row.variable_name = normalized
                    changed = True
                if isinstance(row, SystemHazardSocioDemographic):
                    variable_type = self._profile_variable_type(row.variable_name)
                    if row.variable_type != variable_type:
                        row.variable_type = variable_type
                        changed = True
            if changed:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to normalize stored socio-demographic variable names")

    def _delete_generated_system_socio_demographics(
        self, system_hazard_id: int, session: ChatSession
    ) -> None:
        try:
            self.db.execute(
                delete(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear generated system socio-demographic profiles")

    def _delete_user_linked_system_generated_socio_demographics(
        self, session: ChatSession, hazard: str
    ) -> None:
        try:
            rows = self.db.scalars(
                select(UserHazardSocioDemographic)
                .join(UserHazard, UserHazard.id == UserHazardSocioDemographic.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserHazard.source == "system",
                    UserHazard.name == hazard,
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserHazardSocioDemographic.source == "llm",
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            ).all()
            for row in rows:
                self.db.delete(row)
            if rows:
                self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear user-linked generated system socio-demographic rows")

    def _ensure_system_hazard(self, session: ChatSession, name: str) -> SystemHazard | None:
        if session.sector_id is None:
            return None
        try:
            hazard = self.db.scalar(
                select(SystemHazard).where(
                    SystemHazard.sector_id == session.sector_id,
                    SystemHazard.name == name,
                )
            )
            if hazard is None:
                hazard = SystemHazard(sector_id=session.sector_id, name=name)
                self.db.add(hazard)
                self.db.commit()
                self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system hazard")
            return None

    @staticmethod
    def _custom_hazard_name_key(name: str) -> str:
        return normalize_for_match(name)[:255]

    def _custom_hazard_id_for_context(self, session: ChatSession, hazard: str) -> int | None:
        if session.country_id is None or session.sector_id is None or not hazard.strip():
            return None
        if normalize(hazard) == normalize(session.accepted_custom_hazard or ""):
            if session.accepted_custom_hazard_id is not None:
                return session.accepted_custom_hazard_id
        try:
            hazard_id = self.db.scalar(
                select(CustomHazard.id).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or 0),
                    CustomHazard.name_key == self._custom_hazard_name_key(hazard),
                    or_(
                        CustomHazard.created_by_user_id == self.user_id,
                        and_(
                            CustomHazard.validation_mode == "strict",
                            CustomHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            )
            return int(hazard_id) if isinstance(hazard_id, int) else None
        except Exception:
            logger.exception("Failed to load shared custom hazard id")
            return None

    def _ensure_custom_hazard(
        self,
        session: ChatSession,
        name: str,
        *,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> CustomHazard | None:
        if session.country_id is None or session.sector_id is None or not name.strip():
            return None
        name_key = self._custom_hazard_name_key(name)
        if not name_key:
            return None
        try:
            hazard = self.db.scalar(
                select(CustomHazard).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == (session.region_id or 0),
                    CustomHazard.name_key == name_key,
                    or_(
                        CustomHazard.created_by_user_id == self.user_id,
                        and_(
                            CustomHazard.validation_mode == "strict",
                            CustomHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            )
            if hazard is None:
                hazard = CustomHazard(
                    country_id=session.country_id,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    region_scope_key=session.region_id or 0,
                    name=name.strip(),
                    name_key=name_key,
                    source="user",
                    created_by_user_id=self.user_id,
                )
                self.db.add(hazard)
            hazard.name = name.strip()
            hazard.region_id = session.region_id
            hazard.region_scope_key = session.region_id or 0
            if reason is not None:
                hazard.reason = reason.strip() or None
            if evidence is not None:
                hazard.evidence = evidence.strip() or None
            hazard.validation_mode = session.validation_mode if session.validation_mode in {"strict", "easy"} else "strict"
            hazard.is_crowd_sourced = (
                hazard.validation_mode == "strict" and bool(session.crowd_sourcing_enabled)
            )
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard")
            return None

    def _store_custom_hazard_profile(
        self,
        custom_hazard_id: int | None,
        profile: dict[str, object],
    ) -> None:
        if custom_hazard_id is None:
            return
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        profile_key = normalize_for_match(profile_name)[:255]
        if not profile_name or not profile_key:
            return
        try:
            row = self.db.scalar(
                select(CustomHazardProfile).where(
                    CustomHazardProfile.custom_hazard_id == custom_hazard_id,
                    CustomHazardProfile.profile_key == profile_key,
                )
            )
            if row is None:
                row = CustomHazardProfile(
                    custom_hazard_id=custom_hazard_id,
                    profile=profile_name,
                    profile_key=profile_key,
                )
                self.db.add(row)
            row.profile = profile_name
            row.variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip() or None
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "custom_hazard_extraction").strip()[:40] or "custom_hazard_extraction"
            row.metadata_json = self._metadata_to_json(dict(profile))
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist shared custom hazard profile")

    def _ensure_user_hazard(
        self,
        session_id: str,
        session: ChatSession,
        name: str,
        *,
        source: str = "custom",
        reason: str | None = None,
        evidence: str | None = None,
    ) -> UserHazard | None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return None
            system_hazard = None
            if source == "system":
                system_hazard = self._ensure_system_hazard(session, name)
            custom_hazard = None
            if source == "custom":
                custom_hazard = self._ensure_custom_hazard(
                    session,
                    name,
                    reason=reason,
                    evidence=evidence,
                )
            hazard = self.db.scalar(
                select(UserHazard).where(
                    UserHazard.user_session_id == user_session.id,
                    UserHazard.name == name,
                )
            )
            if hazard is None:
                hazard = UserHazard(
                    user_session_id=user_session.id,
                    custom_hazard_id=custom_hazard.id if custom_hazard else None,
                    system_hazard_id=system_hazard.id if system_hazard else None,
                    sector_id=session.sector_id,
                    region_id=session.region_id,
                    name=name,
                    source=source,
                )
                self.db.add(hazard)
            hazard.source = source
            if custom_hazard is not None:
                hazard.custom_hazard_id = custom_hazard.id
            if system_hazard is not None:
                hazard.system_hazard_id = system_hazard.id
            hazard.sector_id = session.sector_id
            hazard.region_id = session.region_id
            if reason is not None:
                hazard.reason = reason
            if evidence is not None:
                hazard.evidence = evidence
            hazard.validation_mode = session.validation_mode if session.validation_mode in {"strict", "easy"} else "strict"
            hazard.is_crowd_sourced = (
                hazard.validation_mode == "strict" and bool(session.crowd_sourcing_enabled)
            )
            self.db.commit()
            self.db.refresh(hazard)
            return hazard
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist hazard")
            return None

    def _selected_hazard_reference(
        self,
        session_id: str,
        session: ChatSession,
        hazard_name: str | None = None,
    ) -> dict[str, int | None]:
        hazard = (hazard_name or session.selected_hazard or session.accepted_custom_hazard or "").strip()
        reference = {
            "user_session_id": None,
            "user_hazard_id": None,
            "custom_hazard_id": None,
            "system_hazard_id": None,
            "additional_hazard_id": None,
        }
        user_session = self._ensure_user_session(session_id, session)
        if user_session is not None:
            reference["user_session_id"] = user_session.id
        if not hazard:
            return reference
        source = self._selected_user_hazard_source(session, hazard)
        if source == "system":
            system_hazard = self._ensure_system_hazard(session, hazard)
            reference["system_hazard_id"] = system_hazard.id if system_hazard else None
            return reference
        if source == "additional":
            reference["additional_hazard_id"] = self._selected_additional_hazard_id(session, hazard)
            return reference

        custom_hazard = self._ensure_custom_hazard(
            session,
            hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=session.accepted_custom_hazard_evidence,
        )
        if custom_hazard is not None:
            session.accepted_custom_hazard_id = custom_hazard.id
            session.accepted_custom_hazard = hazard
            reference["custom_hazard_id"] = custom_hazard.id
            return reference

        if session.selected_hazard_record_id is not None:
            existing = self.db.get(UserHazard, session.selected_hazard_record_id)
            if existing is not None:
                reference["user_hazard_id"] = existing.id
                return reference
        record = self._ensure_user_hazard(
            session_id,
            session,
            hazard,
            source=source,
        )
        if record is None:
            return reference
        session.selected_hazard_record_id = record.id
        session.accepted_custom_hazard = hazard
        session.accepted_custom_hazard_record_id = record.id
        reference["user_hazard_id"] = record.id
        return reference

    def _selected_user_hazard_source(self, session: ChatSession, hazard: str) -> str:
        if self._is_saved_custom_hazard(session, hazard) or normalize(hazard) == normalize(
            session.accepted_custom_hazard or ""
        ):
            return "custom"
        if self._is_additional_hazard(session, hazard):
            return "additional"
        return "system"

    def _selected_additional_hazard_id(self, session: ChatSession, hazard: str) -> int | None:
        if session.country_id is None or session.sector_id is None:
            return None
        hazard_id = self.db.scalar(
            select(AdditionalHazard.id).where(
                AdditionalHazard.country_id == session.country_id,
                AdditionalHazard.sector_id == session.sector_id,
                func.lower(AdditionalHazard.name) == hazard.casefold(),
            )
        )
        return int(hazard_id) if isinstance(hazard_id, int) else None

    def _store_socio_demographic(
        self,
        session: ChatSession,
        profile: str,
        *,
        user_hazard_id: int | None = None,
        custom_hazard_id: int | None = None,
        system_hazard_id: int | None = None,
        additional_hazard_id: int | None = None,
        source: str,
        variable_name: str | None = None,
        explanation: str | None = None,
        statistical_basis: str | None = None,
        metadata: dict[str, object] | None = None,
        reason: str | None = None,
        evidence: str | None = None,
    ) -> None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ) or not profile.strip():
            return
        try:
            user_session = self._ensure_user_session(session.session_key, session)
            if user_session is None:
                return
            clean_profile = profile.strip()
            hazard_name = session.selected_hazard or session.accepted_custom_hazard
            context_query = (
                select(UserHazardSocioDemographic)
                .where(
                    func.lower(UserHazardSocioDemographic.profile) == clean_profile.casefold(),
                    UserHazardSocioDemographic.user_session_id == user_session.id,
                    UserHazardSocioDemographic.user_hazard_id == user_hazard_id,
                    UserHazardSocioDemographic.custom_hazard_id == custom_hazard_id,
                    UserHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    UserHazardSocioDemographic.additional_hazard_id == additional_hazard_id,
                    UserHazardSocioDemographic.country_id == session.country_id,
                    UserHazardSocioDemographic.region_id == session.region_id,
                    UserHazardSocioDemographic.sector_id == session.sector_id,
                )
            )
            row = self.db.scalar(context_query.limit(1))
            if row is None:
                row = UserHazardSocioDemographic(
                    user_session_id=user_session.id,
                    user_hazard_id=user_hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    profile=clean_profile,
                    source=source,
                )
                self.db.add(row)
            row.user_session_id = user_session.id
            row.user_hazard_id = user_hazard_id
            row.custom_hazard_id = custom_hazard_id
            row.system_hazard_id = system_hazard_id
            row.additional_hazard_id = additional_hazard_id
            row.country_id = session.country_id
            row.region_id = session.region_id
            row.sector_id = session.sector_id
            row.source = source
            if variable_name is not None:
                row.variable_name = self._valid_sdp_variable_name(session, variable_name) or None
            if explanation is not None:
                row.explanation = explanation.strip() or None
            if statistical_basis is not None:
                row.statistical_basis = statistical_basis.strip() or None
            if metadata is not None:
                row.metadata_json = self._metadata_to_json(metadata)
            if reason is not None:
                row.reason = reason
            if evidence is not None:
                row.evidence = evidence
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist socio-demographic profile")

    def _store_system_socio_demographic(
        self,
        session: ChatSession,
        system_hazard_id: int,
        profile: dict[str, object],
    ) -> None:
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        explanation = str(profile.get("explanation") or "").strip()
        statistical_basis = str(
            profile.get("statistical_basis") or profile.get("basis") or ""
        ).strip()
        source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard_id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard_id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.country_id = None
            row.region_id = None
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = explanation or None
            row.statistical_basis = statistical_basis or None
            row.source = source
            self.db.commit()
            self.db.refresh(row)
            if "target_population_option_ids" in profile:
                self._store_system_target_population_matches(
                    row.id,
                    profile.get("target_population_option_ids"),
                )
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile")

    def _store_system_target_population_matches(
        self, system_profile_id: int, option_ids: object
    ) -> None:
        requested_ids: set[int] = set()
        if isinstance(option_ids, list):
            for option_id in option_ids:
                try:
                    requested_ids.add(int(option_id))
                except (TypeError, ValueError):
                    continue
        valid_ids = set(
            self.db.scalars(
                select(QuestionOption.id)
                .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
                .where(
                    QuestionOption.id.in_(requested_ids),
                    EvaluationQuestion.active.is_(True),
                    EvaluationQuestion.category == "target_population",
                )
            ).all()
        ) if requested_ids else set()
        existing = self.db.scalars(
            select(SystemHazardSocioDemographicTargetPopulation).where(
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id
                == system_profile_id
            )
        ).all()
        existing_by_option = {row.question_option_id: row for row in existing}
        for option_id, row in existing_by_option.items():
            if option_id not in valid_ids:
                self.db.delete(row)
        for option_id in valid_ids:
            if option_id not in existing_by_option:
                self.db.add(
                    SystemHazardSocioDemographicTargetPopulation(
                        system_hazard_socio_demographic_id=system_profile_id,
                        question_option_id=option_id,
                    )
                )
        self.db.commit()

    def _store_matched_profile_population_references(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
        matched_population_profiles: list[dict[str, object]],
    ) -> None:
        system_profile = self._ensure_system_socio_demographic_row(session, hazard, profile)
        if system_profile is None:
            return
        try:
            valid_cache_ids: set[int] = set()
            for matched_profile in matched_population_profiles:
                try:
                    cache_id = int(matched_profile.get("eurostat_population_cache_id") or 0)
                except (TypeError, ValueError):
                    continue
                cache_row = self.db.get(EurostatPopulationCache, cache_id)
                if (
                    cache_row is None
                    or cache_row.country_id != session.country_id
                    or cache_row.region_id != session.region_id
                    or cache_row.sector_id != session.sector_id
                    or cache_row.system_hazard_id != system_profile.system_hazard_id
                ):
                    continue
                valid_cache_ids.add(cache_id)
            if not valid_cache_ids:
                return
            existing_rows = self.db.scalars(
                select(SystemHazardSocioDemographicPopulationMatch).where(
                    SystemHazardSocioDemographicPopulationMatch.system_hazard_socio_demographic_id
                    == system_profile.id,
                )
            ).all()
            rows_by_cache_id = {
                row.eurostat_population_cache_id: row
                for row in existing_rows
                if row.eurostat_population_cache_id is not None
            }
            for row in existing_rows:
                if (
                    row.eurostat_population_cache_id is not None
                    and row.eurostat_population_cache_id not in valid_cache_ids
                ):
                    row.match_status = 0
            for cache_id in valid_cache_ids:
                row = rows_by_cache_id.get(cache_id)
                if row is None:
                    row = SystemHazardSocioDemographicPopulationMatch(
                        system_hazard_socio_demographic_id=system_profile.id,
                        eurostat_population_cache_id=cache_id,
                    )
                    self.db.add(row)
                row.match_status = 1
                row.attempt_count = 0
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist matched profile population reference")

    def _system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self.db.scalar(
            select(SystemHazard).where(
                SystemHazard.sector_id == session.sector_id,
                SystemHazard.name == hazard,
            )
        )
        if system_hazard is None:
            return None
        return self.db.scalar(
            select(SystemHazardSocioDemographic).where(
                SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                SystemHazardSocioDemographic.sector_id == session.sector_id,
                func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
            )
        )

    def _ensure_system_socio_demographic_row(
        self,
        session: ChatSession,
        hazard: str,
        profile: dict[str, object],
    ) -> SystemHazardSocioDemographic | None:
        if session.sector_id is None:
            return None
        profile_name = str(profile.get("name") or profile.get("profile") or "").strip()
        if not profile_name:
            return None
        system_hazard = self._ensure_system_hazard(session, hazard)
        if system_hazard is None:
            return None
        variable_name = self._valid_sdp_variable_name(
            session,
            str(profile.get("variable_name") or profile.get("variable") or "").strip(),
        )
        try:
            row = self.db.scalar(
                select(SystemHazardSocioDemographic).where(
                    SystemHazardSocioDemographic.system_hazard_id == system_hazard.id,
                    SystemHazardSocioDemographic.sector_id == session.sector_id,
                    func.lower(SystemHazardSocioDemographic.profile) == profile_name.casefold(),
                )
            )
            if row is None:
                row = SystemHazardSocioDemographic(
                    system_hazard_id=system_hazard.id,
                    profile=profile_name,
                )
                self.db.add(row)
            row.sector_id = session.sector_id
            row.variable_name = variable_name or None
            row.variable_type = self._profile_variable_type(variable_name)
            row.profile = profile_name
            row.explanation = str(profile.get("explanation") or "").strip() or None
            row.statistical_basis = str(
                profile.get("statistical_basis") or profile.get("basis") or ""
            ).strip() or None
            row.source = str(profile.get("source") or "sector_prompt").strip()[:40] or "sector_prompt"
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist system socio-demographic profile row")
            return None

    @staticmethod
    def _metadata_to_json(metadata: dict[str, object]) -> str:
        try:
            return json.dumps(metadata, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _metadata_from_json(value: str | None) -> dict[str, object]:
        if not value:
            return {}
        parsed = parse_json_object(value)
        return parsed if isinstance(parsed, dict) else {}

    def _match_hazard(self, message: str, session: ChatSession) -> str | None:
        normalized = normalize(message)
        hazards = hazard_names(session)
        for index, hazard in enumerate(hazards, start=1):
            if str(index) == message.strip() or normalize(hazard) == normalized:
                return hazard
        return None

    @staticmethod
    def _fuzzy_hazard(message: str, session: ChatSession) -> str | None:
        return best_fuzzy_label(message, hazard_names(session))

    async def _get_hazards_from_llm(self, session: ChatSession) -> list[dict[str, object]]:
        hazard_items = await self._get_hazards_and_profiles_from_sector_rag(session)
        if hazard_items:
            return hazard_items

        hazards = await self._get_hazard_names_from_llm(session)
        profile_lists = await asyncio.gather(
            *(self._get_hazard_profiles_from_llm(session, hazard) for hazard in hazards)
        )
        return [
            {"hazard": hazard, "profiles": profiles}
            for hazard, profiles in zip(hazards, profile_lists, strict=False)
        ]

    async def _get_hazard_names_from_llm(self, session: ChatSession) -> list[str]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            "HAZARD confirmed predictors ranked concern",
            limit=25,
        )
        context = render_prompt_template(
            "llm/hazard_names_extraction.txt",
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_names_extraction_user.txt",
                    sector=session.sector,
                ),
            }
        ]

        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return parse_llm_hazard_list(response)

    async def _get_hazard_profiles_from_llm(
        self, session: ChatSession, hazard: str
    ) -> list[dict[str, str]]:
        hazard_block = await self._sector_prompt_hazard_block_from_rag(
            session,
            hazard,
        )
        if hazard_block:
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                return []
            profiles = self._profiles_from_hazard_block(hazard_block)
            if profiles:
                return profiles

        context = render_prompt_template(
            "llm/hazard_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block or "- No relevant sector-prompt RAG excerpts were found.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=700,
        )
        return self._parse_hazard_profile_items(response)

    async def _get_hazards_and_profiles_from_sector_rag(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        rag_service = SectorPromptRagService(self.db)
        try:
            results = await rag_service.hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block extraction lookup failed")
            results = []
        if not results:
            try:
                results = await rag_service.search(
                    session.sector,
                    "HAZARD confirmed predictors PREDICTOR Plain-English Direction",
                    limit=40,
                )
            except Exception:
                logger.exception("Sector-prompt RAG hazard/profile extraction lookup failed")
                results = []
        sector_context = SectorPromptRagService.format_results(results, content_limit=6000)
        if not sector_context:
            return []

        context = render_prompt_template(
            "llm/hazards_and_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazards_and_profiles_extraction_user.txt"
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=9000,
        )
        hazard_items = self._parse_hazard_items_response(response)
        expected_hazards = self._hazard_names_from_sector_prompt(
            "\n\n".join(str(result.get("content") or "") for result in results)
        )
        if expected_hazards:
            hazard_items = await self._complete_sector_rag_hazard_items(
                session,
                hazard_items,
                expected_hazards,
                results,
            )
        return hazard_items

    async def _complete_sector_rag_hazard_items(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
        expected_hazards: list[str],
        rag_results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        items_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        rag_text = "\n\n".join(str(result.get("content") or "") for result in rag_results)
        completed: list[dict[str, object]] = []
        for hazard in expected_hazards:
            item = items_by_hazard.get(normalize(hazard))
            hazard_block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            deterministic_profiles = self._profiles_from_hazard_block(hazard_block)
            if re.search(r"\b0 confirmed predictors\b", hazard_block, re.IGNORECASE):
                profiles = []
            elif predictor_entries:
                profiles = deterministic_profiles
            else:
                profiles = list(item.get("profiles", [])) if item else []
            completed.append({"hazard": hazard, "profiles": profiles})
        return completed

    async def _sector_prompt_rag_hazard_count(self, sector: str | None) -> int:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(sector)
            return sum(
                1
                for result in results
                if self._profiles_from_hazard_block(str(result.get("content") or ""))
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-count lookup failed")
            return 0

    async def _sector_prompt_profiles_match_rag(
        self,
        session: ChatSession,
        hazard_items: list[dict[str, object]],
    ) -> bool:
        try:
            results = await SectorPromptRagService(self.db).hazard_blocks(session.sector)
        except Exception:
            logger.exception("Sector-prompt RAG profile-integrity lookup failed")
            return True
        rag_text = "\n\n".join(str(result.get("content") or "") for result in results)
        stored_by_hazard = {
            normalize(str(item.get("hazard") or "")): item
            for item in hazard_items
            if str(item.get("hazard") or "").strip()
        }
        for hazard in self._hazard_names_from_sector_prompt(rag_text):
            block = self._confirmed_predictor_hazard_block(rag_text, hazard)
            expected = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in self._profiles_from_hazard_block(block)
            )
            if not expected:
                continue
            item = stored_by_hazard.get(normalize(hazard))
            actual = sorted(
                (
                    str(profile.get("variable_name") or "").strip().casefold(),
                    str(profile.get("name") or "").strip().casefold(),
                )
                for profile in (item.get("profiles", []) if item else [])
                if isinstance(profile, dict)
            )
            if actual != expected:
                return False
        expected_hazards = {
            normalize(hazard)
            for hazard in self._hazard_names_from_sector_prompt(rag_text)
            if self._profiles_from_hazard_block(
                self._confirmed_predictor_hazard_block(rag_text, hazard)
            )
        }
        return set(stored_by_hazard) == expected_hazards

    def _parse_hazard_items_response(self, response: str) -> list[dict[str, object]]:
        parsed = parse_json_array(response)
        if not isinstance(parsed, list):
            return []
        hazard_items: list[dict[str, object]] = []
        seen_hazards: set[str] = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            hazard = self._clean_sector_hazard_name(
                str(item.get("hazard") or item.get("name") or "")
            )
            if not hazard:
                continue
            hazard_key = normalize(hazard)
            if hazard_key in seen_hazards:
                continue
            seen_hazards.add(hazard_key)
            raw_profiles = item.get("profiles")
            profiles: list[dict[str, str]] = []
            if isinstance(raw_profiles, list):
                profiles = [
                    profile
                    for profile in (
                        self._clean_hazard_profile_item(profile)
                        for profile in raw_profiles
                    )
                    if profile
                ]
            hazard_items.append({"hazard": hazard[:180], "profiles": profiles})
        return hazard_items

    @staticmethod
    def _clean_hazard_profile_item(value: object) -> dict[str, str]:
        return clean_hazard_profile_item(value)

    async def _sector_prompt_hazard_block_from_rag(
        self,
        session: ChatSession,
        hazard: str,
    ) -> str:
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                f'HAZARD {hazard} confirmed predictors PREDICTOR',
                limit=3,
            )
        except Exception:
            logger.exception("Sector-prompt RAG hazard-block lookup failed")
            return ""
        combined = "\n\n".join(
            str(result.get("content") or "").strip()
            for result in results
            if str(result.get("content") or "").strip()
        )
        if not combined:
            return ""
        return self._confirmed_predictor_hazard_block(combined, hazard)

    @classmethod
    def _hazard_names_from_sector_prompt(cls, sector_prompt: str) -> list[str]:
        prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
        hazards: list[str] = []
        for match in re.finditer(r"(?m)^HAZARD\s+\d+\.\s+(.+?)\s*$", prompt):
            hazard = cls._clean_sector_hazard_name(match.group(1))
            if hazard and normalize_for_match(hazard) not in {
                normalize_for_match(existing) for existing in hazards
            }:
                hazards.append(hazard)
        return hazards

    @staticmethod
    def _clean_sector_hazard_name(value: str) -> str:
        hazard = re.sub(r"\s+", " ", str(value or "")).strip()
        hazard = re.sub(r"(?i)^HAZARD\s+\d+\.\s*", "", hazard).strip()
        hazard = strip_rule_lines(hazard).strip()
        if re.fullmatch(r"[─═\-_=]{6,}", hazard):
            return ""
        return hazard

    @classmethod
    def _profiles_from_hazard_block(cls, hazard_block: str) -> list[dict[str, str]]:
        profiles: list[dict[str, str]] = []
        for entry in cls._confirmed_predictor_entries(hazard_block):
            profile = cls._profile_from_predictor_entry(entry)
            if profile:
                profiles.append(profile)
        return profiles

    @classmethod
    def _profile_from_predictor_entry(cls, entry: str) -> dict[str, str]:
        return profile_from_predictor_entry(entry)

    @staticmethod
    def _humanize_predictor_label(value: str) -> str:
        return humanize_predictor_label(value)

    async def _get_hazard_block_profiles_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        hazard_block: str,
        expected_count: int | None = None,
        predictor_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        predictor_id_list = predictor_ids or []
        count_rule = (
            f"- Return exactly {expected_count} profile objects: one for each confirmed PREDICTOR entry in the block.\n"
            if expected_count
            else "- Return one profile object for each confirmed PREDICTOR entry in the block.\n"
        )
        id_rule = (
            "- Use this predictor checklist and return one object for each ID in this order: "
            + ", ".join(predictor_id_list)
            + ".\n"
            if predictor_id_list
            else ""
        )
        context = render_prompt_template(
            "llm/hazard_block_profiles_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            hazard_block=hazard_block,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_block_profiles_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                    count_rule=count_rule,
                    id_rule=id_rule,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=900,
        )
        profiles = self._parse_hazard_profile_items(response)
        if expected_count and len(profiles) != expected_count:
            retry_response = await ask_llm_chat(
                context=context,
                messages=[
                    *messages,
                    {
                        "role": "assistant",
                        "content": response,
                    },
                    {
                        "role": "user",
                        "content": render_prompt_template(
                            "llm/hazard_block_profiles_retry_user.txt",
                            profile_count=len(profiles),
                            expected_count=expected_count,
                            checklist=(
                                "Use this checklist in order: "
                                + ", ".join(predictor_id_list)
                                + ". "
                                if predictor_id_list
                                else ""
                            ),
                        ),
                    },
                ],
                temperature=0,
                max_tokens=900,
            )
            retry_profiles = self._parse_hazard_profile_items(retry_response)
            if len(retry_profiles) == expected_count:
                return retry_profiles
            predictor_entries = self._confirmed_predictor_entries(hazard_block)
            if len(predictor_entries) == expected_count:
                single_profiles = await asyncio.gather(
                    *(
                        self._get_single_predictor_profile_from_llm(session, hazard, entry)
                        for entry in predictor_entries
                    )
                )
                fallback_profiles = [
                    profile
                    for profile in single_profiles
                    if profile and profile.get("name", "").strip()
                ]
                if len(fallback_profiles) == expected_count:
                    return fallback_profiles
        return profiles

    async def _get_single_predictor_profile_from_llm(
        self,
        session: ChatSession,
        hazard: str,
        predictor_entry: str,
    ) -> dict[str, str]:
        context = render_prompt_template(
            "llm/single_predictor_profile_extraction.txt",
            scope_instruction=self._scope_instruction(session),
            predictor_entry=predictor_entry,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/single_predictor_profile_extraction_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    hazard=hazard,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0,
            max_tokens=300,
        )
        if is_llm_unavailable_response(response):
            return {}
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return {}
        name = str(parsed.get("name") or parsed.get("profile") or "").strip().strip("`*_ ")
        explanation = str(
            parsed.get("explanation")
            or parsed.get("reason")
            or parsed.get("description")
            or ""
        ).strip().strip("`*_ ")
        variable_name = str(
            parsed.get("variable_name")
            or parsed.get("variable")
            or parsed.get("predictor")
            or ""
        ).strip().strip("`*_ ")
        statistical_basis = str(
            parsed.get("statistical_basis")
            or parsed.get("basis")
            or parsed.get("statistical_evidence")
            or ""
        ).strip().strip("`*_ ")
        source = str(parsed.get("source") or "sector_prompt").strip().strip("`*_ ")
        if not name:
            return {}
        return {
            "name": name[:120],
            "profile": name[:120],
            "explanation": explanation[:260],
            "variable_name": variable_name[:160],
            "statistical_basis": statistical_basis[:600],
            "source": source[:40] if source else "sector_prompt",
            "metadata": parsed,
        }

    @staticmethod
    def _confirmed_predictor_count(hazard_block: str) -> int | None:
        match = re.search(r"\b(\d+)\s+confirmed predictors?\b", hazard_block, re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _confirmed_predictor_ids(hazard_block: str) -> list[str]:
        return re.findall(r"^PREDICTOR\s+([0-9]+[A-Z]):", hazard_block, re.MULTILINE)

    @staticmethod
    def _confirmed_predictor_entries(hazard_block: str) -> list[str]:
        entry_pattern = re.compile(
            r"(?ms)^PREDICTOR\s+[0-9]+[A-Z]:.*?(?=^PREDICTOR\s+[0-9]+[A-Z]:|^COUNTRY PATTERN|^PREDICTORS NOT CONFIRMED|\Z)"
        )
        return [match.group(0).strip() for match in entry_pattern.finditer(hazard_block)]

    def _expected_hazard_profile_count(self, session: ChatSession, hazard: str) -> int | None:
        _ = session, hazard
        return None



    def _mitigation_record_for_match(
        self, session: ChatSession, match: str
    ) -> UserMitigationMeasure | None:
        records = self._existing_mitigation_records_for_selected_hazard(session)
        if not records:
            return None
        normalized_match = normalize(match)
        for record in records:
            if normalize(record.measure) == normalized_match:
                return record
        best_record = None
        best_score = 0.0
        for record in records:
            score = fuzzy_score(match, record.measure)
            if score > best_score:
                best_record = record
                best_score = score
        return best_record if best_score >= 0.45 else None



    @staticmethod
    def _sector_family(sector: str | None) -> str:
        normalized = normalize_for_match(sector or "")
        if "transport" in normalized or "mobility" in normalized:
            return "transport"
        if "housing" in normalized or "home" in normalized or "building" in normalized:
            return "housing"
        if "energy" in normalized or "electric" in normalized or "power" in normalized:
            return "energy"
        return normalized

    @staticmethod
    def _sector_display_name(sector_family: str) -> str:
        return {
            "energy": "Energy",
            "housing": "Housing",
            "transport": "Transport",
        }.get(sector_family, sector_family.title())
