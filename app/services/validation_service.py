import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape

from sqlalchemy import and_, delete, func, select

from app.llm import ask_llm_chat
from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    CustomHazard,
    CustomHazardProfile,
    EvaluationQuestion,
    EurostatPopulationCache,
    MitigationMeasureExample,
    MitigationMeasurePolicy,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
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
    format_additional_dgs,
    format_all_dgs,
    format_evaluation_answers,
    hazard_names,
    normalize_markdown_text,
)
from app.services.chat_json import (
    parse_json_object,
)
from app.services.chat_options import (
    ADD_DGS_OPTIONS,
    DG_REASON_EVIDENCE_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    EVALUATION_CATEGORIES,
    FUZZY_CONFIRMATION_OPTIONS,
    HAZARD_DUPLICATE_OPTIONS,
    HAZARD_POPULATION_REVIEW_OPTIONS,
    MITIGATION_DUPLICATE_OPTIONS,
    MITIGATION_REVIEW_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    best_fuzzy_label,
    compact_for_match,
    exact_option_label,
    fuzzy_score,
    match_option_label,
    normalize,
    normalize_for_match,
    option_list,
)
from app.services.chat_parsers import (
    is_llm_unavailable_response,
    parse_duplicate_check_response,
    parse_entailment_response,
    parse_evaluation_answer,
    parse_grounded_claims_response,
    parse_grounded_validation_response,
    parse_llm_hazard_list,
    parse_mitigation_clarity_response,
    parse_mitigation_reason,
    parse_reason_evidence,
    parse_validation_response,
)
from app.services.chat_population_edits import (
    clean_affected_group_label,
    clean_population_edit_items,
    fallback_population_edits,
    parse_custom_affected_group_edit_message,
    split_affected_group_labels,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    build_custom_hazard_grounding_status,
    custom_hazard_validation_details,
    default_custom_hazard_state,
    frontend_custom_hazard_payload,
    normalize_custom_group,
    validate_custom_hazard_dimensions,
)
from app.services.custom_hazard_text_rules import (
    custom_hazard_sector_mismatch_reason,
    custom_hazard_sector_rewrite_suggestion,
    plain_custom_hazard_rejection_reason,
    sector_signal_scores,
)
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.evidence_contradiction_service import EvidenceContradictionService
from app.services.grounding_models import GroundingModelService
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.hazard_ranking_service import HazardRankingService, slugify_hazard
from app.services.knowledge_base import VALIDATED_EVIDENCE_SCOPE, KnowledgeBaseService
from app.services.mitigation_text_rules import (
    local_mitigation_field_error,
    local_mitigation_measure_error,
    local_mitigation_reason_error,
    mitigations_are_similar,
)
from app.services.message_renderer import markdown_to_html, render_message
from app.services.profile_metadata import compact_profile_metadata
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger(__name__)

