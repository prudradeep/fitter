import json
import logging
import re

from app.llm import ask_llm_chat
from app.schemas import ChatResponse, Option
from app.services.chat_hazard_duplicates import (
    dedupe_hazard_names,
    hazard_duplicate_payloads,
    same_country_sector_additional_hazard_names,
    same_scope_custom_hazard_names,
    same_scope_custom_hazard_summary,
    same_sector_hazard_names,
)
from app.services.chat_json import parse_json_object
from app.services.chat_options import (
    CUSTOM_HAZARD_POLICY_CLARIFICATION_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    compact_for_match,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.chat_population_edits import (
    clean_affected_group_label,
    parse_custom_affected_group_edit_message,
    split_affected_group_labels,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.custom_hazard_validation import (
    DIMENSION_SEQUENCE,
    DIMENSION_STAGES,
    build_custom_hazard_grounding_status,
    custom_hazard_dimension_floor,
    custom_hazard_validation_details,
    default_custom_hazard_state,
    frontend_custom_hazard_payload,
    policy_objective_for_sector,
    validate_custom_hazard_dimensions,
)
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.message_renderer import markdown_to_html, render_message
from app.services.prompt_loader import load_nested_prompt_file

MAX_CUSTOM_HAZARD_GENERATED_TITLE_LENGTH = 100
MAX_CUSTOM_HAZARD_SUMMARY_LENGTH = 700

logger = logging.getLogger(__name__)


class ChatCustomHazardGroundingMixin:
    def _custom_hazard_state(self, session: ChatSession) -> dict[str, object]:
        if not isinstance(session.custom_hazard, dict):
            session.custom_hazard = default_custom_hazard_state()
        dimensions = session.custom_hazard.get("dimension_scores")
        if isinstance(dimensions, dict) and "twin_transition_policy_fit" in dimensions:
            legacy = dimensions.pop("twin_transition_policy_fit")
            dimensions.setdefault("mechanism_fit", legacy)
            if (
                isinstance(legacy, dict)
                and int(legacy.get("score") or 0)
                >= custom_hazard_dimension_floor(session.custom_hazard.get("validation_mode"))
                and not legacy.get("needs_clarification")
            ):
                session.custom_hazard["mechanism_confirmed"] = True
                session.custom_hazard["causal_linkage_confirmed"] = True
        if session.custom_hazard.get("active_validation_dimension") == "twin_transition_policy_fit":
            session.custom_hazard["active_validation_dimension"] = "mechanism_fit"
        active = session.custom_hazard.get("active_validation_dimensions")
        if isinstance(active, list):
            session.custom_hazard["active_validation_dimensions"] = [
                "mechanism_fit" if value == "twin_transition_policy_fit" else value
                for value in active
            ]
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
        if state.pop("show_evidence_linkages", False):
            bot_message = self._prepend_custom_hazard_evidence_linkages(
                state,
                bot_message,
            )
        if state.pop("show_policy_hazard_causal_linkage", False):
            causal_linkage = self._custom_hazard_policy_causal_linkage(state)
            if causal_linkage:
                bot_message = (
                    markdown_to_html(
                        "## Causal linkage to the provided policy document\n\n"
                        f"{self._short_linkage_bullets(causal_linkage)}"
                    )
                    + bot_message
                )
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

    @staticmethod
    def _custom_hazard_policy_causal_linkage(state: dict[str, object]) -> str:
        if not state.get("policy_reference_available"):
            return ""
        dimensions = state.get("dimension_scores")
        mechanism_fit = (
            dimensions.get("mechanism_fit")
            if isinstance(dimensions, dict)
            else None
        )
        if not isinstance(mechanism_fit, dict):
            return ""
        if int(mechanism_fit.get("score") or 0) < 7 or mechanism_fit.get(
            "needs_clarification"
        ):
            return ""
        return str(mechanism_fit.get("causal_linkage") or "").strip()

    @staticmethod
    def _short_linkage_bullets(value: str, *, limit: int = 3) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        parts = [
            part.strip(" -•\t")
            for part in re.split(r"(?<=[.!?])\s+|\s*[•]\s*", text)
            if part.strip(" -•\t")
        ]
        if not parts:
            parts = [text]

        bullets: list[str] = []
        for part in parts[:limit]:
            if len(part) > 280:
                shortened = part[:277].rsplit(" ", 1)[0].rstrip(" ,;:")
                part = f"{shortened or part[:277]}…"
            bullets.append(f"- {part}")
        return "\n".join(bullets)

    @staticmethod
    def _prepend_custom_hazard_evidence_linkages(
        state: dict[str, object],
        bot_message: str,
    ) -> str:
        analysis = state.get("linkage_analysis")
        if not isinstance(analysis, dict):
            analysis = {}
        sections: list[str] = []
        for key, title in (
            ("evidence_hazard_linkage", "Evidence-to-hazard linkage"),
        ):
            item = analysis.get(key)
            supported = isinstance(item, dict) and bool(item.get("supported"))
            linkage = str(item.get("causal_linkage") or "").strip() if isinstance(item, dict) else ""
            reason = str(item.get("reason") or "").strip() if isinstance(item, dict) else ""
            if supported and linkage:
                body = ChatCustomHazardGroundingMixin._short_linkage_bullets(linkage)
            else:
                summary_items = ["No supported causal linkage found."]
                if reason:
                    summary_items.append(reason)
                body = ChatCustomHazardGroundingMixin._short_linkage_bullets(
                    "\n".join(summary_items)
                )
            sections.append(f"## {title}\n\n{body}")
        return markdown_to_html("\n\n".join(sections)) + bot_message

    async def _run_custom_hazard_dimension_check(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(
            state.get("resolved_hazard_text") or state.get("raw_text") or session.pending_hazard or ""
        ).strip()
        if not hazard:
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_input",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="textarea",
                error=True,
            )
        quality_reason = self._text_quality_rejection_reason(hazard, "hazard name")
        if quality_reason:
            session.pending_hazard = None
            transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
            state["status"] = CustomHazardStatus.REJECTED.value
            state["title_validation_status"] = "invalid"
            state["title_validation_code"] = "too_short_input"
            state["title_validation_reason"] = quality_reason
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_input",
                bot_message=markdown_to_html(
                    f"## Rejected\n\n**Reason:** {quality_reason}\n\n"
                    "Please enter a clear, meaningful hazard name."
                ),
                options=HAZARD_ENTRY_OPTIONS,
                input_mode="textarea",
                error=True,
            )
        policy_reference_context = await self._policy_reference_context(
            session,
            [
                str(document_id)
                for document_id in state.get("policy_reference_document_ids") or []
                if str(document_id).strip()
            ],
        )
        evidence_text = str(state.get("evidence") or "").strip()
        evidence_document_ids = re.findall(
            r"Temporary evidence document ID:\s*(\S+)",
            evidence_text,
            flags=re.IGNORECASE,
        )
        evidence_document_context = await self._temporary_evidence_context(
            session,
            evidence_document_ids or None,
        ) if evidence_document_ids else ""
        reused_evidence_context = self._reused_evidence_context(session, evidence_text)
        inline_evidence = re.sub(
            r"Temporary evidence document ID:\s*\S+",
            "",
            evidence_text,
            flags=re.IGNORECASE,
        )
        inline_evidence = re.sub(
            r"Evidence URL:\s*\S+",
            "",
            inline_evidence,
            flags=re.IGNORECASE,
        )
        inline_evidence = re.sub(
            r"Evidence file:\s*.*$",
            "",
            inline_evidence,
            flags=re.IGNORECASE,
        ).strip()
        inline_evidence = re.sub(
            r"^Reused evidence (?:document ID|scope):\s*.*$",
            "",
            inline_evidence,
            flags=re.IGNORECASE | re.MULTILINE,
        ).strip()
        evidence_context = "\n".join(
            part
            for part in (
                reused_evidence_context,
                evidence_document_context,
                inline_evidence,
            )
            if part
        )
        state["policy_reference_available"] = bool(policy_reference_context.strip())
        while True:
            if (
                self._custom_hazard_dimension_is_supported(
                    session,
                    "policy_objective_fit",
                )
                and not bool(state.get("evidence_decision_asked"))
            ):
                break
            if (
                self._custom_hazard_dimension_is_supported(session, "policy_objective_fit")
                and bool(state.get("evidence_decision_asked"))
                and not bool(state.get("causal_linkage_confirmed"))
            ):
                break
            active_dimensions = self._next_custom_hazard_dimensions(session)
            if active_dimensions is None:
                if state.get("show_evidence_linkages") and evidence_context:
                    dimensions_to_validate = DIMENSION_SEQUENCE
                else:
                    break
            else:
                dimensions_to_validate = active_dimensions
                state["active_validation_dimensions"] = list(active_dimensions)
                state["active_validation_dimension"] = next(
                    (
                        dimension
                        for dimension in active_dimensions
                        if not self._custom_hazard_dimension_is_supported(
                            session,
                            dimension,
                        )
                    ),
                    active_dimensions[0],
                )

            result = await validate_custom_hazard_dimensions(
                self._custom_hazard_grounding_text(state, hazard),
                session.sector or "",
                session.country or "",
                session.region or "",
                self._duplicate_hazard_names_for_check(session),
                state,
                session.validation_mode,
                policy_reference_context,
                evidence_context,
                dimensions_to_validate=dimensions_to_validate,
            )
            self._store_custom_hazard_validation_result(session, hazard, result)
            state = self._custom_hazard_state(session)

            if active_dimensions is None:
                break
            if not all(
                self._custom_hazard_dimension_is_supported(session, dimension)
                for dimension in active_dimensions
            ):
                break
        if self._custom_hazard_repeated_clarification_questions_after_answer(session):
            # Affected groups are intentionally confirmed in the dedicated
            # review step. Do not treat that review as a repeated unanswered
            # clarification when all core dimensions are already supported.
            state = self._custom_hazard_state(session)
            if (
                self._custom_hazard_core_dimensions_are_supported(session)
                and state.get("affected_groups")
                and not self._custom_hazard_has_pending_core_dimension_clarification(session)
            ):
                state["next_action"] = CustomHazardAction.REVIEW_GROUPS.value
                return await self._route_custom_hazard_next_action(session_id, session)
            return self._custom_hazard_repeated_clarification_error_response(
                session_id,
                session,
            )
        return await self._route_custom_hazard_next_action(session_id, session)

    def _same_sector_hazard_names_for_duplicate_check(self, session: ChatSession) -> list[str]:
        return same_sector_hazard_names(getattr(self, "db", None), session)

    def _next_custom_hazard_dimensions(
        self,
        session: ChatSession,
    ) -> tuple[str, ...] | None:
        for stage in DIMENSION_STAGES:
            if not all(
                self._custom_hazard_dimension_is_supported(session, dimension)
                for dimension in stage
            ):
                return stage
        return None

    def _same_scope_custom_hazard_names_for_duplicate_check(
        self, session: ChatSession
    ) -> list[str]:
        return same_scope_custom_hazard_names(
            getattr(self, "db", None),
            session,
            getattr(self, "user_id", None),
        )

    def _custom_hazard_summary_for_duplicate(
        self,
        session: ChatSession,
        hazard_name: str,
    ) -> str:
        if (
            normalize_for_match(hazard_name)
            == normalize_for_match(session.accepted_custom_hazard or "")
        ):
            accepted_summary = str(
                session.accepted_custom_hazard_summary or ""
            ).strip()
            if accepted_summary:
                return accepted_summary
        return same_scope_custom_hazard_summary(
            getattr(self, "db", None),
            session,
            getattr(self, "user_id", None),
            hazard_name,
        )

    def _same_country_sector_additional_hazard_names_for_duplicate_check(
        self, session: ChatSession
    ) -> list[str]:
        return same_country_sector_additional_hazard_names(
            getattr(self, "db", None),
            session,
        )

    def _duplicate_hazard_names_for_check(self, session: ChatSession) -> list[str]:
        return self._dedupe_hazard_names(
            [
                *self._same_sector_hazard_names_for_duplicate_check(session),
                *self._same_scope_custom_hazard_names_for_duplicate_check(session),
                *self._same_country_sector_additional_hazard_names_for_duplicate_check(
                    session
                ),
            ]
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
        mechanism = str(state.get("selected_mechanism") or "").strip()
        if reason:
            parts.append(f"Reason: {reason}")
        if evidence:
            parts.append(f"Evidence: {evidence}")
        if mechanism:
            parts.append(f"Confirmed mechanism: {mechanism}")
        for clarification in state.get("clarifications") or []:
            if not isinstance(clarification, dict):
                continue
            answer = str(clarification.get("answer") or "").strip()
            if answer:
                parts.append(f"Clarification: {answer}")
        return "\n".join(parts)

    def _custom_hazard_context_reason(
        self, state: dict[str, object], hazard: str
    ) -> str:
        candidates = [
            str(state.get("reason") or "").strip(),
            str(state.get("title_validation_reason") or "").strip(),
        ]
        for clarification in state.get("clarifications") or []:
            if not isinstance(clarification, dict):
                continue
            answer = str(clarification.get("answer") or "").strip()
            if answer:
                candidates.append(answer)
        if self._hazard_text_contains_justification(hazard):
            candidates.append(hazard)
        return next((candidate for candidate in candidates if len(candidate) >= 24), "")

    @staticmethod
    def _hazard_text_contains_justification(hazard: str) -> bool:
        normalized = normalize_for_match(hazard)
        if len(normalized) < 40:
            return False
        cause_terms = {
            "because",
            "due to",
            "from",
            "caused by",
            "arising from",
            "linked to",
            "when",
            "as",
            "through",
        }
        harm_terms = {
            "cost",
            "costs",
            "loss",
            "losses",
            "increase",
            "increases",
            "exclusion",
            "risk",
            "harm",
            "burden",
            "shortage",
            "disruption",
            "unaffordable",
            "penalty",
            "delay",
        }
        return any(term in normalized for term in cause_terms) and any(
            term in normalized for term in harm_terms
        )

    def _store_custom_hazard_validation_result(
        self, session: ChatSession, hazard: str, result: dict[str, object]
    ) -> None:
        state = self._custom_hazard_state(session)
        state["raw_text"] = str(state.get("raw_text") or hazard).strip()
        state["normalized_text"] = normalize_for_match(hazard)
        state["selected_country"] = session.country or ""
        state["selected_region"] = session.region or ""
        state["selected_sector"] = session.sector or ""
        state["validation_mode"] = session.validation_mode or "strict"
        state["validation_round"] = int(state.get("validation_round") or 0) + 1
        state["overall_score"] = int(result.get("overall_score") or 0)
        scores = list(state.get("scores") or [])
        scores.append(state["overall_score"])
        state["scores"] = scores[-4:]
        state["dimension_scores"] = result.get("dimension_scores") or {}
        state["linkage_analysis"] = result.get("linkage_analysis") or {}
        # Evidence validation is a second pass over the same hazard. Some LLM
        # responses omit affected groups when focusing on the supplied URL;
        # do not discard groups already identified during the title/grounding
        # pass, or the flow will ask the user for them again.
        result_groups = result.get("affected_groups")
        if isinstance(result_groups, list) and result_groups:
            state["affected_groups"] = result_groups
        elif not isinstance(state.get("affected_groups"), list):
            state["affected_groups"] = []
        confirmed_groups = result.get("confirmed_affected_groups")
        if isinstance(confirmed_groups, list) and confirmed_groups:
            state["confirmed_affected_groups"] = confirmed_groups
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
        hazard = str(
            state.get("resolved_hazard_text")
            or session.pending_hazard
            or state.get("raw_text")
            or "this hazard"
        ).strip()
        if (
            action == CustomHazardAction.ASK_DUPLICATE_CONFIRMATION
            and bool(state.get("causal_linkage_confirmed"))
        ):
            candidate = (state.get("duplicate_candidates") or [{}])[0]
            return self._hazard_duplicate_suggestion_step(
                session_id,
                session,
                hazard,
                str(candidate.get("existing_hazard") or "the suggested existing hazard"),
                str(candidate.get("reason") or "The proposed hazard appears similar to an existing hazard."),
            )
        evidence_handled = bool(state.get("evidence_decision_asked")) or bool(
            str(state.get("evidence") or session.accepted_custom_hazard_evidence or "").strip()
        )
        objective_supported = self._custom_hazard_dimension_is_supported(
            session, "policy_objective_fit"
        )
        if objective_supported and not evidence_handled:
            state["reason"] = str(state.get("reason") or self._custom_hazard_dimension_reason(state)).strip()
            state["objective_fit_reason"] = str(state.get("reason") or "").strip()
            session.pending_hazard = hazard
            session.pending_hazard_reason = str(state.get("reason") or "").strip()
            return self._hazard_evidence_decision_step(session_id, session)
        if (
            objective_supported
            and evidence_handled
            and not bool(state.get("causal_linkage_confirmed"))
        ):
            return await self._custom_hazard_mechanism_suggestion_step(session_id, session)
        if (
            action == CustomHazardAction.ASK_CLARIFICATION
            and evidence_handled
            and self._custom_hazard_core_dimensions_are_supported(session)
            and state.get("affected_groups")
        ):
            # Once evidence has been accepted or declined, identified groups
            # belong in the dedicated user review, even when the validator
            # reports NEEDS CLARIFICATION without a question for that field.
            action = CustomHazardAction.REVIEW_GROUPS
        if self._custom_hazard_has_pending_dimension_clarification(session):
            # Affected-group grounding is intentionally reviewed by the user
            # in the next step. If the core hazard dimensions are supported
            # and groups were identified, do not send the user back through
            # generic additional-information clarification.
            if not (
                self._custom_hazard_core_dimensions_are_supported(session)
                and state.get("affected_groups")
            ):
                return self._custom_hazard_clarification_step(session_id, session)
            action = CustomHazardAction.REVIEW_GROUPS
        if not str(state.get("reason") or "").strip():
            state["reason"] = self._custom_hazard_dimension_reason(state)
        if (
            action in {CustomHazardAction.REVIEW_GROUPS, CustomHazardAction.VALIDATE}
            and not bool(state.get("evidence_decision_asked"))
            and not str(state.get("evidence") or session.pending_hazard_evidence or "").strip()
        ):
            session.pending_hazard = hazard
            session.pending_hazard_reason = str(
                state.get("reason") or session.accepted_custom_hazard_reason or ""
            ).strip()
            return self._hazard_evidence_decision_step(session_id, session)
        if action == CustomHazardAction.REVIEW_GROUPS:
            generic_group = self._first_generic_affected_group(
                state.get("affected_groups") or []
            )
            if generic_group:
                return self._custom_hazard_generic_group_clarification_step(
                    session_id,
                    session,
                    generic_group,
                )
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_reason = (
                str(state.get("reason") or "").strip()
                or self._custom_hazard_dimension_reason(state)
            )
            session.accepted_custom_hazard_evidence = (
                str(state.get("evidence") or "").strip() or "Not provided"
            )
            await self._ensure_custom_hazard_generated_title(session, hazard)
            return self._custom_hazard_population_review_step(session_id, session)
        if (
            action == CustomHazardAction.ASK_CLARIFICATION
            and self._custom_hazard_core_dimensions_are_supported(session)
            and state.get("affected_groups")
        ):
            generic_group = self._first_generic_affected_group(
                state.get("affected_groups") or []
            )
            if generic_group:
                return self._custom_hazard_generic_group_clarification_step(
                    session_id,
                    session,
                    generic_group,
                )
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_reason = (
                str(state.get("reason") or "").strip()
                or self._custom_hazard_dimension_reason(state)
            )
            session.accepted_custom_hazard_evidence = (
                str(state.get("evidence") or "").strip() or "Not provided"
            )
            await self._ensure_custom_hazard_generated_title(session, hazard)
            return self._custom_hazard_population_review_step(session_id, session)
        if action == CustomHazardAction.VALIDATE:
            return await self._finalize_custom_hazard_from_grounding(session_id, session)
        if action == CustomHazardAction.REJECT:
            return self._custom_hazard_clarification_step(
                session_id,
                session,
                rejected=True,
            )
        return self._custom_hazard_clarification_step(session_id, session)

    async def _generate_custom_hazard_title(
        self,
        session: ChatSession,
        hazard: str,
    ) -> str:
        """Generate a canonical title after grounding, without grounding the title."""
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        affected_groups = [
            {
                "group": str(group.get("group") or group.get("name") or "").strip(),
                "reason": str(group.get("reason") or "").strip(),
            }
            for group in (
                state.get("confirmed_affected_groups")
                or state.get("affected_groups")
                or []
            )
            if isinstance(group, dict)
            and str(group.get("group") or group.get("name") or "").strip()
        ]
        context = load_nested_prompt_file("llm/custom_hazard_title_generation.txt")
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "country": session.country or "Not selected",
                            "region": session.region or "Not selected",
                            "sector": session.sector or "Not selected",
                            "original_hazard": hazard,
                            "validated_reason": str(
                                state.get("reason")
                                or session.accepted_custom_hazard_reason
                                or ""
                            ).strip(),
                            "validated_evidence": str(
                                state.get("evidence")
                                or session.accepted_custom_hazard_evidence
                                or ""
                            ).strip(),
                            "affected_groups": affected_groups,
                            "negative_consequence": str(
                                state.get("negative_consequence") or ""
                            ).strip(),
                            "clarifications": list(state.get("clarifications") or []),
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=120,
        )
        generated_title = ""
        if not is_llm_unavailable_response(response):
            parsed = parse_json_object(response) or {}
            if isinstance(parsed, dict):
                generated_title = str(parsed.get("title") or "").strip()
        return self._limit_custom_hazard_generated_title(generated_title) or hazard

    async def _ensure_custom_hazard_generated_title(
        self,
        session: ChatSession,
        hazard: str,
    ) -> str:
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else None
        generated_title = str(
            session.generated_custom_hazard_title
            or (state or {}).get("generated_title")
            or ""
        ).strip()
        if not generated_title:
            generated_title = await self._generate_custom_hazard_title(session, hazard)
        session.generated_custom_hazard_title = generated_title
        if state is not None:
            state["generated_title"] = generated_title
        return generated_title

    async def _ensure_custom_hazard_summary(
        self,
        session: ChatSession,
        original_hazard: str,
        generated_title: str,
    ) -> str:
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else None
        summary = str(
            session.accepted_custom_hazard_summary
            or (state or {}).get("generated_summary")
            or ""
        ).strip()
        if not summary:
            summary = await self._generate_custom_hazard_summary(
                session,
                original_hazard,
                generated_title,
            )
        session.accepted_custom_hazard_summary = summary
        if state is not None:
            state["generated_summary"] = summary
        return summary

    async def _generate_custom_hazard_summary(
        self,
        session: ChatSession,
        original_hazard: str,
        generated_title: str,
    ) -> str:
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        clarifications = [
            {
                "questions": [
                    str(question).strip()
                    for question in item.get("questions") or []
                    if str(question or "").strip()
                ],
                "answer": str(item.get("answer") or "").strip(),
            }
            for item in state.get("clarifications") or []
            if isinstance(item, dict) and str(item.get("answer") or "").strip()
        ]
        groups = [
            {
                "group": str(group.get("group") or group.get("name") or "").strip(),
                "reason": str(group.get("reason") or "").strip(),
            }
            for group in (
                state.get("confirmed_affected_groups")
                or state.get("affected_groups")
                or []
            )
            if isinstance(group, dict)
            and str(group.get("group") or group.get("name") or "").strip()
        ]
        fallback = self._custom_hazard_summary_fallback(
            session,
            original_hazard,
            generated_title,
            clarifications,
            groups,
        )
        try:
            response = await ask_llm_chat(
                context=load_nested_prompt_file("llm/custom_hazard_summary_generation.txt"),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "country": session.country or "Not selected",
                                "region": session.region or "Not selected",
                                "sector": session.sector or "Not selected",
                                "title": generated_title,
                                "original_hazard": original_hazard,
                                "validated_reason": str(
                                    state.get("reason")
                                    or session.accepted_custom_hazard_reason
                                    or ""
                                ).strip(),
                                "negative_consequence": str(
                                    state.get("negative_consequence") or ""
                                ).strip(),
                                "clarifications": clarifications,
                                "confirmed_affected_groups": groups,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=240,
            )
        except Exception:
            logger.exception("Failed to generate final custom-hazard summary")
            return fallback

        if is_llm_unavailable_response(response):
            return fallback
        parsed = parse_json_object(response) or {}
        summary = str(parsed.get("summary") or "").strip() if isinstance(parsed, dict) else ""
        return self._limit_custom_hazard_summary(summary) or fallback

    async def _revise_custom_hazard_summary(
        self,
        session: ChatSession,
        original_hazard: str,
        generated_title: str,
        current_summary: str,
        user_context: str,
    ) -> str:
        """Revise a draft summary without changing its validated hazard facts."""
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        groups = [
            {
                "group": str(group.get("group") or group.get("name") or "").strip(),
                "reason": str(group.get("reason") or "").strip(),
            }
            for group in (
                state.get("confirmed_affected_groups")
                or state.get("affected_groups")
                or []
            )
            if isinstance(group, dict)
            and str(group.get("group") or group.get("name") or "").strip()
        ]
        try:
            response = await ask_llm_chat(
                context=load_nested_prompt_file("llm/custom_hazard_summary_revision.txt"),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "country": session.country or "Not selected",
                                "region": session.region or "Not selected",
                                "sector": session.sector or "Not selected",
                                "title": generated_title,
                                "original_hazard": original_hazard,
                                "validated_reason": str(
                                    state.get("reason")
                                    or session.accepted_custom_hazard_reason
                                    or ""
                                ).strip(),
                                "validated_evidence": str(
                                    state.get("evidence")
                                    or session.accepted_custom_hazard_evidence
                                    or ""
                                ).strip(),
                                "negative_consequence": str(
                                    state.get("negative_consequence") or ""
                                ).strip(),
                                "clarifications": list(state.get("clarifications") or []),
                                "confirmed_affected_groups": groups,
                                "current_summary": current_summary,
                                "user_revision_request": user_context,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=240,
            )
        except Exception:
            logger.exception("Failed to revise custom-hazard summary")
            return ""

        if is_llm_unavailable_response(response):
            return ""
        parsed = parse_json_object(response) or {}
        summary = str(parsed.get("summary") or "").strip() if isinstance(parsed, dict) else ""
        return self._limit_custom_hazard_summary(summary)

    def _custom_hazard_summary_fallback(
        self,
        session: ChatSession,
        original_hazard: str,
        generated_title: str,
        clarifications: list[dict[str, object]],
        groups: list[dict[str, str]],
    ) -> str:
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        scope = ", ".join(
            value for value in (session.region, session.country) if str(value or "").strip()
        )
        first_sentence = generated_title.strip().rstrip(".")
        if scope:
            first_sentence += f" is a confirmed {session.sector or 'transition'} hazard in {scope}."
        else:
            first_sentence += " is a confirmed transition hazard."

        parts = [first_sentence]
        original = original_hazard.strip().rstrip(".")
        if original and normalize_for_match(original) != normalize_for_match(generated_title):
            parts.append(f"It concerns {original}.")
        reason = str(
            state.get("reason") or session.accepted_custom_hazard_reason or ""
        ).strip().rstrip(".")
        if reason:
            parts.append(f"The confirmed reason is: {reason}.")
        answers = [str(item.get("answer") or "").strip().rstrip(".") for item in clarifications]
        answers = [answer for answer in answers if answer]
        if answers:
            parts.append(f"User clarification: {'; '.join(answers)}.")
        group_names = [group["group"] for group in groups if group.get("group")]
        if group_names:
            parts.append(f"Confirmed affected groups: {', '.join(group_names)}.")
        return self._limit_custom_hazard_summary(" ".join(parts))

    @staticmethod
    def _limit_custom_hazard_summary(summary: str) -> str:
        summary = re.sub(r"\s+", " ", str(summary or "")).strip().strip("`*_#")
        if len(summary) <= MAX_CUSTOM_HAZARD_SUMMARY_LENGTH:
            return summary
        shortened = summary[:MAX_CUSTOM_HAZARD_SUMMARY_LENGTH].rsplit(" ", 1)[0]
        return shortened.rstrip(" ,;:-") + "."

    @staticmethod
    def _limit_custom_hazard_generated_title(title: str) -> str:
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        if len(title) <= MAX_CUSTOM_HAZARD_GENERATED_TITLE_LENGTH:
            return title
        shortened = title[:MAX_CUSTOM_HAZARD_GENERATED_TITLE_LENGTH].rsplit(" ", 1)[0]
        return shortened.rstrip(" ,;:-") or title[:MAX_CUSTOM_HAZARD_GENERATED_TITLE_LENGTH].rstrip()

    def _custom_hazard_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        rejected: bool = False,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        missing_details = self._custom_hazard_missing_dimension_details(state)
        if self._custom_hazard_needs_policy_reference(state):
            return self._custom_hazard_policy_reference_step(
                session_id,
                session,
                error=rejected,
            )
        questions = [question for _, question, _ in missing_details]
        if not questions:
            questions = ["Can you clarify how this hazard fits the selected sector, place, and twin-transition policy context?"]
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        state["pending_clarification_questions"] = questions
        state["message"] = "Clarification is needed before validation."
        allow_policy_replacement = bool(
            state.get("policy_reference_available")
            and state.get("active_validation_dimension")
            == "mechanism_fit"
        )
        clarification_message = self._custom_hazard_clarification_message(
            state,
            questions,
            missing_details,
            rejected=rejected,
        )
        if allow_policy_replacement:
            clarification_message += (
                "\n\nYou can clarify the causal linkage using the text box, or choose "
                "**Add a Policy Reference** to replace the current document."
            )
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(clarification_message),
            options=(
                CUSTOM_HAZARD_POLICY_CLARIFICATION_OPTIONS
                if allow_policy_replacement
                else HAZARD_ENTRY_OPTIONS
            ),
            input_mode="textarea",
            error=False,
        )

    @staticmethod
    def _custom_hazard_needs_policy_reference(state: dict[str, object]) -> bool:
        return bool(
            state.get("active_validation_dimension") == "mechanism_fit"
            and state.get("mechanism_source") == "user"
            and state.get("selected_mechanism")
            and not state.get("policy_reference_available")
        )

    def _custom_hazard_policy_reference_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error: bool = False,
        detail: str = "",
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        state["awaiting_policy_reference"] = True
        state["pending_clarification_questions"] = [
            "Provide a policy document URL or file that supports the proposed mechanism."
        ]
        message = (
            "**Mechanism Fit - policy source**\n\n"
            "Provide the policy document as a URL or upload a PDF, DOCX, MD, or TXT file. "
            "I will check whether it supports the mechanism you provided and trace the "
            "causal link from its provisions, through the mechanism, to the hazard.\n\n"
            "This document is a policy reference only and will not be treated as evidence "
            "that the hazard occurred or affects a population."
        )
        if detail:
            message = f"{detail}\n\n{message}"
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_policy_reference",
            bot_message=markdown_to_html(message),
            options=CUSTOM_HAZARD_POLICY_CLARIFICATION_OPTIONS,
            input_mode="policy_reference",
            error=error,
        )

    @staticmethod
    def _custom_hazard_missing_dimension_details(
        state: dict[str, object],
    ) -> list[tuple[str, str, str]]:
        dimensions = state.get("dimension_scores")
        if not isinstance(dimensions, dict):
            return []
        active_dimensions = tuple(
            str(dimension).strip()
            for dimension in state.get("active_validation_dimensions") or []
            if str(dimension).strip()
        )
        active_dimension = str(state.get("active_validation_dimension") or "").strip()
        policy_objective = policy_objective_for_sector(
            str(state.get("selected_sector") or "")
        )
        dimension_floor = custom_hazard_dimension_floor(state.get("validation_mode"))
        prompts = {
            "hazard_definition_fit": (
                "What specific negative harm or risk occurs, and who is affected?"
            ),
            "mechanism_fit": (
                "Which specific process, intervention, rule, technology, or market change causes or worsens the harm?"
            ),
            "policy_objective_fit": (
                f"How could pursuing the policy objective '{policy_objective}' cause or worsen this hazard?"
            ),
            "selected_sector_fit": (
                "How does this hazard relate specifically to the selected sector?"
            ),
            "country_region_fit": (
                "Why is this hazard relevant to the selected country?"
            ),
            "affected_groups_fit": (
                "Which specific population groups are affected, and what impact do they experience?"
            ),
        }
        labels = {
            "hazard_definition_fit": "Hazard definition",
            "mechanism_fit": "Mechanism Fit",
            "policy_objective_fit": "Policy Objective Fit",
            "selected_sector_fit": "Sector fit",
            "country_region_fit": "Country / region fit",
            "affected_groups_fit": "Affected population groups",
        }
        core_dimensions = (
            "policy_objective_fit",
            "mechanism_fit",
            "hazard_definition_fit",
            "selected_sector_fit",
            "country_region_fit",
        )
        objective_item = dimensions.get("policy_objective_fit")
        objective_supported = (
            isinstance(objective_item, dict)
            and int(objective_item.get("score") or 0) >= dimension_floor
            and not objective_item.get("needs_clarification")
            and str(objective_item.get("status") or "").strip().upper()
            not in {"REJECTED", "INSUFFICIENT INFO"}
        )
        core_supported = all(
            isinstance(dimensions.get(key), dict)
            and int(dimensions[key].get("score") or 0) >= dimension_floor
            and not dimensions[key].get("needs_clarification")
            and str(dimensions[key].get("status") or "").strip().upper()
            not in {"REJECTED", "INSUFFICIENT INFO"}
            for key in core_dimensions
        )
        if active_dimensions and all(
            dimension in prompts for dimension in active_dimensions
        ):
            dimensions_to_check = active_dimensions
        elif active_dimension in prompts:
            dimensions_to_check = (active_dimension,)
        elif not objective_supported:
            dimensions_to_check = ("policy_objective_fit",)
        elif core_supported:
            dimensions_to_check = (*core_dimensions, "affected_groups_fit")
        else:
            dimensions_to_check = core_dimensions
        details: list[tuple[str, str, str]] = []
        for key in dimensions_to_check:
            prompt = prompts[key]
            item = dimensions.get(key)
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().upper()
            score = int(item.get("score") or 0)
            if not item.get("needs_clarification") and status not in {
                "REJECTED",
                "INSUFFICIENT INFO",
            } and score >= dimension_floor:
                continue
            question = str(item.get("clarification_question") or "").strip() or prompt
            reason = str(item.get("reason") or "").strip()
            if not reason:
                reason = "The required details were not found in the submitted information."
            detail = (labels.get(key, key), question, reason)
            if detail not in details:
                details.append(detail)
        return details[: len(active_dimensions) if active_dimensions else 2]

    @classmethod
    def _custom_hazard_missing_dimension_questions(
        cls,
        state: dict[str, object],
    ) -> list[str]:
        return [
            question
            for _, question, _ in cls._custom_hazard_missing_dimension_details(state)
        ]

    @staticmethod
    def _custom_hazard_clarification_message(
        state: dict[str, object],
        questions: list[str],
        missing_details: list[tuple[str, str, str]],
        *,
        rejected: bool,
    ) -> str:
        validation_reason = str(state.get("validation_reason") or "").strip()
        if rejected:
            message = (
                "This custom hazard was not accepted because the required details "
                "were not found or were not sufficiently specific."
            )
        else:
            message = "Additional information is required before this custom hazard can be validated."
        if validation_reason and not any(
            reason == validation_reason for _, _, reason in missing_details
        ):
            message += f"\n\n**Reason:** {validation_reason}"
        if missing_details:
            message += "\n\nPlease provide the following details:\n\n"
            message += "\n\n".join(
                f"**{label}**\nReason: {reason}\nPlease provide: {question}"
                for label, question, reason in missing_details
            )
        else:
            message += (
                "\n\nPlease clarify the hazard, its transition-policy link."
            )
        return message + "\n\nPlease answer the questions above in one response."

    def _custom_hazard_repeated_clarification_error_response(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        questions = self._custom_hazard_missing_dimension_questions(state)
        if not questions:
            questions = [
                "Can you clarify how this hazard fits the selected sector, place, and twin-transition policy context?"
            ]
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        state["pending_clarification_questions"] = questions
        state["message"] = (
            "The latest clarification did not resolve one or more requested details."
        )
        missing_details = self._custom_hazard_missing_dimension_details(state)
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(
                "That answer still does not resolve the clarification needed for this custom hazard.\n\n"
                + self._custom_hazard_clarification_message(
                    state,
                    questions,
                    missing_details,
                    rejected=True,
                )
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=True,
        )

    def _custom_hazard_repeated_clarification_questions_after_answer(
        self,
        session: ChatSession,
    ) -> bool:
        state = self._custom_hazard_state(session)
        if self._custom_hazard_needs_policy_reference(state):
            return False
        clarifications = [
            item
            for item in state.get("clarifications") or []
            if isinstance(item, dict)
        ]
        if not clarifications:
            return False
        last_questions = {
            normalize(str(question))
            for question in clarifications[-1].get("questions") or []
            if str(question or "").strip()
        }
        if not last_questions:
            return False
        current_questions = {
            normalize(question)
            for question in self._custom_hazard_missing_dimension_questions(state)
        }
        return bool(last_questions & current_questions)

    @staticmethod
    def _custom_hazard_pending_dimension_questions(
        state: dict[str, object],
    ) -> list[str]:
        questions: list[str] = []
        dimensions = (
            state.get("dimension_scores")
            if isinstance(state.get("dimension_scores"), dict)
            else {}
        )
        for value in dimensions.values():
            if not isinstance(value, dict) or not value.get("needs_clarification"):
                continue
            question = str(value.get("clarification_question") or "").strip()
            if question:
                questions.append(question)
            if len(questions) >= 2:
                break
        return questions

    def _custom_hazard_validation_clarification_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        hazard: str,
        reason: str,
        evidence: str = "",
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        state["raw_text"] = str(state.get("raw_text") or hazard).strip()
        state["normalized_text"] = normalize_for_match(hazard)
        state["reason"] = str(
            state.get("reason") or session.pending_hazard_reason or ""
        ).strip()
        state["evidence"] = evidence or ""
        state["validation_reason"] = reason or "The submitted details were not sufficient for validation."
        state["message"] = reason or "Clarification is needed before validation."
        session.pending_hazard = hazard
        session.pending_hazard_reason = state["reason"]
        session.pending_hazard_evidence = evidence or ""

        dimensions = (
            state.get("dimension_scores")
            if isinstance(state.get("dimension_scores"), dict)
            else {}
        )
        has_pending_question = any(
            isinstance(value, dict)
            and value.get("needs_clarification")
            and str(value.get("clarification_question") or "").strip()
            for value in dimensions.values()
        )
        if not has_pending_question:
            self._mark_custom_hazard_dimension(
                session,
                self._custom_hazard_rejection_dimension(reason),
                status="NEEDS CLARIFICATION",
                score=5,
                reason=reason or "The hazard needs more detail before it can be validated.",
                clarification_question=self._custom_hazard_validation_clarification_question(
                    session,
                    reason,
                ),
            )
        return self._custom_hazard_clarification_step(session_id, session)

    @staticmethod
    def _custom_hazard_validation_failure_needs_clarification(reason: str) -> bool:
        lowered = reason.casefold()
        clarification_terms = (
            "clarification",
            "clarify",
            "unclear",
            "clearer",
            "too vague",
            "vague",
            "generic",
            "insufficient",
            "not enough",
            "does not explain",
            "doesn't explain",
            "does not identify",
            "doesn't identify",
            "missing",
            "cannot determine",
            "can't determine",
        )
        return any(term in lowered for term in clarification_terms)

    def _custom_hazard_has_pending_dimension_clarification(
        self,
        session: ChatSession,
    ) -> bool:
        state = self._custom_hazard_state(session)
        dimensions = (
            state.get("dimension_scores")
            if isinstance(state.get("dimension_scores"), dict)
            else {}
        )
        return any(
            isinstance(value, dict)
            and value.get("needs_clarification")
            and str(value.get("clarification_question") or "").strip()
            for value in dimensions.values()
        )

    def _custom_hazard_has_pending_core_dimension_clarification(
        self,
        session: ChatSession,
    ) -> bool:
        state = self._custom_hazard_state(session)
        dimensions = (
            state.get("dimension_scores")
            if isinstance(state.get("dimension_scores"), dict)
            else {}
        )
        core_dimensions = (
            "policy_objective_fit",
            "mechanism_fit",
            "hazard_definition_fit",
            "selected_sector_fit",
            "country_region_fit",
        )
        return any(
            isinstance(dimensions.get(key), dict)
            and dimensions[key].get("needs_clarification")
            and str(dimensions[key].get("clarification_question") or "").strip()
            for key in core_dimensions
        )

    def _custom_hazard_dimension_is_supported(
        self,
        session: ChatSession,
        dimension: str,
        *,
        minimum_score: int | None = None,
    ) -> bool:
        state = self._custom_hazard_state(session)
        dimensions = state.get("dimension_scores")
        item = dimensions.get(dimension) if isinstance(dimensions, dict) else {}
        if not isinstance(item, dict):
            return False
        if minimum_score is None:
            minimum_score = custom_hazard_dimension_floor(session.validation_mode)
        status = str(item.get("status") or "").strip().upper()
        if status in {"REJECTED", "INSUFFICIENT INFO"}:
            return False
        if item.get("needs_clarification"):
            return False
        return int(item.get("score") or 0) >= minimum_score

    def _custom_hazard_core_dimensions_are_supported(
        self,
        session: ChatSession,
    ) -> bool:
        return all(
            self._custom_hazard_dimension_is_supported(session, dimension)
            for dimension in (
                "policy_objective_fit",
                "mechanism_fit",
                "hazard_definition_fit",
                "selected_sector_fit",
                "country_region_fit",
            )
        )

    def _custom_hazard_validation_clarification_question(
        self,
        session: ChatSession,
        reason: str,
    ) -> str:
        if self._custom_hazard_context_clarification_is_satisfied(session, "affected group"):
            base = (
                "Can you clarify the negative impact and how the "
                f"selected {session.sector or 'sector'} transition policy causes or worsens it?"
            )
        else:
            base = (
                "Can you clarify the affected group, the negative impact, and how the "
                f"selected {session.sector or 'sector'} transition policy causes or worsens it?"
            )
        if reason.strip():
            return f"{base} Validation note: {reason.strip()}"
        return base

    def _custom_hazard_generic_group_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        group: str,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        state["pending_generic_affected_group"] = group
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        question = (
            f"'{group}' is too broad to use as an affected population group. "
            "Which specific group is affected? For example, low-income households, "
            "rural residents, tenants, older adults, workers in a specific sector, "
            "or another clearly affected group."
        )
        state["pending_clarification_questions"] = [question]
        state["message"] = "A more specific affected population group is needed."
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(question),
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
        if self._is_generic_affected_group_label(label):
            return (
                f"'{label}' is too broad. Please name a more specific affected group, "
                "such as low-income households, rural residents, tenants, older adults, "
                "or workers in a specific sector."
            )
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

    @classmethod
    def _first_generic_affected_group(cls, groups: object) -> str:
        if not isinstance(groups, list):
            return ""
        for group in groups:
            if isinstance(group, dict):
                label = str(group.get("group") or group.get("name") or "").strip()
            else:
                label = str(group or "").strip()
            if cls._is_generic_affected_group_label(label):
                return label
        return ""

    @staticmethod
    def _is_generic_affected_group_label(group: str) -> bool:
        normalized = normalize_for_match(group)
        compact = compact_for_match(group)
        generic = {
            "general population",
            "population",
            "people",
            "persons",
            "citizens",
            "communities",
            "community",
            "households",
            "household",
            "residents",
            "resident",
            "users",
            "user",
            "consumers",
            "consumer",
            "public",
            "families",
            "family",
        }
        return normalized in {normalize_for_match(item) for item in generic} or compact in {
            compact_for_match(item) for item in generic
        }

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
        known_hazards = self._duplicate_hazard_names_for_check(session)
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
        state["raw_text"] = str(state.get("raw_text") or hazard).strip()
        state["normalized_text"] = normalize_for_match(hazard)
        if evidence:
            state["evidence"] = evidence
        reason = str(reason or "The submitted details were not sufficient for validation.").strip()
        state["validation_reason"] = reason
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
            step="custom_hazard_clarification",
            bot_message=markdown_to_html(
                "This custom hazard still cannot be saved as written.\n\n"
                f"**Reason:** {reason}\n\n"
                "Please clarify or rewrite the custom hazard in a way that resolves this issue, "
                "or choose **Write hazard again** / **Go back to list of hazards**."
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
            error=True,
        )
