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
    MitigationMeasureExample,
    MitigationMeasurePolicy,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
    QuestionOption,
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
from app.services.chat_json import extract_json_array as extract_json_array_text
from app.services.chat_options import (
    ADD_DGS_OPTIONS,
    DG_REASON_EVIDENCE_OPTIONS,
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
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.evidence_contradiction_service import EvidenceContradictionService
from app.services.grounding_models import GroundingModelService
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.hazard_ranking_service import HazardRankingService, slugify_hazard
from app.services.message_renderer import markdown_to_html, render_message
from app.services.profile_metadata import compact_profile_metadata
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.sector_prompt_rag import (
    SectorPromptRagService,
    section_five_primary_data,
    strip_rule_lines,
)

logger = logging.getLogger(__name__)

class ChatMitigationCreationMixin:
    async def _handle_mitigation_clarity_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if not message.strip():
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message="Please answer the clarification questions so I can freeze the mitigation inputs.",
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )
        if self._is_invalid_user_text(message):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=self._invalid_text_message(),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )

        if session.pending_mitigation_clarity_dimension in {
            "target_population",
            "target_population_additional",
        }:
            input_review = {
                "valid": len(compact_for_match(message)) >= 3,
                "reason": "Please describe at least one target group in words.",
            }
        else:
            input_review = await self._validate_clarification_answer_quality(session, message)
        if input_review is None:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )
        if not input_review.get("valid"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=str(
                        input_review.get("reason")
                        or "Please answer with clear, meaningful text."
                    ),
                ),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=True,
            )

        mitigation_measure = session.pending_mitigation_measure or ""
        reason = session.pending_mitigation_reason or ""
        evidence_text = session.pending_mitigation_evidence or ""
        if not mitigation_measure or not reason:
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter the mitigation reason again so I can restart the clarity check.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if session.pending_mitigation_clarity_dimension in {
            "target_population",
            "target_population_additional",
        }:
            matched_labels = await self._match_mitigation_target_population_answer(message)
            if not matched_labels:
                if (
                    session.pending_mitigation_clarity_dimension
                    == "target_population_additional"
                ):
                    session.pending_mitigation_clarity_dimension = None
                    return self._mitigation_target_population_review_step(
                        session_id,
                        session,
                        mitigation_measure,
                        reason,
                        evidence_text,
                        error_reason=(
                            "No valid target population group found. Choose **Continue** "
                            "to proceed with the current groups, or **Add more target "
                            "population** to try again."
                        ),
                    )
                return self._mitigation_target_population_clarification_step(
                    session_id,
                    session,
                    mitigation_measure,
                    reason,
                    evidence_text,
                    additional=session.pending_mitigation_clarity_dimension
                    == "target_population_additional",
                    error_reason=(
                        "No valid target population group found. Please try again."
                    ),
                )
            session.mitigation_target_population = self._merge_target_population_labels(
                session.mitigation_target_population or [],
                matched_labels,
            )
            self._append_mitigation_clarification_message(
                session,
                "user",
                "Target population answer: " + message.strip(),
            )
            session.pending_mitigation_clarity_dimension = None
            return self._mitigation_target_population_review_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
            )

        self._append_mitigation_clarification_message(session, "user", message)
        mitigation_measure, reason, evidence_text = self._merge_mitigation_clarification(
            mitigation_measure,
            reason,
            evidence_text,
            message,
            session.pending_mitigation_clarity_dimension,
        )
        clarity_response = await self._run_mitigation_clarity_track(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer=message,
        )
        if clarity_response is not None:
            return clarity_response

        frozen_inputs = session.mitigation_frozen_inputs or {}
        return await self._validate_frozen_mitigation_inputs(
            session_id,
            session,
            frozen_inputs.get("measure_description") or mitigation_measure,
            frozen_inputs.get("justification") or reason,
            frozen_inputs.get("evidence") or evidence_text,
        )

    @classmethod
    def _merge_mitigation_clarification(
        cls,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str,
        clarity_dimension: str | None = None,
    ) -> tuple[str, str, str]:
        answer = clarification_answer.strip()
        if not answer:
            return mitigation_measure, reason, evidence_text
        fields = cls._clarification_fields(answer)
        if fields["measure"]:
            mitigation_measure = fields["measure"]
        if fields["justification"]:
            reason = f"{reason}\nClarification: {fields['justification']}".strip()
        if fields["evidence"]:
            evidence_text = f"{evidence_text}\n{fields['evidence']}".strip()
        if not any(fields.values()):
            mitigation_measure, reason, evidence_text = (
                cls._merge_unlabelled_mitigation_clarification(
                    mitigation_measure,
                    reason,
                    evidence_text,
                    answer,
                    clarity_dimension,
                )
            )
        return mitigation_measure, reason, evidence_text

    @staticmethod
    def _merge_unlabelled_mitigation_clarification(
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        answer: str,
        clarity_dimension: str | None,
    ) -> tuple[str, str, str]:
        clarification = f"Clarification: {answer}"
        if clarity_dimension == "specificity":
            mitigation_measure = f"{mitigation_measure}\n{clarification}".strip()
        elif clarity_dimension == "evidence_identifiability":
            evidence_text = f"{evidence_text}\n{clarification}".strip()
        else:
            reason = f"{reason}\n{clarification}".strip()
        return mitigation_measure, reason, evidence_text

    @classmethod
    def _clarification_fields(cls, answer: str) -> dict[str, str]:
        buffers: dict[str, list[str]] = {
            "measure": [],
            "justification": [],
            "evidence": [],
        }
        current: str | None = None
        for raw_line in answer.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^(?:\d+[.)]\s*)?([^:]+):\s*(.*)$", line)
            if match:
                key = cls.mitigation_clarity_field_aliases.get(
                    match.group(1).strip().casefold()
                )
                if key:
                    current = key
                    if match.group(2).strip():
                        buffers[key].append(match.group(2).strip())
                    continue
            if current:
                buffers[current].append(line)
        return {key: " ".join(parts).strip() for key, parts in buffers.items()}

    async def _run_mitigation_clarity_track(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str | None = None,
    ) -> ChatResponse | None:
        clarity = await self._assess_mitigation_clarity(
            session,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer,
        )
        if clarity is None or clarity.get("error"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message("mitigation_validation_unavailable.md"),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if clarity.get("clear"):
            session.mitigation_frozen_inputs = self._frozen_mitigation_inputs(
                clarity,
                mitigation_measure,
                reason,
                evidence_text,
            )
            return None

        if self._can_freeze_after_mitigation_clarification(
            clarity,
            mitigation_measure,
            reason,
            evidence_text,
            clarification_answer,
            session.pending_mitigation_clarity_dimension,
        ):
            session.mitigation_frozen_inputs = self._frozen_mitigation_inputs(
                clarity,
                mitigation_measure,
                reason,
                evidence_text,
            )
            return None

        if session.mitigation_clarity_turns >= self.mitigation_clarity_turn_cap:
            self._discard_temporary_evidence(session, evidence_text)
            session.phase = "mitigation_reason"
            self._clear_mitigation_clarity_state(session)
            clarity_reason = str(clarity.get("reason") or "").strip()
            revision_reason = (
                "I still cannot freeze an unambiguous version of the mitigation "
                "measure, justification, and evidence after the clarification limit. "
                "Please resubmit them with more concrete wording."
            )
            if clarity_reason:
                revision_reason = f"{revision_reason} Last clarity issue: {clarity_reason}"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=revision_reason,
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        unresolved_dimension = self._unresolved_mitigation_clarity_dimension(clarity)
        follow_up_questions = self._mitigation_clarification_questions(
            clarity,
            unresolved_dimension,
            selected_hazard=session.selected_hazard or session.accepted_custom_hazard,
        )
        question_list = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(follow_up_questions, start=1)
        )
        dimension_label = self.mitigation_clarity_labels.get(
            unresolved_dimension,
            "Mitigation input",
        )
        session.phase = "mitigation_clarity"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        session.pending_mitigation_clarity_dimension = unresolved_dimension
        session.mitigation_clarity_turns += 1
        clarification_prompt = (
            f"Currently clarifying: {dimension_label}\n\n"
            f"Please answer these questions in one response:\n\n{question_list}"
        )
        self._append_mitigation_clarification_message(
            session,
            "assistant",
            clarification_prompt,
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=markdown_to_html(
                "### Clarification needed\n\n"
                f"**Currently clarifying: {dimension_label}**\n\n"
                f"Please answer these questions in one response:\n\n{question_list}\n\n"
                "I will use your answers only to clarify the inputs, not as evidence "
                "that the measure is supported."
            ),
            options=self._mitigation_clarity_options(),
            session=session.summary(),
            input_mode="textarea",
            error=False,
            validation_details=self._clarity_validation_details(
                clarity,
                session,
                unresolved_dimension,
                follow_up_questions,
            ),
        )

    def _mitigation_target_population_clarification_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        *,
        additional: bool = False,
        error_reason: str | None = None,
    ) -> ChatResponse:
        session.phase = "mitigation_clarity"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        session.pending_mitigation_clarity_dimension = (
            "target_population_additional" if additional else "target_population"
        )
        if additional:
            question = (
                "Share any additional target population this mitigation measure should "
                "support. Use open text; I will match it to the available target-population groups."
            )
            heading = "Add more target population"
        else:
            question = (
                "I could not identify a target population from the mitigation measure "
                "and reason. Which target groups or population is this mitigation measure "
                "intended to support? Describe every relevant group in your own words."
            )
            heading = "Target population needed"
        message = f"### {heading}\n\n{question}"
        if error_reason:
            message += f"\n\n> {error_reason}"
        self._append_mitigation_clarification_message(session, "assistant", question)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_clarity",
            bot_message=markdown_to_html(message),
            options=self._mitigation_clarity_options(),
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    async def _ensure_mitigation_target_population_from_inputs(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> ChatResponse:
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        if session.mitigation_target_population is None:
            inferred = await self._infer_mitigation_target_population_from_inputs(
                mitigation_measure,
                reason,
            )
            if inferred:
                session.mitigation_target_population = inferred
            else:
                return self._mitigation_target_population_clarification_step(
                    session_id,
                    session,
                    mitigation_measure,
                    reason,
                    evidence_text,
                )
        return self._mitigation_target_population_review_step(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
        )

    async def _infer_mitigation_target_population_from_inputs(
        self,
        mitigation_measure: str,
        reason: str,
    ) -> list[str]:
        text = (
            f"Mitigation measure:\n{mitigation_measure.strip()}\n\n"
            f"Justification/reason:\n{reason.strip()}"
        )
        return await self._match_mitigation_target_population_answer(text)

    def _mitigation_target_population_review_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        *,
        error_reason: str | None = None,
    ) -> ChatResponse:
        if not session.mitigation_target_population:
            return self._mitigation_target_population_clarification_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
                error_reason=(
                    "I could not identify a specific target population. Please name a "
                    "concrete group, such as low-income households, rural residents, "
                    "tenants, older adults, or another affected group."
                ),
            )
        session.phase = "mitigation_target_population_review"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        target_population = self._group_target_population_labels(
            session.mitigation_target_population or []
        )
        target_lines = "\n".join(f"- **{label}**" for label in target_population)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population_review",
            bot_message=markdown_to_html(
                "### Target population identified\n\n"
                "I identified these target-population groups from the mitigation information:\n\n"
                f"{target_lines or '- No target population matched.'}\n\n"
                f"{f'> {error_reason}\n\n' if error_reason else ''}"
                "Choose **Continue** to use these groups, or **Add more target population** "
                "to describe another group in open text."
            ),
            options=self._mitigation_target_population_review_options(),
            session=session.summary(),
            error=bool(error_reason),
        )

    async def _handle_mitigation_target_population_review(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(
            message,
            self._mitigation_target_population_review_options(),
        )
        if exact_label is None:
            fuzzy_label = match_option_label(
                message,
                self._mitigation_target_population_review_options(),
            )
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        mitigation_measure = session.pending_mitigation_measure or session.mitigation_measure or ""
        reason = session.pending_mitigation_reason or session.mitigation_reason or ""
        evidence_text = session.pending_mitigation_evidence or ""
        if not mitigation_measure or not reason:
            session.phase = "mitigation_reason"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason="Please enter the mitigation measure and reason again.",
                ),
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=True,
            )

        if action == normalize("Add more target population"):
            return self._mitigation_target_population_clarification_step(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
                additional=True,
            )

        if action == normalize("Continue"):
            if session.mitigation_validation and session.mitigation_grounded_synthesis:
                return await self._finalize_validated_mitigation(session_id, session)
            clarity_response = await self._run_mitigation_clarity_track(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
            )
            if clarity_response is not None:
                return clarity_response
            frozen_inputs = session.mitigation_frozen_inputs or {}
            return await self._validate_frozen_mitigation_inputs(
                session_id,
                session,
                frozen_inputs.get("measure_description") or mitigation_measure,
                frozen_inputs.get("justification") or reason,
                frozen_inputs.get("evidence") or evidence_text,
            )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population_review",
            bot_message=self.invalid_message,
            options=self._mitigation_target_population_review_options(),
            session=session.summary(),
            error=True,
        )

    @staticmethod
    def _merge_target_population_labels(
        existing: list[str],
        additions: list[str],
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for label in [*existing, *additions]:
            cleaned = str(label or "").strip()
            key = normalize(cleaned)
            if cleaned and key not in seen:
                seen.add(key)
                labels.append(cleaned)
        return labels

    @staticmethod
    def _group_target_population_labels(labels: list[str]) -> list[str]:
        grouped: dict[str, list[str]] = {}
        passthrough: list[str] = []
        for label in labels:
            cleaned = str(label or "").strip()
            if not cleaned:
                continue
            if ":" not in cleaned:
                passthrough.append(cleaned)
                continue
            question, answer = [part.strip() for part in cleaned.split(":", 1)]
            if not question or not answer:
                passthrough.append(cleaned)
                continue
            answers = grouped.setdefault(question, [])
            if normalize(answer) not in {normalize(existing) for existing in answers}:
                answers.append(answer)
        return [
            f"{question}: {', '.join(answers)}"
            for question, answers in grouped.items()
            if answers
        ] + passthrough

    @classmethod
    def _normalize_population_group_labels(cls, labels: list[str]) -> list[str]:
        normalized_labels: list[str] = []
        seen: set[str] = set()
        for label in labels:
            normalized = cls._normalize_population_group_label(label)
            key = normalize(normalized)
            if normalized and key not in seen:
                seen.add(key)
                normalized_labels.append(normalized)
        return normalized_labels

    @classmethod
    def _normalize_population_group_label(cls, label: object) -> str:
        raw = re.sub(r"\s+", " ", str(label or "")).strip(" .")
        if not raw:
            return ""
        question = ""
        answer = ""
        if ":" in raw:
            question, answer = [part.strip() for part in raw.split(":", 1)]
        question_key = normalize_for_match(question)
        answer_key = normalize_for_match(answer)
        full_key = normalize_for_match(raw)
        compact_key = compact_for_match(raw)

        mapped = cls._population_group_from_question_answer(question_key, answer_key)
        if mapped:
            return mapped

        phrase_map: tuple[tuple[tuple[str, ...], str], ...] = (
            (
                (
                    "households with repeated utility bill arrears",
                    "repeated utility bill arrears",
                    "utility bill arrears",
                    "utility arrears",
                    "arrears on utility bills",
                    "struggling to pay bills each month",
                    "high energy bills",
                    "issue high energy bills",
                    "energy affordability",
                ),
                "Households experiencing energy affordability challenges",
            ),
            (
                (
                    "living in a house with low energy efficiency",
                    "low energy efficiency",
                    "energy inefficient homes",
                    "energy inefficient housing",
                    "poorly insulated homes",
                    "poor insulation",
                ),
                "Residents of energy-inefficient homes",
            ),
            (
                (
                    "countries with higher electricity consumption",
                    "higher electricity consumption",
                    "electricity consumption",
                    "high energy consumption",
                ),
                "Residents of high-energy-consumption regions",
            ),
            (
                (
                    "countries with higher cold home pct",
                    "cold home pct",
                    "cold homes",
                    "inadequate heating",
                ),
                "Residents of cold or inadequately heated homes",
            ),
            (
                (
                    "countries with higher cost overburden",
                    "cost overburden",
                    "housing cost overburden",
                ),
                "Households facing housing-cost pressure",
            ),
            (
                (
                    "damp",
                    "mould",
                    "mold",
                    "leak",
                    "rot",
                    "home problems count",
                    "higher home problems count",
                ),
                "Residents of poor-quality housing",
            ),
            (
                ("low income", "income poor", "financially vulnerable"),
                "Low-income households",
            ),
            (
                ("medium income", "middle income"),
                "Middle-income households",
            ),
            (
                ("high income", "wealthy households"),
                "High-income households",
            ),
            (
                ("tenant", "tenants", "renters", "renting"),
                "Tenant households",
            ),
            (
                ("homeowner", "home owner", "home owners", "owner occupiers"),
                "Homeowner households",
            ),
            (
                ("unemployed", "jobless"),
                "Unemployed people",
            ),
            (
                ("retired", "retirees", "pensioners"),
                "Retired people",
            ),
            (
                ("women", "woman", "female"),
                "Women",
            ),
            (
                ("non binary", "nonbinary"),
                "Non-binary people",
            ),
            (
                ("people with disabilities", "disabled people", "long term condition", "chronic illness"),
                "People with disabilities or long-term conditions",
            ),
            (
                ("rural residents", "rural area", "remote communities"),
                "Rural residents",
            ),
            (
                ("urban residents", "urban area", "city residents"),
                "Urban residents",
            ),
        )
        for phrases, canonical in phrase_map:
            for phrase in phrases:
                phrase_key = normalize_for_match(phrase)
                phrase_compact = compact_for_match(phrase)
                if (
                    phrase_key and phrase_key in full_key
                    or phrase_compact and phrase_compact in compact_key
                ):
                    return canonical

        if question_key.startswith("countries with higher"):
            remainder = re.sub(
                r"(?i)^countries with higher\s+",
                "",
                raw,
            ).strip()
            if remainder:
                descriptor = cls._population_region_descriptor(remainder)
                return f"Residents of {descriptor} regions"
        if full_key.startswith("countries with higher"):
            remainder = re.sub(
                r"(?i)^countries with higher\s+",
                "",
                raw,
            ).strip()
            if remainder:
                descriptor = cls._population_region_descriptor(remainder)
                return f"Residents of {descriptor} regions"

        return cls._people_centric_label(raw)

    @classmethod
    def _population_group_from_question_answer(
        cls,
        question_key: str,
        answer_key: str,
    ) -> str:
        if not question_key:
            return ""

        yes_values = {
            "yes",
            "yes once",
            "yes twice or more",
            "twice or more",
        }

        mappings: dict[tuple[str, str], str] = {
            ("level of income", "low income"): "Low-income households",
            ("level of income", "medium income"): "Middle-income households",
            ("level of income", "high income"): "High-income households",

            ("living in a house with low energy efficiency", "yes"): (
                "Residents of energy-inefficient homes"
            ),

            ("utility arrears", "yes"): (
                "Households experiencing energy affordability challenges"
            ),
            ("utility arrears", "yes once"): (
                "Households experiencing energy affordability challenges"
            ),
            ("utility arrears", "yes twice or more"): (
                "Households experiencing energy affordability challenges"
            ),
            ("utility arrears", "twice or more"): (
                "Households experiencing energy affordability challenges"
            ),

            ("religious minority", "yes"): "Religious minority groups",

            ("tenancy status", "tenant"): "Tenant households",
            ("tenancy status", "homeowner"): "Homeowner households",

            ("economic status", "unemployed"): "Unemployed people",
            ("economic status", "employed"): "Employed people",
            ("economic status", "retired"): "Retired people",

            ("gender", "woman"): "Women",
            ("gender", "male"): "Men",
            ("gender", "non binary"): "Non-binary people",

            ("disability of long term condition", "yes"): (
                "People with disabilities or long-term conditions"
            ),
            ("disability or long term condition", "yes"): (
                "People with disabilities or long-term conditions"
            ),

            ("location of residency", "urban area"): "Urban residents",
            ("location of residency", "suburban area"): "Suburban residents",
            ("location of residency", "rural area"): "Rural residents",

            ("need of a car to perform daily activities", "yes"): (
                "Car-dependent residents"
            ),
            ("needs a car for daily activities", "yes"): (
                "Car-dependent residents"
            ),

            ("care responsibility as the main activity", "yes remunerated"): (
                "Paid carers"
            ),
            ("care responsibility as the main activity", "yes non remunerated"): (
                "Unpaid carers"
            ),

            ("eu citizenship", "no"): "Non-EU citizens",
            ("eu citizenship", "yes"): "EU citizens",

            ("level of education", "no formal education"): (
                "People with no formal education"
            ),
            ("level of education", "primary"): "People with primary education",
            ("level of education", "secondary"): "People with secondary education",
            ("level of education", "further normal education"): (
                "People with further or higher education"
            ),
            ("level of education", "further formal education"): (
                "People with further or higher education"
            ),

            ("age range", "18"): "Children and young people",
            ("age range", "25 35"): "Young adults",
            ("age range", "35 65"): "Working-age adults",
            ("age range", "65"): "Older adults",
        }

        mapped = mappings.get((question_key, answer_key))
        if mapped:
            return mapped

        # More tolerant utility arrears handling
        if question_key in {
            "utility arrears",
            "households with repeated utility bill arrears",
            "arrears on utility bills",
            "utility bill arrears",
        } and answer_key in yes_values:
            return "Households experiencing energy affordability challenges"

        # More tolerant religious minority handling
        if question_key in {
            "religious minority",
            "belongs to a religious minority",
            "religion minority",
            "minority religion",
        } and answer_key == "yes":
            return "Religious minority groups"

        # Do not create people labels for "No"
        if answer_key == "no":
            return ""

        # Generic yes-only fallback
        if answer_key == "yes":
            descriptor = cls._humanize_population_fragment(question_key)
            return f"People affected by {descriptor}" if descriptor else ""

        # Avoid bad labels like "People in yes twice or more"
        if answer_key in {
            "yes once",
            "yes twice or more",
            "twice or more",
            "once",
        }:
            descriptor = cls._humanize_population_fragment(question_key)
            return f"People affected by {descriptor}" if descriptor else ""

        if answer_key:
            return cls._people_centric_label(
                cls._humanize_population_fragment(answer_key)
            )

        return ""

    @classmethod
    def _population_region_descriptor(cls, value: str) -> str:
        fragment = cls._humanize_population_fragment(value)
        fragment = re.sub(r"\b(pct|percentage|rate|count)\b", "", fragment, flags=re.I)
        fragment = re.sub(r"\s+", " ", fragment).strip().lower()
        if not fragment:
            return "higher-risk"
        return fragment.replace(" ", "-")

    @staticmethod
    def _humanize_population_fragment(value: str) -> str:
        words = normalize_for_match(value)
        replacements = {
            "electricity consumption": "electricity consumption",
            "cold home": "cold homes",
            "cost overburden": "housing cost pressure",
            "home problems count": "home-quality problems",
        }
        for old, new in replacements.items():
            words = words.replace(old, new)
        return re.sub(r"\s+", " ", words).strip()

    @staticmethod
    def _people_centric_label(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .")
        if not cleaned:
            return ""
        lower = cleaned.casefold()
        people_prefixes = (
            "people",
            "households",
            "residents",
            "families",
            "workers",
            "tenants",
            "homeowners",
            "women",
            "men",
            "children",
            "older adults",
            "young adults",
            "students",
            "carers",
            "businesses",
            "communities",
        )
        if lower.startswith(people_prefixes):
            return cleaned[:1].upper() + cleaned[1:]
        if any(term in lower for term in ("household", "family", "families")):
            return cleaned[:1].upper() + cleaned[1:]
        if "business" in lower or "sme" in lower:
            return cleaned[:1].upper() + cleaned[1:]
        return f"People in {cleaned[:1].lower() + cleaned[1:]}"

    @staticmethod
    def _append_mitigation_clarification_message(
        session: ChatSession,
        role: str,
        content: str,
    ) -> None:
        clean_content = content.strip()
        if not clean_content:
            return
        history = session.mitigation_clarification_history or []
        history.append({"role": role, "content": clean_content})
        session.mitigation_clarification_history = history

    @staticmethod
    def _mitigation_clarification_history_block(
        session: ChatSession,
        clarification_answer: str | None = None,
    ) -> str:
        history = [
            entry
            for entry in (session.mitigation_clarification_history or [])
            if isinstance(entry, dict)
            and str(entry.get("role") or "").strip()
            and str(entry.get("content") or "").strip()
        ]
        latest_answer = (clarification_answer or "").strip()
        if latest_answer and not any(
            entry.get("role") == "user"
            and str(entry.get("content") or "").strip() == latest_answer
            for entry in history
        ):
            history.append({"role": "user", "content": latest_answer})
        if not history:
            return "None yet"
        return "\n\n".join(
            f"{str(entry['role']).strip().title()}:\n{str(entry['content']).strip()}"
            for entry in history
        )

    @classmethod
    def _unresolved_mitigation_clarity_dimension(
        cls,
        clarity: dict[str, object],
    ) -> str | None:
        dimensions = clarity.get("dimensions")
        if not isinstance(dimensions, dict):
            return None
        return next(
            (
                dimension
                for dimension in cls.mitigation_clarity_dimensions
                if dimensions.get(dimension) == "NEEDS_CLARIFICATION"
            ),
            None,
        )

    @classmethod
    def _mitigation_clarification_questions(
        cls,
        clarity: dict[str, object],
        unresolved_dimension: str | None,
        selected_hazard: str | None = None,
    ) -> list[str]:
        raw_questions = clarity.get("follow_up_questions")
        questions = [
            str(question).strip()
            for question in raw_questions
            if str(question).strip()
        ] if isinstance(raw_questions, list) else []
        if not questions:
            legacy_question = str(clarity.get("follow_up_question") or "").strip()
            if legacy_question:
                questions.append(legacy_question)

        if selected_hazard:
            questions = [
                question
                for question in questions
                if not cls._asks_for_already_selected_hazard(question)
            ]

        fallback_questions = cls.mitigation_clarity_fallback_questions.get(
            unresolved_dimension,
            cls.mitigation_clarity_default_questions,
        )
        for question in fallback_questions:
            if len(questions) >= 2:
                break
            if question not in questions:
                questions.append(question)
        return questions[:3]

    @staticmethod
    def _asks_for_already_selected_hazard(question: str) -> bool:
        normalized = normalize_for_match(question)
        asks_for_hazard = any(
            phrase in normalized
            for phrase in (
                "what specific hazard",
                "which hazard",
                "what hazard",
                "what specific risk",
                "which risk",
                "what risk",
                "what problem",
                "which problem",
            )
        )
        mitigation_target = any(
            phrase in normalized
            for phrase in (
                "aiming to mitigate",
                "intended to mitigate",
                "trying to mitigate",
                "seeking to mitigate",
                "measure address",
                "measure mitigate",
            )
        )
        return asks_for_hazard and mitigation_target

    def _can_freeze_after_mitigation_clarification(
        self,
        clarity: dict[str, object],
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
        clarification_answer: str | None,
        answered_dimension: str | None = None,
    ) -> bool:
        dimensions = clarity.get("dimensions")
        if not isinstance(dimensions, dict):
            return False

        unresolved = {
            key
            for key, value in dimensions.items()
            if value != "CLEAR"
        }
        allowed_unresolved = answered_dimension or "justification_clarity"
        if unresolved != {allowed_unresolved}:
            return False

        clarification = (clarification_answer or "").strip()
        if not clarification and "Clarification:" in reason:
            clarification = reason.rsplit("Clarification:", 1)[1].strip()
        if not clarification:
            return False
        if self._is_invalid_user_text(clarification):
            return False
        if len(compact_for_match(clarification)) < 12:
            return False
        if len(compact_for_match(mitigation_measure)) < 8:
            return False
        if len(compact_for_match(reason)) < 30:
            return False
        if evidence_text and self._is_invalid_user_text(evidence_text):
            return False
        if answered_dimension == "evidence_identifiability" and not evidence_text:
            return False
        return True

    @staticmethod
    def _frozen_mitigation_inputs(
        clarity: dict[str, object],
        mitigation_measure: str,
        reason: str,
        evidence_text: str,
    ) -> dict[str, str]:
        frozen = clarity.get("frozen_inputs")
        if not isinstance(frozen, dict):
            frozen = {}
        return {
            "measure_description": str(
                frozen.get("measure_description") or mitigation_measure
            ).strip(),
            "justification": str(frozen.get("justification") or reason).strip(),
            # Evidence is an immutable source reference/content payload. Do not
            # let the clarity model replace an absent value with "Not provided"
            # or rewrite uploaded-document identifiers.
            "evidence": evidence_text.strip(),
        }

    @classmethod
    def _normalized_mitigation_evidence(cls, evidence: str | None) -> str:
        clean_evidence = str(evidence or "").strip()
        if clean_evidence.casefold() in {
            "none",
            "none yet",
            "not provided",
            "no evidence",
            "no evidence provided",
            "n/a",
        }:
            return ""
        if cls._has_evidence_url_reference(clean_evidence):
            lines = [
                line
                for line in clean_evidence.splitlines()
                if not (
                    line.strip().casefold().startswith("evidence content:")
                    and "unable to extract evidence" in line.casefold()
                )
            ]
            return "\n".join(lines).strip()
        return clean_evidence


    async def _finalize_validated_mitigation(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        hazard_reference = self._selected_hazard_reference(session_id, session)
        session.mitigation_record_id = self._store_mitigation_measure(
            user_session_id=hazard_reference["user_session_id"],
            user_hazard_id=hazard_reference["user_hazard_id"],
            custom_hazard_id=hazard_reference["custom_hazard_id"],
            system_hazard_id=hazard_reference["system_hazard_id"],
            additional_hazard_id=hazard_reference["additional_hazard_id"],
            mitigation_measure=session.mitigation_measure or "",
            reason=session.mitigation_reason or "",
            target_population=session.mitigation_target_population,
        )
        self._record_activity(
            session_id,
            session,
            "mitigation_measure_validated",
            session.mitigation_measure or "",
        )
        return await self._mitigation_review_step(session_id, session)

    def _mitigation_target_population_labels(self, session: ChatSession) -> list[str]:
        if session.sector_id is None or not session.selected_hazard:
            return []
        rows = self.db.execute(
            select(
                SystemHazardSocioDemographic.profile,
                SystemHazardSocioDemographic.variable_name,
                SystemHazardSocioDemographic.explanation,
                SystemHazardSocioDemographic.statistical_basis,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                SystemHazard,
                SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id,
            )
            .outerjoin(
                SystemHazardSocioDemographicTargetPopulation,
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id
                == SystemHazardSocioDemographic.id,
            )
            .outerjoin(
                QuestionOption,
                QuestionOption.id
                == SystemHazardSocioDemographicTargetPopulation.question_option_id,
            )
            .outerjoin(
                EvaluationQuestion,
                and_(
                    EvaluationQuestion.id == QuestionOption.question_id,
                    EvaluationQuestion.active.is_(True),
                    EvaluationQuestion.category == "target_population",
                ),
            )
            .where(
                SystemHazard.sector_id == session.sector_id,
                func.lower(SystemHazard.name) == session.selected_hazard.casefold(),
                SystemHazardSocioDemographic.sector_id == session.sector_id,
            )
            .order_by(
                SystemHazardSocioDemographic.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
        ).all()
        if not rows:
            stored_profile_labels = self._target_population_labels_from_stored_profiles(session)
            if stored_profile_labels:
                return stored_profile_labels
            selected_labels = self._selected_target_population_labels(session)
            return selected_labels or self._selected_hazard_profile_names_for_venn(session)

        labels: list[str] = []
        seen: set[str] = set()
        labels_by_profile: dict[str, list[str]] = {}
        profile_names: dict[str, str] = {}
        excluded_profile_keys = self._selected_hazard_or_below_one_profile_keys(session)
        for row in rows:
            profile_name = str(row.profile or "").strip()
            if not profile_name:
                continue
            if normalize(profile_name) in excluded_profile_keys:
                continue
            variable_name = str(row.variable_name or "").strip()
            if self._system_profile_has_or_below_one_effect(
                session,
                variable_name,
                profile_name,
            ):
                continue
            if self._profile_has_odds_ratio_below_one(
                {
                    "name": profile_name,
                    "profile": profile_name,
                    "variable_name": variable_name,
                    "explanation": str(row.explanation or ""),
                    "statistical_basis": str(row.statistical_basis or ""),
                }
            ):
                continue
            profile_key = normalize(profile_name)
            profile_names.setdefault(profile_key, profile_name)
            if row.question and row.option:
                profile_labels = labels_by_profile.setdefault(profile_key, [])
                label = f"{row.question}: {row.option}"
                if normalize(label) not in {normalize(item) for item in profile_labels}:
                    profile_labels.append(label)

        for profile_key, profile_name in profile_names.items():
            profile_labels = labels_by_profile.get(profile_key) or [profile_name]
            for label in profile_labels:
                key = normalize(label)
                if key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    def _target_population_labels_from_stored_profiles(self, session: ChatSession) -> list[str]:
        if not session.selected_hazard:
            return []
        labels: list[str] = []
        seen: set[str] = set()
        for profile in self._stored_hazard_profiles(session, session.selected_hazard):
            profile_labels = profile.get("target_population_labels")
            values = profile_labels if isinstance(profile_labels, list) else []
            for value in values:
                label = str(value or "").strip()
                key = normalize(label)
                if label and key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    def _selected_hazard_or_below_one_profile_keys(self, session: ChatSession) -> set[str]:
        if not session.selected_hazard:
            return set()
        return {
            normalize(str(profile.get("name") or profile.get("profile") or ""))
            for profile in self._stored_hazard_profiles(session, session.selected_hazard)
            if self._profile_has_odds_ratio_below_one(profile)
        }

    def _system_profile_has_or_below_one_effect(
        self,
        session: ChatSession,
        variable_name: str,
        profile_name: str,
    ) -> bool:
        if not session.sector or not session.selected_hazard:
            return False
        candidates = self._effect_predictor_candidates(variable_name, profile_name)
        if not candidates:
            return False
        hazard_key = slugify_hazard(session.selected_hazard)
        for row in hazard_predictor_effect_rows(sector=session.sector, min_or=0.0):
            row_hazard = slugify_hazard(str(row.get("hazard") or ""))
            if row_hazard != hazard_key:
                continue
            predictor = normalize_for_match(str(row.get("predictor") or ""))
            if not predictor:
                continue
            if not any(
                predictor == candidate
                or predictor.startswith(f"{candidate} ")
                or candidate.startswith(f"{predictor} ")
                for candidate in candidates
            ):
                continue
            try:
                return float(row.get("odds_ratio") or 0) < 1
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _effect_predictor_candidates(variable_name: str, profile_name: str) -> set[str]:
        candidates: set[str] = set()
        for value in (variable_name, profile_name):
            cleaned = str(value or "").strip()
            if not cleaned:
                continue
            candidates.add(normalize_for_match(cleaned))
            if ":" in cleaned:
                question, answer = [part.strip() for part in cleaned.split(":", 1)]
                if question:
                    candidates.add(normalize_for_match(question))
                if question and answer:
                    candidates.add(normalize_for_match(f"{question} {answer}"))
                    candidates.add(normalize_for_match(f"{question}__{answer}"))
        return {candidate for candidate in candidates if candidate}

    def _selected_hazard_profile_names_for_venn(self, session: ChatSession) -> list[str]:
        if not session.selected_hazard:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for profile in self._stored_hazard_profiles(session, session.selected_hazard):
            if self._profile_has_odds_ratio_below_one(profile):
                continue
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            key = normalize(name)
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        return names

    def _affected_profile_target_population_labels(self, session: ChatSession) -> list[str]:
        return (
            self._mitigation_target_population_labels(session)
            or self._selected_target_population_labels(session)
        )

    def _mitigation_target_population_options(self, session: ChatSession) -> list[Option]:
        # Kept for restoring legacy sessions; mitigation no longer uses the
        # target-population quick-select dialog.
        return []

    @staticmethod
    def _selected_target_population_labels(session: ChatSession) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for answer in session.target_population_answers or []:
            question = str(answer.get("question") or "Target population").strip()
            selected = answer.get("selected")
            values = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for value in values:
                option = str(value).strip()
                label = f"{question}: {option}" if question and option else option
                key = normalize(label)
                if label and key not in seen:
                    seen.add(key)
                    labels.append(label)
        return labels

    async def _match_mitigation_target_population_answer(self, answer: str) -> list[str]:
        rows = self.db.execute(
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
        if not rows:
            return []

        allowed_ids = {int(row.id) for row in rows}
        option_catalogue = "\n".join(
            f"- {int(row.id)} | {row.question}: {row.option}" for row in rows
        )
        context = render_prompt_template(
            "llm/mitigation_target_population_extraction.txt",
            target_population_options=option_catalogue,)
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Target-group answer:\n{answer.strip()}\n\n"
                        f"Available options:\n{option_catalogue}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=220,
        )
        matched_ids: set[int] = set()
        additional_groups: list[str] = []
        rows_by_id = {int(row.id): row for row in rows}
        if not is_llm_unavailable_response(response):
            try:
                parsed = json.loads(self._extract_json_object(response))
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = {}
            raw_ids = parsed.get("option_ids") if isinstance(parsed, dict) else []
            if isinstance(raw_ids, list):
                for raw_id in raw_ids:
                    try:
                        option_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if (
                        option_id in allowed_ids
                        and self._target_population_option_is_supported_by_text(
                            answer,
                            rows_by_id[option_id],
                        )
                    ):
                        matched_ids.add(option_id)
            raw_groups = parsed.get("additional_groups") if isinstance(parsed, dict) else []
            if isinstance(raw_groups, list):
                for raw_group in raw_groups:
                    group = re.sub(r"\s+", " ", str(raw_group or "")).strip()
                    if self._is_valid_custom_target_population_group(group):
                        additional_groups.append(group)

        matched_ids.update(self._fallback_target_population_option_ids(answer, rows))
        labels = [
            f"{row.question}: {row.option}"
            for row in rows
            if int(row.id) in matched_ids
        ]
        return self._merge_target_population_labels(labels, additional_groups)

    @classmethod
    def _is_valid_custom_target_population_group(cls, group: str) -> bool:
        cleaned = re.sub(r"\s+", " ", str(group or "")).strip(" .,:;")
        if len(cleaned) < 3:
            return False
        if len(cleaned) > 120:
            return False
        normalized = normalize_for_match(cleaned)
        compact_key = compact_for_match(cleaned)
        if len(normalized) < 3:
            return False
        if re.fullmatch(r"(.)\1{3,}", normalized):
            return False
        invalid_terms = {
            "none",
            "no",
            "noadditional",
            "notapplicable",
            "n/a",
            "na",
            "policy",
            "mitigation",
            "hazard",
            "measure",
            "evidence",
            "people",
            "persons",
            "person",
            "citizens",
            "citizen",
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
            "population",
            "generalpopulation",
            "public",
            "family",
            "families",
            "targetpopulation",
            "noadditionaltargetpopulation",
            "notargetpopulation",
            "noneidentified",
        }
        if normalized in {normalize_for_match(term) for term in invalid_terms}:
            return False
        compact_invalid_terms = {compact_for_match(term) for term in invalid_terms}
        if compact_key in compact_invalid_terms:
            return False
        if compact_key.startswith("noadditional") or compact_key.startswith("notarget"):
            return False
        if not cls._has_specific_target_population_qualifier(cleaned):
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))

    @staticmethod
    def _target_population_phrase_map() -> dict[tuple[str, str], tuple[str, ...]]:
        return {
            ("age range", "18"): ("children", "child", "minors", "under 18", "youth"),
            ("age range", "25 35"): ("young adults", "aged 25 35", "25 to 35"),
            ("age range", "35 65"): ("middle aged", "working age", "aged 35 65", "35 to 65"),
            ("age range", "65"): ("older", "older adults", "older people", "elderly", "seniors", "over 65"),
            ("living in a house with low energy efficiency", "yes"): ("energy inefficient homes", "low energy efficiency", "poorly insulated", "cold homes"),
            ("gender", "woman"): ("women", "woman", "female"),
            ("gender", "male"): ("men", "man", "male"),
            ("gender", "non binary"): ("non binary", "nonbinary"),
            ("need of a car to perform daily activities", "yes"): ("car dependent", "car reliance", "need a car"),
            ("level of education", "no formal education"): ("no formal education",),
            ("level of education", "primary"): ("primary education",),
            ("level of education", "secondary"): ("secondary education",),
            ("level of education", "further normal education"): ("further education", "higher education"),
            ("location of residency", "urban area"): ("urban residents", "urban areas", "city residents"),
            ("location of residency", "suburban area"): ("suburban residents", "suburban areas"),
            ("location of residency", "rural area"): ("rural residents", "rural areas", "remote communities"),
            ("economic status", "employed"): ("employed people", "workers"),
            ("economic status", "unemployed"): ("unemployed", "jobless"),
            ("economic status", "retired"): ("retired people", "retirees", "pensioners"),
            ("care responsibility as the main activity", "yes remunerated"): ("paid carers", "paid caregivers"),
            ("care responsibility as the main activity", "yes non remunerated"): ("unpaid carers", "unpaid caregivers", "informal carers"),
            ("eu citizenship", "yes"): ("eu citizens",),
            ("eu citizenship", "no"): ("non eu citizens", "non eu migrants"),
            ("disability of long term condition", "yes"): ("people with disabilities", "disabled people", "long term condition", "chronic illness"),
            ("level of income", "low income"): ("low income", "income poor", "financially vulnerable"),
            ("level of income", "medium income"): ("middle income", "medium income"),
            ("level of income", "high income"): ("high income", "wealthy households"),
            ("tenancy status", "homeowner"): ("homeowners", "home owners", "owner occupiers"),
            ("tenancy status", "tenant"): ("tenants", "renters", "people who rent", "renting", "rent", "rented housing"),
        }

    @staticmethod
    def _has_specific_target_population_qualifier(group: str) -> bool:
        normalized = normalize_for_match(group)
        compact = compact_for_match(group)
        generic_terms = {
            "people",
            "persons",
            "person",
            "citizens",
            "citizen",
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
            "population",
            "public",
            "family",
            "families",
        }
        has_generic_term = any(
            f" {normalize_for_match(term)} " in f" {normalized} "
            for term in generic_terms
        )
        if not has_generic_term:
            return True

        specific_qualifiers = (
            "low income",
            "middle income",
            "medium income",
            "high income",
            "income poor",
            "energy poor",
            "fuel poor",
            "financially vulnerable",
            "vulnerable",
            "poor",
            "rural",
            "urban",
            "suburban",
            "remote",
            "elderly",
            "older",
            "senior",
            "young",
            "youth",
            "children",
            "disabled",
            "disability",
            "long term condition",
            "tenant",
            "renter",
            "renting",
            "homeowner",
            "owner occupier",
            "unemployed",
            "retired",
            "worker",
            "employed",
            "carer",
            "caregiver",
            "migrant",
            "non eu",
            "women",
            "woman",
            "female",
            "men",
            "male",
            "small business",
            "sme",
            "utility arrears",
            "car dependent",
            "low energy efficiency",
            "poorly insulated",
            "student",
        )
        return any(
            f" {normalize_for_match(qualifier)} " in f" {normalized} "
            or compact_for_match(qualifier) in compact
            for qualifier in specific_qualifiers
        )

    @classmethod
    def _target_population_option_is_supported_by_text(cls, answer: str, row: object) -> bool:
        text = f" {normalize_for_match(answer)} "
        question = normalize_for_match(str(row.question))
        option = normalize_for_match(str(row.option))
        if not question or not option:
            return False

        phrases = cls._target_population_phrase_map().get((question, option), ())
        if any(f" {normalize_for_match(phrase)} " in text for phrase in phrases):
            return True

        if option in {"yes", "no", "other"}:
            return False

        if len(option) < 3:
            return False

        broad_options = {
            "citizens",
            "community",
            "communities",
            "households",
            "people",
            "residents",
            "users",
            "public",
        }
        if option in broad_options:
            return False

        option_words = option.split()
        if len(option_words) > 1:
            return f" {option} " in text

        exact_single_word_options = {
            "woman",
            "male",
            "unemployed",
            "retired",
            "tenant",
            "homeowner",
        }
        return option in exact_single_word_options and f" {option} " in text

    @classmethod
    def _fallback_target_population_option_ids(cls, answer: str, rows: list[object]) -> set[int]:
        matched: set[int] = set()
        for row in rows:
            if cls._target_population_option_is_supported_by_text(answer, row):
                matched.add(int(row.id))
        return matched

    def _mitigation_target_population_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        options = self._mitigation_target_population_options(session)
        if not options:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message="No target population selection is required.",
                options=[],
                session=session.summary(),
                error=False,
            )
        session.phase = "mitigation_target_population"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population",
            bot_message=render_message(
                "mitigation_target_population.md",
                error_reason=error_reason or "",
            ),
            options=options,
            session=session.summary(),
            input_mode="target_population_multi",
            error=bool(error_reason),
        )

    async def _handle_mitigation_target_population(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        return await self._create_mitigation_measure_step(
            session_id,
            session,
        )

    async def _handle_mitigation_target_population_batch(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        raw_json = message.split(":", 1)[1].strip()
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, list):
            return self._mitigation_target_population_step(
                session_id, session, error_reason="Please submit valid target-population selections."
            )

        questions_by_id = {
            int(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        labels: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                question_id = int(item.get("question_id"))
            except (TypeError, ValueError):
                continue
            question = questions_by_id.get(question_id)
            answers = item.get("answers")
            if question is None or not isinstance(answers, list):
                continue
            allowed = {
                normalize(str(option)): str(option)
                for option in question.get("options", [])
            }
            for answer in answers:
                option = allowed.get(normalize(str(answer)))
                if not option:
                    continue
                label = f"{question['question']}: {option}"
                key = normalize(label)
                if key not in seen:
                    seen.add(key)
                    labels.append(label)

        if not labels:
            return self._mitigation_target_population_step(
                session_id,
                session,
                error_reason="Select at least one target-population option in the dialog.",
            )
        session.mitigation_target_population = labels
        return await self._create_mitigation_measure_step(
            session_id, session, target_population_confirmed=True
        )

    @staticmethod
    def _mitigation_target_population_text(session: ChatSession) -> str:
        return ", ".join(session.mitigation_target_population or []) or "Not specified"

    async def _mitigation_review_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "mitigation_review"
        answer = session.mitigation_grounded_synthesis or await self._mitigation_review_response(
            session,
            (
                "Provide a concise conclusion about the validated mitigation measure. "
                "Include related statistical context, affected groups, expected strengths, "
                "and limitations. Do not ask evaluation questions yet."
            ),
        )
        self._update_mitigation_review_details(
            session,
            answer,
            self._mitigation_target_affected_groups_json(session),
        )
        affected_target_populations = self._normalize_population_group_labels(
            self._affected_profile_target_population_labels(session)
        )
        mitigation_target_populations = self._normalize_population_group_labels(
            session.mitigation_target_population or []
        )
        affected_target_population_display = self._group_target_population_labels(
            affected_target_populations
        )
        mitigation_target_population_display = self._group_target_population_labels(
            mitigation_target_populations
        )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_review",
            bot_message=(
                render_message(
                    "mitigation_review.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    reason=session.mitigation_reason or "Not provided",
                    target_population=", ".join(
                        mitigation_target_population_display
                    ),
                    affected_target_population_json=json.dumps(
                        affected_target_populations,
                        ensure_ascii=False,
                    ),
                    mitigation_target_population_json=json.dumps(
                        mitigation_target_populations,
                        ensure_ascii=False,
                    ),
                    affected_target_populations=affected_target_population_display,
                    mitigation_target_populations=mitigation_target_population_display,
                    show_target_population_venn=bool(
                        affected_target_populations and mitigation_target_populations
                    ),
                    review=answer,
                )
            ),
            options=MITIGATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=False,
            validation_details=self._grounding_validation_details(session),
        )

    async def _handle_mitigation_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_REVIEW_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_REVIEW_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label

        if normalize(exact_label or "") == normalize("Move to next step"):
            return self._start_evaluation_questions(session_id, session)

        local_reason = None
        if self._is_invalid_user_text(message):
            local_reason = (
                "The question appears to contain gibberish, keyboard mashing, "
                "or unrecognizable text."
            )
        elif len(compact_for_match(message)) < 4:
            local_reason = "The question is too short to understand."
        if local_reason:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=local_reason,
                ),
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                input_mode="mitigation_review",
                error=True,
            )

        input_review = await self._validate_input_quality(
            session=session,
            purpose=(
                "A follow-up question or request about the already validated "
                "mitigation measure and its reasoning."
            ),
            fields={"Follow-up question": message},
        )
        if input_review is not None and not input_review.get("valid"):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=str(input_review.get("reason") or "Please rewrite the question."),
                ),
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                input_mode="mitigation_review",
                error=True,
            )

        answer = await self._mitigation_review_response(session, message)
        if session.stats_conversation is None:
            session.stats_conversation = []
        session.stats_conversation.extend(
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ]
        )

        return ChatResponse(
            session_id=session_id,
            step="mitigation_review",
            bot_message=markdown_to_html(answer),
            options=MITIGATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _start_evaluation_questions(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.evaluation_questions = self._evaluation_questions()
        session.evaluation_index = 0
        session.evaluation_answers = []

        if not session.evaluation_questions:
            session.phase = "evaluation_complete"
            self._promote_temporary_evidence(session)
            return ChatResponse(
                session_id=session_id,
                step="evaluation_complete",
                bot_message=render_message(
                    "mitigation_recorded.md",
                    hazard=session.selected_hazard or "the selected hazard",
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    reason=session.mitigation_reason or "Not provided",
                ),
                options=[],
                session=session.summary(),
                error=False,
            )

        session.phase = "evaluation_question"
        return self._evaluation_question_step(session_id, session)

    async def _handle_evaluation_answer(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        score, reason, evidence = parse_evaluation_answer(message)
        if score is None:
            return self._evaluation_question_step(
                session_id,
                session,
                error_reason="Please provide a score from 1 to 10.",
            )

        question = self._current_evaluation_question(session)
        if question is None:
            return self._evaluation_complete_step(session_id, session)

        if reason or evidence:
            evidence_text = self._evaluation_evidence_text(evidence)
            input_review = await self._validate_input_quality(
                session=session,
                purpose=(
                    "an optional evaluation reason and optional evidence supporting "
                    "the selected mitigation score"
                ),
                fields=self._reason_evidence_quality_fields(reason or "", evidence_text),
            )
            if input_review is None:
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=(
                        "I could not validate the reason and evidence because the "
                        "local LLM is unavailable. Please try this question again."
                    ),
                )
            if not input_review["valid"]:
                self._discard_temporary_evidence(session, evidence or "")
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=str(input_review["reason"]),
                )

            validation = await self._validate_evaluation_answer_against_stats(
                session=session,
                question=question,
                score=score,
                reason=reason or "",
                evidence=evidence_text or "",
            )

            if validation is None:
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=(
                        "I could not validate the reason and evidence because the "
                        "local LLM is unavailable. Please try this question again."
                    ),
                )

            if not validation["valid"]:
                self._discard_temporary_evidence(session, evidence or "")
                return self._evaluation_question_step(
                    session_id,
                    session,
                    error_reason=str(validation["reason"]),
                )

        if session.evaluation_answers is None:
            session.evaluation_answers = []
        session.evaluation_answers.append(
            {
                "question_id": question["id"],
                "category": question["category"],
                "chart_title": question.get("chart_title") or question["question"],
                "question": question["question"],
                "score": score,
                "reason": reason,
                "evidence": self._evaluation_evidence_text(evidence),
            }
        )
        self._store_question_response(
            session_id,
            session,
            question_id=int(question["id"]),
            category=str(question["category"]),
            response_text=str(score),
            score=score,
            reason=reason,
            evidence=self._evaluation_evidence_text(evidence),
            hazard_id=session.selected_hazard_record_id,
            mitigation_measure_id=session.mitigation_record_id,
        )
        self._record_activity(
            session_id,
            session,
            "evaluation_question_answered",
            f"{question['category']}: {question['question']} -> {score}",
        )
        session.evaluation_index += 1

        if session.evaluation_index >= len(session.evaluation_questions or []):
            return self._evaluation_complete_step(session_id, session)

        return self._evaluation_question_step(session_id, session)

    async def _mitigation_review_response(self, session: ChatSession, user_message: str) -> str:
        context, messages = await self._build_mitigation_review_messages(session, user_message)
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.35,
            max_tokens=1050,
        )

    def _mitigation_reason_prompt(
        self, session: ChatSession, error_reason: str | bool | None = None
    ) -> str:
        prompt = render_message(
            "mitigation_measure_reason.md",
            hazard=session.selected_hazard or "the selected hazard",
            dgs=format_all_dgs(session),
            mitigation_examples=self._mitigation_measure_examples(session.sector_id),
        )
        if isinstance(error_reason, str) and error_reason.strip():
            return (
                render_message(
                    "mitigation_validation_failed.md",
                    reason=error_reason.strip(),
                )
                + "\n"
                + prompt
            )
        return prompt

    async def _build_mitigation_review_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            (
                f"{session.selected_hazard or ''} {format_all_dgs(session)} "
                f"{self._mitigation_target_population_text(session)} "
                f"{session.mitigation_measure or ''} {session.mitigation_reason or ''} {user_message}"
            ),
            limit=8,
        )
        knowledge_context = await self._mitigation_knowledge_context(
            session,
            session.mitigation_measure or "",
            session.mitigation_reason or "",
        )
        examples = self._mitigation_measure_examples(session.sector_id)
        context = render_prompt_template(
            "llm/mitigation_review_assistant.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
            knowledge_context=knowledge_context
            or "- No relevant knowledge-base excerpts were found.",
        )

        history = list(session.stats_conversation or [])[-8:]
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_review_assistant_user_context.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    selected_hazard=session.selected_hazard or "Not selected",
                    target_population=self._mitigation_target_population_text(session),
                    socio_demographic_profiles=format_all_dgs(session),
                    mitigation_measure=session.mitigation_measure or "Not provided",
                    mitigation_reason=session.mitigation_reason or "Not provided",
                    examples=examples or "- No sector-specific examples are available.",
                ),
            },
            *history,
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/mitigation_review_assistant_user_followup.txt",
                    user_message=user_message,
                ),
            },
        ]
        return context, messages

    def _evaluation_question_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        question = self._current_evaluation_question(session)
        if question is None:
            return self._evaluation_complete_step(session_id, session)

        message = render_message(
            "evaluation_question.md",
            category=question["category"],
            question=question["question"],
            current=session.evaluation_index + 1,
            total=len(session.evaluation_questions or []),
            error_reason=error_reason or "",
        )
        return ChatResponse(
            session_id=session_id,
            step="evaluation_question",
            bot_message=message,
            options=[],
            session=session.summary(),
            input_mode="evaluation_question",
            error=bool(error_reason),
        )

    def _evaluation_complete_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        session.phase = "evaluation_complete"
        self._promote_temporary_evidence(session)
        return ChatResponse(
            session_id=session_id,
            step="evaluation_complete",
            bot_message=render_message(
                "evaluation_complete.md",
                hazard=session.selected_hazard or "the selected hazard",
                mitigation_measure=session.mitigation_measure or "Not provided",
                reason=session.mitigation_reason or "Not provided",
                answers=format_evaluation_answers(
                    session,
                    self._historical_evaluation_series(session),
                ),
            ),
            options=[],
            session=session.summary(),
            error=False,
        )

    def _store_mitigation_measure(
        self,
        *,
        user_session_id: int | None,
        user_hazard_id: int | None,
        custom_hazard_id: int | None,
        system_hazard_id: int | None,
        additional_hazard_id: int | None,
        mitigation_measure: str,
        reason: str,
        target_population: list[str] | None = None,
    ) -> int | None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ):
            return None
        try:
            row = UserMitigationMeasure(
                user_session_id=user_session_id,
                user_hazard_id=user_hazard_id,
                custom_hazard_id=custom_hazard_id,
                system_hazard_id=system_hazard_id,
                additional_hazard_id=additional_hazard_id,
                measure=mitigation_measure,
                reason=reason,
                target_population=(
                    json.dumps(target_population, ensure_ascii=False)
                    if target_population
                    else None
                ),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return row.id
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation measure")
            return None

    def _update_mitigation_review_details(
        self,
        session: ChatSession,
        conclusion: str,
        target_groups: dict[str, object],
    ) -> None:
        if session.mitigation_record_id is None:
            return
        try:
            row = self.db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == session.mitigation_record_id
                )
            )
            if row is None:
                return
            row.conclusion = conclusion.strip() or None
            row.target_groups_json = self._metadata_to_json(target_groups)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation conclusion and target groups")

    def _mitigation_target_affected_groups_json(
        self,
        session: ChatSession,
    ) -> dict[str, object]:
        hazard = session.selected_hazard or session.accepted_custom_hazard or ""
        system_profiles = self._stored_hazard_profiles(session, hazard) if hazard else []
        user_profiles = self._stored_user_hazard_profiles(session, hazard) if hazard else []
        target_answers = self._target_population_answer_objects(session)
        target_profiles = [
            self._group_json_item(profile, "target_group")
            for profile in self._target_population_profiles_from_answers(
                session.target_population_answers or [],
                hazard or "the selected hazard",
            )
        ]
        affected_profiles = [
            self._group_json_item(profile, "affected_group")
            for profile in [*system_profiles, *user_profiles]
        ]
        return {
            "hazard": hazard,
            "target_population_answers": target_answers,
            "target_groups": self._dedupe_group_items(target_profiles),
            "affected_groups": self._dedupe_group_items(affected_profiles),
            "all_groups": self._dedupe_group_items([*target_profiles, *affected_profiles]),
        }

    @staticmethod
    def _group_json_item(profile: dict[str, object], group_type: str) -> dict[str, object]:
        name = str(profile.get("name") or profile.get("profile") or "").strip()
        variable_name = str(
            profile.get("variable_name") or profile.get("variable") or ""
        ).strip()
        return {
            "type": group_type,
            "name": name,
            "variable_name": variable_name,
            "explanation": str(profile.get("explanation") or "").strip(),
            "statistical_basis": str(profile.get("statistical_basis") or "").strip(),
            "source": str(profile.get("source") or "").strip(),
        }

    @staticmethod
    def _dedupe_group_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            name = str(item.get("name") or "").strip()
            variable_name = str(item.get("variable_name") or "").strip()
            source = str(item.get("source") or "").strip()
            key = (normalize(name), normalize(variable_name), normalize(source))
            if not name or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _target_population_answer_objects(session: ChatSession) -> list[dict[str, object]]:
        answers: list[dict[str, object]] = []
        for answer in session.target_population_answers or []:
            question = str(answer.get("question") or "").strip()
            stored_selected = answer.get("selected")
            selected = (
                [str(item).strip() for item in stored_selected if str(item).strip()]
                if isinstance(stored_selected, list)
                else [
                    item.strip()
                    for item in str(answer.get("answer") or "").split(",")
                    if item.strip()
                ]
            )
            if not question and not selected:
                continue
            answers.append(
                {
                    "question_id": answer.get("question_id"),
                    "question": question,
                    "selected": selected,
                }
            )
        return answers

    def _store_question_response(
        self,
        session_id: str,
        session: ChatSession,
        *,
        question_id: int | None,
        category: str | None,
        response_text: str | None = None,
        question_option_id: int | None = None,
        score: int | None = None,
        reason: str | None = None,
        evidence: str | None = None,
        hazard_id: int | None = None,
        custom_hazard_id: int | None = None,
        system_hazard_id: int | None = None,
        additional_hazard_id: int | None = None,
        mitigation_measure_id: int | None = None,
    ) -> None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            if (
                hazard_id is None
                and custom_hazard_id is None
                and system_hazard_id is None
                and additional_hazard_id is None
            ):
                hazard_reference = self._selected_hazard_reference(session_id, session)
                hazard_id = hazard_reference["user_hazard_id"]
                custom_hazard_id = hazard_reference["custom_hazard_id"]
                system_hazard_id = hazard_reference["system_hazard_id"]
                additional_hazard_id = hazard_reference["additional_hazard_id"]
            self.db.add(
                UserQuestionResponse(
                    user_session_id=user_session.id,
                    user_hazard_id=hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    mitigation_measure_id=mitigation_measure_id or session.mitigation_record_id,
                    question_id=question_id,
                    question_option_id=question_option_id,
                    category=category,
                    response_text=response_text,
                    score=score,
                    reason=reason,
                    evidence=evidence,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist question response")

























    def _selected_system_hazard_id(self, session: ChatSession) -> int | None:
        if session.sector_id is None or not session.selected_hazard:
            return None
        hazard_id = self.db.scalar(
            select(SystemHazard.id).where(
                SystemHazard.sector_id == session.sector_id,
                func.lower(SystemHazard.name) == session.selected_hazard.casefold(),
            )
        )
        if isinstance(hazard_id, int):
            return hazard_id
        return None

    def _selected_system_profile_ids(
        self, session: ChatSession, system_hazard_id: int | None
    ) -> list[int]:
        if system_hazard_id is None:
            return []

        selected_profiles = self._selected_hazard_profile_names(session)
        selected_keys = {normalize(profile) for profile in selected_profiles if normalize(profile)}
        selected_variable_keys = {
            normalize(str(profile.get("variable_name") or ""))
            for profile in self._stored_hazard_profiles(
                session,
                session.selected_hazard or session.accepted_custom_hazard or "",
            )
            if normalize(str(profile.get("variable_name") or ""))
        }
        if not selected_keys and not selected_variable_keys:
            return []

        rows = self.db.execute(
            select(
                SystemHazardSocioDemographic.id,
                SystemHazardSocioDemographic.profile,
                SystemHazardSocioDemographic.variable_name,
            ).where(SystemHazardSocioDemographic.system_hazard_id == system_hazard_id)
        ).all()

        profile_ids: list[int] = []
        seen: set[int] = set()
        for row in rows:
            row_id = int(row.id)
            row_keys = {
                normalize(str(row.profile or "")),
                normalize(str(row.variable_name or "")),
            }
            if row_keys & selected_keys or row_keys & selected_variable_keys:
                if row_id not in seen:
                    seen.add(row_id)
                    profile_ids.append(row_id)
        return profile_ids

    def _matched_mitigation_measure_examples(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)

        if not rows:
            return ""

        lines: list[str] = []
        for index, example in enumerate(rows, start=1):
            profile = str(example.profile_label or "Matched profile").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            evidence = str(example.evidence or "").strip()
            country = str(example.country_city or "").strip()
            case_study = str(example.policy_case_study or "").strip()
            reference_links = str(example.reference_links or "").strip()
            details: list[str] = [f"measure: {example.measure}"]
            if summary:
                details.append(f"implementation: {summary}")
            if evidence:
                details.append(f"evidence: {evidence}")
            if country:
                details.append(f"implemented country/city: {country}")
            if case_study or country:
                details.append(
                    f"case: {case_study}{f' ({country})' if country else ''}"
                )
            if reference_links:
                details.append(
                    "reference links: "
                    + self._format_mitigation_reference_links(reference_links)
                )
            lines.append(f"{index}. Profile '{profile}' - " + " | ".join(details))
        return "\n".join(lines)

    def _matched_mitigation_measure_example_rows(
        self, session: ChatSession, limit: int | None = None
    ) -> list[MitigationMeasureExample]:
        if session.sector_id is None:
            return []

        system_hazard_id = self._selected_system_hazard_id(session)

        filters = [
            MitigationMeasureExample.sector_id == session.sector_id,
            MitigationMeasureExample.source == "mm_csv",
        ]
        if system_hazard_id is not None:
            filters.append(MitigationMeasureExample.system_hazard_id == system_hazard_id)

        query = (
            select(MitigationMeasureExample)
            .where(*filters)
            .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = self.db.scalars(query).all()

        if not rows and system_hazard_id is not None:
            fallback_query = (
                select(MitigationMeasureExample)
                .where(
                    MitigationMeasureExample.sector_id == session.sector_id,
                    MitigationMeasureExample.source == "mm_csv",
                )
                .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
            )
            if limit is not None:
                fallback_query = fallback_query.limit(limit)
            rows = self.db.scalars(fallback_query).all()

        return list(rows)

    def _current_policy_implementations_section(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)
        heading = self._policy_section_heading(
            "Current Policy Implementations",
            self._current_policy_implementations_intro(),
        )
        if not rows:
            return (
                f"{heading}\n\n"
                "No matching current policy implementations were found for this "
                "sector, hazard, and profile set."
            )

        sections = [heading]
        grouped_examples: dict[str, dict[str, object]] = {}
        for example in rows:
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if not measure:
                continue
            measure_key = normalize_for_match(measure)
            if not measure_key:
                continue
            group = grouped_examples.setdefault(
                measure_key,
                {
                    "measure": measure,
                    "countries": [],
                    "summaries": [],
                    "evidence": [],
                    "reference_links": [],
                },
            )

            country = normalize_markdown_text(str(example.country_city or "")).strip()
            evidence = normalize_markdown_text(str(example.evidence or "")).strip()
            reference_links = str(example.reference_links or "").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            case_study = normalize_markdown_text(str(example.policy_case_study or "")).strip()
            summary_text = summary or case_study

            self._append_unique_text(group["countries"], country)
            self._append_unique_text(group["summaries"], summary_text)
            self._append_unique_text(group["evidence"], evidence)
            for link in self._mitigation_reference_link_values(reference_links):
                self._append_unique_text(group["reference_links"], link)

        for group in list(grouped_examples.values())[:1]:
            measure = str(group["measure"])
            countries = group["countries"]
            summaries = group["summaries"]
            evidence_items = group["evidence"]
            reference_links = group["reference_links"]

            details: list[str] = []
            if countries:
                details.append(
                    "- **Implemented in:** " + "; ".join(str(item) for item in countries)
                )
            if summaries:
                details.append(
                    "- **Summary:** " + " ".join(str(item) for item in summaries)
                )
            if evidence_items:
                details.append(
                    "- **Evidence:** " + " ".join(str(item) for item in evidence_items)
                )
            if reference_links:
                details.append(
                    "- **Reference links:** "
                    + self._format_mitigation_reference_links("; ".join(str(item) for item in reference_links))
                )

            if not details:
                details.append("- No implementation details were provided for this example.")

            sections.append(
                f"### {self._normalize_current_policy_measure_title(measure)}\n\n"
                + "\n".join(details)
            )

        return "\n\n".join(sections)

    @classmethod
    def _ensure_practical_considerations_intro(cls, markdown: str) -> str:
        intro = (
            "This section translates the selected hazard and affected profiles into "
            "practical design considerations for mitigation. It highlights issues to "
            "check before choosing a measure, such as delivery barriers, targeting, "
            "and implementation risks."
        )
        heading = cls._policy_section_heading(
            "General considerations to mitigate the negative effects",
            intro,
        )
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return heading
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "Practical Considerations",
        )
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
        )
        cleaned = cls._strip_section_intro_paragraph(
            cleaned,
            (
                "practical design considerations",
                "delivery barriers",
                "implementation risks",
                "design trade-offs",
            ),
        )
        if not cleaned:
            return heading
        if cleaned.casefold().lstrip().startswith("## practical considerations"):
            cleaned = cls._strip_policy_section_heading(
                cleaned,
                "Practical Considerations",
            )
        if cleaned.casefold().lstrip().startswith(
            "## general considerations to mitigate the negative effects"
        ):
            cleaned = cls._strip_policy_section_heading(
                cleaned,
                "General considerations to mitigate the negative effects",
            )
        return f"{heading}\n\n{cleaned}"

    @staticmethod
    def _current_policy_implementations_intro() -> str:
        return (
            "This section shows real policy implementations mitigating similar twin transition "
            "policy hazards, relevant to the selected sector and socio-demographic "
            "profiles. For each match, it summarizes where it has been implemented, "
            "the available evidence, and any reference links that support the example."
        )

    @staticmethod
    def _normalize_current_policy_measure_title(title: str) -> str:
        cleaned = normalize_markdown_text(str(title or "")).strip()
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
        if not cleaned:
            return "Current implementation example"
        return cleaned[:1].upper() + cleaned[1:]

    @staticmethod
    def _new_policy_proposals_intro() -> str:
        return (
            "New policy proposals created using the data collection from open labs. The open labs followed a structured co-creation process that began with identifying twin-transition challenges and mapping their systemic causes. Participants then envisioned a fair future transition, translated the required systemic changes into new or improved policy measures, and finally refined and evaluated each proposal for its impact, feasibility, and contribution to an inclusive twin transition."
        )

    @staticmethod
    def _new_policy_proposals_title() -> str:
        return "New policy proposals (Inspiration for the regional mitigation plans)"

    @staticmethod
    def _policy_section_heading(title: str, tooltip: str) -> str:
        safe_title = escape(str(title or "").strip())
        safe_tooltip = escape(str(tooltip or "").strip())
        return (
            f'<h2 class="policy-section-heading">{safe_title} '
            '<span class="policy-section-info" tabindex="0" '
            f'aria-label="{safe_tooltip}" title="{safe_tooltip}">'
            '<span aria-hidden="true">i</span>'
            f'<span class="policy-section-tooltip" aria-hidden="true">{safe_tooltip}</span>'
            "</span></h2>"
        )

    @staticmethod
    def _strip_policy_section_heading(markdown: str, title: str) -> str:
        title_key = normalize_for_match(title)
        kept: list[str] = []
        for line in str(markdown or "").splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip().strip("*_:- ")
            if normalize_for_match(heading_text) == title_key:
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _strip_section_intro_paragraph(markdown: str, markers: tuple[str, ...]) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
        first = parts[0].strip()
        first_key = first.casefold()
        if first and any(marker.casefold() in first_key for marker in markers):
            return parts[1].strip() if len(parts) > 1 else ""
        return cleaned

    @classmethod
    def _practical_considerations_json_to_markdown(cls, response: str) -> tuple[str, list[str]]:
        raw = str(response or "").strip()
        if not raw:
            return "", []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            extracted = cls._extract_json_object(raw)
            if extracted == "{}":
                return raw, []
            try:
                payload = json.loads(extracted)
            except json.JSONDecodeError:
                return raw, []
        if not isinstance(payload, dict):
            return raw, []

        title = cls._clean_practical_json_text(
            payload.get("title"),
            default="# Practical Considerations",
        )
        sections: list[str] = [title]
        panel_items: list[str] = []
        seen_panel_items: set[str] = set()
        themes = payload.get("themes")
        if not isinstance(themes, list):
            themes = []

        for theme in themes:
            if not isinstance(theme, dict):
                continue
            heading = cls._clean_practical_json_text(theme.get("heading"))
            heading_title = cls._markdown_heading_title(heading)
            if not heading_title:
                continue
            heading = f"## {heading_title}"
            panel_key = normalize_for_match(heading_title)
            if panel_key and panel_key not in seen_panel_items:
                seen_panel_items.add(panel_key)
                panel_items.append(heading_title)

            block: list[str] = [heading]
            summary = cls._clean_practical_json_text(theme.get("summary"))
            if summary:
                block.extend(["", summary])

            concerns = theme.get("concerns")
            if isinstance(concerns, list):
                cleaned_concerns = [
                    cls._clean_practical_json_bullet(concern)
                    for concern in concerns
                ]
                cleaned_concerns = [concern for concern in cleaned_concerns if concern]
                if cleaned_concerns:
                    block.extend(["", *cleaned_concerns])

            sections.append("\n".join(block).strip())

        return "\n\n".join(section for section in sections if section.strip()), panel_items

    @staticmethod
    def _clean_practical_json_text(value: object, default: str = "") -> str:
        cleaned = str(value or "").strip()
        cleaned = re.sub(r"^```(?:json|markdown|md)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or default

    @classmethod
    def _clean_practical_json_bullet(cls, value: object) -> str:
        cleaned = cls._clean_practical_json_text(value)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned).strip()
        return f"- {cleaned}" if cleaned else ""

    @classmethod
    def _markdown_heading_title(cls, value: object) -> str:
        cleaned = cls._clean_practical_json_text(value)
        cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        return cleaned.strip(" -#:\t\r\n")

    @classmethod
    def _extract_practical_consideration_items(cls, markdown: str) -> list[str]:
        cleaned = str(markdown or "")
        cleaned = re.sub(
            r'<h[1-6][^>]*class="[^"]*\bpolicy-section-heading\b[^"]*"[^>]*>.*?</h[1-6]>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*\bpolicy-section-tooltip\b[^"]*"[^>]*>.*?</span>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = cls._strip_policy_section_heading(cleaned, "Practical Considerations")
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
        )
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        items: list[str] = []
        current: list[str] = []

        def flush_current() -> None:
            if not current:
                return
            item = cls._clean_practical_consideration_item(" ".join(current))
            current.clear()
            if item and normalize_for_match(item) not in {
                normalize_for_match(existing) for existing in items
            }:
                items.append(item)

        skipping_nested_bullet = False
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            leading_whitespace = len(raw_line) - len(raw_line.lstrip(" \t"))
            if not line:
                flush_current()
                skipping_nested_bullet = False
                continue
            if re.match(r"^\s*#{1,6}\s+", line):
                flush_current()
                skipping_nested_bullet = False
                continue
            bullet_match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
            if bullet_match:
                if leading_whitespace >= 2:
                    skipping_nested_bullet = True
                    continue
                flush_current()
                skipping_nested_bullet = False
                current.append(bullet_match.group(1).strip())
                continue
            if skipping_nested_bullet:
                continue
            if current:
                current.append(line)
            elif len(line) > 24:
                current.append(line)

        flush_current()
        return items

    @staticmethod
    def _clean_practical_consideration_item(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or ""))
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -•\t\r\n")
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations This section translates")
        ):
            return ""
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations i This section translates")
        ):
            return ""
        colon_index = cleaned.find(":")
        if 4 <= colon_index <= 120:
            cleaned = cleaned[:colon_index]
        else:
            sentence_match = re.match(r"^(.{18,120}?[.!?])\s+", cleaned)
            if sentence_match:
                cleaned = sentence_match.group(1).rstrip(".!?")
        return cleaned.strip(" -•\t\r\n.:")

    async def _new_policy_suggestions_section(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> str:
        candidates = self._ranked_new_policy_suggestions(session, limit=limit)
        intro = self._new_policy_proposals_intro()
        heading = self._policy_section_heading(self._new_policy_proposals_title(), intro)
        if not candidates:
            return (
                f"{heading}\n\n"
                "No matching policy proposals were found for this country, sector, "
                "and selected hazard context."
            )

        candidate_context = self._new_policy_suggestion_context(candidates)
        current_policy_context = self._matched_mitigation_measure_examples(session, limit=5)
        context = load_nested_prompt_file("llm/new_policy_suggestion.txt")
        messages = [
            {
                "role": "user",
                "content": (
                    "Create the markdown body for the policy proposal section. "
                    "Do not include the section heading and do not include an "
                    "introductory paragraph; start directly with ONE synthesized "
                    "proposal.\n\n"
                    "Output exactly one proposal, 150-200 words total, using this structure:\n"
                    "### [short proposal title]\n"
                    "- **Proposal:** one clear, user-ready mitigation measure sentence "
                    "tailored to the selected country and region where possible.\n"
                    "- **Top policy basis:** mention that it combines the strongest/top-scored "
                    "MM policy proposals; name only the most relevant policy codes/titles.\n"
                    "- **Target-group mechanisms:** short bullets explaining how each covered "
                    "target group is mitigated.\n"
                    "- **Why this helps:** one short sentence linking the combined measure to "
                    "the selected hazard and high proposal scores.\n\n"
                    "Do not output multiple policy candidates. Do not include a score table. "
                    "Keep it concise and make the proposal sound like a single coherent "
                    "regional mitigation measure that inspires the user to create their own.\n\n"
                    f"Selected country: {session.country or 'Not specified'}\n"
                    f"Selected region: {session.region or 'Not specified'}\n"
                    f"Selected sector: {session.sector or 'Not specified'}\n"
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not specified'}\n"
                    f"Selected socio-demographic profiles:\n{format_all_dgs(session)}\n\n"
                    "Current policy implementation context:\n"
                    f"{current_policy_context or '- No matching current implementation context was found.'}\n\n"
                    f"Candidate policy context:\n{candidate_context}"
                ),
            }
        ]
        for attempt in range(2):
            attempt_messages = messages
            if attempt:
                retry_instruction = {
                    "role": "user",
                    "content": (
                        "Retry once. The previous response could not be used. "
                        "Return only the requested markdown body with the exact "
                        "proposal structure and no introductory paragraph."
                    ),
                }
                attempt_messages = [*messages, retry_instruction]
            response = await ask_llm_chat(
                context=context,
                messages=attempt_messages,
                temperature=0.2,
                max_tokens=1000,
            )
            if response and not is_llm_unavailable_response(response):
                cleaned = self._strip_new_policy_suggestions_heading(response)
                if cleaned:
                    ensured = self._ensure_new_policy_intro(cleaned)
                    if ensured:
                        return heading + "\n\n" + self._format_new_policy_proposal_body(ensured)

        return (
            f"{heading}\n\n"
            "I could not generate a reliable new policy proposal from the matched "
            "policy basis after retrying. Please try again, or continue by writing "
            "your own regional mitigation measure."
        )

    @classmethod
    def _strip_new_policy_suggestions_heading(cls, markdown: str) -> str:
        lines = []
        heading_keys = {
            normalize_for_match("new policy proposals"),
            normalize_for_match(cls._new_policy_proposals_title()),
        }
        for line in str(markdown or "").strip().splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            heading_text = heading_text.strip("*_:- ")
            if normalize_for_match(heading_text) in heading_keys:
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _ranked_new_policy_suggestions(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> list[dict[str, object]]:
        if session.country_id is None or session.sector_id is None:
            return []

        selected_system_hazard_id = self._selected_system_hazard_id(session)
        hazard_target_option_ids = self._selected_system_hazard_target_option_ids(
            session,
            selected_system_hazard_id,
        )

        query = (
            select(
                MitigationMeasurePolicy.id,
                MitigationMeasurePolicy.policy_code,
                MitigationMeasurePolicy.policy_title,
                MitigationMeasurePolicy.policy_type,
                MitigationMeasurePolicy.short_description,
                MitigationMeasurePolicySystemHazard.system_hazard_id,
                MitigationMeasurePolicySystemHazard.mitigation_effect,
                SystemHazard.name.label("hazard_name"),
            )
            .join(
                MitigationMeasurePolicySystemHazard,
                MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id
                == MitigationMeasurePolicy.id,
            )
            .join(
                SystemHazard,
                SystemHazard.id == MitigationMeasurePolicySystemHazard.system_hazard_id,
            )
            .where(
                MitigationMeasurePolicy.country_id == session.country_id,
                MitigationMeasurePolicy.sector_id == session.sector_id,
                MitigationMeasurePolicy.source == "xlsx",
            )
        )
        if selected_system_hazard_id is not None:
            query = query.where(
                MitigationMeasurePolicySystemHazard.system_hazard_id
                == selected_system_hazard_id
            )

        policy_rows = self.db.execute(query).mappings().all()
        if not policy_rows:
            return []

        policy_ids = [int(row["id"]) for row in policy_rows]
        target_rows = self.db.execute(
            select(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id.label("policy_id"),
                MitigationMeasureTargetGroup.question_option_id,
                MitigationMeasureTargetGroup.match_value,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id == MitigationMeasureTargetGroup.question_option_id,
            )
            .join(
                EvaluationQuestion,
                and_(
                    EvaluationQuestion.id == QuestionOption.question_id,
                    EvaluationQuestion.category == "target_population",
                ),
            )
            .where(MitigationMeasureTargetGroup.mitigation_measure_policy_id.in_(policy_ids))
            .order_by(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
        ).mappings().all()

        targets_by_policy: dict[int, list[dict[str, object]]] = {}
        for row in target_rows:
            policy_id = int(row["policy_id"])
            label = self._target_population_label(
                str(row["question"] or ""),
                str(row["option"] or ""),
            )
            targets_by_policy.setdefault(policy_id, []).append(
                {
                    "question_option_id": int(row["question_option_id"]),
                    "label": label,
                    "match_value": str(row["match_value"] or "").strip(),
                }
            )

        candidates: list[dict[str, object]] = []
        for row in policy_rows:
            policy_id = int(row["id"])
            target_groups = targets_by_policy.get(policy_id, [])
            score_details = self._new_policy_suggestion_score(
                mitigation_effect=str(row["mitigation_effect"] or ""),
                target_groups=target_groups,
                hazard_target_option_ids=hazard_target_option_ids,
            )
            if score_details["score"] <= 0:
                continue
            candidates.append(
                {
                    "policy_id": policy_id,
                    "policy_code": str(row["policy_code"] or ""),
                    "policy_title": normalize_markdown_text(str(row["policy_title"] or "")).strip(),
                    "policy_type": normalize_markdown_text(str(row["policy_type"] or "")).strip(),
                    "short_description": normalize_markdown_text(
                        str(row["short_description"] or "")
                    ).strip(),
                    "hazard_name": normalize_markdown_text(str(row["hazard_name"] or "")).strip(),
                    "mitigation_effect": str(row["mitigation_effect"] or "").strip(),
                    "target_groups": target_groups,
                    **score_details,
                }
            )

        candidates.sort(
            key=lambda candidate: (
                float(candidate.get("score") or 0),
                float(candidate.get("hazard_effect_score") or 0),
                float(candidate.get("target_match_score") or 0),
                str(candidate.get("policy_title") or ""),
            ),
            reverse=True,
        )
        return candidates[:limit]

    def _selected_system_hazard_target_option_ids(
        self,
        session: ChatSession,
        system_hazard_id: int | None,
    ) -> set[int]:
        if system_hazard_id is None:
            return self._selected_target_population_option_ids(session)

        profile_ids = self._selected_system_profile_ids(session, system_hazard_id)
        if not profile_ids:
            profile_ids = [
                int(row_id)
                for row_id in self.db.scalars(
                    select(SystemHazardSocioDemographic.id).where(
                        SystemHazardSocioDemographic.system_hazard_id
                        == system_hazard_id
                    )
                ).all()
            ]
        if not profile_ids:
            return self._selected_target_population_option_ids(session)

        option_ids = {
            int(option_id)
            for option_id in self.db.scalars(
                select(
                    SystemHazardSocioDemographicTargetPopulation.question_option_id
                ).where(
                    SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id.in_(
                        profile_ids
                    )
                )
            ).all()
        }
        return option_ids or self._selected_target_population_option_ids(session)

    def _selected_target_population_option_ids(self, session: ChatSession) -> set[int]:
        answer_pairs: set[tuple[str, str]] = set()
        for answer in session.target_population_answers or []:
            question = normalize_for_match(str(answer.get("question") or ""))
            selected = answer.get("selected")
            labels = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for label in labels:
                option = normalize_for_match(str(label or ""))
                if question and option:
                    answer_pairs.add((question, option))
        if not answer_pairs:
            return set()

        rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(EvaluationQuestion.category == "target_population")
        ).all()
        return {
            int(row.id)
            for row in rows
            if (
                normalize_for_match(str(row.question or "")),
                normalize_for_match(str(row.option or "")),
            )
            in answer_pairs
        }

    @staticmethod
    def _new_policy_suggestion_score(
        *,
        mitigation_effect: str,
        target_groups: list[dict[str, object]],
        hazard_target_option_ids: set[int],
    ) -> dict[str, object]:
        effect_key = normalize_for_match(mitigation_effect)
        hazard_effect_score = {
            "high mitigation": 60.0,
            "medium mitigation": 35.0,
            "low mitigation": 15.0,
        }.get(effect_key, 0.0)

        value_scores = {
            "yes": 12.0,
            "partially": 6.0,
        }
        matched_targets: list[dict[str, object]] = []
        target_match_score = 0.0
        if not hazard_target_option_ids:
            return {
                "score": round(hazard_effect_score, 2),
                "hazard_effect_score": hazard_effect_score,
                "target_match_score": 0.0,
                "matched_target_groups": matched_targets,
                "hazard_target_option_count": 0,
            }
        for group in target_groups:
            option_id = int(group.get("question_option_id") or 0)
            if option_id not in hazard_target_option_ids:
                continue
            value = str(group.get("match_value") or "").strip()
            value_score = value_scores.get(value.casefold(), 0.0)
            if value_score <= 0:
                continue
            matched_targets.append(group)
            target_match_score += value_score

        target_match_score = min(40.0, target_match_score)
        return {
            "score": round(hazard_effect_score + target_match_score, 2),
            "hazard_effect_score": hazard_effect_score,
            "target_match_score": round(target_match_score, 2),
            "matched_target_groups": matched_targets,
            "hazard_target_option_count": len(hazard_target_option_ids),
        }

    def _new_policy_suggestion_context(self, candidates: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            matched_targets = candidate.get("matched_target_groups")
            target_groups = candidate.get("target_groups")
            matched_labels = self._policy_target_group_summary(
                matched_targets if isinstance(matched_targets, list) else []
            )
            all_target_labels = self._policy_target_group_summary(
                target_groups if isinstance(target_groups, list) else []
            )
            lines.append(
                "\n".join(
                    [
                        f"{index}. Policy code: {candidate.get('policy_code')}",
                        f"   Title: {candidate.get('policy_title')}",
                        f"   Type: {candidate.get('policy_type') or 'Not specified'}",
                        f"   Description: {candidate.get('short_description') or 'Not specified'}",
                        f"   Related system hazard: {candidate.get('hazard_name')}",
                        f"   Hazard mitigation effect: {candidate.get('mitigation_effect')}",
                        f"   Matched target groups: {matched_labels or 'None'}",
                        f"   All policy target groups: {all_target_labels or 'None'}",
                        (
                            f"   Score: {candidate.get('score')}/100 "
                            f"(hazard effect {candidate.get('hazard_effect_score')}, "
                            f"target match {candidate.get('target_match_score')})"
                        ),
                    ]
                )
            )
        return "\n\n".join(lines)

    def _fallback_new_policy_suggestions_section(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> str:
        sections = [
            self._policy_section_heading(
                self._new_policy_proposals_title(),
                self._new_policy_proposals_intro(),
            )
        ]
        top_candidate = candidates[0]
        matched_targets: list[dict[str, object]] = []
        all_targets: list[dict[str, object]] = []
        source_policy_lines: list[str] = []
        action_parts: list[str] = []
        for candidate in candidates:
            candidate_targets = candidate.get("matched_target_groups")
            if isinstance(candidate_targets, list):
                matched_targets.extend(candidate_targets)
            candidate_all_targets = candidate.get("target_groups")
            if isinstance(candidate_all_targets, list):
                all_targets.extend(candidate_all_targets)
            title = str(candidate.get("policy_title") or "Untitled policy").strip()
            code = str(candidate.get("policy_code") or "Policy").strip()
            effect = str(candidate.get("mitigation_effect") or "relevant mitigation effect").strip()
            description = str(candidate.get("short_description") or "").strip()
            source_policy_lines.append(
                f"{code}: {title}"
                + (f" ({effect})" if effect else "")
            )
            if description:
                self._append_unique_text(action_parts, description)

        target_groups = matched_targets or all_targets
        target_mechanisms = self._fallback_target_group_mechanisms(target_groups)
        policy_basis = "; ".join(source_policy_lines[:3])
        hazard_name = str(top_candidate.get("hazard_name") or "the selected hazard").strip()
        effect_label = str(top_candidate.get("mitigation_effect") or "relevant").strip()
        action_summary = (
            action_parts[:3]
            if action_parts
            else [f"Adapt the strongest scored policy actions to reduce {hazard_name.lower()}."]
        )
        body = (
            "### Integrated Regional Mitigation Support Package\n\n"
            f"- **Proposal:** In {self._session_place_label_for_sentence(session)}, "
            f"combine the top-scored MM policy proposals into a regional support package "
            f"that reduces **{hazard_name}** through targeted assistance, delivery "
            "guidance, and safeguards for affected groups.\n"
            f"- **Top policy basis:** {policy_basis or 'Top-ranked MM policy proposals'}.\n"
            "- **Target-group mechanisms:**\n"
            + "\n".join(f"    - {item}" for item in target_mechanisms)
            + "\n"
            f"- **Why this helps:** The proposal is a strong inspiration because its source "
            f"policies have **{effect_label}** mitigation relevance and combine complementary "
            "actions: "
            + "; ".join(action_summary[:2])
            + "."
        )
        sections.append(self._format_new_policy_proposal_body(body))
        return "\n\n".join(sections)

    @staticmethod
    def _session_place_label_for_sentence(session: ChatSession) -> str:
        region = str(session.region or "").strip()
        country = str(session.country or "").strip()
        if region and country:
            return f"{region}, {country}"
        return region or country or "the selected region"

    def _fallback_target_group_mechanisms(
        self,
        target_groups: list[dict[str, object]],
        *,
        limit: int = 5,
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip().casefold()
            if not label or value == "pp":
                continue
            key = normalize_for_match(label)
            if key and key not in seen:
                seen.add(key)
                labels.append(label)
        if not labels:
            return [
                "Affected profiles — use the selected hazard profiles to target support and prevent exclusion."
            ]
        return [
            f"{label} — receives tailored support, reduced exposure to the hazard, and clearer access to the measure."
            for label in labels[:limit]
        ]

    @classmethod
    def _ensure_new_policy_intro(cls, markdown: str) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        cleaned = cls._strip_section_intro_paragraph(
            cleaned,
            (
                "candidate policies",
                "hazard mitigation effect",
                "target-group overlap",
                "policy database",
            ),
        )
        return cleaned

    @classmethod
    def _format_new_policy_proposal_body(cls, markdown: str) -> str:
        cleaned = cls._normalize_target_group_mechanism_indentation(markdown)
        return cls._append_top_policy_basis_to_proposal(cleaned)

    @staticmethod
    def _normalize_target_group_mechanism_indentation(markdown: str) -> str:
        lines: list[str] = []
        in_target_group_block = False
        for raw_line in str(markdown or "").splitlines():
            line = raw_line.rstrip()
            section_key = normalize_for_match(line)
            if "target group mechanisms" in section_key:
                in_target_group_block = True
                lines.append(line)
                continue
            if in_target_group_block and re.match(r"^\s*[-*]\s+\*\*(?:why this helps|proposal|top policy basis)\s*:", line, flags=re.IGNORECASE):
                in_target_group_block = False
            if in_target_group_block and re.match(r"^\s{0,3}[-*]\s+", line):
                lines.append("    " + line.lstrip())
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @classmethod
    def _append_top_policy_basis_to_proposal(cls, markdown: str) -> str:
        text = str(markdown or "").strip()
        basis_match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Top policy basis:\*\*\s*(?P<basis>.+?)\s*$",
            text,
        )
        if not basis_match:
            return text

        basis = cls._clean_policy_basis_source(basis_match.group("basis"))
        text_without_basis = (
            text[: basis_match.start()] + text[basis_match.end() :]
        ).strip()
        if not basis:
            return re.sub(r"\n{3,}", "\n\n", text_without_basis)

        def append_source(match: re.Match[str]) -> str:
            proposal = match.group("proposal").rstrip()
            proposal = cls._strip_policy_source_reference(proposal)
            safe_basis = escape(basis)
            return (
                f"{match.group('prefix')}{proposal} "
                '<span class="policy-section-info proposal-source-info" '
                f'tabindex="0" aria-label="{safe_basis}" title="{safe_basis}">'
                '<span aria-hidden="true">i</span>'
                f'<span class="policy-section-tooltip" aria-hidden="true">{safe_basis}</span>'
                "</span>"
            )

        updated, count = re.subn(
            r"(?im)^(?P<prefix>\s*[-*]\s*\*\*Proposal:\*\*\s*)(?P<proposal>.+?)\s*$",
            append_source,
            text_without_basis,
            count=1,
        )
        return re.sub(r"\n{3,}", "\n\n", updated if count else text_without_basis).strip()

    @staticmethod
    def _clean_policy_basis_source(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or "")).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;")
        return cleaned

    @staticmethod
    def _strip_policy_source_reference(value: str) -> str:
        return re.sub(
            r"\s*\[(?:Source|Sources):\s*[^\]]+\]\s*$",
            "",
            str(value or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    @classmethod
    def _extract_suggested_policy_proposal(cls, markdown: str) -> str:
        text = str(markdown or "")
        title = ""
        title_match = re.search(r"(?im)^\s*###\s+(.+?)\s*$", text)
        if title_match:
            title = normalize_markdown_text(title_match.group(1))
            title = re.sub(r"\*\*(.*?)\*\*", r"\1", title)
            title = re.sub(r"\*(.*?)\*", r"\1", title)
            title = re.sub(r"\s+", " ", title).strip()
        match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Proposal:\*\*\s*(.+?)\s*$",
            text,
        )
        if not match:
            match = re.search(r"(?im)^\s*[-*]\s*Proposal:\s*(.+?)\s*$", text)
        if not match:
            return ""
        proposal = normalize_markdown_text(match.group(1))
        proposal = re.sub(r"\*\*(.*?)\*\*", r"\1", proposal)
        proposal = re.sub(r"\*(.*?)\*", r"\1", proposal)
        proposal = cls._strip_policy_source_reference(proposal)
        proposal = re.sub(r"\s+", " ", proposal).strip()
        if title and proposal and not normalize_for_match(proposal).startswith(
            normalize_for_match(title)
        ):
            return f"{title}: {proposal}"
        return proposal

    @staticmethod
    def _policy_target_group_summary(target_groups: list[dict[str, object]]) -> str:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip()
            if value.casefold() == "pp":
                continue
            if not label:
                continue
            rendered = f"{label} ({value})" if value else label
            key = normalize_for_match(rendered)
            if key and key not in seen:
                seen.add(key)
                labels.append(rendered)
        return "; ".join(labels)

    @staticmethod
    def _target_population_label(question: str, option: str) -> str:
        question = str(question or "").strip()
        option = str(option or "").strip()
        if question and option:
            return f"{question}: {option}"
        return question or option

    def _current_policy_mitigation_measure(self, session: ChatSession) -> str:
        for example in self._matched_mitigation_measure_example_rows(session, limit=None):
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if measure:
                return measure
        return ""

    @staticmethod
    def _append_unique_text(items: object, value: str) -> None:
        if not isinstance(items, list):
            return
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return
        if normalize_for_match(cleaned) not in {normalize_for_match(str(item)) for item in items}:
            items.append(cleaned)

    @staticmethod
    def _mitigation_reference_link_values(reference_links: str) -> list[str]:
        links = re.findall(r"https?://[^\s;,]+", reference_links)
        if links:
            return links
        cleaned = re.sub(r"\s+", " ", reference_links or "").strip()
        return [cleaned] if cleaned else []

    @staticmethod
    def _simplify_mitigation_implementation_summary(summary: str) -> str:
        cleaned = normalize_markdown_text(str(summary or "")).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(
            r'(?i)^for\s+the\s+profile\s+["“][^"”]+["”]\s*,?\s*',
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)^for\s+the\s+profile\s+[^,]+,\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""
        return cleaned[:1].upper() + cleaned[1:]

    @staticmethod
    def _format_mitigation_reference_links(reference_links: str) -> str:
        links = re.findall(r"https?://[^\s;,]+", reference_links)
        if not links:
            return reference_links.strip()
        return "; ".join(f"[Reference {index}]({link})" for index, link in enumerate(links, start=1))




    def _existing_mitigation_records_for_selected_hazard(
        self, session: ChatSession
    ) -> list[UserMitigationMeasure]:
        hazard_name = session.selected_hazard or session.accepted_custom_hazard
        if not hazard_name:
            return []
        try:
            if self._is_saved_custom_hazard(session, hazard_name):
                query = (
                    select(UserMitigationMeasure)
                    .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
                    .join(UserSession, UserSession.id == UserHazard.user_session_id)
                    .where(
                        UserHazard.name == hazard_name,
                        UserHazard.sector_id == session.sector_id,
                        UserSession.country_id == session.country_id,
                        UserHazard.region_id.is_(None)
                        if session.region_id is None
                        else UserHazard.region_id == session.region_id,
                    )
                    .order_by(UserMitigationMeasure.id)
                )
            else:
                system_hazard_id = None
                additional_hazard_id = None
                if self._is_additional_hazard(session, hazard_name):
                    additional_hazard_id = self._selected_additional_hazard_id(
                        session, hazard_name
                    )
                else:
                    system_hazard_id = self.db.scalar(
                        select(SystemHazard.id).where(
                            SystemHazard.sector_id == session.sector_id,
                            func.lower(SystemHazard.name) == hazard_name.casefold(),
                        )
                    )
                if system_hazard_id is None and additional_hazard_id is None:
                    return []
                query = (
                    select(UserMitigationMeasure)
                    .join(UserSession, UserSession.id == UserMitigationMeasure.user_session_id)
                    .where(
                        UserSession.country_id == session.country_id,
                        UserSession.region_id.is_(None)
                        if session.region_id is None
                        else UserSession.region_id == session.region_id,
                        UserSession.sector_id == session.sector_id,
                        UserMitigationMeasure.system_hazard_id == system_hazard_id,
                        UserMitigationMeasure.additional_hazard_id == additional_hazard_id,
                    )
                    .order_by(UserMitigationMeasure.id)
                )
            if self.user_id is not None:
                query = query.where(UserSession.user_id == self.user_id)
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load mitigation measures for duplicate check")
            return []
        records: list[UserMitigationMeasure] = []
        seen: set[str] = set()
        for row in rows:
            measure = str(row.measure or "").strip()
            key = normalize(measure)
            if not measure or key in seen:
                continue
            seen.add(key)
            records.append(row)
        return records

    def _existing_mitigation_measures_for_selected_hazard(
        self, session: ChatSession
    ) -> list[str]:
        return [
            record.measure
            for record in self._existing_mitigation_records_for_selected_hazard(session)
            if str(record.measure or "").strip()
        ]






    def _suggested_mitigation_record(self, session: ChatSession) -> UserMitigationMeasure | None:
        if session.suggested_mitigation_measure_id is None:
            return None
        try:
            return self.db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == session.suggested_mitigation_measure_id,
                )
            )
        except Exception:
            logger.exception("Failed to load suggested mitigation measure")
            return None

    def _suggested_mitigation_reason(self, session: ChatSession) -> str:
        record = self._suggested_mitigation_record(session)
        if record is None:
            return "No saved reason was found for this mitigation measure."
        return record.reason or "No saved reason was found for this mitigation measure."

    def _suggested_mitigation_evaluation_report(self, session: ChatSession) -> str:
        if session.suggested_mitigation_measure_id is None:
            return "- No evaluation report was found for this mitigation measure."
        try:
            rows = self.db.execute(
                select(
                    UserQuestionResponse.category,
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                    UserQuestionResponse.score,
                    UserQuestionResponse.reason,
                    UserQuestionResponse.evidence,
                )
                .outerjoin(
                    EvaluationQuestion,
                    EvaluationQuestion.id == UserQuestionResponse.question_id,
                )
                .where(
                    UserQuestionResponse.mitigation_measure_id
                    == session.suggested_mitigation_measure_id
                )
                .order_by(UserQuestionResponse.id)
            ).all()
        except Exception:
            logger.exception("Failed to load suggested mitigation evaluation report")
            return "- No evaluation report was found for this mitigation measure."

        if not rows:
            return "- No evaluation report was found for this mitigation measure."

        lines: list[str] = []
        for category, question, response_text, score, reason, evidence in rows:
            category_label = str(category or "Evaluation")
            question_label = normalize_markdown_text(str(question or category_label))
            lines.append(f"- **{category_label}: {question_label}**")
            if score is not None:
                lines.append(f"  - Score: **{score} / 10**")
            elif response_text:
                lines.append(f"  - Response: {response_text}")
            if reason:
                lines.append(f"  - Reason: {reason}")
            if evidence:
                lines.append(f"  - Evidence: {evidence}")
        return "\n".join(lines)

