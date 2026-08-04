# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)


class ChatMitigationCreationWorkflowMixin:
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
        if not mitigation_measure:
            return self._mitigation_initial_clarification_step(
                session_id,
                session,
                "Your proposed mitigation measure",
            )
        clarification_error = local_mitigation_clarification_error(
            message,
            [
                mitigation_measure,
                reason,
                session.mitigation_measure or "",
                session.mitigation_reason or "",
            ],
        )
        if clarification_error:
            return ChatResponse(
                session_id=session_id,
                step="mitigation_clarity",
                bot_message=render_message(
                    "input_validation_failed.md",
                    reason=clarification_error,
                ),
                options=self._mitigation_clarity_options(),
                session=session.summary(),
                input_mode="textarea",
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
        return self._mitigation_evidence_decision_step(
            session_id,
            session,
            frozen_inputs.get("measure_description") or mitigation_measure,
            frozen_inputs.get("justification") or reason,
            "",
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
            return self._mitigation_initial_clarification_step(
                session_id,
                session,
                mitigation_measure or "Your proposed mitigation measure",
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
            session.phase = "mitigation_measure"
            self._clear_mitigation_clarity_state(session)
            clarity_reason = str(clarity.get("reason") or "").strip()
            revision_reason = (
                "I still cannot freeze an unambiguous version of the mitigation "
                "measure and justification after the clarification limit. "
                "Please resubmit the mitigation measure with more concrete wording."
            )
            if clarity_reason:
                revision_reason = f"{revision_reason} Last clarity issue: {clarity_reason}"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_measure",
                bot_message=render_message(
                    "mitigation_validation_failed.md",
                    reason=revision_reason,
                ),
                options=[],
                session=session.summary(),
                input_mode="mitigation_measure",
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
                "I will use your answers only to clarify the measure and "
                "justification. Evidence will be collected next."
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

    def _mitigation_evidence_decision_step(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
        evidence_text: str = "",
        *,
        error: bool = False,
        message: str | None = None,
    ) -> ChatResponse:
        session.phase = "mitigation_evidence_decision"
        session.pending_mitigation_measure = mitigation_measure
        session.pending_mitigation_reason = reason
        session.pending_mitigation_evidence = evidence_text
        bot_message = message or markdown_to_html(
            "Do you have evidence to validate this mitigation measure?\n\n"
            "Choose **Yes** to add a URL or file, or **No** to continue without evidence."
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_evidence_decision",
            bot_message=bot_message,
            options=MITIGATION_EVIDENCE_DECISION_OPTIONS,
            session=session.summary(),
            error=error,
        )

    async def _handle_mitigation_evidence_decision(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_EVIDENCE_DECISION_OPTIONS)
        open_action = open_evidence_decision_action(message)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_EVIDENCE_DECISION_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or open_action or message)
        mitigation_measure = session.pending_mitigation_measure or session.mitigation_measure or ""
        reason = session.pending_mitigation_reason or session.mitigation_reason or ""
        if not mitigation_measure or not reason:
            return self._mitigation_initial_clarification_step(
                session_id,
                session,
                mitigation_measure or "Your proposed mitigation measure",
            )
        if open_action == "evidence":
            evidence_text = normalize_evidence_message(message)
            if not self._has_readable_evidence_content(evidence_text):
                return self._mitigation_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=(
                        "Evidence is optional. If you provide evidence, it must be "
                        "readable supporting content. Please provide a DOI/URL with "
                        "extractable text or a supported file: PDF, DOCX, MD, or TXT."
                    ),
                )
            return await self._validate_frozen_mitigation_inputs(
                session_id,
                session,
                mitigation_measure,
                reason,
                evidence_text,
            )
        if action == normalize("Yes"):
            return self._mitigation_evidence_input_step(session_id, session)
        if action == normalize("No"):
            return await self._validate_frozen_mitigation_inputs(
                session_id,
                session,
                mitigation_measure,
                reason,
                "",
            )
        return self._mitigation_evidence_decision_step(
            session_id,
            session,
            mitigation_measure,
            reason,
            session.pending_mitigation_evidence or "",
            error=True,
            message=self.invalid_message,
        )

    def _mitigation_evidence_input_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error: bool = False,
        message: str | None = None,
    ) -> ChatResponse:
        session.phase = "mitigation_evidence_input"
        bot_message = message or markdown_to_html(
            "Please add evidence for this mitigation measure. You can paste a URL "
            "or attach a supported file: PDF, DOCX, MD, or TXT."
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_evidence",
            bot_message=bot_message,
            options=MITIGATION_EVIDENCE_INPUT_OPTIONS,
            session=session.summary(),
            input_mode="evidence_only",
            error=error,
        )

    async def _capture_mitigation_evidence(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, MITIGATION_EVIDENCE_INPUT_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, MITIGATION_EVIDENCE_INPUT_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        if action == normalize("Back to evidence question"):
            return self._mitigation_evidence_decision_step(
                session_id,
                session,
                session.pending_mitigation_measure or session.mitigation_measure or "",
                session.pending_mitigation_reason or session.mitigation_reason or "",
                session.pending_mitigation_evidence or "",
            )

        mitigation_measure = session.pending_mitigation_measure or session.mitigation_measure or ""
        reason = session.pending_mitigation_reason or session.mitigation_reason or ""
        if not mitigation_measure or not reason:
            return self._mitigation_initial_clarification_step(
                session_id,
                session,
                mitigation_measure or "Your proposed mitigation measure",
            )
        if action == normalize("Skip"):
            evidence_text = ""
        else:
            evidence_text = normalize_evidence_message(message)
            if not evidence_text:
                return self._mitigation_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message="Please add an evidence URL or file, or choose Skip.",
                )
            if not self._has_readable_evidence_content(evidence_text):
                return self._mitigation_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=(
                        "Evidence is optional. If you provide evidence, it must be "
                        "readable supporting content. Please provide a DOI/URL with "
                        "extractable text or a supported file: PDF, DOCX, MD, or TXT."
                    ),
                )
        return await self._validate_frozen_mitigation_inputs(
            session_id,
            session,
            mitigation_measure,
            reason,
            evidence_text,
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
                session,
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
        session: ChatSession,
        mitigation_measure: str,
        reason: str,
    ) -> list[str]:
        mechanisms = str(
            session.suggested_new_policy_target_group_mechanisms or ""
        ).strip()
        text = (
            f"Mitigation measure:\n{mitigation_measure.strip()}\n\n"
            f"Justification/reason:\n{reason.strip()}"
        )
        if mechanisms:
            text += f"\n\nTarget-group mechanisms:\n{mechanisms}"
        inferred = await self._match_mitigation_target_population_answer(text)
        mechanism_groups = self._extract_target_groups_from_mechanisms(mechanisms)
        return self._merge_target_population_labels(inferred, mechanism_groups)

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
        error_block = f"> {error_reason}\n\n" if error_reason else ""
        return ChatResponse(
            session_id=session_id,
            step="mitigation_target_population_review",
            bot_message=markdown_to_html(
                "### Target population identified\n\n"
                "I identified these target-population groups from the mitigation information:\n\n"
                f"{target_lines or '- No target population matched.'}\n\n"
                f"{error_block}"
                "Choose **Continue** to use these groups, or **Add more target population** "
                "to describe another group in open text."
            ),
            options=self._mitigation_target_population_review_options(),
            session=session.summary(),
            error=bool(error_reason),
        )

    @staticmethod
    def _mitigation_target_population_review_options() -> list[Option]:
        return [
            Option(id=1, label="Continue"),
            Option(id=2, label="Add more target population"),
        ]

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
            return self._mitigation_initial_clarification_step(
                session_id,
                session,
                mitigation_measure or "Your proposed mitigation measure",
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
            if cls._matches_population_phrase(full_key, compact_key, phrases):
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

        return cls._safe_population_label_fallback(raw, question_key, answer_key)

    @staticmethod
    def _matches_population_phrase(
        full_key: str,
        compact_key: str,
        phrases: tuple[str, ...],
    ) -> bool:
        for phrase in phrases:
            phrase_key = normalize_for_match(phrase)
            phrase_compact = compact_for_match(phrase)
            if not phrase_key:
                continue
            if re.search(rf"\b{re.escape(phrase_key)}\b", full_key):
                return True
            if phrase_compact and len(phrase_compact) >= 6 and phrase_compact in compact_key:
                return True
        return False

    @classmethod
    def _safe_population_label_fallback(
        cls,
        raw: str,
        question_key: str,
        answer_key: str,
    ) -> str:
        value_key = normalize_for_match(raw)
        answer_only_values = {
            "yes",
            "no",
            "yes once",
            "yes twice or more",
            "twice or more",
            "once",
            "higher",
            "lower",
            "high",
            "low",
        }
        indicator_terms = {
            "count",
            "pct",
            "percentage",
            "rate",
            "index",
            "score",
            "higher",
            "lower",
            "yes",
            "no",
        }
        if answer_key in answer_only_values:
            return ""
        if question_key and answer_key:
            return ""
        if any(re.search(rf"\b{re.escape(term)}\b", value_key) for term in indicator_terms):
            return ""
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
            existing_id=session.mitigation_record_id,
            user_session_id=hazard_reference["user_session_id"],
            user_hazard_id=hazard_reference["user_hazard_id"],
            custom_hazard_id=hazard_reference["custom_hazard_id"],
            system_hazard_id=hazard_reference["system_hazard_id"],
            additional_hazard_id=hazard_reference["additional_hazard_id"],
            mitigation_measure=session.mitigation_measure or "",
            reason=session.mitigation_reason or "",
            target_population=session.mitigation_target_population,
            validation_mode=session.validation_mode,
            is_crowd_sourced=(
                session.validation_mode == "strict" and bool(session.crowd_sourcing_enabled)
            ),
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

        allowed_ids = {str(row.id) for row in rows}
        option_catalogue = "\n".join(
            f"- {row.id} | {row.question}: {row.option}" for row in rows
        )
        context = render_prompt_template(
            "llm/mitigation_target_population_extraction.txt",
            target_population_options=option_catalogue,
        )
        try:
            response = await _ask_llm_chat(
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
        except Exception:
            logger.exception("Target-population LLM matching failed; using deterministic fallback.")
            response = ""
        matched_ids: set[str] = set()
        additional_groups: list[str] = []
        rows_by_id = {str(row.id): row for row in rows}
        if not is_llm_unavailable_response(response):
            parsed = parse_json_object(response) or {}
            raw_ids = parsed.get("option_ids") if isinstance(parsed, dict) else []
            if isinstance(raw_ids, list):
                for raw_id in raw_ids:
                    option_id = str(raw_id or "").strip()
                    if not option_id:
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
            if str(row.id) in matched_ids
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
    def _fallback_target_population_option_ids(cls, answer: str, rows: list[object]) -> set[str]:
        matched: set[str] = set()
        for row in rows:
            if cls._target_population_option_is_supported_by_text(answer, row):
                matched.add(str(row.id))
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
        payload = parse_json_array(raw_json)
        if not isinstance(payload, list):
            return self._mitigation_target_population_step(
                session_id, session, error_reason="Please submit valid target-population selections."
            )

        questions_by_id = {
            str(question["id"]): question
            for question in (session.target_population_questions or [])
            if question.get("id") is not None
        }
        labels: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id") or "").strip()
            if not question_id:
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
