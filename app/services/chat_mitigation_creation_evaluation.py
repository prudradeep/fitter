# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)

def _facade_d23_conceptual_review_page_texts() -> tuple[tuple[int, str], ...]:
    from app.services import chat_mitigation_creation as facade

    return facade._d23_conceptual_review_page_texts()


class ChatMitigationCreationEvaluationMixin:
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
            return await self._evaluation_complete_step_with_llm(session_id, session)

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
            question_id=str(question["id"]),
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
            return await self._evaluation_complete_step_with_llm(session_id, session)

        return self._evaluation_question_step(session_id, session)

    async def _mitigation_review_response(self, session: ChatSession, user_message: str) -> str:
        context, messages = await self._build_mitigation_review_messages(session, user_message)
        return await _ask_llm_chat(
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
                f"{session.mitigation_measure or ''} "
                f"{session.mitigation_reason or ''} {user_message}"
            ),
            limit=8,
        )
        knowledge_context = await self._mitigation_knowledge_context(
            session,
            session.mitigation_measure or "",
            session.mitigation_reason or "",
        )
        d23_context = self._d23_conceptual_review_context(session, user_message)
        if d23_context:
            knowledge_context = "\n\n".join(
                part
                for part in (
                    "Conceptual source excerpts for the pre-evaluation discussion:\n"
                    + d23_context,
                    knowledge_context,
                )
                if part
            )
        if session.mitigation_grounded_synthesis:
            knowledge_context = "\n\n".join(
                part
                for part in (
                    "Grounded validation synthesis for the saved mitigation measure:\n"
                    + session.mitigation_grounded_synthesis,
                    knowledge_context,
                )
                if part
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

    @staticmethod
    def _crowd_sourcing_visibility_notice(
        session: ChatSession,
        item_type: str,
    ) -> str:
        if session.validation_mode != "strict" or not session.crowd_sourcing_enabled:
            return ""
        region = str(session.region or "").strip()
        country = str(session.country or "").strip()
        location = ", ".join(part for part in (region, country) if part)
        if not location:
            location = "this selected location"
        if item_type == "hazard":
            return (
                "Once saved, this hazard will be visible to other platform users "
                f"interested in transition risks for {location}."
            )
        if item_type == "saved_hazard":
            return (
                "This hazard is now visible to other platform users interested "
                f"in transition risks for {location}."
            )
        return (
            "Once saved, this mitigation measure will be visible to other platform "
            f"users interested in mitigation options for {location}."
        )

    def _d23_conceptual_review_context(
        self,
        session: ChatSession,
        user_message: str = "",
    ) -> str:
        pages = _facade_d23_conceptual_review_page_texts()
        if not pages:
            return ""

        query = normalize_for_match(
            " ".join(
                [
                    str(session.country or ""),
                    str(session.region or ""),
                    str(session.sector or ""),
                    str(session.selected_hazard or ""),
                    self._mitigation_target_population_text(session),
                    format_all_dgs(session),
                    str(session.mitigation_measure or ""),
                    str(session.mitigation_reason or ""),
                    str(user_message or ""),
                    "conceptual framework pros cons coverage limitations "
                    "trade offs implementation feasibility",
                ]
            )
        )
        query_terms = {
            token
            for token in query.split()
            if len(token) >= 4
            and token
            not in {
                "this",
                "that",
                "with",
                "from",
                "into",
                "about",
                "which",
                "their",
                "there",
                "measure",
                "mitigation",
            }
        }
        scored: list[tuple[int, int, str]] = []
        for page_number, page_text in pages:
            if not (
                D23_CONCEPTUAL_REVIEW_START_PAGE
                <= page_number
                <= D23_CONCEPTUAL_REVIEW_END_PAGE
            ):
                continue
            page_key = normalize_for_match(page_text)
            page_terms = set(page_key.split())
            overlap = len(query_terms & page_terms)
            phrase_bonus = sum(
                3
                for phrase in (
                    normalize_for_match(str(session.sector or "")),
                    normalize_for_match(str(session.selected_hazard or "")),
                    normalize_for_match(str(session.country or "")),
                )
                if phrase and phrase in page_key
            )
            score = overlap + phrase_bonus
            if score > 0:
                scored.append((score, page_number, page_text))

        if not scored:
            scored = [
                (0, page_number, page_text)
                for page_number, page_text in pages[:D23_CONCEPTUAL_REVIEW_MAX_EXCERPTS]
            ]

        selected = sorted(scored, key=lambda item: (-item[0], item[1]))[
            :D23_CONCEPTUAL_REVIEW_MAX_EXCERPTS
        ]
        excerpts: list[str] = []
        total_chars = 0
        for _score, page_number, page_text in selected:
            excerpt = page_text[:900].strip()
            if not excerpt:
                continue
            rendered = f"- [Source p. {page_number}] {excerpt}"
            if total_chars + len(rendered) > D23_CONCEPTUAL_REVIEW_MAX_CHARS:
                break
            excerpts.append(rendered)
            total_chars += len(rendered)
        return "\n".join(excerpts)

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
        evaluation_message = render_message(
            "evaluation_complete.md",
            hazard=session.selected_hazard or "the selected hazard",
            mitigation_measure=session.mitigation_measure or "Not provided",
            reason=session.mitigation_reason or "Not provided",
            answers=format_evaluation_answers(
                session,
                self._historical_evaluation_series(session),
            ),
        )
        return self._system_inquiry_intro_step(
            session_id,
            session,
            prefix_message=evaluation_message,
        )

    async def _evaluation_complete_step_with_llm(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        session.phase = "evaluation_complete"
        self._promote_temporary_evidence(session)
        evaluation_message = render_message(
            "evaluation_complete.md",
            hazard=session.selected_hazard or "the selected hazard",
            mitigation_measure=session.mitigation_measure or "Not provided",
            reason=session.mitigation_reason or "Not provided",
            answers=format_evaluation_answers(
                session,
                self._historical_evaluation_series(session),
            ),
        )
        return await self._system_inquiry_intro_step_with_llm(
            session_id,
            session,
            prefix_message=evaluation_message,
        )
