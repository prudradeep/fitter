# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *
from app.services.knowledge_base import VALIDATED_EVIDENCE_SCOPE


class ChatMitigationCreationGuidedMixin:
    """Guided, confirmation-based mitigation-measure creation flow."""

    @staticmethod
    def _guided_options(*labels: str) -> list[Option]:
        return [Option(id=index, label=label) for index, label in enumerate(labels, 1)]

    async def _start_guided_mitigation_flow(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        initial_reason: str = "",
    ) -> ChatResponse:
        session.pending_mitigation_measure = mitigation_measure.strip()
        session.pending_mitigation_reason = initial_reason.strip()
        session.pending_mitigation_evidence = ""
        session.mitigation_mechanisms = None
        session.mitigation_creation_summary = None
        session.mitigation_target_population = None
        session.mitigation_dg_evidence = None
        session.mitigation_dg_evidence_index = 0
        session.mitigation_equity = None
        session.mitigation_revision_stage = None
        relevance, mechanisms = await self._assess_measure_policy_and_mechanisms(
            session, mitigation_measure
        )
        if relevance and not bool(relevance.get("relevant", True)):
            session.phase = "mitigation_summary_revision"
            session.mitigation_revision_stage = "description"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_summary_revision",
                bot_message=markdown_to_html(
                    "### Clarification needed\n\n"
                    f"{relevance.get('reason') or 'The measure is not clearly linked to the policy or mechanism associated with the selected hazard.'}\n\n"
                    "Please revise or clarify the mitigation-measure description so the link is explicit."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._mitigation_mechanism_confirmation_step(session_id, session, mechanisms)

    async def _assess_measure_policy_and_mechanisms(
        self, session: ChatSession, mitigation_measure: str
    ) -> tuple[dict[str, object], list[str]]:
        source_parts: list[str] = []
        state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        for key in ("selected_mechanism", "causal_linkage", "policy_reference"):
            value = str(state.get(key) or "").strip()
            if value:
                source_parts.append(value)
        if session.accepted_custom_hazard_reason:
            source_parts.append(session.accepted_custom_hazard_reason)
        for result in self._mitigation_policy_reference_results(session)[:5]:
            content = str(result.get("content") or "").strip()
            if content:
                source_parts.append(content[:1600])
        for candidate in self._guided_open_labs_candidates(session):
            text_value = " — ".join(
                str(candidate.get(key) or "").strip()
                for key in ("policy_title", "policy_type", "short_description")
                if str(candidate.get(key) or "").strip()
            )
            if text_value:
                source_parts.append(text_value)
        linked_context = "\n".join(source_parts[:10])
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/mitigation_mechanism_extraction.txt"),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not selected'}\n"
                        f"Country: {session.country or 'Not selected'}\nRegion: {session.region or 'Not selected'}\n"
                        f"Sector: {session.sector or 'Not selected'}\n"
                        f"Mitigation measure: {mitigation_measure}\n\n"
                        f"Linked policy/mechanism context (may be absent):\n{linked_context or 'None available'}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=600,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        if not isinstance(payload, dict):
            fallback = str(session.pending_mitigation_reason or "").strip()
            mechanisms = [fallback] if fallback else []
            return {"relevant": True, "reason": ""}, mechanisms
        raw_mechanisms = payload.get("mechanisms")
        mechanisms = [
            str(item).strip()
            for item in (raw_mechanisms if isinstance(raw_mechanisms, list) else [])
            if str(item).strip()
        ][:5]
        return payload, mechanisms

    def _mitigation_mechanism_confirmation_step(
        self, session_id: str, session: ChatSession, mechanisms: list[str]
    ) -> ChatResponse:
        session.mitigation_mechanisms = mechanisms
        if not mechanisms:
            session.phase = "mitigation_mechanism_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_mechanism_input",
                bot_message=markdown_to_html(
                    "### Mechanism to be mitigated\n\n"
                    "I could not reliably extract a specific mechanism. Describe how the selected hazard arises and which part of that causal process this measure will change."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        lines = "\n".join(f"- {item}" for item in mechanisms)
        session.phase = "mitigation_mechanism_confirmation"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_mechanism_confirmation",
            bot_message=markdown_to_html(
                "### Suggested mechanisms to be mitigated\n\n"
                f"{lines}\n\nConfirm these suggestions or provide your own mechanisms."
            ),
            options=self._guided_options("Confirm mechanisms", "Provide different mechanisms"),
            session=session.summary(),
            error=False,
        )

    async def _handle_mitigation_mechanism_confirmation(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message, self._guided_options("Confirm mechanisms", "Provide different mechanisms")
            )
            or message
        )
        if action == normalize("Confirm mechanisms"):
            session.pending_mitigation_reason = "; ".join(session.mitigation_mechanisms or [])
            return self._mitigation_evidence_decision_step(
                session_id,
                session,
                session.pending_mitigation_measure or "",
                session.pending_mitigation_reason or "",
                "",
            )
        if action == normalize("Provide different mechanisms"):
            session.phase = "mitigation_mechanism_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_mechanism_input",
                bot_message="Describe the specific mechanism or mechanisms this measure will mitigate.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_mechanism_input(self, session_id, session, message):
        review = await self._guided_text_review("mechanisms", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_mechanism_input", review
            )
        mechanisms = self._split_guided_items(str(review.get("normalized_text") or message))
        if not mechanisms:
            return self._guided_clarification_response(
                session_id,
                session,
                "mitigation_mechanism_input",
                {
                    "clarification_question": "Name the causal process that the measure changes, rather than a broad topic or intended outcome."
                },
            )
        session.mitigation_mechanisms = mechanisms
        session.pending_mitigation_reason = "; ".join(mechanisms)
        return self._mitigation_evidence_decision_step(
            session_id,
            session,
            session.pending_mitigation_measure or "",
            session.pending_mitigation_reason,
            "",
        )

    async def _guided_after_evidence(self, session_id, session, evidence_text: str) -> ChatResponse:
        if evidence_text:
            relevant = await self._guided_evidence_relevance(session, evidence_text)
            if not relevant.get("relevant"):
                session.phase = "mitigation_evidence_input"
                return self._mitigation_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=str(
                        relevant.get("reason")
                        or "The evidence does not clearly support the measure and confirmed mechanisms. Please clarify the connection or provide different evidence."
                    ),
                )
        session.pending_mitigation_evidence = evidence_text
        return await self._guided_summary_step(session_id, session, "mitigation_summary_review")

    async def _guided_evidence_relevance(
        self, session: ChatSession, evidence_text: str
    ) -> dict[str, object]:
        context_text = await self._mitigation_evidence_context(
            session,
            session.pending_mitigation_measure or "",
            "; ".join(session.mitigation_mechanisms or []),
            evidence_text,
        )
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/mitigation_creation_evidence_relevance.txt"),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Measure: {session.pending_mitigation_measure or ''}\n"
                        f"Mechanisms: {'; '.join(session.mitigation_mechanisms or [])}\n"
                        f"Evidence reference: {evidence_text}\nEvidence content:\n{context_text}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=350,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        return (
            payload
            if isinstance(payload, dict)
            else {
                "relevant": True,
                "reason": "Evidence relevance could not be independently assessed.",
            }
        )

    async def _guided_summary_step(self, session_id, session, phase: str) -> ChatResponse:
        summary = await self._generate_guided_summary(session)
        session.mitigation_creation_summary = summary
        session.phase = phase
        if phase == "mitigation_summary_review":
            options = self._guided_options("Confirm summary", "Modify summary inputs")
        elif phase == "mitigation_dg_summary_review":
            options = self._guided_options("Confirm DG summary", "Modify disadvantaged groups")
        else:
            options = self._guided_options("Confirm final summary", "Modify final inputs")
        return ChatResponse(
            session_id=session_id,
            step=phase,
            bot_message=markdown_to_html(
                f"### Summary of our understanding\n\n{summary}\n\nPlease confirm or modify it."
            ),
            options=options,
            session=session.summary(),
            error=False,
        )

    async def _generate_guided_summary(self, session: ChatSession) -> str:
        evidence = "Provided" if session.pending_mitigation_evidence else "Not provided (optional)"
        groups = ", ".join(session.mitigation_target_population or []) or "Not confirmed yet"
        dg_evidence = session.mitigation_dg_evidence or {}
        inspiration = session.mitigation_inspiration_decision or {}
        return (
            f"- **Measure:** {session.pending_mitigation_measure or session.mitigation_measure or 'Not provided'}\n"
            f"- **Mechanisms mitigated:** {'; '.join(session.mitigation_mechanisms or []) or 'Not provided'}\n"
            f"- **Measure evidence:** {evidence}\n"
            f"- **Open Labs inspiration decision:** {inspiration.get('action') or 'Not reviewed yet'}"
            f"{(': ' + str(inspiration.get('detail'))) if inspiration.get('detail') else ''}\n"
            f"- **Disadvantaged groups benefited:** {groups}\n"
            f"- **DG evidence:** {sum(bool(value) for value in dg_evidence.values())} of {len(dg_evidence)} group(s) supplied evidence\n"
            f"- **How the measure is equitable:** {session.mitigation_equity or 'Not described yet'}"
        )

    async def _handle_mitigation_summary_review(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message, self._guided_options("Confirm summary", "Modify summary inputs")
            )
            or message
        )
        if action == normalize("Confirm summary"):
            if session.mitigation_inspiration_decision:
                return await self._guided_dg_suggestion_step(session_id, session)
            return self._guided_inspiration_step(session_id, session)
        if action == normalize("Modify summary inputs"):
            session.phase = "mitigation_summary_revision"
            session.mitigation_revision_stage = "summary"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_summary_revision",
                bot_message="Describe exactly what should change in the measure or mechanisms.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_summary_revision(self, session_id, session, message):
        review = await self._guided_text_review("mitigation description revision", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_summary_revision", review
            )
        if session.mitigation_revision_stage == "description":
            revised = str(review.get("normalized_text") or message).strip()
        else:
            revised = f"{session.pending_mitigation_measure or ''} Clarification: {str(review.get('normalized_text') or message).strip()}"
        decision = session.mitigation_inspiration_decision
        response = await self._start_guided_mitigation_flow(session_id, session, revised)
        session.mitigation_inspiration_decision = decision
        return response

    def _guided_inspiration_step(self, session_id, session) -> ChatResponse:
        candidates = self._guided_open_labs_candidates(session)
        sections: list[str] = []
        found_types: set[str] = set()
        for index, candidate in enumerate(candidates, 1):
            policy_type = str(candidate.get("policy_type") or "new or adjusted policy")
            found_types.add(
                "adjustment" if "adjust" in normalize_for_match(policy_type) else "proposal"
            )
            sections.append(
                f"{index}. **{candidate.get('policy_title') or 'Open Labs policy'}** — "
                f"{candidate.get('short_description') or candidate.get('policy_title') or ''} "
                f"_(Type: {policy_type})_"
            )
        if session.suggested_existing_policy_modification:
            sections.append(
                f"{len(sections) + 1}. **Policy adjustment** — {session.suggested_existing_policy_modification}"
            )
        if not sections:
            sections.append(
                "No related Open Labs proposal or policy adjustment was found for this hazard."
            )
        elif "proposal" not in found_types:
            sections.append(
                "No related new policy proposal was found in the Open Labs data for this hazard."
            )
        elif "adjustment" not in found_types and not session.suggested_existing_policy_modification:
            sections.append(
                "No related adjustment to an existing policy was found in the Open Labs data for this hazard."
            )
        usable_count = len(candidates) + (
            1 if session.suggested_existing_policy_modification else 0
        )
        options = [f"Use inspiration {index} fully" for index in range(1, usable_count + 1)]
        options.extend(["Adopt selected parts", "Discard inspirations"])
        session.phase = "mitigation_inspiration_review"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_inspiration_review",
            bot_message=markdown_to_html(
                "### FITTER Open Labs inspirations\n\n"
                + "\n\n".join(sections)
                + "\n\nYou may use one fully, adopt parts, or discard them."
            ),
            options=self._guided_options(*options),
            session=session.summary(),
            error=False,
        )

    async def _handle_mitigation_inspiration_review(self, session_id, session, message):
        normalized = normalize(message)
        if normalized == normalize("Discard inspirations"):
            session.mitigation_inspiration_decision = {"action": "Discarded"}
            return await self._guided_dg_suggestion_step(session_id, session)
        if normalized == normalize("Adopt selected parts"):
            session.phase = "mitigation_inspiration_parts"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_inspiration_parts",
                bot_message="Describe which Open Labs proposal or policy-adjustment parts you want to adopt and how they should change your measure.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        match = re.fullmatch(r"use inspiration\s+(\d+)\s+fully", normalized)
        if match:
            index = int(match.group(1))
            candidates = self._guided_open_labs_candidates(session)
            choices = [
                str(item.get("short_description") or item.get("policy_title") or "").strip()
                for item in candidates
            ]
            if session.suggested_existing_policy_modification:
                choices.append(session.suggested_existing_policy_modification)
            if 1 <= index <= len(choices) and choices[index - 1]:
                session.mitigation_inspiration_decision = {
                    "action": "Adopted fully",
                    "detail": f"Inspiration {index}",
                }
                session.pending_mitigation_measure = choices[index - 1]
                relevance, mechanisms = await self._assess_measure_policy_and_mechanisms(
                    session, choices[index - 1]
                )
                return self._mitigation_mechanism_confirmation_step(session_id, session, mechanisms)
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_inspiration_parts(self, session_id, session, message):
        review = await self._guided_text_review("adopted Open Labs parts", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_inspiration_parts", review
            )
        detail = str(review.get("normalized_text") or message).strip()
        session.mitigation_inspiration_decision = {
            "action": "Adopted selected parts",
            "detail": detail,
        }
        session.pending_mitigation_measure = f"{session.pending_mitigation_measure or ''} Adopted Open Labs elements: {detail}".strip()
        relevance, mechanisms = await self._assess_measure_policy_and_mechanisms(
            session, session.pending_mitigation_measure
        )
        return self._mitigation_mechanism_confirmation_step(session_id, session, mechanisms)

    async def _guided_dg_suggestion_step(self, session_id, session) -> ChatResponse:
        groups = await self._infer_mitigation_target_population_from_inputs(
            session,
            session.pending_mitigation_measure or "",
            "; ".join(session.mitigation_mechanisms or []),
        )
        session.mitigation_target_population = groups
        if not groups:
            session.phase = "mitigation_dg_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_dg_input",
                bot_message="I could not identify a specific disadvantaged group. Name each group expected to benefit from the measure.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        session.phase = "mitigation_dg_review"
        lines = "\n".join(f"- {group}" for group in groups)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_dg_review",
            bot_message=markdown_to_html(
                f"### Suggested disadvantaged groups benefited\n\n{lines}\n\nConfirm or provide different groups."
            ),
            options=self._guided_options(
                "Confirm disadvantaged groups", "Provide different groups"
            ),
            session=session.summary(),
            error=False,
        )

    def _guided_open_labs_candidates(self, session: ChatSession) -> list[dict[str, object]]:
        ranked = self._ranked_new_policy_suggestions(session, limit=20)
        proposals = [
            item
            for item in ranked
            if "adjust" not in normalize_for_match(str(item.get("policy_type") or ""))
        ]
        adjustments = [
            item
            for item in ranked
            if "adjust" in normalize_for_match(str(item.get("policy_type") or ""))
        ]
        return [*proposals[:2], *adjustments[:2]]

    async def _handle_mitigation_dg_review(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message,
                self._guided_options("Confirm disadvantaged groups", "Provide different groups"),
            )
            or message
        )
        if action == normalize("Confirm disadvantaged groups"):
            session.mitigation_dg_evidence = {}
            session.mitigation_dg_evidence_index = 0
            return self._guided_dg_evidence_decision_step(session_id, session)
        if action == normalize("Provide different groups"):
            session.phase = "mitigation_dg_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_dg_input",
                bot_message="Name the specific disadvantaged groups expected to benefit.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_dg_input(self, session_id, session, message):
        review = await self._guided_text_review("disadvantaged groups", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_dg_input", review
            )
        groups = await self._match_mitigation_target_population_answer(
            str(review.get("normalized_text") or message)
        )
        if not groups:
            return self._guided_clarification_response(
                session_id,
                session,
                "mitigation_dg_input",
                {
                    "clarification_question": "Name at least one concrete group with a distinguishing characteristic, such as low income, tenancy, age, disability, location, or employment status."
                },
            )
        session.mitigation_target_population = groups
        session.mitigation_dg_evidence = {}
        session.mitigation_dg_evidence_index = 0
        return self._guided_dg_evidence_decision_step(session_id, session)

    def _guided_dg_evidence_decision_step(self, session_id, session) -> ChatResponse:
        groups = session.mitigation_target_population or []
        if session.mitigation_dg_evidence_index >= len(groups):
            return ChatResponse(
                session_id=session_id,
                step="mitigation_dg_summary_review",
                bot_message="Preparing disadvantaged-group summary…",
                options=[],
                session=session.summary(),
                error=False,
            )
        group = groups[session.mitigation_dg_evidence_index]
        session.phase = "mitigation_dg_evidence_decision"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_dg_evidence_decision",
            bot_message=markdown_to_html(
                f"Do you have optional URL or document evidence that **{group}** will benefit from this measure?"
            ),
            options=self._guided_options("Yes, add DG evidence", "No DG evidence"),
            session=session.summary(),
            error=False,
        )

    async def _handle_mitigation_dg_evidence_decision(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message, self._guided_options("Yes, add DG evidence", "No DG evidence")
            )
            or message
        )
        groups = session.mitigation_target_population or []
        if session.mitigation_dg_evidence_index >= len(groups):
            return await self._guided_summary_step(
                session_id, session, "mitigation_dg_summary_review"
            )
        group = groups[session.mitigation_dg_evidence_index]
        if action == normalize("No DG evidence"):
            (session.mitigation_dg_evidence or {})[group] = ""
            session.mitigation_dg_evidence_index += 1
            if session.mitigation_dg_evidence_index >= len(groups):
                return await self._guided_summary_step(
                    session_id, session, "mitigation_dg_summary_review"
                )
            return self._guided_dg_evidence_decision_step(session_id, session)
        if action == normalize("Yes, add DG evidence"):
            session.phase = "mitigation_dg_evidence_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_dg_evidence_input",
                bot_message=f"Provide a URL or supported document for **{group}**.",
                options=self._guided_options("Skip DG evidence"),
                session=session.summary(),
                input_mode="evidence_only",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_dg_evidence_input(self, session_id, session, message):
        groups = session.mitigation_target_population or []
        if session.mitigation_dg_evidence_index >= len(groups):
            return await self._guided_summary_step(
                session_id, session, "mitigation_dg_summary_review"
            )
        group = groups[session.mitigation_dg_evidence_index]
        if normalize(message) == normalize("Skip DG evidence"):
            evidence_text = ""
        else:
            evidence_text = normalize_evidence_message(message)
            if not self._has_readable_evidence_content(evidence_text):
                return ChatResponse(
                    session_id=session_id,
                    step="mitigation_dg_evidence_input",
                    bot_message="Please provide a readable URL or supported PDF, DOCX, MD, or TXT file, or skip this optional evidence.",
                    options=self._guided_options("Skip DG evidence"),
                    session=session.summary(),
                    input_mode="evidence_only",
                    error=True,
                )
            relevance = await self._guided_dg_evidence_relevance(session, group, evidence_text)
            if not relevance.get("relevant"):
                return ChatResponse(
                    session_id=session_id,
                    step="mitigation_dg_evidence_input",
                    bot_message=str(
                        relevance.get("reason")
                        or "The evidence does not show how this group benefits from the measure. Clarify the connection or provide different evidence."
                    ),
                    options=self._guided_options("Skip DG evidence"),
                    session=session.summary(),
                    input_mode="evidence_only",
                    error=True,
                )
            self._promote_temporary_evidence(
                session,
                target_scope=VALIDATED_EVIDENCE_SCOPE,
                provenance="validated_disadvantaged_group_evidence",
            )
        (session.mitigation_dg_evidence or {})[group] = evidence_text
        session.mitigation_dg_evidence_index += 1
        if session.mitigation_dg_evidence_index >= len(groups):
            return await self._guided_summary_step(
                session_id, session, "mitigation_dg_summary_review"
            )
        return self._guided_dg_evidence_decision_step(session_id, session)

    async def _guided_dg_evidence_relevance(self, session, group, evidence_text):
        evidence_context = await self._mitigation_evidence_context(
            session,
            session.pending_mitigation_measure or "",
            "; ".join(session.mitigation_mechanisms or []),
            evidence_text,
        )
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/mitigation_dg_evidence_relevance.txt"),
            messages=[
                {
                    "role": "user",
                    "content": f"Measure: {session.pending_mitigation_measure or ''}\nMechanisms: {'; '.join(session.mitigation_mechanisms or [])}\nDisadvantaged group: {group}\nEvidence reference: {evidence_text}\nEvidence content:\n{evidence_context}",
                }
            ],
            temperature=0.0,
            max_tokens=350,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        return payload if isinstance(payload, dict) else {"relevant": True, "reason": ""}

    async def _handle_mitigation_dg_summary_review(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message, self._guided_options("Confirm DG summary", "Modify disadvantaged groups")
            )
            or message
        )
        if action == normalize("Confirm DG summary"):
            session.phase = "mitigation_equity"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_equity",
                bot_message="How is this mitigation measure equitable for the confirmed disadvantaged groups? Describe the concrete distribution, access, affordability, participation, or protection mechanism.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        if action == normalize("Modify disadvantaged groups"):
            session.phase = "mitigation_dg_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_dg_input",
                bot_message="Provide the revised list of specific disadvantaged groups.",
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_equity(self, session_id, session, message):
        review = await self._guided_text_review("equity explanation", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_equity", review
            )
        session.mitigation_equity = str(review.get("normalized_text") or message).strip()
        return await self._guided_summary_step(
            session_id, session, "mitigation_final_summary_review"
        )

    async def _handle_mitigation_final_summary_review(self, session_id, session, message):
        action = normalize(
            exact_option_label(
                message, self._guided_options("Confirm final summary", "Modify final inputs")
            )
            or message
        )
        if action == normalize("Modify final inputs"):
            session.phase = "mitigation_summary_revision"
            session.mitigation_revision_stage = "final"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_summary_revision",
                bot_message=(
                    "Describe exactly what should change in the measure, mechanisms, "
                    "disadvantaged groups, evidence interpretation, or equity explanation."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        if action == normalize("Confirm final summary"):
            reason = (
                "Mechanisms mitigated: "
                + "; ".join(session.mitigation_mechanisms or [])
                + ". Equity: "
                + (session.mitigation_equity or "")
            )
            session.pending_mitigation_reason = reason
            return await self._validate_frozen_mitigation_inputs(
                session_id,
                session,
                session.pending_mitigation_measure or "",
                reason,
                session.pending_mitigation_evidence or "",
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _guided_text_review(
        self, field_name: str, value: str, session: ChatSession
    ) -> dict[str, object]:
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/mitigation_guided_input_clarity.txt"),
            messages=[
                {
                    "role": "user",
                    "content": f"Field: {field_name}\nSelected hazard: {session.selected_hazard or session.accepted_custom_hazard or ''}\nText: {value}",
                }
            ],
            temperature=0.0,
            max_tokens=300,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        if isinstance(payload, dict):
            return payload
        clean = re.sub(r"\s+", " ", value).strip()
        vague = normalize_for_match(clean) in {
            "it helps",
            "helps people",
            "everyone",
            "all people",
            "general support",
            "make it better",
        }
        return {
            "clear": bool(clean) and len(compact_for_match(clean)) >= 12 and not vague,
            "normalized_text": clean,
            "clarification_question": "Please replace the broad statement with the specific actor, action, affected group, and causal effect.",
        }

    def _guided_clarification_response(self, session_id, session, phase, review):
        session.phase = phase
        return ChatResponse(
            session_id=session_id,
            step=phase,
            bot_message=markdown_to_html(
                "### Clarification needed\n\n"
                + str(
                    review.get("clarification_question")
                    or "Please provide more specific, unambiguous information."
                )
            ),
            options=[],
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    @staticmethod
    def _split_guided_items(value: str) -> list[str]:
        items = [re.sub(r"^[-*\d.)\s]+", "", item).strip() for item in re.split(r"[\n;]+", value)]
        return list(dict.fromkeys(item for item in items if len(compact_for_match(item)) >= 8))[:8]
