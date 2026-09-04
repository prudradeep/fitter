import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.llm import ask_llm_chat
from app.models import (
    Country,
    CustomHazardProfile,
    EurostatPopulationCache,
    EvaluationQuestion,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicPopulationMatch,
    UserHazard,
    UserHazardSocioDemographic,
    UserQuestionResponse,
    UserSession,
)
from app.services.chat_formatters import normalize_markdown_text
from app.services.chat_hazard_duplicates import hazard_similarity_words, local_similar_hazards
from app.services.chat_json import parse_json_array, parse_json_object
from app.services.chat_options import (
    EVALUATION_CATEGORIES,
    compact_for_match,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.chat_session import ChatSession
from app.services.hazard_profile_parsing import (
    extract_socio_demographic_profiles,
    parse_hazard_profile_items,
)
from app.services.knowledge_base import (
    TEMPORARY_KB_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
)
from app.services.prompt_loader import load_nested_prompt_file
from app.services.sector_prompt_rag import section_five_primary_data, strip_rule_lines

logger = logging.getLogger("app.services.chat_hazard_creation")


class ChatHazardProfilesMixin:
    async def _synthesize_target_population_profile(self, session: ChatSession) -> None:
        hazard = session.accepted_custom_hazard
        answers = session.target_population_answers or []
        if not hazard or not answers:
            return
        questions_by_id = {
            str(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        answers_by_id = {
            str(answer.get("question_id") or ""): answer
            for answer in answers
            if str(answer.get("question_id") or "").strip()
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
    def _temporary_evidence_document_ids(evidence: str | None) -> list[str]:
        if not evidence:
            return []
        return [
            match
            for match in re.findall(
                r"Temporary evidence document ID:\s*([A-Za-z0-9-]+)",
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
        cache_ids: set[str] = set()
        for population_profile in population_profiles:
            cache_id = str(population_profile.get("eurostat_population_cache_id") or "").strip()
            if not cache_id:
                continue
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
            if str(population_profile.get("eurostat_population_cache_id") or "").strip()
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
        seen: set[tuple[str, str]] = set()
        for group in match_groups:
            for profile in group:
                cache_id = str(profile.get("eurostat_population_cache_id") or "").strip()
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
