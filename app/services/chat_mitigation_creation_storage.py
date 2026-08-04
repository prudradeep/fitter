# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *


class ChatMitigationCreationStorageMixin:
    def _store_mitigation_measure(
        self,
        *,
        existing_id: str | None = None,
        user_session_id: str | None,
        user_hazard_id: str | None,
        custom_hazard_id: str | None,
        system_hazard_id: str | None,
        additional_hazard_id: str | None,
        mitigation_measure: str,
        reason: str,
        target_population: list[str] | None = None,
        validation_mode: str = "strict",
        is_crowd_sourced: bool = False,
    ) -> str | None:
        if (
            user_hazard_id is None
            and custom_hazard_id is None
            and system_hazard_id is None
            and additional_hazard_id is None
        ):
            return None
        normalized_validation_mode = self._validation_mode(validation_mode)
        normalized_is_crowd_sourced = (
            normalized_validation_mode == "strict" and bool(is_crowd_sourced)
        )
        target_population_json = (
            json.dumps(target_population, ensure_ascii=False)
            if target_population
            else None
        )
        try:
            row = self.db.get(UserMitigationMeasure, existing_id) if existing_id else None
            if row is None:
                row = self._existing_mitigation_measure_row(
                    user_session_id=user_session_id,
                    user_hazard_id=user_hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    mitigation_measure=mitigation_measure,
                    reason=reason,
                    target_population_json=target_population_json,
                    validation_mode=normalized_validation_mode,
                    is_crowd_sourced=normalized_is_crowd_sourced,
                )
            if row is None:
                row = UserMitigationMeasure(
                    user_session_id=user_session_id,
                    user_hazard_id=user_hazard_id,
                    custom_hazard_id=custom_hazard_id,
                    system_hazard_id=system_hazard_id,
                    additional_hazard_id=additional_hazard_id,
                    measure=mitigation_measure,
                    reason=reason,
                    target_population=target_population_json,
                    validation_mode=normalized_validation_mode,
                    is_crowd_sourced=normalized_is_crowd_sourced,
                )
                self.db.add(row)
            else:
                row.user_session_id = user_session_id
                row.user_hazard_id = user_hazard_id
                row.custom_hazard_id = custom_hazard_id
                row.system_hazard_id = system_hazard_id
                row.additional_hazard_id = additional_hazard_id
                row.measure = mitigation_measure
                row.reason = reason
                row.target_population = target_population_json
                row.validation_mode = normalized_validation_mode
                row.is_crowd_sourced = normalized_is_crowd_sourced
            self.db.commit()
            self.db.refresh(row)
            return row.id
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist mitigation measure")
            return None

    def _existing_mitigation_measure_row(
        self,
        *,
        user_session_id: str | None,
        user_hazard_id: str | None,
        custom_hazard_id: str | None,
        system_hazard_id: str | None,
        additional_hazard_id: str | None,
        mitigation_measure: str,
        reason: str,
        target_population_json: str | None,
        validation_mode: str,
        is_crowd_sourced: bool,
    ) -> UserMitigationMeasure | None:
        def column_matches(column, value):
            return column.is_(None) if value is None else column == value

        return self.db.scalar(
            select(UserMitigationMeasure)
            .where(
                column_matches(UserMitigationMeasure.user_session_id, user_session_id),
                column_matches(UserMitigationMeasure.user_hazard_id, user_hazard_id),
                column_matches(UserMitigationMeasure.custom_hazard_id, custom_hazard_id),
                column_matches(UserMitigationMeasure.system_hazard_id, system_hazard_id),
                column_matches(UserMitigationMeasure.additional_hazard_id, additional_hazard_id),
                UserMitigationMeasure.measure == mitigation_measure,
                UserMitigationMeasure.reason == reason,
                column_matches(UserMitigationMeasure.target_population, target_population_json),
                UserMitigationMeasure.validation_mode == validation_mode,
                UserMitigationMeasure.is_crowd_sourced.is_(is_crowd_sourced),
            )
            .order_by(UserMitigationMeasure.id.desc())
        )

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
        question_id: str | None,
        category: str | None,
        response_text: str | None = None,
        question_option_id: str | None = None,
        score: int | None = None,
        reason: str | None = None,
        evidence: str | None = None,
        hazard_id: str | None = None,
        custom_hazard_id: str | None = None,
        system_hazard_id: str | None = None,
        additional_hazard_id: str | None = None,
        mitigation_measure_id: str | None = None,
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
