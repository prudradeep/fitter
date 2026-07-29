import re
from dataclasses import asdict

from sqlalchemy import select

from app.models import Country, Region
from app.schemas import ChatResponse
from app.services.chat_formatters import format_all_dgs, normalize_markdown_text
from app.services.chat_options import (
    ADD_DGS_OPTIONS,
    DG_REASON_EVIDENCE_OPTIONS,
    FUZZY_CONFIRMATION_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    HAZARD_EVIDENCE_DECISION_OPTIONS,
    HAZARD_EVIDENCE_INPUT_OPTIONS,
    MITIGATION_DUPLICATE_OPTIONS,
    MITIGATION_REVIEW_OPTIONS,
    OTHER_NAV_OPTIONS,
    POST_SECTOR_OPTIONS,
    REASON_CONFIRMATION_OPTIONS,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    normalize,
    option_list,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import default_custom_hazard_state
from app.services.message_renderer import render_message


class ChatNavigationStepsMixin:
    def _country_step(
        self, session_id: str, session: ChatSession, bot_message: str, error: bool = False
    ) -> ChatResponse:
        countries = self.db.scalars(select(Country).order_by(Country.name)).all()
        return ChatResponse(
            session_id=session_id,
            step="country",
            bot_message=bot_message,
            options=option_list(list(countries)),
            session=session.summary(),
            error=error,
        )

    async def _handle_pending_fuzzy_option(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse | None:
        action = normalize(message)
        if action == normalize("Yes"):
            selected_option = session.pending_fuzzy_option
            session.pending_fuzzy_option = None
            if selected_option:
                if normalize(selected_option) == normalize("Dive deeper into statistical findings"):
                    return self._stats_deep_dive_dialog_step(session_id, session)
                return await self._chat_response(session_id, session, selected_option)

        if action == normalize("No"):
            session.pending_fuzzy_option = None
            return self._repeat_current_options(
                session_id,
                session,
                self.fuzzy_rejected_message,
                error=False,
            )

        if self._is_invalid_user_text(message):
            return ChatResponse(
                session_id=session_id,
                step="fuzzy_confirmation",
                bot_message=self._invalid_text_message(),
                options=FUZZY_CONFIRMATION_OPTIONS,
                session=session.summary(),
                error=True,
            )

        return ChatResponse(
            session_id=session_id,
            step="fuzzy_confirmation",
            bot_message=self.invalid_message,
            options=FUZZY_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _fuzzy_confirmation_step(
        self, session_id: str, session: ChatSession, option_label: str
    ) -> ChatResponse:
        session.pending_fuzzy_option = option_label
        return ChatResponse(
            session_id=session_id,
            step="fuzzy_confirmation",
            bot_message=render_message("fuzzy_confirmation.md", option=option_label),
            options=FUZZY_CONFIRMATION_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_other_nav_action(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse | None:
        if normalize(message) not in {normalize(option) for option in OTHER_NAV_OPTIONS}:
            return None

        action = normalize(message)
        if action == normalize("Analyse another hazard in the same sector"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new hazard"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            self._clear_selected_hazard_context(session)
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            session.pending_hazard_title_clarification_question = None
            session.pending_hazard_title_clarification_answers = []
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Write hazard again"):
            if session.sector is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            hazard_to_rewrite = session.accepted_custom_hazard or session.selected_hazard
            if hazard_to_rewrite and session.custom_hazards:
                session.custom_hazards = [
                    hazard
                    for hazard in session.custom_hazards
                    if normalize(hazard) != normalize(hazard_to_rewrite)
                ]
            self._clear_selected_hazard_context(session)
            session.phase = "custom_hazard_input"
            session.custom_hazard = default_custom_hazard_state()
            session.pending_hazard_title_clarification_question = None
            session.pending_hazard_title_clarification_answers = []
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=render_message("add_hazard.md", sector=session.sector),
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=False,
            )

        if action == normalize("Write mitigation measure again"):
            if session.selected_hazard is None:
                return self._repeat_current_options(session_id, session, self.invalid_message, True)
            session.phase = "mitigation_measure"
            session.pending_mitigation_measure = None
            self._clear_mitigation_clarity_state(session)
            session.mitigation_measure = None
            session.mitigation_reason = None
            session.mitigation_record_id = None
            self._clear_mitigation_validation_state(session)
            session.evaluation_questions = None
            session.evaluation_index = 0
            session.evaluation_answers = None
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

        if action == normalize("Select another region"):
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self._clear_region_context(session)
            session.phase = "region"
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            return ChatResponse(
                session_id=session_id,
                step="region",
                bot_message=render_message(
                    "country_selected.md",
                    country=session.country or "your selected country",
                ),
                options=option_list(list(regions)),
                session=session.summary(),
                error=False,
            )

        if action == normalize("Choose a different sector"):
            if session.country_id is None:
                return self._country_step(session_id, session, self.invalid_message, True)
            self._clear_sector_context(session)
            session.phase = "sector"
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=render_message(
                    "region_selected.md",
                    region=session.region or session.country or "your selected country",
                ),
                options=option_list(self._sectors_for_country(session.country_id)),
                session=session.summary(),
                error=False,
            )

        if action == normalize("Start over with a different country"):
            self._reset_session(session)
            return self._country_step(
                session_id,
                session,
                await self._intro_message_from_llm(session_id),
            )

        return None

    @classmethod
    def _clear_sector_context(cls, session: ChatSession) -> None:
        session.sector_id = None
        session.sector = None
        session.hazards = None
        session.hazard_profiles = None
        session.custom_hazards = None
        session.additional_hazards = None
        cls._clear_selected_hazard_context(session)

    @classmethod
    def _clear_region_context(cls, session: ChatSession) -> None:
        session.region_id = None
        session.region = None
        cls._clear_sector_context(session)

    @classmethod
    def _clear_selected_hazard_context(cls, session: ChatSession) -> None:
        session.pending_hazard = None
        session.selected_hazard = None
        session.selected_hazard_record_id = None
        session.socio_demographic_findings = None
        session.socio_demographic_profiles = None
        session.additional_dgs = None
        session.pending_additional_dgs = None
        session.additional_dg_answers = None
        session.stats_conversation = None
        session.dg_reason = None
        session.dg_evidence = None
        session.pending_mitigation_measure = None
        cls._clear_mitigation_clarity_state(session)
        session.suggested_mitigation_measure_id = None
        session.suggested_mitigation_measure_name = None
        session.mitigation_measure = None
        session.mitigation_reason = None
        session.mitigation_target_population = None
        session.mitigation_record_id = None
        cls._clear_mitigation_validation_state(session)
        session.evaluation_questions = None
        session.evaluation_index = 0
        session.evaluation_answers = None
        session.target_population_questions = None
        session.target_population_index = 0
        session.target_population_answers = None
        session.saved_target_population_answers = None
        session.accepted_custom_hazard = None
        session.accepted_custom_hazard_reason = None
        session.accepted_custom_hazard_evidence = None
        session.accepted_custom_hazard_id = None
        session.accepted_custom_hazard_record_id = None
        session.pending_hazard_reason = None
        session.pending_hazard_evidence = None
        session.pending_hazard_clarification_question = None
        session.pending_hazard_clarification_answer = None
        session.pending_hazard_title_clarification_question = None
        session.pending_hazard_title_clarification_answers = []
        session.pending_fuzzy_option = None
        session.pending_selection = None
        session.pending_selection_confirmation = None
        session.pending_selection_action = None
        session.stats_dialog_conversation = None

    @staticmethod
    def _reset_session(session: ChatSession) -> None:
        fresh_session = ChatSession()
        for key, value in asdict(fresh_session).items():
            setattr(session, key, value)

    @staticmethod
    def _clear_mitigation_clarity_state(session: ChatSession) -> None:
        session.pending_mitigation_reason = None
        session.pending_mitigation_evidence = None
        session.pending_mitigation_clarity_dimension = None
        session.mitigation_clarity_turns = 0
        session.mitigation_clarification_history = None
        session.mitigation_frozen_inputs = None

    @staticmethod
    def _clear_mitigation_validation_state(session: ChatSession) -> None:
        session.mitigation_validation = None
        session.mitigation_grounded_synthesis = None

    def _clarity_validation_details(
        self,
        clarity: dict[str, object],
        session: ChatSession,
        active_dimension: str | None = None,
        clarification_questions: list[str] | None = None,
    ) -> dict[str, object]:
        dimensions = clarity.get("dimensions")
        return {
            "phase": "clarity",
            "title": "Mitigation clarification status",
            "dimensions": dimensions if isinstance(dimensions, dict) else {},
            "active_dimension": active_dimension,
            "clarification_questions": clarification_questions or [],
            "metrics": {
                "clarification_turn": session.mitigation_clarity_turns,
                "clarification_turn_cap": self.mitigation_clarity_turn_cap,
            },
            "checks": {
                "groundedness": "PENDING_INPUT_FREEZE",
                "reranker": "PENDING_INPUT_FREEZE",
                "entailment": "PENDING_INPUT_FREEZE",
            },
            "reason": str(clarity.get("reason") or "").strip(),
        }

    def _grounding_validation_details(
        self,
        session: ChatSession,
        validation: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        validation = validation or session.mitigation_validation
        if not isinstance(validation, dict):
            return None
        return {
            "phase": "grounding",
            "title": "Mitigation grounding status",
            "dimensions": validation.get("dimensions") or {},
            "metrics": {
                "outcome": validation.get("outcome"),
                "rubric_coverage": validation.get("rubric_coverage"),
                "retrieval_support": validation.get("retrieval_support"),
                "verdict_stability": validation.get("verdict_stability"),
                "sample_count": validation.get("sample_count"),
                "confidence_score": validation.get("confidence_score"),
            },
            "checks": {
                "support_corpus": validation.get("support_label"),
                "reranker": self.grounding_models.reranker_status,
                "entailment": self.grounding_models.nli_status,
                "evidence_contradiction": validation.get("evidence_contradiction"),
            },
            "reason": str(validation.get("reason") or "").strip(),
        }

    def _attach_other_options(self, response: ChatResponse, session: ChatSession) -> None:
        self._apply_country_profile_count(response, session)
        main_options = {normalize(option.label) for option in response.options}
        response_specific_options = [
            option
            for option in (response.other_options or [])
            if normalize(option) not in main_options
        ]
        existing_options = {normalize(option) for option in response_specific_options}
        response.other_options = response_specific_options + [
            option
            for option in self._other_nav_options(session, response.step)
            if normalize(option) not in main_options
            and normalize(option) not in existing_options
        ]

    def _apply_country_profile_count(self, response: ChatResponse, session: ChatSession) -> None:
        response.session.affected_profile_count = session.eligible_hazard_profile_count()

    def _valid_sdp_variable_name(
        self, session: ChatSession, variable_name: str | None
    ) -> str:
        cleaned = normalize_markdown_text(str(variable_name or "")).strip().strip(".:;,- ")
        if not cleaned:
            return ""
        prefixed_match = re.match(
            r"^(?:PREDICTOR\s+)?[0-9]+[A-Z]\s*:\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if prefixed_match:
            cleaned = prefixed_match.group(1).strip().strip(".:;,- ")
            variable_token = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", cleaned)
            if variable_token:
                return variable_token.group(1)
        predictor_id = self._predictor_id_from_variable_name(cleaned)
        if predictor_id:
            return self._predictor_variable_name_from_prompt(session, predictor_id)
        return cleaned

    @staticmethod
    def _predictor_id_from_variable_name(variable_name: str) -> str | None:
        normalized = normalize_markdown_text(variable_name).strip().strip(".:;,- ")
        match = re.fullmatch(
            r"(?:PREDICTOR\s+)?([0-9]+[A-Z])",
            normalized,
            flags=re.IGNORECASE,
        )
        return match.group(1).upper() if match else None

    def _predictor_variable_name_from_prompt(
        self, session: ChatSession, predictor_id: str
    ) -> str:
        _ = session, predictor_id
        return ""

    @staticmethod
    def _other_nav_options(session: ChatSession, step: str) -> list[str]:
        options: list[str] = []
        if session.mitigation_measure or session.pending_mitigation_measure:
            options.append("Write mitigation measure again")
        if session.sector and session.selected_hazard:
            options.append("Analyse another hazard in the same sector")
        if session.sector and step != "sector":
            options.append("Add a new hazard")
        if session.sector and (
            session.accepted_custom_hazard
            or session.pending_hazard
            or (
                session.selected_hazard
                and session.custom_hazards
                and normalize(session.selected_hazard)
                in {normalize(hazard) for hazard in session.custom_hazards}
            )
        ):
            options.append("Write hazard again")
        if session.sector and step != "sector":
            options.append("Choose a different sector")
        if session.country and session.region_id is not None and step != "region":
            options.append("Select another region")
        if session.country:
            options.append("Start over with a different country")
        return options

    def _repeat_current_options(
        self,
        session_id: str,
        session: ChatSession,
        bot_message: str | None = None,
        error: bool = True,
    ) -> ChatResponse:
        message = bot_message or self.invalid_message
        if session.country is None:
            return self._country_step(session_id, session, message, error)

        if session.region is None:
            regions = self.db.scalars(
                select(Region).where(Region.country_id == session.country_id).order_by(Region.name)
            ).all()
            return ChatResponse(
                session_id=session_id,
                step="region",
                bot_message=message,
                options=option_list(list(regions)),
                session=session.summary(),
                error=error,
            )

        if session.sector is None:
            return ChatResponse(
                session_id=session_id,
                step="sector",
                bot_message=message,
                options=option_list(self._sectors_for_country(session.country_id)),
                session=session.summary(),
                error=error,
            )

        if session.phase == "hazards":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=POST_SECTOR_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "stats_deep_dive":
            return ChatResponse(
                session_id=session_id,
                step="stats_deep_dive",
                bot_message=message,
                options=STATS_DEEP_DIVE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "hazard_profile_selection":
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=message,
                options=self._hazard_options(session),
                session=session.summary(),
                error=error,
            )

        if session.phase == "socio_demographic_review":
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=message,
                options=SOCIO_DEMOGRAPHIC_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "reason_confirmation":
            return ChatResponse(
                session_id=session_id,
                step="reason_confirmation",
                bot_message=message,
                options=REASON_CONFIRMATION_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "other_actions":
            return ChatResponse(
                session_id=session_id,
                step="complete",
                bot_message=message,
                options=self._primary_other_nav_options(session, "complete"),
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_dgs":
            question = self._current_target_population_question(session)
            if question is not None:
                return ChatResponse(
                    session_id=session_id,
                    step="add_dgs",
                    bot_message=message,
                    options=self._target_population_options(question),
                    session=session.summary(),
                    input_mode="target_population_multi",
                    error=error,
                )
            return ChatResponse(
                session_id=session_id,
                step="add_dgs",
                bot_message=message,
                options=ADD_DGS_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase in {
            "custom_hazard_input",
            "custom_hazard_review",
            "custom_hazard_clarification",
            "custom_hazard_duplicate_confirmation",
            "custom_hazard_group_review",
            "custom_hazard_reason",
            "custom_hazard_evidence",
        }:
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard_reason":
            return ChatResponse(
                session_id=session_id,
                step="custom_hazard_clarification"
                if isinstance(session.custom_hazard, dict)
                else "hazard_clarification",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="textarea",
                error=error,
            )

        if session.phase == "add_hazard_evidence_decision":
            return ChatResponse(
                session_id=session_id,
                step="custom_hazard_evidence_decision"
                if isinstance(session.custom_hazard, dict)
                else "hazard_evidence_decision",
                bot_message=message,
                options=HAZARD_EVIDENCE_DECISION_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "add_hazard_evidence_input":
            return ChatResponse(
                session_id=session_id,
                step="custom_hazard_evidence"
                if isinstance(session.custom_hazard, dict)
                else "hazard_evidence",
                bot_message=message,
                options=HAZARD_EVIDENCE_INPUT_OPTIONS,
                session=session.summary(),
                input_mode="evidence_only",
                error=error,
            )

        if session.phase == "add_hazard_evidence":
            return ChatResponse(
                session_id=session_id,
                step="hazards",
                bot_message=message,
                options=HAZARD_ENTRY_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "target_population_question":
            question = self._current_target_population_question(session)
            options = self._target_population_options(question) if question else []
            return ChatResponse(
                session_id=session_id,
                step="target_population_question",
                bot_message=message,
                options=options,
                session=session.summary(),
                error=error,
            )

        if session.phase == "dg_reason_evidence":
            return ChatResponse(
                session_id=session_id,
                step="socio_demographic_review",
                bot_message=message,
                options=DG_REASON_EVIDENCE_OPTIONS,
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "mitigation_measure":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
                error=error,
            )

        if session.phase == "mitigation_duplicate_suggestion":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_duplicate_suggestion",
                bot_message=message,
                options=MITIGATION_DUPLICATE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_duplicate_report":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_duplicate_report",
                bot_message=message,
                options=MITIGATION_DUPLICATE_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_reason":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_reason",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="reason_evidence",
                error=error,
            )

        if session.phase == "mitigation_clarity":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=message,
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
                error=error,
            )

        if session.phase == "mitigation_target_population":
            return self._mitigation_target_population_step(
                session_id,
                session,
                error_reason=message if error else None,
            )

        if session.phase == "mitigation_target_population_review":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_target_population_review",
                bot_message=message,
                options=self._mitigation_target_population_review_options(),
                session=session.summary(),
                error=error,
            )

        if session.phase == "mitigation_review":
            return ChatResponse(
                session_id=session_id,
                step="mitigation_review",
                bot_message=message,
                options=MITIGATION_REVIEW_OPTIONS,
                session=session.summary(),
                error=error,
            )

        if session.phase == "evaluation_question":
            return ChatResponse(
                session_id=session_id,
                step="evaluation_question",
                bot_message=message,
                options=[],
                session=session.summary(),
                input_mode="evaluation_question",
                error=error,
            )

        if session.phase == "evaluation_complete":
            return ChatResponse(
                session_id=session_id,
                step="evaluation_complete",
                bot_message=message,
                options=[],
                session=session.summary(),
                error=error,
            )

        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=message,
            options=[],
            session=session.summary(),
            error=error,
        )

    @staticmethod
    def _invalid_text_message() -> str:
        return render_message(
            "input_validation_failed.md",
            reason=(
                "The input appears to contain gibberish, keyboard mashing, "
                "random characters, or unrecognizable text."
            ),
        )

    @staticmethod
    def _current_step(session: ChatSession) -> str:
        if session.country is None:
            return "country"
        if session.region is None:
            return "region"
        if session.sector is None:
            return "sector"
        if session.phase in {
            "add_hazard",
            "add_hazard_reason",
            "add_hazard_evidence_decision",
            "add_hazard_evidence_input",
            "add_hazard_evidence",
            "custom_hazard_input",
            "custom_hazard_review",
            "custom_hazard_clarification",
            "custom_hazard_duplicate_confirmation",
            "custom_hazard_group_review",
            "custom_hazard_reason",
            "custom_hazard_evidence",
        }:
            return "hazards"
        if session.phase == "dg_reason_evidence":
            return "socio_demographic_review"
        return session.phase or "complete"
