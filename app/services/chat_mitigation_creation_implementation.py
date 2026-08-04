# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)


class ChatMitigationCreationImplementationMixin:
    async def _mitigation_review_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.phase = "mitigation_review"
        answer = await self._mitigation_review_response(
            session,
            (
                "Open a conversational discussion before evaluation questions. "
                "Compare the conceptual design of the validated mitigation measure "
                "against the provided conceptual source excerpts. Explain what the "
                "measure covers, what is not covered, pros, cons, likely trade-offs, "
                "and practical ways to strengthen it. Do not ask evaluation questions "
                "yet, and do not name the source document or page range in the answer."
            ),
        )
        session.mitigation_review_analysis = answer
        session.implementation_challenges = None
        session.implementation_challenge_index = 0
        session.implementation_mitigation_strategy = []
        session.implementation_readiness_assessment = None
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
                    visibility_notice=self._crowd_sourcing_visibility_notice(
                        session,
                        "mitigation_measure",
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

    async def _start_implementation_challenge_discussion(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        challenges = await self._ranked_implementation_challenges(session)
        session.implementation_challenges = challenges
        session.implementation_challenge_index = self._next_unresolved_challenge_index(
            challenges,
            0,
        )
        session.implementation_mitigation_strategy = []
        session.implementation_readiness_assessment = None
        if session.implementation_challenge_index >= len(challenges):
            return await self._implementation_readiness_assessment_step(session_id, session)
        return self._implementation_challenge_step(session_id, session)

    async def _handle_implementation_challenge_response(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        challenges = list(session.implementation_challenges or [])
        index = session.implementation_challenge_index
        if index < 0 or index >= len(challenges):
            return await self._implementation_readiness_assessment_step(
                session_id,
                session,
            )

        if self._is_invalid_user_text(message) or len(compact_for_match(message)) < 4:
            return self._implementation_challenge_step(
                session_id,
                session,
                error_reason=(
                    "Please describe how this specific implementation challenge "
                    "will be addressed."
                ),
            )

        challenge = dict(challenges[index])
        evaluation = await self._evaluate_implementation_challenge_response(
            session,
            challenge,
            message,
        )
        status = str(evaluation.get("status") or "partial").casefold()
        if status not in {"resolved", "partial", "unresolved"}:
            status = "partial"
        strategy = str(evaluation.get("mitigation_strategy") or message).strip()
        challenge["status"] = status
        challenge["mitigation_strategy"] = strategy
        challenge["latest_user_response"] = message.strip()
        challenge["evaluation"] = str(evaluation.get("evaluation") or "").strip()
        challenge["follow_up_question"] = str(
            evaluation.get("follow_up_question") or ""
        ).strip()
        ready_to_continue = self._coerce_ready_to_continue(
            evaluation.get("ready_to_continue")
        )
        challenge["ready_to_continue"] = ready_to_continue
        challenges[index] = challenge
        session.implementation_challenges = challenges
        if session.implementation_mitigation_strategy is None:
            session.implementation_mitigation_strategy = []
        session.implementation_mitigation_strategy.append(
            {
                "challenge": str(challenge.get("title") or "").strip(),
                "status": status,
                "strategy": strategy,
                "evaluation": challenge["evaluation"],
            }
        )

        if status == "resolved" or ready_to_continue:
            session.implementation_challenge_index = self._next_unresolved_challenge_index(
                challenges,
                index + 1,
            )
            if session.implementation_challenge_index >= len(challenges):
                return await self._implementation_readiness_assessment_step(
                    session_id,
                    session,
                )
            next_challenge = challenges[session.implementation_challenge_index]
            heading = (
                "Challenge resolved"
                if status == "resolved"
                else "Challenge reviewed"
            )
            return self._implementation_challenge_step(
                session_id,
                session,
                message=(
                    f"### {heading}\n\n{challenge['evaluation']}\n\n"
                    + self._implementation_challenge_prompt_markdown(
                        session,
                        next_challenge,
                    )
                ),
            )

        follow_up = challenge["follow_up_question"] or (
            "What concrete owner, resource, timeline, safeguard, or evidence will close this gap?"
        )
        return self._implementation_challenge_step(
            session_id,
            session,
            message=(
                "### More detail needed\n\n"
                f"{challenge['evaluation'] or self._incomplete_challenge_response_text()}\n\n"
                f"{follow_up}"
            ),
        )

    async def _ranked_implementation_challenges(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        existing = [
            challenge
            for challenge in (session.implementation_challenges or [])
            if str(challenge.get("title") or "").strip()
        ]
        if existing:
            return existing

        generated = await self._generate_implementation_challenges_from_context(session)
        challenges = [
            *generated,
            *self._implementation_challenges_from_review_text(session),
        ]
        if not challenges:
            challenges = self._fallback_implementation_challenges(session)
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for challenge in challenges:
            title = normalize_markdown_text(str(challenge.get("title") or "")).strip()
            why = normalize_markdown_text(str(challenge.get("why_important") or "")).strip()
            if not title:
                continue
            key = normalize_for_match(title)
            if key in seen:
                continue
            seen.add(key)
            try:
                importance = int(challenge.get("importance") or 3)
            except (TypeError, ValueError):
                importance = 3
            try:
                impact = int(
                    challenge.get("implementation_impact")
                    or challenge.get("impact")
                    or 3
                )
            except (TypeError, ValueError):
                impact = 3
            normalized.append(
                {
                    "title": title,
                    "category": str(
                        challenge.get("category") or "Implementation"
                    ).strip(),
                    "why_important": why
                    or self._implementation_challenge_importance_fallback(),
                    "importance": max(1, min(5, importance)),
                    "implementation_impact": max(1, min(5, impact)),
                    "status": "unresolved",
                    "mitigation_strategy": "",
                    "evaluation": "",
                    "follow_up_question": "",
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                -int(item.get("importance") or 0),
                -int(item.get("implementation_impact") or 0),
                str(item.get("title") or ""),
            ),
        )

    async def _generate_implementation_challenges_from_context(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        context = (
            "You consolidate implementation challenges for a mitigation workflow. "
            "Use only the supplied mitigation review analysis, concept comparison "
            "content, validation synthesis, validation details, and evaluation answers. "
            "Return JSON only: an array of objects with title, category, why_important, "
            "importance, and implementation_impact. Include meaningful disadvantages, "
            "risks, limitations, feasibility concerns, cost barriers, operational "
            "constraints, governance issues, technical challenges, social acceptance "
            "concerns, legal or regulatory obstacles, scalability limitations, "
            "maintenance burdens, and unintended consequences. Rank impact from 1 to 5."
        )
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "country": session.country,
                        "region": session.region,
                        "sector": session.sector,
                        "selected_hazard": session.selected_hazard,
                        "target_population": session.mitigation_target_population or [],
                        "mitigation_measure": session.mitigation_measure,
                        "mitigation_reason": session.mitigation_reason,
                        "mitigation_review_analysis": session.mitigation_review_analysis,
                        "concept_comparison_discussion": session.stats_conversation or [],
                        "mitigation_validation": session.mitigation_validation or {},
                        "grounded_synthesis": session.mitigation_grounded_synthesis,
                        "evaluation_answers": session.evaluation_answers or [],
                    },
                    ensure_ascii=True,
                ),
            }
        ]
        response = await _ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=1200,
        )
        if is_llm_unavailable_response(response):
            return []
        parsed = parse_json_array(response)
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _fallback_implementation_challenges(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        challenges: list[dict[str, object]] = []
        validation = session.mitigation_validation or {}
        dimensions = validation.get("dimensions") if isinstance(validation, dict) else {}
        if isinstance(dimensions, dict):
            for name, value in dimensions.items():
                if not isinstance(value, dict):
                    continue
                status = str(value.get("status") or "").casefold()
                if status == "supported":
                    continue
                explanation = str(
                    value.get("explanation") or value.get("reason") or ""
                ).strip()
                challenges.append(
                    {
                        "title": f"{str(name).replace('_', ' ').title()} concern",
                        "category": "Validation",
                        "why_important": explanation
                        or "The mitigation review did not fully support this validation dimension.",
                        "importance": 5,
                        "implementation_impact": 5,
                    }
                )
        for item in session.practical_considerations or []:
            challenges.append(
                {
                    "title": normalize_markdown_text(str(item)).strip(),
                    "category": "Practical implementation",
                    "why_important": (
                        "This was identified as a practical consideration during "
                        "mitigation generation."
                    ),
                    "importance": 4,
                    "implementation_impact": 4,
                }
            )
        if not challenges:
            challenges.append(
                {
                    "title": "Implementation ownership and delivery plan",
                    "category": "Operational feasibility",
                    "why_important": (
                        "Even a well-designed mitigation measure can fail without clear "
                        "ownership, funding, delivery steps, and monitoring."
                    ),
                    "importance": 4,
                    "implementation_impact": 4,
                }
            )
        return challenges

    def _implementation_challenges_from_review_text(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        text = "\n".join(
            part
            for part in (
                session.mitigation_review_analysis or "",
                "\n".join(
                    str(item.get("content") or "")
                    for item in (session.stats_conversation or [])
                    if isinstance(item, dict)
                ),
            )
            if part.strip()
        )
        if not text.strip():
            return []

        challenges: list[dict[str, object]] = []
        current_heading = ""
        challenge_headings = {
            "cons",
            "risk",
            "risks",
            "limitations",
            "trade offs",
            "trade off",
            "barriers",
            "constraints",
            "feasibility",
            "implementation concerns",
            "unintended consequences",
        }
        for raw_line in text.splitlines():
            line = normalize_markdown_text(raw_line).strip()
            if not line:
                continue
            heading_match = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
            if heading_match:
                current_heading = normalize_for_match(heading_match.group(1))
                continue
            if not any(heading in current_heading for heading in challenge_headings):
                continue
            bullet_match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+?)\s*$", line)
            if not bullet_match:
                continue
            concern = bullet_match.group(1).strip()
            if len(compact_for_match(concern)) < 8:
                continue
            challenges.append(
                {
                    "title": concern[:120].rstrip(" ."),
                    "category": "Concept comparison concern",
                    "why_important": concern,
                    "importance": 4,
                    "implementation_impact": 4,
                }
            )
        return challenges

    @staticmethod
    def _implementation_challenge_importance_fallback() -> str:
        return "This could affect whether the mitigation measure can be implemented reliably."

    async def _evaluate_implementation_challenge_response(
        self,
        session: ChatSession,
        challenge: dict[str, object],
        user_response: str,
    ) -> dict[str, object]:
        context = (
            "Evaluate a user's mitigation response for one implementation challenge. "
            "Return JSON only with status, ready_to_continue, evaluation, "
            "follow_up_question, and mitigation_strategy. status must be resolved, "
            "partial, or unresolved. Resolved requires concrete actions, ownership "
            "or accountable actor, and enough detail to reduce the concern. Set "
            "ready_to_continue true only when the user's answer gives enough "
            "information to classify the concern, even if residual risk remains. "
            "Ask about only this challenge."
        )
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "challenge": challenge,
                        "user_response": user_response,
                        "previous_mitigation_strategy": (
                            session.implementation_mitigation_strategy or []
                        ),
                        "mitigation_measure": session.mitigation_measure,
                        "mitigation_reason": session.mitigation_reason,
                        "target_population": session.mitigation_target_population or [],
                    },
                    ensure_ascii=True,
                ),
            }
        ]
        response = await _ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=700,
        )
        if is_llm_unavailable_response(response):
            return self._fallback_implementation_response_evaluation(
                user_response,
            )
        parsed = parse_json_object(response) or {}
        if isinstance(parsed, dict):
            return parsed
        return self._fallback_implementation_response_evaluation(user_response)

    @staticmethod
    def _incomplete_challenge_response_text() -> str:
        return "The response does not yet fully mitigate this concern."

    @staticmethod
    def _coerce_ready_to_continue(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return normalize_for_match(value) in {"true", "yes", "ready", "continue"}
        return False

    @staticmethod
    def _fallback_implementation_response_evaluation(user_response: str) -> dict[str, object]:
        normalized = normalize_for_match(user_response)
        detail_markers = {
            "budget",
            "funding",
            "owner",
            "responsible",
            "timeline",
            "monitor",
            "legal",
            "governance",
            "pilot",
            "maintenance",
            "stakeholder",
        }
        marker_count = sum(1 for marker in detail_markers if marker in normalized)
        if len(normalized) >= 80 and marker_count >= 2:
            return {
                "status": "resolved",
                "ready_to_continue": True,
                "evaluation": "The response gives concrete mitigation detail for this challenge.",
                "follow_up_question": "",
                "mitigation_strategy": user_response.strip(),
            }
        return {
            "status": "partial",
            "ready_to_continue": False,
            "evaluation": (
                "The response is directionally useful but needs more implementation detail."
            ),
            "follow_up_question": (
                "Who is accountable, what resources or safeguards are required, "
                "and how will progress be checked?"
            ),
            "mitigation_strategy": user_response.strip(),
        }

    @staticmethod
    def _next_unresolved_challenge_index(
        challenges: list[dict[str, object]],
        start: int,
    ) -> int:
        for index in range(max(0, start), len(challenges)):
            if str(challenges[index].get("status") or "unresolved") != "resolved":
                return index
        return len(challenges)

    def _implementation_challenge_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        message: str | None = None,
        error_reason: str | None = None,
    ) -> ChatResponse:
        session.phase = "implementation_challenge_discussion"
        challenges = session.implementation_challenges or []
        index = session.implementation_challenge_index
        challenge = challenges[index] if 0 <= index < len(challenges) else {}
        prompt = message or self._implementation_challenge_prompt_markdown(
            session,
            challenge,
        )
        if error_reason:
            prompt = f"### Clarification needed\n\n{error_reason}\n\n" + prompt
        return ChatResponse(
            session_id=session_id,
            step="implementation_challenge_discussion",
            bot_message=markdown_to_html(prompt),
            options=[],
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    def _implementation_challenge_prompt_markdown(
        self,
        session: ChatSession,
        challenge: dict[str, object],
    ) -> str:
        challenges = session.implementation_challenges or []
        current = session.implementation_challenge_index + 1
        total = len(challenges)
        title = str(challenge.get("title") or "Implementation challenge").strip()
        why = str(challenge.get("why_important") or "").strip()
        category = str(challenge.get("category") or "Implementation").strip()
        return (
            "## Implementation Challenge Discussion\n\n"
            f"Challenge {current} of {total}: **{title}**\n\n"
            f"Category: **{category}**\n\n"
            f"Why this matters: {why}\n\n"
            "How do you intend to address this specific challenge?"
        )

    async def _implementation_readiness_assessment_step(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        session.phase = "implementation_readiness_assessment"
        assessment = await self._implementation_readiness_assessment(session)
        session.implementation_readiness_assessment = assessment
        self._promote_temporary_evidence(session)
        return ChatResponse(
            session_id=session_id,
            step="implementation_readiness_assessment",
            bot_message=markdown_to_html(assessment),
            options=IMPLEMENTATION_READINESS_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_implementation_readiness_action(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, IMPLEMENTATION_READINESS_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, IMPLEMENTATION_READINESS_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label

        action = normalize(exact_label or message)
        if action == normalize("Continue to evaluation"):
            return self._start_evaluation_questions(session_id, session)

        if action == normalize("Review unresolved and partially resolved challenges again"):
            return self._review_remaining_implementation_challenges(session_id, session)

        return ChatResponse(
            session_id=session_id,
            step="implementation_readiness_assessment",
            bot_message=markdown_to_html(
                "Please choose **Continue to evaluation** or "
                "**Review unresolved and partially resolved challenges again**."
            ),
            options=IMPLEMENTATION_READINESS_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _review_remaining_implementation_challenges(
        self,
        session_id: str,
        session: ChatSession,
    ) -> ChatResponse:
        challenges = list(session.implementation_challenges or [])
        next_index = self._next_unresolved_challenge_index(challenges, 0)
        if next_index >= len(challenges):
            challenges = self._merge_readiness_assessment_remaining_challenges(
                session,
                challenges,
            )
            session.implementation_challenges = challenges
            next_index = self._next_unresolved_challenge_index(challenges, 0)
        if next_index >= len(challenges):
            return ChatResponse(
                session_id=session_id,
                step="implementation_readiness_assessment",
                bot_message=markdown_to_html(
                    "All implementation challenges are currently marked as resolved. "
                    "Choose **Continue to evaluation** when you are ready."
                ),
                options=IMPLEMENTATION_READINESS_OPTIONS,
                session=session.summary(),
                error=True,
            )
        session.implementation_challenge_index = next_index
        session.implementation_readiness_assessment = None
        return self._implementation_challenge_step(
            session_id,
            session,
            message=(
                "## Implementation Challenge Review\n\n"
                "We will revisit only the challenges still marked as partially "
                "resolved or unresolved.\n\n"
                + self._implementation_challenge_prompt_markdown(
                    session,
                    challenges[next_index],
                )
            ),
        )

    def _merge_readiness_assessment_remaining_challenges(
        self,
        session: ChatSession,
        challenges: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        assessment = str(session.implementation_readiness_assessment or "")
        extracted = self._remaining_challenges_from_readiness_assessment(assessment)
        if not extracted:
            return challenges

        merged = [dict(challenge) for challenge in challenges]
        for extracted_challenge in extracted:
            match_index = self._matching_implementation_challenge_index(
                merged,
                str(extracted_challenge.get("title") or ""),
            )
            if match_index is None:
                merged.append(extracted_challenge)
                continue
            merged[match_index]["status"] = extracted_challenge["status"]
            merged[match_index]["why_important"] = (
                merged[match_index].get("why_important")
                or extracted_challenge.get("why_important")
            )
            merged[match_index]["evaluation"] = (
                merged[match_index].get("evaluation")
                or extracted_challenge.get("evaluation")
            )
        return merged

    @classmethod
    def _remaining_challenges_from_readiness_assessment(
        cls,
        assessment: str,
    ) -> list[dict[str, object]]:
        challenges: list[dict[str, object]] = []
        current_status = ""
        for raw_line in str(assessment or "").splitlines():
            line = normalize_markdown_text(raw_line).strip()
            heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            heading_text = heading_match.group(1) if heading_match else line
            heading_status = cls._readiness_assessment_section_status(heading_text)
            if heading_match or heading_status is not None:
                current_status = heading_status or ""
                continue
            if current_status not in {"partial", "unresolved"}:
                continue
            item = cls._readiness_assessment_item_text(line)
            if not item:
                continue
            if cls._readiness_assessment_empty_item(item):
                continue
            title, detail = cls._split_readiness_challenge_item(item)
            challenges.append(
                {
                    "title": title,
                    "category": "Readiness assessment concern",
                    "why_important": detail or title,
                    "importance": 4 if current_status == "partial" else 5,
                    "implementation_impact": 4 if current_status == "partial" else 5,
                    "status": current_status,
                    "mitigation_strategy": "",
                    "evaluation": detail,
                    "follow_up_question": "",
                }
            )
        return challenges

    @staticmethod
    def _readiness_assessment_section_status(value: str) -> str | None:
        heading = re.sub(r"^\s*\d+[.)]\s*", "", str(value or "")).strip()
        heading = re.sub(r"^\s*[-*+]\s*", "", heading).strip()
        heading = re.sub(r"^\*\*(.*?)\*\*:?\s*$", r"\1", heading).strip()
        heading = re.sub(r"\*\*(.*?)\*\*", r"\1", heading)
        heading = heading.strip(" :.-")
        normalized = normalize_for_match(heading)
        if not normalized:
            return None
        if "partially resolved" in normalized:
            return "partial"
        if "remaining unresolved" in normalized or "unresolved risks" in normalized:
            return "unresolved"
        section_keywords = (
            "resolved challenges",
            "residual implementation concerns",
            "recommended improvements",
            "overall implementation",
            "readiness score",
            "implementation confidence",
        )
        if any(keyword in normalized for keyword in section_keywords):
            return ""
        return None

    @staticmethod
    def _readiness_assessment_item_text(line: str) -> str:
        bullet_match = re.match(r"^(?:[-*+•]|\d+[.)])\s+(.+?)\s*$", line)
        if bullet_match:
            return bullet_match.group(1).strip()
        bold_item_match = re.match(r"^\*\*([^*]{3,120})\*\*\s*:?\s*(.*)$", line)
        if bold_item_match:
            detail = str(bold_item_match.group(2) or "").strip()
            return (
                f"{bold_item_match.group(1).strip()}: {detail}"
                if detail
                else bold_item_match.group(1).strip()
            )
        return ""

    @staticmethod
    def _readiness_assessment_empty_item(value: str) -> bool:
        normalized = normalize_for_match(value)
        return normalized in {
            "none",
            "no",
            "no unresolved concerns remain",
            "no unresolved risks remain",
            "no concerns were partially resolved",
            "no concerns were fully resolved",
            "not applicable",
            "n a",
        }

    @staticmethod
    def _split_readiness_challenge_item(value: str) -> tuple[str, str]:
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", str(value or "")).strip()
        if ":" in cleaned:
            title, detail = cleaned.split(":", 1)
            return title.strip(" -."), detail.strip()
        return cleaned.strip(" -."), cleaned.strip(" -.")

    @staticmethod
    def _matching_implementation_challenge_index(
        challenges: list[dict[str, object]],
        title: str,
    ) -> int | None:
        title_key = normalize_for_match(title)
        if not title_key:
            return None
        for index, challenge in enumerate(challenges):
            challenge_title = normalize_for_match(str(challenge.get("title") or ""))
            challenge_why = normalize_for_match(str(challenge.get("why_important") or ""))
            if title_key == challenge_title:
                return index
            if title_key in challenge_title or challenge_title in title_key:
                return index
            if title_key and title_key in challenge_why:
                return index
        return None

    async def _implementation_readiness_assessment(self, session: ChatSession) -> str:
        context = (
            "Write an Implementation Readiness Assessment for the mitigation measure. "
            "Use the reviewed challenges and user mitigation strategies. Include exactly "
            "these sections: Resolved challenges, Partially resolved challenges, "
            "Remaining unresolved risks, Residual implementation concerns, Recommended "
            "improvements, Overall implementation confidence/readiness score."
        )
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mitigation_measure": session.mitigation_measure,
                        "mitigation_reason": session.mitigation_reason,
                        "target_population": session.mitigation_target_population or [],
                        "challenges": session.implementation_challenges or [],
                    },
                    ensure_ascii=True,
                ),
            }
        ]
        response = await _ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.2,
            max_tokens=1100,
        )
        if not is_llm_unavailable_response(response) and response.strip():
            return response.strip()
        return self._fallback_implementation_readiness_assessment(session)

    @staticmethod
    def _fallback_implementation_readiness_assessment(session: ChatSession) -> str:
        challenges = session.implementation_challenges or []
        resolved = [item for item in challenges if item.get("status") == "resolved"]
        partial = [item for item in challenges if item.get("status") == "partial"]
        unresolved = [
            item
            for item in challenges
            if item.get("status") not in {"resolved", "partial"}
        ]

        def lines(items: list[dict[str, object]], empty: str) -> str:
            if not items:
                return f"- {empty}"
            rendered = []
            for item in items:
                title = item.get("title")
                detail = (
                    item.get("mitigation_strategy")
                    or item.get("why_important")
                    or "No mitigation recorded."
                )
                rendered.append(f"- **{title}**: {detail}")
            return "\n".join(rendered)

        total = max(1, len(challenges))
        score = round((len(resolved) + 0.5 * len(partial)) / total * 100)
        return (
            "## Implementation Readiness Assessment\n\n"
            "### Resolved challenges\n"
            f"{lines(resolved, 'No concerns were fully resolved.')}\n\n"
            "### Partially resolved challenges\n"
            f"{lines(partial, 'No concerns were partially resolved.')}\n\n"
            "### Remaining unresolved risks\n"
            f"{lines(unresolved, 'No unresolved concerns remain.')}\n\n"
            "### Residual implementation concerns\n"
            "- Continue monitoring partially resolved and high-impact challenges during implementation.\n\n"
            "### Recommended improvements\n"
            "- Assign accountable owners, timelines, funding assumptions, monitoring indicators, and review checkpoints for every mitigation action.\n\n"
            "### Overall implementation confidence/readiness score\n"
            f"- **{score}/100**"
        )