class ChatValidationServiceMixin:
    @staticmethod
    def _validation_mode(value: object) -> str:
        return "easy" if str(value or "").strip().casefold() == "easy" else "strict"

    async def _validate_dgs_against_stats(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, DG_REASON_EVIDENCE_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, DG_REASON_EVIDENCE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        skipped_reason_evidence = normalize(exact_label or message) == normalize("Skip")
        if skipped_reason_evidence:
            discarded = list(session.pending_additional_dgs or [])
            session.pending_additional_dgs = None
            session.additional_dg_answers = None
            session.dg_reason = None
            session.dg_evidence = None
            session.phase = "socio_demographic_review"
            self._record_activity(
                session_id,
                session,
                "socio_demographics_skipped",
                ", ".join(discarded) or "No pending socio-demographic profiles.",
            )
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=markdown_to_html(
                    "The pending affected population groups were skipped and were not saved.\n\n"
                    "You can continue with the selected hazard options."
                ),
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=False,
            )

        reason, evidence = parse_reason_evidence(message)
        if not reason and not evidence:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_validation_failed.md",
                    sector=session.sector,
                    reason=(
                        "Please provide a reason or evidence to validate these affected "
                        "population groups, or choose Skip to discard them without saving."
                    ),
                ),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
        if reason or evidence:
            input_review = await self._validate_input_quality(
                session=session,
                purpose=(
                    "an optional reason and optional evidence explaining why the listed "
                    "socio-demographic profiles are severely affected by the selected hazard"
                ),
                fields=self._reason_evidence_quality_fields(reason or "", evidence),
            )
            if input_review is None:
                return ChatResponse(
                    session_id=session_id,
                    step="socio_demographic_review",
                    bot_message=render_message("dg_validation_unavailable.md"),
                    options=DG_REASON_EVIDENCE_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )
            if not input_review["valid"]:
                return ChatResponse(
                    session_id=session_id,
                    step="socio_demographic_review",
                    bot_message=render_message(
                        "dg_validation_failed.md",
                        sector=session.sector,
                        reason=str(input_review["reason"]),
                    ),
                    options=DG_REASON_EVIDENCE_OPTIONS,
                    session=session.summary(),
                    input_mode="reason_evidence",
                    error=True,
                )

        validation = await self._validate_dgs_context_against_stats(
            session=session,
            reason=reason or "",
            evidence=evidence or "",
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message("dg_validation_unavailable.md"),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if not validation["valid"]:
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=render_message(
                    "dg_validation_failed.md",
                    sector=session.sector,
                    reason=validation["reason"],
                ),
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        session.dg_reason = reason
        session.dg_evidence = evidence
        pending_dgs = session.pending_additional_dgs or []
        if pending_dgs:
            hazard_reference = self._selected_hazard_reference(session_id, session)
            pending_profile_details = {
                normalize(str(profile.get("name") or "")): profile
                for profile in self._target_population_profiles_from_answers(
                    session.additional_dg_answers or [],
                    session.selected_hazard or "the selected hazard",
                )
                if profile.get("name")
            }
            if session.additional_dgs is None:
                session.additional_dgs = []
            self._extend_unique_profiles(session.additional_dgs, pending_dgs)
            for dg in pending_dgs:
                profile_detail = pending_profile_details.get(normalize(dg), {})
                self._store_socio_demographic(
                    session,
                    dg,
                    user_hazard_id=hazard_reference["user_hazard_id"],
                    custom_hazard_id=hazard_reference["custom_hazard_id"],
                    system_hazard_id=hazard_reference["system_hazard_id"],
                    additional_hazard_id=hazard_reference["additional_hazard_id"],
                    source="user_validated",
                    variable_name=str(profile_detail.get("variable_name") or "") or None,
                    explanation=str(profile_detail.get("explanation") or "") or None,
                    statistical_basis=str(profile_detail.get("statistical_basis") or "") or None,
                    metadata=profile_detail or None,
                    reason=reason or None,
                    evidence=evidence or None,
                )
            session.pending_additional_dgs = None
        self._record_activity(
            session_id,
            session,
            "socio_demographics_validated",
            reason or "Validated without user-provided reason.",
        )
        session.phase = "mitigation_measure"
        session.pending_mitigation_measure = None
        self._clear_mitigation_clarity_state(session)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_measure",
            bot_message=render_message(
                "mitigation_measure_reason.md",
                hazard=session.selected_hazard or "the selected hazard",
                dgs=format_all_dgs(session),
                mitigation_examples=self._mitigation_measure_examples(session.sector_id),
            ),
            options=[],
            session=session.summary(),
            input_mode="mitigation_measure",
            error=False,
        )

    @staticmethod
    def _is_style_only_validation_rejection(reason: str) -> bool:
        lowered = reason.casefold()
        style_terms = (
            "grammar",
            "grammatical",
            "spelling",
            "punctuation",
            "capitalization",
            "capitalisation",
            "typo",
            "wording",
            "style",
        )
        meaning_terms = (
            "gibberish",
            "keyboard",
            "random",
            "unrecognizable",
            "unrecognisable",
            "meaningless",
            "too short",
            "ambiguous",
            "incomplete",
            "unrelated",
            "unsupported",
            "vague",
        )
        return any(term in lowered for term in style_terms) and not any(
            term in lowered for term in meaning_terms
        )

    @staticmethod
    def _is_hard_validation_rejection(reason: str) -> bool:
        lowered = reason.casefold()
        hard_terms = (
            "gibberish",
            "keyboard",
            "random",
            "unrecognizable",
            "unrecognisable",
            "meaningless",
            "unrelated",
            "not related",
            "outside the scope",
            "out of scope",
            "does not relate",
            "contradicts",
            "contradictory",
            "unsafe",
        )
        return any(term in lowered for term in hard_terms)

    @staticmethod
    def _is_invalid_user_text(message: str) -> bool:
        value = message.strip()
        if not value:
            return False
        if value.startswith(("TARGET_POPULATION_BATCH:", "http://", "https://")):
            return False
        if value.isdigit():
            return False

        normalized = normalize_for_match(value)
        compact = compact_for_match(value)
        if len(compact) < 2:
            return True
        if not re.search(r"[a-z0-9]", compact):
            return True

        total_chars = len(value)
        alnum_chars = sum(1 for char in value if char.isalnum())
        if total_chars >= 5 and alnum_chars / total_chars < 0.45:
            return True
        if re.search(r"(.)\1{5,}", compact):
            return True

        tokens = normalized.split()
        if not tokens:
            return True
        keyboard_rows = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")
        for token in tokens:
            if len(token) >= 4 and any(token in row or token[::-1] in row for row in keyboard_rows):
                return True

        long_tokens = [token for token in tokens if len(token) >= 4]
        if long_tokens and all(not re.search(r"[aeiou]", token) for token in long_tokens):
            return True

        unique_chars = set(compact)
        if len(compact) >= 8 and len(unique_chars) <= 2:
            return True

        return False

    async def _validate_custom_affected_group_reason(
        self,
        session: ChatSession,
        group: str,
        reason: str,
    ) -> dict[str, str | bool] | None:
        local_error = self._custom_affected_group_reason_local_error(
            session,
            group,
            reason,
        )
        if local_error:
            return {"valid": False, "reason": local_error}

        review = await self._validate_input_quality(
            session=session,
            purpose=(
                f"an impact reason explaining how the custom hazard affects "
                f"the affected population group '{group}'"
            ),
            fields={
                "affected group": group,
                "impact reason": reason,
            },
        )
        if review is None:
            return {"valid": True, "reason": "The impact reason is locally meaningful."}
        return review

    def _custom_affected_group_reason_local_error(
        self,
        session: ChatSession,
        group: str,
        reason: str,
    ) -> str | None:
        group_label = self._clean_affected_group_label(group)
        reason_text = re.sub(r"\s+", " ", reason).strip()
        if not group_label:
            return "Please name the affected group before explaining the impact."
        if self._is_invalid_user_text(reason_text) or len(compact_for_match(reason_text)) < 8:
            return "Please provide a meaningful impact reason, not just a short label or unclear text."
        weak_reasons = {
            "yes",
            "ok",
            "okay",
            "affected",
            "impact",
            "bad",
            "problem",
            "issue",
            "poverty",
            "cost",
            "costs",
            "jobs",
        }
        if normalize_for_match(reason_text) in weak_reasons:
            return "Please explain the mechanism, such as the cost, job, access, exposure, or exclusion impact for this group."

        hazard_text = " ".join(
            str(value or "")
            for value in [
                session.accepted_custom_hazard,
                session.pending_hazard,
                (session.custom_hazard or {}).get("raw_text")
                if isinstance(session.custom_hazard, dict)
                else "",
                (session.custom_hazard or {}).get("reason")
                if isinstance(session.custom_hazard, dict)
                else "",
            ]
        )
        reason_words = self._profile_similarity_words(normalize_for_match(reason_text))
        group_words = self._profile_similarity_words(normalize_for_match(group_label))
        hazard_words = self._profile_similarity_words(normalize_for_match(hazard_text))
        mechanism_words = {
            "cost",
            "costs",
            "income",
            "job",
            "jobs",
            "employment",
            "unemployment",
            "access",
            "exposure",
            "health",
            "housing",
            "energy",
            "poverty",
            "arrears",
            "exclusion",
            "training",
            "retraining",
            "mobility",
            "tax",
            "prices",
            "bills",
            "wages",
            "livelihood",
        }
        if not (reason_words & group_words) and not (reason_words & hazard_words):
            return "Please connect the reason to this affected group or to the custom hazard."
        if not (reason_words & mechanism_words):
            return "Please explain the concrete impact mechanism, such as costs, jobs, access, exposure, or exclusion."
        return None

    @staticmethod
    def _custom_hazard_rejection_dimension(reason: str) -> str:
        lowered = reason.casefold()
        policy_terms = (
            "green and digital transition",
            "green transition",
            "digital transition",
            "transition policies",
            "transition policy",
            "twin-transition",
            "twin transition",
            "does not discuss a hazard",
            "merely states a fact",
        )
        if any(term in lowered for term in policy_terms):
            return "twin_transition_policy_fit"
        if "selected sector" in lowered or any(
            term in lowered for term in ("wrong sector", "sector mismatch", "unrelated to the selected sector")
        ):
            return "selected_sector_fit"
        if any(term in lowered for term in ("country", "region", "regional", "local")):
            return "country_region_fit"
        if any(term in lowered for term in ("affected", "population", "group", "people")):
            return "affected_groups_fit"
        if any(term in lowered for term in ("sector", "housing", "transport", "energy")):
            return "selected_sector_fit"
        return "twin_transition_policy_fit"

    async def _validate_custom_hazard(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_ENTRY_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, HAZARD_ENTRY_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        if normalize(exact_label or message) == normalize("Go back to list of hazards"):
            session.pending_hazard = None
            session.phase = "hazards"
            return self._hazards_step(session_id, session)

        reason, evidence = parse_reason_evidence(message)
        if not reason:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason="`Reason:` is required. Evidence URL and evidence file are optional.",
                    rewrite_suggestion="",
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        input_review = await self._validate_input_quality(
            session=session,
            purpose=(
                "a reason and optional evidence explaining why the proposed hazard "
                "is a negative impact or risk created by twin-transition policies "
                "for the selected country, region, and sector"
            ),
            fields=self._reason_evidence_quality_fields(reason, evidence),
        )
        if input_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
        if not input_review["valid"]:
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=str(input_review["reason"]),
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=str(input_review["reason"]),
                    rewrite_suggestion="",
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        plain_rejection_reason = self._plain_custom_hazard_rejection_reason(
            session,
            session.pending_hazard or "",
            reason,
            evidence or "",
        )
        if plain_rejection_reason:
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=plain_rejection_reason,
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=plain_rejection_reason,
                    rewrite_suggestion="",
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        sector_mismatch_reason = self._custom_hazard_sector_mismatch_reason(
            session,
            session.pending_hazard or "",
            reason,
            evidence,
        )
        if sector_mismatch_reason:
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=sector_mismatch_reason,
                    dimension="selected_sector_fit",
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=sector_mismatch_reason,
                    rewrite_suggestion=self._custom_hazard_sector_rewrite_suggestion(
                        session,
                        session.pending_hazard or "",
                        reason,
                        evidence or "",
                    ),
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        validation = await self._validate_hazard_against_stats(
            session=session,
            hazard=session.pending_hazard or "",
            reason=reason,
            evidence=evidence or "",
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if not validation["valid"]:
            self._discard_temporary_evidence(session, evidence)
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=session.pending_hazard or "New hazard",
                    reason=str(validation["reason"]),
                    evidence=evidence or "",
                )
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message(
                    "hazard_validation_failed.md",
                    sector=session.sector,
                    reason=validation["reason"],
                    rewrite_suggestion="",
                ),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        hazard = session.pending_hazard or "New hazard"
        context_review = await self._review_custom_hazard_context(
            session,
            hazard,
            reason,
            evidence or "",
        )
        if context_review is None:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("hazard_validation_unavailable.md"),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )
        if context_review["status"] == "clarification":
            session.pending_hazard_reason = reason
            session.pending_hazard_evidence = evidence or ""
            return self._hazard_clarification_step(
                session_id,
                session,
                hazard,
                str(context_review["question"]),
            )
        if not context_review["valid"]:
            self._discard_temporary_evidence(session, evidence)
            if isinstance(session.custom_hazard, dict):
                return self._custom_hazard_validation_failed_response(
                    session_id,
                    session,
                    hazard=hazard,
                    reason=str(context_review["reason"]),
                    evidence=evidence or "",
                )
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

        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            state["raw_text"] = hazard
            state["normalized_text"] = normalize_for_match(hazard)
            state["reason"] = reason
            state["evidence"] = evidence or ""
            state["message"] = "Reason and optional evidence were accepted for grounding validation."
            session.accepted_custom_hazard_reason = reason
            session.accepted_custom_hazard_evidence = evidence or "Not provided"
            session.phase = "custom_hazard_dimension_check"
            return await self._run_custom_hazard_dimension_check(session_id, session)

        return await self._finalize_valid_custom_hazard(
            session_id,
            session,
            hazard,
            reason,
            evidence or "",
        )

    @staticmethod
    def _reason_evidence_quality_fields(
        reason: str, evidence: str | None
    ) -> dict[str, str]:
        fields = {"Reason": reason}
        if evidence and evidence.strip():
            fields["Evidence URL or file content"] = evidence
        return fields

    async def _validate_hazard_against_stats(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        if self._has_user_supplied_evidence(evidence):
            contradiction_check = await self._validate_user_evidence_against_core_kb(
                session=session,
                claim_type="hazard",
                claim_text=self._hazard_evidence_claim_text(session, hazard, reason),
                evidence=evidence,
            )
            verdict = str(contradiction_check.get("verdict") or "").upper()
            if verdict != "VALID":
                return {
                    "valid": False,
                    "reason": self._evidence_contradiction_reason(contradiction_check),
                }

        existing_hazards = "\n".join(f"- {item}" for item in (session.hazards or []))
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"{hazard} {reason} {evidence} {existing_hazards}",
        )
        user_evidence_context = await self._temporary_evidence_context(session)
        context = render_prompt_template(
            "llm/custom_hazard_stats_validation.txt",
            scope_instruction=self._scope_instruction(session),
            twin_transition_hazard_scope_instruction=(
                self._twin_transition_hazard_scope_instruction()
            ),
            sector_context=sector_context,
            user_evidence_context=user_evidence_context
            or "- No readable user evidence excerpts were indexed for this session.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/custom_hazard_stats_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    existing_hazards=existing_hazards
                    or "- No existing hazards were generated.",
                    hazard=hazard,
                    reason=reason,
                    evidence=evidence or "Not provided",
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        return parse_validation_response(response)

    async def _semantic_hazard_duplicate_check(
        self, session: ChatSession, hazard: str
    ) -> dict[str, object] | None:
        existing_hazards = self._same_sector_hazard_names_for_duplicate_check(session)
        if not existing_hazards:
            return {"duplicate": False, "match": "", "reason": "", "duplicates": []}

        context = render_prompt_template(
            "llm/hazard_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/hazard_duplicate_check_user.txt",
                    existing_hazards="\n".join(f"- {item}" for item in existing_hazards),
                    hazard=hazard,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=300,
        )

        if is_llm_unavailable_response(response):
            return None

        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        return parsed

    @classmethod
    def _custom_hazard_sector_mismatch_reason(
        cls,
        session: ChatSession,
        hazard: str,
        reason: str = "",
        evidence: str = "",
    ) -> str | None:
        return custom_hazard_sector_mismatch_reason(
            selected_sector=session.sector,
            hazard=hazard,
            reason=reason,
            evidence=evidence,
        )

    @classmethod
    def _custom_hazard_sector_rewrite_suggestion(
        cls,
        session: ChatSession,
        hazard: str,
        reason: str = "",
        evidence: str = "",
    ) -> str:
        return custom_hazard_sector_rewrite_suggestion(
            selected_sector=session.sector,
            hazard=hazard,
            reason=reason,
            evidence=evidence,
        )

    @classmethod
    def _plain_custom_hazard_rejection_reason(
        cls,
        session: ChatSession,
        hazard: str,
        reason: str = "",
        evidence: str = "",
    ) -> str | None:
        return plain_custom_hazard_rejection_reason(
            selected_sector=session.sector,
            hazard=hazard,
            reason=reason,
            evidence=evidence,
        )

    @staticmethod
    def _sector_signal_scores(text: str) -> dict[str, int]:
        return sector_signal_scores(text)

    async def _review_custom_hazard_input(
        self, session: ChatSession, hazard: str
    ) -> dict[str, object] | None:
        context = render_prompt_template(
            "llm/custom_hazard_input_classifier.txt",
            sector=session.sector or "Not selected",
            country=session.country or "Not selected",
            region=session.region or "Not selected",
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Selected sector: {session.sector or 'Not selected'}\n"
                    f"User input: {hazard}\n\n"
                    "Return exactly ACCEPT, or REJECT followed by one concise reason."
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=450,
        )
        if is_llm_unavailable_response(response):
            return None

        return self._parse_custom_hazard_classifier_response(response)

    async def _review_custom_hazard_context(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> dict[str, object] | None:
        context = render_prompt_template(
            "llm/custom_hazard_context_review.txt",
            scope_instruction=self._scope_instruction(session),
            country=session.country or "Not selected",
            region=session.region or "Not selected",
            sector=session.sector or "Not selected",
        )
        user_content = (
            f"Hazard: {hazard}\n"
            f"Reason: {reason}\n"
            f"Evidence: {evidence or 'Not provided'}\n"
            f"Clarification: {clarification or 'Not provided'}"
        )
        response = await ask_llm_chat(
            context=context,
            messages=[{"role": "user", "content": user_content}],
            temperature=0,
            max_tokens=280,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return None
        status = normalize(str(parsed.get("status") or ""))
        valid = bool(parsed.get("valid"))
        reason_text = re.sub(
            r"\s+",
            " ",
            normalize_markdown_text(str(parsed.get("reason") or "")),
        ).strip("`*_ ")
        question = re.sub(
            r"\s+",
            " ",
            normalize_markdown_text(str(parsed.get("question") or "")),
        ).strip("`*_ ")
        if status == normalize("clarification"):
            return {
                "status": "clarification",
                "valid": False,
                "reason": reason_text or "Clarification is needed.",
                "question": question
                or "Could you clarify which people or households are affected by this hazard?",
            }
        if status == normalize("reject") or not valid:
            return {
                "status": "reject",
                "valid": False,
                "reason": reason_text
                or "The hazard and reason are not clear enough to save for this context.",
                "question": "",
            }
        return {
            "status": "accept",
            "valid": True,
            "reason": reason_text or "The custom hazard is clear enough.",
            "question": "",
        }

    async def _extract_custom_hazard_affected_population_profiles(
        self,
        session: ChatSession,
        hazard: str,
        reason: str,
        evidence: str,
        *,
        clarification: str | None = None,
    ) -> list[dict[str, str]]:
        option_rows = self._target_population_option_rows()
        option_catalogue = "\n".join(
            f"- {row.id} | {row.question}: {row.option}" for row in option_rows
        )
        context = render_prompt_template(
            "llm/custom_hazard_population_extraction.txt",
            scope_instruction=self._scope_instruction(session),
        )
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Country: {session.country or 'Not selected'}\n"
                        f"Region: {session.region or 'Not selected'}\n"
                        f"Sector: {session.sector or 'Not selected'}\n"
                        f"Hazard: {hazard}\n"
                        f"Reason: {reason}\n"
                        f"Evidence: {evidence or 'Not provided'}\n"
                        f"Clarification: {clarification or 'Not provided'}\n\n"
                        "Saved target population options:\n"
                        f"{option_catalogue or '- No saved target population options found.'}"
                    ),
                }
            ],
            temperature=0,
            max_tokens=700,
        )
        profiles = self._parse_hazard_profile_items(response)
        cleaned: list[dict[str, str]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            cleaned.append(
                {
                    **profile,
                    "name": name,
                    "profile": name,
                    "source": "custom_hazard_extraction",
                    "statistical_basis": str(
                        profile.get("statistical_basis")
                        or "Extracted from the validated custom hazard and reason."
                    )[:600],
                }
            )
        matched_profiles = self._attach_target_population_matches_to_profiles(
            cleaned,
            option_rows,
            trust_option_ids=True,
        )
        if matched_profiles:
            return matched_profiles
        return self._profiles_from_target_population_option_ids(
            self._selected_target_population_option_ids(session),
            option_rows,
            reason=reason,
        )

    @staticmethod
    def _profiles_from_target_population_option_ids(
        option_ids: set[str],
        option_rows: list[object],
        *,
        reason: str,
    ) -> list[dict[str, str]]:
        if not option_ids:
            return []
        rows_by_id = {str(row.id): row for row in option_rows}
        profiles: list[dict[str, str]] = []
        for option_id in sorted(option_ids):
            row = rows_by_id.get(option_id)
            if row is None:
                continue
            question = str(row.question or "").strip()
            option = str(row.option or "").strip()
            name = f"{question}: {option}" if question and option else option or question
            if not name:
                continue
            profiles.append(
                {
                    "name": name,
                    "profile": name,
                    "variable_name": question,
                    "explanation": "Matched to a saved affected population group.",
                    "statistical_basis": reason[:600],
                    "source": "custom_hazard_extraction",
                    "target_population_option_ids": [option_id],
                    "target_population_labels": [name],
                }
            )
        return profiles

    def _target_population_option_rows(self) -> list[object]:
        return list(
            self.db.execute(
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
        )

    def _attach_target_population_matches_to_profiles(
        self,
        profiles: list[dict[str, str]],
        option_rows: list[object] | None = None,
        *,
        trust_option_ids: bool = False,
    ) -> list[dict[str, str]]:
        rows = option_rows if option_rows is not None else self._target_population_option_rows()
        if not rows:
            return profiles
        rows_by_id = {str(row.id): row for row in rows}
        allowed_ids = set(rows_by_id)
        for profile in profiles:
            support_text = " ".join(
                str(profile.get(field) or "")
                for field in (
                    "name",
                    "profile",
                    "variable_name",
                    "explanation",
                    "statistical_basis",
                )
            )
            matched_ids = self._coerce_target_population_option_ids(
                profile.get("target_population_option_ids"),
                allowed_ids,
                rows_by_id,
                support_text,
                trust_ids=trust_option_ids,
            )
            metadata = profile.get("metadata")
            if isinstance(metadata, dict):
                matched_ids.update(
                    self._coerce_target_population_option_ids(
                        metadata.get("target_population_option_ids"),
                        allowed_ids,
                        rows_by_id,
                        support_text,
                        trust_ids=trust_option_ids,
                    )
                )
            matched_ids.update(
                self._deterministic_target_population_option_ids(profile, rows)
            )
            ordered_ids = [
                str(row.id)
                for row in rows
                if str(row.id) in matched_ids
            ]
            if not ordered_ids:
                profile["target_population_option_ids"] = []
                profile["target_population_labels"] = []
                continue
            profile["target_population_option_ids"] = ordered_ids
            profile["target_population_labels"] = [
                f"{rows_by_id[option_id].question}: {rows_by_id[option_id].option}"
                for option_id in ordered_ids
                if option_id in rows_by_id
            ]
            metadata = profile.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["target_population_option_ids"] = ordered_ids
            metadata["target_population_labels"] = list(profile["target_population_labels"])
            profile["metadata"] = metadata
        return profiles

    @classmethod
    def _coerce_target_population_option_ids(
        cls,
        value: object,
        allowed_ids: set[str],
        rows_by_id: dict[str, object],
        support_text: str,
        *,
        trust_ids: bool = False,
    ) -> set[str]:
        if not isinstance(value, list):
            return set()
        ids: set[str] = set()
        for raw_id in value:
            option_id = str(raw_id or "").strip()
            if not option_id:
                continue
            if option_id not in allowed_ids or option_id not in rows_by_id:
                continue
            if trust_ids or cls._target_population_option_is_supported_by_text(
                support_text,
                rows_by_id[option_id],
            ):
                ids.add(option_id)
        return ids

    @staticmethod
    def _parse_custom_hazard_classifier_response(response: str) -> dict[str, object] | None:
        cleaned = str(response or "").strip()
        if not cleaned:
            return None
        first_line, _, rest = cleaned.partition("\n")
        label_match = re.match(r"^\s*(ACCEPT|REJECT|CLARIFICATION)\b\s*:?\s*(.*)$", first_line, re.IGNORECASE)
        label = label_match.group(1).casefold() if label_match else first_line.strip().strip(":").casefold()
        inline_detail = label_match.group(2).strip() if label_match else ""
        if label == "accept":
            return {
                "status": "Valid",
                "valid": True,
                "reason": "The input is within the European green or digital transition hazard scope.",
                "suggestions": [],
            }
        if label == "reject":
            reason = inline_detail
            if not reason:
                reason = rest.strip()
            reason = re.sub(r"\s+", " ", normalize_markdown_text(reason)).strip("`*_ ")
            return {
                "status": "Invalid",
                "valid": False,
                "reason": reason
                or (
                    "Please enter a hazard, risk, vulnerability, unintended consequence, "
                    "or negative impact related to Europe's Green or Digital Transition "
                    "policies in the selected sector."
                ),
                "suggestions": [],
            }
        if label.startswith("clarification"):
            question = inline_detail
            if not question:
                question = rest.strip()
            if not question:
                question = (
                    "Could you clarify how this relates to hazards of Europe's "
                    "Green or Digital Transition policies?"
                )
            return {
                "status": "Ambiguous",
                "valid": False,
                "reason": question,
                "suggestions": [],
            }
        return None

    async def _validate_input_quality(
        self,
        session: ChatSession,
        purpose: str,
        fields: dict[str, str],
    ) -> dict[str, str | bool] | None:
        cleaned_fields = {
            label: value.strip()
            for label, value in fields.items()
            if isinstance(value, str) and value.strip()
        }
        field_text = "\n".join(f"{label}: {value}" for label, value in cleaned_fields.items())
        context = render_prompt_template(
            "llm/input_quality_validation.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/input_quality_validation_user.txt",
                    purpose=purpose,
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=(
                        session.selected_hazard
                        or session.pending_hazard
                        or "Not provided"
                    ),
                    field_text=field_text or "- No text provided",
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_validation_response(response)
        if (
            session.validation_mode == "easy"
            and self._fields_are_locally_meaningful(cleaned_fields)
            and (
                not parsed.get("valid")
                or str(parsed.get("reason") or "").strip()
                == "The text is meaningful and specific enough."
            )
            and not self._is_hard_validation_rejection(str(parsed.get("reason") or ""))
        ):
            return {
                "valid": True,
                "reason": "Easy validation accepted locally meaningful input.",
            }
        if (
            not parsed.get("valid")
            and self._is_style_only_validation_rejection(str(parsed.get("reason") or ""))
            and self._fields_are_locally_meaningful(cleaned_fields)
        ):
            return {
                "valid": True,
                "reason": "The text is understandable despite minor wording issues.",
            }
        if (
            not parsed.get("valid")
            and session.validation_mode == "easy"
            and self._fields_are_locally_meaningful(cleaned_fields)
            and not self._is_hard_validation_rejection(str(parsed.get("reason") or ""))
        ):
            return {
                "valid": True,
                "reason": "Easy validation accepted locally meaningful input.",
            }
        return parsed

    async def _validate_mitigation_measure_only(
        self,
        session: ChatSession,
        mitigation_measure: str,
    ) -> dict[str, object] | None:
        local_review = self._local_mitigation_measure_only_review(
            session,
            mitigation_measure,
        )
        if local_review is not None:
            return local_review

        context = render_prompt_template("llm/mitigation_measure_validation.txt")
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_measure_validation_user.txt",
                    country=session.country or "Not provided",
                    region=session.region or "Not provided",
                    sector=session.sector or "Not provided",
                    hazard=(
                        session.selected_hazard
                        or session.pending_hazard
                        or "Not provided"
                    ),
                    mitigation_measure=mitigation_measure,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=700,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return self._local_mitigation_measure_only_review(
                session,
                mitigation_measure,
                allow_valid=True,
            )
        return self._normalize_mitigation_measure_only_review(parsed)

    def _local_mitigation_measure_only_review(
        self,
        session: ChatSession,
        mitigation_measure: str,
        *,
        allow_valid: bool = False,
    ) -> dict[str, object] | None:
        measure = str(mitigation_measure or "").strip()
        normalized = normalize_for_match(measure)
        if not normalized:
            return self._mitigation_measure_only_review_payload(
                "INVALID",
                "The mitigation measure is empty.",
                policy_quality=False,
                clarification_question="Please provide a concrete mitigation intervention.",
            )
        weak_exact = {
            "improve awareness",
            "government should help",
            "reduce emissions",
            "make transport better",
            "help people",
            "support residents",
            "address the issue",
            "solve the problem",
            "reduce risk",
            "mitigate the hazard",
        }
        if normalized in weak_exact:
            return self._mitigation_measure_only_review_payload(
                "NEEDS_CLARIFICATION",
                "The mitigation measure is too vague to evaluate as a concrete intervention.",
                policy_quality=False,
                clarification_question=(
                    "What concrete intervention, target group, instrument, or delivery mechanism "
                    "does this mitigation measure propose?"
                ),
                suggested_improvement=(
                    "Describe a specific policy instrument, such as targeted grants, infrastructure deployment, "
                    "advisory services, data systems, or standards."
                ),
            )
        hazard = normalize_for_match(session.selected_hazard or session.pending_hazard or "")
        if hazard and normalized == hazard:
            return self._mitigation_measure_only_review_payload(
                "INVALID",
                "The text restates the hazard instead of proposing a mitigation intervention.",
                hazard_fit=False,
                policy_quality=False,
            )
        token_count = len(normalized.split())
        intervention_terms = {
            "grant",
            "grants",
            "subsidy",
            "subsidies",
            "install",
            "deploy",
            "expand",
            "introduce",
            "retrofit",
            "upgrade",
            "build",
            "fund",
            "provide",
            "develop",
            "improve",
            "implement",
            "infrastructure",
            "service",
            "services",
            "programme",
            "program",
            "platform",
            "standards",
            "charging",
            "heat",
            "pump",
            "smart",
            "digital",
            "meter",
            "meters",
            "retrofits",
            "renovation",
            "renovations",
        }
        has_intervention = any(term in normalized.split() for term in intervention_terms)
        if token_count < 2 or (token_count < 5 and not has_intervention):
            return self._mitigation_measure_only_review_payload(
                "NEEDS_CLARIFICATION",
                "The mitigation measure needs slightly more detail.",
                policy_quality=False,
                clarification_question=(
                    "Can you specify the mitigation action or policy intervention?"
                ),
                suggested_improvement=(
                    "Name the intervention or action, such as grants, subsidies, retrofits, smart meters, or infrastructure upgrades."
                ),
            )
        return (
            self._mitigation_measure_only_review_payload(
                "VALID",
                "The mitigation measure is concrete enough for the selected context.",
            )
            if allow_valid
            else None
        )

    @staticmethod
    def _normalize_mitigation_measure_only_review(payload: dict[str, object]) -> dict[str, object]:
        status = str(payload.get("status") or "").strip().upper()
        if status not in {"VALID", "INVALID", "NEEDS_CLARIFICATION"}:
            status = "NEEDS_CLARIFICATION"
        checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        normalized_checks: dict[str, bool] = {}
        for key in (
            "hazard_fit",
            "sector_fit",
            "country_region_fit",
            "twin_transition_fit",
            "policy_quality",
        ):
            raw = checks.get(key) if isinstance(checks, dict) else None
            if isinstance(raw, bool):
                normalized_checks[key] = raw
            elif isinstance(raw, dict):
                normalized_checks[key] = bool(raw.get("valid", status == "VALID"))
            else:
                normalized_checks[key] = status == "VALID"
        return {
            "status": status,
            "summary": str(payload.get("summary") or "").strip(),
            "checks": normalized_checks,
            "clarification_question": str(payload.get("clarification_question") or "").strip(),
            "suggested_improvement": str(payload.get("suggested_improvement") or "").strip(),
        }

    @classmethod
    def _mitigation_measure_only_review_payload(
        cls,
        status: str,
        summary: str,
        *,
        hazard_fit: bool = True,
        sector_fit: bool = True,
        country_region_fit: bool = True,
        twin_transition_fit: bool = True,
        policy_quality: bool = True,
        clarification_question: str = "",
        suggested_improvement: str = "",
    ) -> dict[str, object]:
        return cls._normalize_mitigation_measure_only_review(
            {
                "status": status,
                "summary": summary,
                "checks": {
                    "hazard_fit": hazard_fit,
                    "sector_fit": sector_fit,
                    "country_region_fit": country_region_fit,
                    "twin_transition_fit": twin_transition_fit,
                    "policy_quality": policy_quality,
                },
                "clarification_question": clarification_question,
                "suggested_improvement": suggested_improvement,
            }
        )

    async def _validate_clarification_answer_quality(
        self, session: ChatSession, answer: str
    ) -> dict[str, str | bool] | None:
        context = render_prompt_template(
            "llm/clarification_answer_quality.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/clarification_answer_quality_user.txt",
                    answer=answer,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=350,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_validation_response(response)
        if (
            not parsed.get("valid")
            and session.validation_mode == "easy"
            and self._fields_are_locally_meaningful({"Clarification answer": answer})
            and not self._is_hard_validation_rejection(str(parsed.get("reason") or ""))
        ):
            return {
                "valid": True,
                "reason": "Easy validation accepted locally meaningful clarification.",
            }
        if (
            not parsed.get("valid")
            and self._is_style_only_validation_rejection(str(parsed.get("reason") or ""))
            and self._fields_are_locally_meaningful({"Clarification answer": answer})
        ):
            return {
                "valid": True,
                "reason": "The clarification answer is understandable despite minor wording issues.",
            }
        return parsed

    async def _validate_profile_names_input(
        self, session: ChatSession, profiles: list[str]
    ) -> dict[str, str | bool] | None:
        context = render_prompt_template(
            "llm/profile_names_validation.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/profile_names_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "Not provided",
                    profiles="\n".join(f"- {profile}" for profile in profiles),
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_validation_response(response)
        if (
            not parsed.get("valid")
            and session.validation_mode == "easy"
            and self._fields_are_locally_meaningful(
                {f"Profile {index}": profile for index, profile in enumerate(profiles)}
            )
            and not self._is_hard_validation_rejection(str(parsed.get("reason") or ""))
        ):
            return {
                "valid": True,
                "reason": "Easy validation accepted locally meaningful profile names.",
            }
        return parsed

    async def _semantic_dg_duplicate_check(
        self, session: ChatSession, dgs: list[str]
    ) -> dict[str, object] | None:
        existing_context = self._format_selected_hazard_profiles_for_duplicate_check(session)
        context = render_prompt_template(
            "llm/socio_demographic_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/socio_demographic_duplicate_check_user.txt",
                    existing_context=existing_context,
                    proposed_profiles="\n".join(f"- {item}" for item in dgs),
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=500,
        )

        if is_llm_unavailable_response(response):
            return None

        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        return parsed

    async def _validate_dgs_context_against_stats(
        self,
        session: ChatSession,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} "
                f"{self._format_pending_additional_dgs(session)} "
                f"{reason} {evidence}"
            ),
        )
        context = render_prompt_template(
            "llm/dgs_stats_validation.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/dgs_stats_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    pending_profiles=self._format_pending_additional_dgs(session),
                    confirmed_profiles=format_all_dgs(session),
                    reason=reason or "Not provided",
                    evidence=evidence or "Not provided",
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.5,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        parsed = parse_validation_response(response)
        if (
            not parsed.get("valid")
            and session.validation_mode == "easy"
            and self._fields_are_locally_meaningful(
                self._reason_evidence_quality_fields(reason, evidence)
            )
            and not self._is_hard_validation_rejection(str(parsed.get("reason") or ""))
        ):
            return {
                "valid": True,
                "reason": "Easy validation accepted locally meaningful reason and evidence.",
            }
        return parsed

    async def _validate_frozen_mitigation_inputs(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> ChatResponse:
        evaluated_inputs = {
            "measure_description": mitigation_measure.strip(),
            "justification": reason.strip(),
            "evidence": self._normalized_mitigation_evidence(evidence_text),
        }
        mitigation_measure = evaluated_inputs["measure_description"]
        reason = evaluated_inputs["justification"]
        evidence_text = evaluated_inputs["evidence"]
        session.mitigation_frozen_inputs = evaluated_inputs
        session.phase = "mitigation_reason"
        evidence_branch = self._has_user_supplied_evidence(evidence_text)
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text

        validation = await self._validate_mitigation_against_stats(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            evidence=evidence_text,
        )

        if validation is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        validation["evaluated_inputs"] = evaluated_inputs.copy()
        outcome = str(validation.get("outcome") or ("PASS" if validation["valid"] else "REJECT"))
        if outcome == "REJECT":
            self._discard_temporary_evidence(session, evidence_text)
            session.pending_mitigation_evidence = None
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_rejected.md",
                    reason=self._mitigation_outcome_reason(validation, "REJECT"),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
                validation_details=self._grounding_validation_details(session, validation),
            )
        if outcome == "ABSTAIN":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_abstained.md",
                    reason=self._mitigation_outcome_reason(validation, "ABSTAIN"),
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                input_values={
                    "reason": reason,
                    "evidence_url": self._evidence_url(evidence_text),
                },
                error=False,
                validation_details=self._grounding_validation_details(session, validation),
            )

        synthesis = await self._grounded_mitigation_synthesis(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            validation=validation,
        )
        if synthesis is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if evidence_branch:
            self._promote_temporary_evidence(
                session,
                target_scope=VALIDATED_EVIDENCE_SCOPE,
                provenance="validated_user_evidence",
            )
            await self._admit_inline_evidence_to_quarantine(
                session,
                evidence_text,
                validation,
            )
        session.mitigation_measure = mitigation_measure
        session.mitigation_reason = reason
        session.mitigation_validation = validation
        session.mitigation_grounded_synthesis = synthesis
        session.pending_mitigation_measure = None
        self._clear_mitigation_clarity_state(session)
        return await self._ensure_mitigation_target_population_from_inputs(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
        )

    async def _validate_mitigation_against_stats(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence: str = "",
    ) -> dict[str, object] | None:
        has_evidence = self._has_user_supplied_evidence(evidence)
        raw_support_context = (
            await self._mitigation_evidence_context(session, mitigation_measure, reason, evidence)
            if has_evidence
            else await self._mitigation_main_knowledge_context(session, mitigation_measure, reason)
        )
        support_label = (
            self.mitigation_support_label_user_evidence
            if has_evidence
            else self.mitigation_support_label_curated_knowledge_base
        )
        if has_evidence:
            contradiction_check = await self._validate_user_evidence_against_core_kb(
                session=session,
                claim_type="mitigation",
                claim_text=self._mitigation_evidence_claim_text(
                    session,
                    mitigation_measure,
                    reason,
                ),
                evidence=evidence,
            )
            verdict = str(contradiction_check.get("verdict") or "").upper()
            if verdict != "VALID":
                outcome = "REJECT" if verdict == "INVALID" else "ABSTAIN"
                return {
                    "valid": False,
                    "outcome": outcome,
                    "reason": self._evidence_contradiction_reason(contradiction_check),
                    "dimensions": {},
                    "rubric_coverage": 0.0,
                    "retrieval_support": 0.0,
                    "verdict_stability": 0.0,
                    "sample_count": 1,
                    "confidence_score": int(
                        round(float(contradiction_check.get("confidence") or 0.0) * 100)
                    ),
                    "support_context": "",
                    "support_label": support_label,
                    "evidence_contradiction": contradiction_check,
                }
        support_context = self._floor_filtered_support_context(
            raw_support_context,
            session.validation_mode,
        )
        valid_citation_ids = set(self._support_citation_scores(support_context))
        clarification_block = self._mitigation_clarification_history_block(session)
        context = render_prompt_template("llm/mitigation_groundedness_validation.txt")
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_groundedness_validation_user.txt",
                    support_label=support_label,
                    country=session.country,
                    sector=session.sector,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    clarification_history=clarification_block,
                    evidence=evidence or "Not provided",
                    support_context=support_context
                    or "- No relevant support excerpts were found.",
                ),
            }
        ]

        async def sample_validator(sample_count: int) -> list[dict[str, object]]:
            responses = await asyncio.gather(
                *[
                    ask_llm_chat(
                        context=context,
                        messages=messages,
                        temperature=self.settings.mitigation_verdict_temperature,
                        max_tokens=700,
                    )
                    for _ in range(max(0, sample_count))
                ]
            )
            if any(is_llm_unavailable_response(response) for response in responses):
                return []
            return [
                self._sanitize_grounding_sample(parsed, valid_citation_ids)
                for response in responses
                if not (parsed := parse_grounded_validation_response(response)).get("error")
            ]

        parsed_samples = await sample_validator(max(1, self.settings.mitigation_verdict_samples))        
        if not parsed_samples:
            return None
        contradiction_dimensions = self._dimensions_with_any_status(
            parsed_samples,
            "CONTRADICTED",
        )
        contradiction_samples = (
            await sample_validator(self.settings.mitigation_contradiction_resamples)
            if contradiction_dimensions
            else []
        )
        parsed = self._majority_grounding_verdict(
            parsed_samples,
            contradiction_samples=contradiction_samples,
            contradiction_dimensions=contradiction_dimensions,
        )
        return self._score_mitigation_grounding(
            parsed,
            support_context=support_context,
            support_label=support_label,
            has_user_evidence=has_evidence,
            validation_mode=session.validation_mode,
        )

    def _sanitize_grounding_sample(
        self,
        sample: dict[str, object],
        valid_citation_ids: set[str],
    ) -> dict[str, object]:
        raw_dimensions = sample.get("dimensions")
        raw_dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
        dimensions: dict[str, dict[str, object]] = {}
        for name in self.mitigation_grounding_dimensions:
            raw_dimension = raw_dimensions.get(name)
            raw_dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
            status = str(raw_dimension.get("status") or "INSUFFICIENT_INFO").upper()
            raw_citation_ids = raw_dimension.get("citation_ids")
            raw_citation_ids = raw_citation_ids if isinstance(raw_citation_ids, list) else []
            citation_ids = sorted({
                citation_id.upper()
                for citation_id in raw_citation_ids
                if isinstance(citation_id, str)
                and citation_id.upper() in valid_citation_ids
            })
            citation_required = not (
                name in self.mitigation_input_supported_dimensions
                and status == "SUPPORTED"
            )
            if status in {"SUPPORTED", "CONTRADICTED"} and citation_required and not citation_ids:
                status = "INSUFFICIENT_INFO"
            dimensions[name] = {
                "status": status,
                "citation_ids": citation_ids,
                "explanation": str(raw_dimension.get("explanation") or "").strip(),
            }
        return {
            "dimensions": dimensions,
            "reason": str(sample.get("reason") or "").strip(),
        }

    def _dimensions_with_any_status(
        self,
        samples: list[dict[str, object]],
        status: str,
    ) -> set[str]:
        dimensions: set[str] = set()
        for sample in samples:
            sample_dimensions = sample.get("dimensions")
            if not isinstance(sample_dimensions, dict):
                continue
            for name in self.mitigation_grounding_dimensions:
                dimension = sample_dimensions.get(name)
                if isinstance(dimension, dict) and dimension.get("status") == status:
                    dimensions.add(name)
        return dimensions

    def _floor_filtered_support_context(
        self,
        support_context: str,
        validation_mode: str = "strict",
    ) -> str:
        floor = self._mitigation_support_score_floor(validation_mode)
        lines: list[str] = []
        keep_current_excerpt = False
        for line in support_context.splitlines():
            if re.match(r"^\s*-\s*\[S\d+\]", line, flags=re.IGNORECASE):
                score_match = re.search(r", score (-?\d+(?:\.\d+)?)[: ,]", line)
                keep_current_excerpt = bool(
                    score_match and float(score_match.group(1)) >= floor
                )
            if keep_current_excerpt:
                lines.append(line)
        return "\n".join(lines)

    def _majority_grounding_verdict(
        self,
        samples: list[dict[str, object]],
        *,
        contradiction_samples: list[dict[str, object]] | None = None,
        contradiction_dimensions: set[str] | None = None,
    ) -> dict[str, object]:
        dimensions: dict[str, dict[str, object]] = {}
        stability_values: list[float] = []
        contradiction_samples = contradiction_samples or []
        contradiction_dimensions = contradiction_dimensions or set()
        for name in self.mitigation_grounding_dimensions:
            candidates: list[dict[str, object]] = []
            dimension_samples = [
                *samples,
                *(contradiction_samples if name in contradiction_dimensions else []),
            ]
            for sample in dimension_samples:
                sample_dimensions = sample.get("dimensions")
                if not isinstance(sample_dimensions, dict):
                    continue
                dimension = sample_dimensions.get(name)
                if isinstance(dimension, dict):
                    candidates.append(dimension)
            status_counts: dict[str, int] = {}
            for candidate in candidates:
                status = str(candidate.get("status") or "INSUFFICIENT_INFO")
                status_counts[status] = status_counts.get(status, 0) + 1
            total_samples = max(1, len(candidates))
            contradiction_fraction = status_counts.get("CONTRADICTED", 0) / total_samples
            if (
                status_counts.get("CONTRADICTED", 0)
                and contradiction_fraction
                >= self.settings.mitigation_contradiction_confirmation_fraction
            ):
                winning_status = "CONTRADICTED"
            else:
                supported_count = status_counts.get("SUPPORTED", 0)
                insufficient_count = (
                    status_counts.get("INSUFFICIENT_INFO", 0)
                    + status_counts.get("CONTRADICTED", 0)
                )
                winning_status = (
                    "SUPPORTED"
                    if supported_count > insufficient_count
                    else "INSUFFICIENT_INFO"
                )
            winning = [
                candidate
                for candidate in candidates
                if candidate.get("status") == winning_status
            ]
            unconfirmed_contradiction = (
                winning_status == "INSUFFICIENT_INFO"
                and not winning
                and status_counts.get("CONTRADICTED", 0) > 0
            )
            winning_count = status_counts.get(winning_status, 0)
            if winning_status == "INSUFFICIENT_INFO":
                winning_count += status_counts.get("CONTRADICTED", 0)
            stability_values.append(winning_count / total_samples)
            citation_ids = sorted({
                citation_id
                for candidate in winning
                for citation_id in candidate.get("citation_ids", [])
                if isinstance(citation_id, str)
            })
            dimensions[name] = {
                "status": winning_status,
                "citation_ids": citation_ids,
                "explanation": next(
                    (
                        str(candidate.get("explanation") or "")
                        for candidate in winning
                        if candidate.get("explanation")
                    ),
                    (
                        "A possible contradiction was sampled but did not meet the "
                        "confirmation threshold."
                        if unconfirmed_contradiction
                        else ""
                    ),
                ),
            }
        reasons = [str(sample.get("reason") or "").strip() for sample in samples]
        return {
            "dimensions": dimensions,
            "reason": next((reason for reason in reasons if reason), ""),
            "verdict_stability": (
                sum(stability_values) / len(stability_values) if stability_values else 0.0
            ),
            "sample_count": len(samples) + len(contradiction_samples),
        }

    def _score_mitigation_grounding(
        self,
        parsed: dict[str, object],
        *,
        support_context: str,
        support_label: str,
        has_user_evidence: bool | None = None,
        validation_mode: str = "strict",
    ) -> dict[str, object]:
        if has_user_evidence is None:
            has_user_evidence = support_label == self.mitigation_support_label_user_evidence
        citation_scores = self._support_citation_scores(support_context)
        support_score_floor = self._mitigation_support_score_floor(validation_mode)
        raw_dimensions = parsed.get("dimensions")
        raw_dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
        dimensions, supported_scores = self._scored_mitigation_dimensions(
            raw_dimensions,
            citation_scores,
            has_user_evidence,
            support_score_floor,
        )

        applicable_dimensions = {
            name: dimension
            for name, dimension in dimensions.items()
            if dimension["status"] != "NOT_APPLICABLE"
        }
        critical_dimensions = {
            name: dimensions[name]
            for name in self.mitigation_critical_grounding_dimensions
            if name in dimensions
        }
        critical_statuses = [
            str(dimension["status"]) for dimension in critical_dimensions.values()
        ]
        supported_count = critical_statuses.count("SUPPORTED")
        coverage = supported_count / len(critical_dimensions) if critical_dimensions else 0.0
        has_contradiction = any(
            dimension["status"] == "CONTRADICTED"
            for dimension in applicable_dimensions.values()
        )
        minimum_supported = self._minimum_supported_mitigation_dimensions(
            len(critical_dimensions),
            validation_mode,
        )
        all_critical_supported = bool(critical_dimensions) and supported_count >= minimum_supported
        if has_contradiction:
            outcome = "REJECT"
        elif not all_critical_supported:
            outcome = "ABSTAIN"
        else:
            outcome = "PASS"
        retrieval_support = (
            sum(min(score, 1.0) for score in supported_scores) / len(supported_scores)
            if supported_scores
            else 0.0
        )
        verdict_stability = float(parsed.get("verdict_stability") or 0.0)
        confidence_score = round(
            100 * ((0.6 * coverage) + (0.25 * retrieval_support) + (0.15 * verdict_stability))
        )

        reason = self._mitigation_grounding_reason(
            str(parsed.get("reason") or "").strip(),
            outcome,
            applicable_dimensions,
            critical_dimensions,
        )

        return {
            "valid": outcome == "PASS",
            "outcome": outcome,
            "reason": reason or "The grounded validation rubric was incomplete.",
            "dimensions": dimensions,
            "rubric_coverage": round(coverage, 4),
            "retrieval_support": round(retrieval_support, 4),
            "verdict_stability": round(verdict_stability, 4),
            "sample_count": int(parsed.get("sample_count") or 1),
            "confidence_score": confidence_score,
            "support_context": support_context,
            "support_label": support_label,
        }

    def _scored_mitigation_dimensions(
        self,
        raw_dimensions: dict[object, object],
        citation_scores: dict[str, float],
        has_user_evidence: bool,
        support_score_floor: float | None = None,
    ) -> tuple[dict[str, dict[str, object]], list[float]]:
        if support_score_floor is None:
            support_score_floor = self.settings.mitigation_support_score_floor
        dimensions: dict[str, dict[str, object]] = {}
        supported_scores: list[float] = []
        for name in self.mitigation_grounding_dimensions:
            raw_dimension = raw_dimensions.get(name)
            raw_dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
            status = str(raw_dimension.get("status") or "INSUFFICIENT_INFO").upper()
            citation_ids = raw_dimension.get("citation_ids")
            citation_ids = citation_ids if isinstance(citation_ids, list) else []
            valid_scores = [
                citation_scores[citation_id]
                for citation_id in citation_ids
                if isinstance(citation_id, str)
                and citation_id in citation_scores
                and citation_scores[citation_id] >= support_score_floor
            ]
            citation_optional = (
                name in self.mitigation_input_supported_dimensions
                and status == "SUPPORTED"
            )
            if status == "SUPPORTED" and not valid_scores and not citation_optional:
                status = "INSUFFICIENT_INFO"
            if status == "SUPPORTED" and valid_scores:
                supported_scores.append(max(valid_scores))
            dimensions[name] = {
                "status": status,
                "citation_ids": citation_ids,
                "support_score": round(max(valid_scores), 4) if valid_scores else None,
                "explanation": str(raw_dimension.get("explanation") or "").strip(),
            }
        return dimensions, supported_scores

    def _mitigation_grounding_reason(
        self,
        reason: str,
        outcome: str,
        applicable_dimensions: dict[str, dict[str, object]],
        critical_dimensions: dict[str, dict[str, object]],
    ) -> str:
        if outcome == "PASS":
            unresolved_cautions = self._dimension_names_with_status(
                applicable_dimensions,
                "INSUFFICIENT_INFO",
                exclude=set(self.mitigation_critical_grounding_dimensions),
            )
            if not unresolved_cautions:
                return reason
            caution = (
                "Proceed with caution because the authoritative corpus did not resolve: "
                + ", ".join(unresolved_cautions)
                + "."
            )
            return f"{reason} {caution}".strip()

        reason_parts: list[str] = []
        contradicted = self._dimension_names_with_status(
            applicable_dimensions,
            "CONTRADICTED",
        )
        if outcome == "REJECT":
            reason_parts.append(
                "The authoritative corpus actively conflicts with the mitigation measure."
            )
            reason_parts.append("Contradicted dimensions: " + ", ".join(contradicted) + ".")
            return " ".join(reason_parts)

        reason_parts.append(
            "The authoritative corpus does not cover every critical dimension needed "
            "to validate this mitigation measure."
        )
        insufficient = self._dimension_names_with_status(
            critical_dimensions,
            "INSUFFICIENT_INFO",
        )
        if insufficient:
            reason_parts.append(
                "Insufficiently supported dimensions: " + ", ".join(insufficient) + "."
            )
        return " ".join(reason_parts)

    def _mitigation_outcome_reason(
        self,
        validation: dict[str, object],
        outcome: str,
    ) -> str:
        dimensions = validation.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, dict) else {}
        support_context = str(validation.get("support_context") or "")
        support_label = str(validation.get("support_label") or "")
        if outcome == "REJECT":
            relevant_names = self._dimension_names_with_status_keys(dimensions, "CONTRADICTED")
            heading = "### Confirmed contradictions"
        else:
            relevant_names = self._dimension_names_with_status_keys(
                dimensions,
                "INSUFFICIENT_INFO",
                include=set(self.mitigation_critical_grounding_dimensions),
            )
            heading = "### Critical coverage gaps"

        lines = [str(validation.get("reason") or "").strip(), "", heading, ""]
        for name in relevant_names:
            dimension = dimensions.get(name)
            dimension = dimension if isinstance(dimension, dict) else {}
            explanation = str(dimension.get("explanation") or "No explanation was provided.")
            citations = [
                str(citation_id)
                for citation_id in dimension.get("citation_ids", [])
                if isinstance(citation_id, str)
            ]
            label = name.replace("_", " ").title()
            citation_text = f" Citations: {', '.join(citations)}." if citations else ""
            lines.append(f"- **{label}:** {explanation}{citation_text}")
            if outcome == "REJECT":
                lines.extend(
                    f"  - `{citation_id}`: {self._support_excerpt(support_context, citation_id)}"
                    for citation_id in citations
                )

        if outcome == "ABSTAIN":
            if support_label == self.mitigation_support_label_user_evidence:
                lines.extend([
                    "",
                    "Add a published source that directly covers the critical gap, or "
                    "revise the justification so the claimed mechanism is clear.",
                ])
            else:
                lines.extend([
                    "",
                    "This measure is beyond the curated knowledge base's current scope. "
                    "Attach a published source that covers the critical gap to proceed.",
                ])
        return "\n".join(lines).strip()

    @staticmethod
    def _dimension_names_with_status_keys(
        dimensions: dict[str, object],
        status: str,
        include: set[str] | None = None,
    ) -> list[str]:
        return [
            name
            for name, dimension in dimensions.items()
            if (include is None or name in include)
            and isinstance(dimension, dict)
            and dimension.get("status") == status
        ]

    @staticmethod
    def _support_excerpt(support_context: str, citation_id: str) -> str:
        marker = f"[{citation_id}]"
        return next(
            (
                line.split(":", 1)[1].strip()
                for line in support_context.splitlines()
                if marker in line and ":" in line
            ),
            "The cited excerpt was not available.",
        )

    @staticmethod
    def _evidence_url(evidence: str | None) -> str:
        if not evidence:
            return ""
        match = re.search(r"^Evidence URL:\s*(.+)$", evidence, flags=re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else ""

    async def _admit_inline_evidence_to_quarantine(
        self,
        session: ChatSession,
        evidence: str,
        validation: dict[str, object],
    ) -> None:
        inline_evidence = self._inline_evidence_content(evidence)
        if not inline_evidence or self._temporary_evidence_document_ids(evidence):
            return
        source_uri = self._evidence_url(evidence) or None
        validated_at = datetime.now(timezone.utc).isoformat()
        title = (
            f"[validated_user_evidence; validated_at={validated_at}; "
            f"session={session.session_key}; outcome={validation.get('outcome')}] "
            f"{source_uri or 'Inline user evidence'}"
        )
        try:
            await KnowledgeBaseService(
                self.db,
                self.user_id,
                scope=VALIDATED_EVIDENCE_SCOPE,
                session_key=session.session_key,
                country_id=session.country_id,
                region_id=session.region_id,
                sector_id=session.sector_id,
            ).ingest_text(
                inline_evidence,
                title[:255],
                "validated_user_evidence",
                source_uri,
            )
        except Exception:
            logger.exception("Failed to admit validated inline evidence to quarantine")

    @staticmethod
    def _dimension_names_with_status(
        dimensions: dict[str, dict[str, object]],
        status: str,
        exclude: set[str] | None = None,
    ) -> list[str]:
        excluded = exclude or set()
        return [
            name.replace("_", " ")
            for name, dimension in dimensions.items()
            if name not in excluded and dimension["status"] == status
        ]

    @staticmethod
    def _support_citation_scores(support_context: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for citation_id, score in re.findall(
            r"\[(S\d+)\][^\n]*?, score (-?\d+(?:\.\d+)?)[: ,]",
            support_context,
            flags=re.IGNORECASE,
        ):
            scores[citation_id.upper()] = float(score)
        return scores

    async def _grounded_mitigation_synthesis(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        validation: dict[str, object],
    ) -> str | None:
        support_context = str(validation.get("support_context") or "")
        support_label = str(validation.get("support_label") or "the authoritative corpus")
        context = render_prompt_template(
            "llm/grounded_mitigation_synthesis.txt",
            support_label=support_label,
            support_context=support_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/grounded_mitigation_synthesis_user.txt",
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    selected_hazard=session.selected_hazard or "Not provided",
                    target_population=self._mitigation_target_population_text(session),
                    affected_groups=format_all_dgs(session),
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=900,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_grounded_claims_response(response)
        if parsed.get("error"):
            return None

        citation_scores = self._support_citation_scores(support_context)
        support_score_floor = self._mitigation_support_score_floor(session.validation_mode)
        allowed_user_fields = {
            "measure_description",
            "justification",
            "selected_hazard",
            "target_population",
            "affected_groups",
        }
        claims = [
            claim
            for claim in parsed.get("claims", [])
            if isinstance(claim, dict)
            and (
                any(
                    citation_id in citation_scores
                    and citation_scores[citation_id] >= support_score_floor
                    for citation_id in claim.get("citation_ids", [])
                )
                or any(field in allowed_user_fields for field in claim.get("user_fields", []))
            )
        ]
        if not claims:
            return None

        entailed_claims = await self._entailed_mitigation_claims(
            session=session,
            mitigation_measure=mitigation_measure,
            reason=reason,
            support_context=support_context,
            claims=claims,
        )
        if not entailed_claims:
            return None

        confidence_score = int(validation.get("confidence_score") or 0)
        evidence_note = (
            "This conclusion is grounded in user-supplied evidence."
            if support_label == self.mitigation_support_label_user_evidence
            else "This conclusion is grounded in the curated main knowledge base."
        )
        claim_lines = []
        for claim in entailed_claims:
            citations = [*claim.get("citation_ids", []), *claim.get("user_fields", [])]
            citation_text = ", ".join(f"`{citation}`" for citation in citations)
            claim_lines.append(f"- {claim['text']} ({citation_text})")
        dimensions = validation.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, dict) else {}
        caution_lines = [
            (
                f"- The authoritative corpus does not address "
                f"**{name.replace('_', ' ')}** for this measure: "
                f"{str(dimension.get('explanation') or 'coverage is insufficient.').strip()}"
            )
            for name, dimension in dimensions.items()
            if name not in self.mitigation_critical_grounding_dimensions
            and isinstance(dimension, dict)
            and dimension.get("status") == "INSUFFICIENT_INFO"
        ]
        caution_block = (
            "\n\n### What to be careful about\n\n" + "\n".join(caution_lines)
            if caution_lines
            else ""
        )
        return (
            "### Grounded conclusion\n\n"
            + "\n".join(claim_lines)
            + caution_block
            + f"\n\n**Grounding confidence:** {confidence_score}/100. {evidence_note}"
        )

    def _mitigation_support_score_floor(self, validation_mode: str = "strict") -> float:
        floor = float(self.settings.mitigation_support_score_floor)
        if self._validation_mode(validation_mode) == "easy":
            return max(0.05, floor * 0.5)
        return floor

    @staticmethod
    def _minimum_supported_mitigation_dimensions(
        critical_dimension_count: int,
        validation_mode: str = "strict",
    ) -> int:
        if critical_dimension_count <= 0:
            return 0
        if str(validation_mode or "").strip().casefold() == "easy":
            return max(1, critical_dimension_count - 1)
        return critical_dimension_count

    async def _entailed_mitigation_claims(
        self,
        *,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        support_context: str,
        claims: list[dict[str, object]],
    ) -> list[dict[str, object]] | None:
        user_fields = {
            "measure_description": mitigation_measure,
            "justification": reason,
            "selected_hazard": session.selected_hazard or "Not provided",
            "target_population": self._mitigation_target_population_text(session),
            "affected_groups": format_all_dgs(session),
        }
        premises = [
            self._claim_entailment_premise(claim, support_context, user_fields)
            for claim in claims
        ]
        nli_verdicts = await self.grounding_models.entail(
            premises,
            [str(claim.get("text") or "") for claim in claims],
        )
        if nli_verdicts is not None:
            return [
                claim
                for claim, verdict in zip(claims, nli_verdicts, strict=True)
                if verdict.get("entailed") is True
            ]

        claim_text = "\n".join(
            f"{index}. {claim['text']} | citations={claim.get('citation_ids', [])} "
            f"| user_fields={claim.get('user_fields', [])}"
            for index, claim in enumerate(claims, start=1)
        )
        context = load_nested_prompt_file("llm/claim_entailment_verifier.txt")
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/claim_entailment_verifier_user.txt",
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    selected_hazard=session.selected_hazard or "Not provided",
                    target_population=self._mitigation_target_population_text(session),
                    affected_groups=format_all_dgs(session),
                    support_context=support_context,
                    claim_text=claim_text,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=700,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_entailment_response(response)
        if parsed.get("error"):
            return None
        entailed_indexes = {
            verdict["claim_index"]
            for verdict in parsed.get("verdicts", [])
            if isinstance(verdict, dict) and verdict.get("entailed") is True
        }
        return [
            claim
            for index, claim in enumerate(claims, start=1)
            if index in entailed_indexes
        ]

    @staticmethod
    def _claim_entailment_premise(
        claim: dict[str, object],
        support_context: str,
        user_fields: dict[str, str],
    ) -> str:
        citation_ids = {
            citation_id
            for citation_id in claim.get("citation_ids", [])
            if isinstance(citation_id, str)
        }
        cited_lines = [
            line
            for line in support_context.splitlines()
            if any(f"[{citation_id}]" in line for citation_id in citation_ids)
        ]
        field_lines = [
            f"{field}: {user_fields[field]}"
            for field in claim.get("user_fields", [])
            if isinstance(field, str) and field in user_fields
        ]
        return "\n".join([*cited_lines, *field_lines])

    async def _validate_user_evidence_against_core_kb(
        self,
        *,
        session: ChatSession,
        claim_type: str,
        claim_text: str,
        evidence: str,
    ) -> dict[str, object]:
        evidence_context = await self._user_evidence_context_for_contradiction_check(
            session,
            evidence,
        )
        return await EvidenceContradictionService(
            self.db,
            self.user_id,
        ).validate_evidence_against_kb(
            claim_type=claim_type,
            claim_text=claim_text,
            evidence_text=evidence,
            l2_evidence_context=evidence_context,
            sector=session.sector or "",
            country=session.country or "",
            region=session.region or "",
        )

    @staticmethod
    def _hazard_evidence_claim_text(
        session: ChatSession,
        hazard: str,
        reason: str,
    ) -> str:
        return (
            f"Claim type: hazard\n"
            f"Sector: {session.sector or 'Not provided'}\n"
            f"Country: {session.country or 'Not provided'}\n"
            f"Region: {session.region or 'Not provided'}\n"
            f"Hazard: {hazard or 'Not provided'}\n"
            f"Reason: {reason or 'Not provided'}"
        )

    def _mitigation_evidence_claim_text(
        self,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> str:
        return (
            f"Claim type: mitigation\n"
            f"Sector: {session.sector or 'Not provided'}\n"
            f"Country: {session.country or 'Not provided'}\n"
            f"Region: {session.region or 'Not provided'}\n"
            f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not provided'}\n"
            f"Affected groups: {format_all_dgs(session) or 'Not provided'}\n"
            f"Target population: {self._mitigation_target_population_text(session)}\n"
            f"Mitigation measure: {mitigation_measure or 'Not provided'}\n"
            f"Reason: {reason or 'Not provided'}"
        )

    @staticmethod
    def _evidence_contradiction_reason(result: dict[str, object]) -> str:
        verdict = str(result.get("verdict") or "NEEDS_CLARIFICATION").upper()
        reason = str(result.get("reason") or "").strip()
        questions = [
            str(item).strip()
            for item in result.get("clarification_questions", [])
            if isinstance(item, str) and item.strip()
        ]
        suffix = ""
        if questions:
            suffix = "\n\nClarification needed:\n" + "\n".join(
                f"- {question}" for question in questions
            )
        if verdict == "INVALID":
            prefix = "User evidence conflicts with the core knowledge base."
        else:
            prefix = "User evidence could not be validated against the core knowledge base."
        return f"{prefix} {reason}".strip() + suffix

    def _local_mitigation_measure_error(self, mitigation_measure: str) -> str | None:
        return local_mitigation_measure_error(mitigation_measure, self._is_invalid_user_text)

    def _local_mitigation_field_error(self, mitigation_measure: str, reason: str) -> str | None:
        return local_mitigation_field_error(
            mitigation_measure,
            reason,
            self._is_invalid_user_text,
        )

    def _local_mitigation_reason_error(self, reason: str) -> str | None:
        return local_mitigation_reason_error(reason, self._is_invalid_user_text)

    def _local_mitigation_duplicate_check(
        self, session: ChatSession, mitigation_measure: str
    ) -> dict[str, object] | None:
        for existing in self._existing_mitigation_records_for_selected_hazard(session):
            if self._mitigations_are_similar(mitigation_measure, existing.measure):
                return {
                    "duplicate": True,
                    "match": existing.measure,
                    "match_id": existing.id,
                    "reason": "The proposed measure is the same as, or very similar to, an existing mitigation measure for this hazard.",
                    "duplicates": [],
                }
        return None

    @classmethod
    def _mitigations_are_similar(cls, left: str, right: str) -> bool:
        return mitigations_are_similar(left, right)

    async def _semantic_mitigation_duplicate_check(
        self, session: ChatSession, mitigation_measure: str
    ) -> dict[str, object] | None:
        existing_measures = self._existing_mitigation_measures_for_selected_hazard(session)
        if not existing_measures:
            return {"duplicate": False, "match": "", "reason": "", "duplicates": []}

        context = render_prompt_template(
            "llm/mitigation_duplicate_check.txt",
            scope_instruction=self._scope_instruction(session),
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_duplicate_check_user.txt",
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    existing_measures="\n".join(f"- {item}" for item in existing_measures),
                    mitigation_measure=mitigation_measure,
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=350,
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_duplicate_check_response(response)
        if parsed.get("error"):
            return None
        return parsed

    @staticmethod
    def _format_mitigation_duplicate_reason(duplicate_check: dict[str, object]) -> str:
        match = str(duplicate_check.get("match") or "").strip()
        reason = str(duplicate_check.get("reason") or "").strip()
        if match and reason:
            return f"This mitigation measure appears to duplicate **{match}**: {reason}"
        if match:
            return f"This mitigation measure appears to duplicate **{match}**."
        return reason or "This mitigation measure appears to duplicate an existing measure for this hazard."

    def _duplicate_mitigation_match_id(
        self, session: ChatSession, duplicate_check: dict[str, object]
    ) -> int | None:
        try:
            match_id = int(duplicate_check.get("match_id"))
        except (TypeError, ValueError):
            match_id = None
        if match_id is not None:
            return match_id
        record = self._mitigation_record_for_match(session, str(duplicate_check.get("match") or ""))
        return record.id if record else None

    async def _validate_evaluation_answer_against_stats(
        self,
        session: ChatSession,
        question: dict[str, str | int],
        score: int,
        reason: str,
        evidence: str,
    ) -> dict[str, str | bool] | None:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} {session.mitigation_measure or ''} "
                f"{question['question']} {reason} {evidence}"
            ),
        )
        knowledge_context = await self._mitigation_knowledge_context(
            session,
            session.mitigation_measure or "",
            f"{question['question']} {reason} {evidence}",
        )
        context = render_prompt_template(
            "llm/evaluation_answer_validation.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
            knowledge_context=knowledge_context
            or "- No relevant knowledge-base excerpts were found.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/evaluation_answer_validation_user.txt",
                    sector=session.sector,
                    country=session.country,
                    region=session.region,
                    selected_hazard=session.selected_hazard or "No selected hazard",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    mitigation_reason=session.mitigation_reason or "Not provided",
                    question_category=question["category"],
                    question=question["question"],
                    score=score,
                    reason=reason or "Not provided",
                    evidence=evidence or "Not provided",
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=700,
        )

        if is_llm_unavailable_response(response):
            return None

        return parse_validation_response(response)
