# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *
from app.services.custom_hazard_validation import (
    summarize_custom_hazard_supporting_policy,
    validate_policy_reference_twin_transition,
)
from app.services.knowledge_base import VALIDATED_EVIDENCE_SCOPE


class ChatMitigationCreationGuidedMixin:
    """Guided, confirmation-based mitigation-measure creation flow."""

    @staticmethod
    def _guided_options(*labels: str) -> list[Option]:
        return [Option(id=index, label=label) for index, label in enumerate(labels, 1)]

    async def _mitigation_mechanism_selection_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        """Show general guidance and let the user choose a hazard mechanism first."""
        session.pending_mitigation_measure = None
        session.pending_mitigation_reason = None
        session.pending_mitigation_evidence = None
        session.selected_mitigation_mechanism = None
        session.selected_mitigation_policy = None
        session.pending_mitigation_policy = None
        session.pending_mitigation_policy_context = None
        session.pending_mitigation_policy_document_ids = None
        session.mitigation_mechanism_guidance = None
        session.mitigation_mechanisms = None
        session.mitigation_mechanism_reflection = None
        session.mitigation_policy_effects = None
        session.mitigation_policy_effect_index = 0

        general_considerations = await self._practical_policy_recommendations(session)
        overview = await self._mitigation_mechanism_planning_overview(session)
        candidates = overview.get("mechanisms")
        session.mitigation_mechanism_candidates = candidates if isinstance(candidates, list) else []
        session.phase = "mitigation_mechanism_selection"

        mechanism_lines: list[str] = []
        for index, candidate in enumerate(session.mitigation_mechanism_candidates, 1):
            if not isinstance(candidate, dict):
                continue
            mechanism = str(candidate.get("mechanism") or "").strip()
            if not mechanism:
                continue
            policy = str(candidate.get("policy_title") or "").strip()
            linkage = str(candidate.get("causal_linkage") or "").strip()
            mechanism_lines.append(f"{index}. **{mechanism}**")
            if policy:
                mechanism_lines.append(f"   - **Mapped policy:** {policy}")
            if linkage:
                mechanism_lines.append(f"   - **How it leads to the hazard:** {linkage}")
        if not mechanism_lines:
            mechanism_lines.append(
                "No predefined mechanism could be grounded reliably. Describe the causal mechanism you want to mitigate in your own words."
            )

        suggestions = self._guided_string_list(overview.get("general_suggestions"), limit=5)
        if not suggestions:
            suggestions = [
                "Target the part of the causal pathway that can be changed through policy design or implementation.",
                "Check that the measure reaches affected groups without creating new access or affordability barriers.",
            ]
        prompt = (
            f"{general_considerations}\n\n"
            "## Mechanisms leading to the hazard\n\n"
            + "\n".join(mechanism_lines)
            + "\n\n## General suggestions for mitigation\n\n"
            + "\n".join(f"- {item}" for item in suggestions)
            + "\n\n## Choose a mechanism\n\n"
            "Which mechanism leading to the hazard are you interested in mitigating? "
            "Choose one above or type another mechanism in your own words."
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_mechanism_selection",
            bot_message=markdown_to_html(prompt),
            options=self._mitigation_mechanism_selection_options(session),
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    def _mitigation_mechanism_selection_options(self, session: ChatSession) -> list[Option]:
        options: list[Option] = []
        seen: set[str] = set()
        for candidate in session.mitigation_mechanism_candidates or []:
            if not isinstance(candidate, dict):
                continue
            label = str(candidate.get("mechanism") or "").strip()
            key = normalize_for_match(label)
            if not label or key in seen:
                continue
            seen.add(key)
            options.append(Option(id=f"mechanism-{len(options) + 1}", label=label))
        return options

    async def _mitigation_mechanism_planning_overview(self, session: ChatSession) -> dict[str, object]:
        policy_context, policies = self._mitigation_mechanism_policy_context(session)
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"causal mechanisms by which policy implementation leads to {session.selected_hazard or 'the selected hazard'}",
            limit=8,
        )
        response = await ask_llm_chat(
            context=load_prompt_file("mitigation_mechanism_planning.txt"),
            messages=[{
                "role": "user",
                "content": (
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not selected'}\n"
                    f"Country: {session.country or 'Not selected'}\nRegion: {session.region or 'Not selected'}\n"
                    f"Sector: {session.sector or 'Not selected'}\nAffected profiles: {format_all_dgs(session)}\n\n"
                    f"Mapped policy context:\n{policy_context or 'None available'}\n\n"
                    f"Sector evidence context:\n{sector_context}"
                ),
            }],
            temperature=0.2,
            max_tokens=1300,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        if not isinstance(payload, dict):
            return self._fallback_mitigation_mechanism_overview(session, policies)
        normalized: list[dict[str, object]] = []
        if isinstance(payload.get("mechanisms"), list):
            for item in payload["mechanisms"][:5]:
                candidate = self._normalize_mitigation_mechanism_candidate(item)
                if candidate:
                    candidate["policy_title"] = self._grounded_mitigation_policy_title(
                        session,
                        str(candidate.get("policy_title") or ""),
                        policies,
                    )
                    normalized.append(candidate)
        if not normalized:
            return self._fallback_mitigation_mechanism_overview(session, policies)
        return {
            "general_suggestions": self._guided_string_list(payload.get("general_suggestions"), limit=5),
            "mechanisms": normalized,
        }

    def _mitigation_mechanism_policy_context(
        self, session: ChatSession
    ) -> tuple[str, list[dict[str, object]]]:
        try:
            policies = list(self._ranked_new_policy_suggestions(session, limit=8))
        except Exception:
            logger.exception("Failed to load mapped policies for mechanism selection")
            policies = []
        lines: list[str] = []
        for index, policy in enumerate(policies, 1):
            lines.append(
                f"P{index} | title: {str(policy.get('policy_title') or 'Untitled policy').strip()} | "
                f"description: {str(policy.get('short_description') or 'Not provided').strip()} | "
                f"mapped effect on hazard: {str(policy.get('mitigation_effect') or 'Not provided').strip()}"
            )
        custom_state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        for label, key in (
            ("Validated mechanism", "selected_mechanism"),
            ("Validated causal linkage", "causal_linkage"),
            ("Policy reference", "policy_reference"),
        ):
            value = str(custom_state.get(key) or "").strip()
            if value:
                lines.append(f"{label}: {value}")
        return "\n".join(lines), policies

    @staticmethod
    def _grounded_mitigation_policy_title(
        session: ChatSession,
        proposed_title: str,
        policies: list[dict[str, object]],
    ) -> str:
        proposed_key = normalize_for_match(proposed_title)
        if not proposed_key:
            return ""
        for policy in policies:
            title = str(policy.get("policy_title") or "").strip()
            if normalize_for_match(title) == proposed_key:
                return title
        custom_state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        custom_reference = str(custom_state.get("policy_reference") or "").strip()
        if normalize_for_match(custom_reference) == proposed_key:
            return custom_reference
        return ""

    @classmethod
    def _fallback_mitigation_mechanism_overview(
        cls, session: ChatSession, policies: list[dict[str, object]]
    ) -> dict[str, object]:
        mechanisms: list[dict[str, object]] = []
        custom_state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
        selected = str(custom_state.get("selected_mechanism") or "").strip()
        if selected:
            mechanisms.append({
                "mechanism": selected,
                "policy_title": str(custom_state.get("policy_reference") or "").strip(),
                "causal_linkage": str(custom_state.get("causal_linkage") or "").strip(),
                "considerations": [],
                "mitigation_suggestions": [],
            })
        return {"general_suggestions": [], "mechanisms": mechanisms[:5]}

    @classmethod
    def _normalize_mitigation_mechanism_candidate(cls, item: object) -> dict[str, object] | None:
        if not isinstance(item, dict):
            return None
        mechanism = str(item.get("mechanism") or "").strip()
        if not mechanism:
            return None
        return {
            "mechanism": mechanism,
            "policy_title": str(item.get("policy_title") or "").strip(),
            "causal_linkage": str(item.get("causal_linkage") or "").strip(),
            "considerations": cls._guided_string_list(item.get("considerations"), limit=5),
            "mitigation_suggestions": cls._guided_string_list(item.get("mitigation_suggestions"), limit=5),
        }

    @staticmethod
    def _guided_string_list(value: object, *, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:limit]

    async def _handle_mitigation_mechanism_selection(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._mitigation_mechanism_selection_options(session)
        exact = exact_option_label(message, options)
        candidate: dict[str, object] | None = None
        if exact:
            selected = exact
        else:
            ordinal = self._ordinal_index_from_open_text(message)
            option_index = (
                ordinal if ordinal is None or ordinal >= 0 else len(options) + ordinal
            )
            if option_index is not None and 0 <= option_index < len(options):
                selected = options[option_index].label
            else:
                review = await self._guided_text_review(
                    "mechanism leading to the selected hazard", message, session
                )
                if not review.get("clear"):
                    return self._guided_clarification_response(
                        session_id, session, "mitigation_mechanism_selection", review
                    )
                selected = str(review.get("normalized_text") or message).strip()
        selected_key = normalize_for_match(selected)
        candidate = next(
            (
                item for item in (session.mitigation_mechanism_candidates or [])
                if isinstance(item, dict)
                and normalize_for_match(str(item.get("mechanism") or "")) == selected_key
            ),
            None,
        )
        if not selected:
            return self._guided_clarification_response(
                session_id, session, "mitigation_mechanism_selection",
                {"clarification_question": "Which causal mechanism leading to the selected hazard do you want to mitigate?"},
            )
        guidance = candidate or await self._mitigation_specific_mechanism_guidance(session, selected)
        session.selected_mitigation_mechanism = selected
        session.selected_mitigation_policy = None
        session.mitigation_mechanism_guidance = dict(guidance)
        session.mitigation_mechanisms = [selected]
        return await self._start_mitigation_policy_gate(session_id, session)

    async def _start_mitigation_policy_gate(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        mechanism = str(session.selected_mitigation_mechanism or "").strip()
        hazard = str(session.selected_hazard or session.accepted_custom_hazard or "").strip()
        query = " ".join(
            value
            for value in (
                hazard,
                mechanism,
                session.sector or "",
                session.country or "",
                "policy provision implementation",
            )
            if value
        )
        try:
            results = await self._shared_knowledge_results(
                session, query, main_limit=10, evidence_limit=6
            )
            linked_policy_results = self._mitigation_policy_reference_results(session)
            grounded = await self.grounding_models.ground_results(
                query, [*linked_policy_results, *results]
            )
        except Exception:
            logger.exception("Policy KB lookup failed before mitigation guidance")
            grounded = []
        context = self._format_knowledge_results(grounded)
        if context:
            mapped_title = str(
                (session.mitigation_mechanism_guidance or {}).get("policy_title") or ""
            ).strip()
            title = self._matched_mitigation_policy_title(mapped_title, grounded)
            sources = [
                {
                    "title": str(item.get("title") or "Knowledge source").strip(),
                    "source_uri": str(item.get("source_uri") or "").strip(),
                    "page_number": item.get("page_number"),
                }
                for item in grounded[:6]
                if isinstance(item, dict)
            ]
            response = await self._validate_mitigation_policy_candidate(
                session_id,
                session,
                context=context,
                title=title,
                sources=sources,
                origin="knowledge base",
                allow_retry=False,
            )
            if response is not None:
                return response
        return self._mitigation_policy_reference_step(
            session_id,
            session,
            detail=(
                "I could not find a knowledge-base policy with a supported connection "
                "to the selected mechanism and hazard."
            ),
        )

    @staticmethod
    def _matched_mitigation_policy_title(
        mapped_title: str, results: list[dict[str, object]]
    ) -> str:
        mapped_key = normalize_for_match(mapped_title)
        if mapped_key:
            for item in results:
                title = str(item.get("title") or "").strip()
                title_key = normalize_for_match(title)
                if title_key and (title_key == mapped_key or mapped_key in title_key):
                    return title
        for item in results:
            title = str(item.get("title") or "").strip()
            if title:
                return title
        return mapped_title or "Policy found in the knowledge base"

    async def _validate_mitigation_policy_candidate(
        self,
        session_id: str,
        session: ChatSession,
        *,
        context: str,
        title: str,
        sources: list[dict[str, object]],
        origin: str,
        clarification: str = "",
        allow_retry: bool = True,
    ) -> ChatResponse | None:
        relevance = await validate_policy_reference_twin_transition(context)
        if relevance is None:
            return None
        if not relevance.get("related"):
            if not allow_retry:
                return None
            session.pending_mitigation_policy = {
                "title": title,
                "sources": sources,
                "origin": origin,
            }
            session.pending_mitigation_policy_context = context
            return self._mitigation_policy_retry_step(
                session_id,
                session,
                str(relevance.get("reason") or "The policy is not clearly relevant to the twin transition."),
            )
        hazard = str(session.selected_hazard or session.accepted_custom_hazard or "").strip()
        mechanism = str(session.selected_mitigation_mechanism or "").strip()
        policy = await summarize_custom_hazard_supporting_policy(
            hazard,
            mechanism,
            context,
            relevance_clarification=clarification,
        )
        if not policy.get("supported") or not str(policy.get("causal_linkage") or "").strip():
            if not allow_retry:
                return None
            session.pending_mitigation_policy = {
                "title": title,
                "sources": sources,
                "origin": origin,
            }
            session.pending_mitigation_policy_context = context
            return self._mitigation_policy_retry_step(
                session_id,
                session,
                str(
                    policy.get("reason")
                    or "The policy's relevance or causal linkage to the mechanism and hazard is unclear."
                ),
            )
        session.pending_mitigation_policy = {
            "title": title,
            "summary": str(policy.get("summary") or "").strip(),
            "policy_details": str(policy.get("policy_details") or "").strip(),
            "causal_linkage": str(policy.get("causal_linkage") or "").strip(),
            "reason": str(policy.get("reason") or "").strip(),
            "sources": sources,
            "origin": origin,
        }
        session.pending_mitigation_policy_context = context
        return self._mitigation_policy_confirmation_step(session_id, session)

    def _mitigation_policy_confirmation_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        policy = session.pending_mitigation_policy or {}
        source_lines: list[str] = []
        for source in policy.get("sources") or []:
            if not isinstance(source, dict):
                continue
            label = str(source.get("title") or "Knowledge source").strip()
            uri = str(source.get("source_uri") or "").strip()
            page = source.get("page_number")
            if uri:
                label = f"[{label}]({uri})"
            source_lines.append(f"- {label}" + (f", page {page}" if page else ""))
        session.phase = "mitigation_policy_confirmation"
        message = (
            "## Confirm the policy linked to this mechanism\n\n"
            f"**Policy:** {policy.get('title') or 'Policy found'}\n\n"
            f"**Summary:** {policy.get('summary') or policy.get('reason') or 'Relevant policy content was found.'}\n\n"
            f"**Relevant policy details:** {policy.get('policy_details') or policy.get('summary') or 'Not separately stated.'}\n\n"
            f"**Policy-to-mechanism-to-hazard linkage:** {policy.get('causal_linkage')}\n\n"
            + ("**Sources:**\n" + "\n".join(source_lines) + "\n\n" if source_lines else "")
            + "Is this the policy that caused the hazard through the selected mechanism?"
        )
        return ChatResponse(
            session_id=session_id,
            step="mitigation_policy_confirmation",
            bot_message=markdown_to_html(message),
            options=self._guided_options("Yes, use this policy", "No, provide another policy"),
            session=session.summary(),
            input_mode="text",
            error=False,
        )

    async def _handle_mitigation_policy_confirmation(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._guided_options("Yes, use this policy", "No, provide another policy")
        label = exact_option_label(message, options) or match_option_label(message, options)
        action = normalize(label or message)
        if action in {normalize("Yes, use this policy"), normalize("Yes")}:
            policy = session.pending_mitigation_policy or {}
            session.selected_mitigation_policy = str(policy.get("title") or "").strip() or None
            guidance = dict(session.mitigation_mechanism_guidance or {})
            guidance["policy_title"] = session.selected_mitigation_policy or ""
            guidance["causal_linkage"] = str(policy.get("causal_linkage") or "").strip()
            session.mitigation_mechanism_guidance = guidance
            session.pending_mitigation_reason = (
                str(policy.get("causal_linkage") or "").strip()
                or str(session.selected_mitigation_mechanism or "").strip()
            )
            return self._mitigation_selected_mechanism_guidance_step(session_id, session)
        if action in {normalize("No, provide another policy"), normalize("No")}:
            session.selected_mitigation_policy = None
            return self._mitigation_policy_reference_step(
                session_id,
                session,
                detail="Please provide the policy document or URL you want to use instead.",
            )
        return self._mitigation_policy_confirmation_step(session_id, session)

    def _mitigation_policy_reference_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        detail: str = "",
        error: bool = False,
    ) -> ChatResponse:
        session.phase = "mitigation_policy_reference"
        message = "## Provide the policy document\n\n"
        if detail:
            message += f"{detail}\n\n"
        message += "Paste a policy URL or attach a PDF, DOCX, MD, or TXT file."
        return ChatResponse(
            session_id=session_id,
            step="mitigation_policy_reference",
            bot_message=markdown_to_html(message),
            options=[],
            session=session.summary(),
            input_mode="policy_reference",
            error=error,
        )

    async def _handle_mitigation_policy_reference(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if "Policy reference error:" in message and "Policy reference document ID:" not in message:
            detail = message.split("Policy reference error:", 1)[1].strip()
            return self._mitigation_policy_reference_step(
                session_id, session, detail=f"The policy could not be read: {detail}", error=True
            )
        document_ids = re.findall(
            r"^Policy reference document ID:\s*(\S+)",
            message,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if not document_ids:
            return self._mitigation_policy_reference_step(
                session_id,
                session,
                detail="Please provide a readable policy URL or file.",
                error=True,
            )
        context = await self._policy_reference_context(session, document_ids)
        if not context:
            return self._mitigation_policy_reference_step(
                session_id,
                session,
                detail="No readable policy text could be extracted. Please provide the policy again.",
                error=True,
            )
        reference = re.search(
            r"^Policy reference (?:URL|file):\s*(.+)$",
            message,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        title = reference.group(1).strip() if reference else "Provided policy document"
        session.pending_mitigation_policy_document_ids = document_ids
        response = await self._validate_mitigation_policy_candidate(
            session_id,
            session,
            context=context,
            title=title,
            sources=[{"title": title, "source_uri": "", "page_number": None}],
            origin="user-provided policy",
        )
        if response is not None:
            return response
        return self._mitigation_policy_retry_step(
            session_id,
            session,
            "I could not verify the policy's relevance. Clarify the connection or provide the policy again.",
        )

    def _mitigation_policy_retry_step(
        self, session_id: str, session: ChatSession, reason: str
    ) -> ChatResponse:
        session.phase = "mitigation_policy_retry"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_policy_retry",
            bot_message=markdown_to_html(
                "## Policy needs clarification\n\n"
                f"{reason}\n\n"
                "Clarify how a specific policy provision leads through the selected mechanism "
                "to the hazard, or provide another policy URL/file."
            ),
            options=self._guided_options("Clarify the relevance", "Provide policy again"),
            session=session.summary(),
            input_mode="textarea",
            error=True,
        )

    async def _handle_mitigation_policy_retry(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._guided_options("Clarify the relevance", "Provide policy again")
        label = exact_option_label(message, options) or match_option_label(message, options)
        action = normalize(label or message)
        if action == normalize("Provide policy again"):
            return self._mitigation_policy_reference_step(session_id, session)
        if action == normalize("Clarify the relevance"):
            session.phase = "mitigation_policy_clarification"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_policy_clarification",
                bot_message=markdown_to_html(
                    "Explain which policy provision is relevant and how it leads through "
                    "the selected mechanism to the hazard."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return await self._handle_mitigation_policy_clarification(
            session_id, session, message
        )

    async def _handle_mitigation_policy_clarification(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        context = str(session.pending_mitigation_policy_context or "").strip()
        policy = session.pending_mitigation_policy or {}
        if not context:
            return self._mitigation_policy_reference_step(
                session_id, session, detail="Please provide the policy document before clarifying it.", error=True
            )
        clarification = str(message or "").strip()
        if not clarification:
            return self._mitigation_policy_retry_step(
                session_id, session, "Please explain the relevance and causal linkage."
            )
        response = await self._validate_mitigation_policy_candidate(
            session_id,
            session,
            context=context,
            title=str(policy.get("title") or "Provided policy document"),
            sources=[item for item in policy.get("sources") or [] if isinstance(item, dict)],
            origin=str(policy.get("origin") or "user-provided policy"),
            clarification=clarification,
        )
        return response or self._mitigation_policy_retry_step(
            session_id,
            session,
            "The clarification did not establish relevance and causal linkage. Please clarify further or provide the policy again.",
        )

    def _mitigation_selected_mechanism_guidance_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        selected = str(session.selected_mitigation_mechanism or "").strip()
        guidance = dict(session.mitigation_mechanism_guidance or {})
        session.phase = "mitigation_measure"
        considerations = self._guided_string_list(guidance.get("considerations"), limit=5)
        suggestions = self._guided_string_list(guidance.get("mitigation_suggestions"), limit=5)
        return ChatResponse(
            session_id=session_id,
            step="mitigation_measure",
            bot_message=render_message(
                "mitigation_mechanism_guidance.md",
                mechanism=selected,
                policy=session.selected_mitigation_policy or "",
                causal_linkage=str(guidance.get("causal_linkage") or "").strip(),
                considerations="\n".join(f"- {item}" for item in considerations)
                or "- Check how this mechanism operates for affected groups in the selected context.",
                suggestions="\n".join(f"- {item}" for item in suggestions)
                or "- Design the measure to interrupt or reduce this causal pathway.",
            ),
            options=[],
            session=session.summary(),
            input_mode="mitigation_measure",
            error=False,
        )

    async def _mitigation_specific_mechanism_guidance(
        self, session: ChatSession, mechanism: str
    ) -> dict[str, object]:
        policy_context, policies = self._mitigation_mechanism_policy_context(session)
        response = await ask_llm_chat(
            context=load_prompt_file("mitigation_mechanism_guidance.txt"),
            messages=[{
                "role": "user",
                "content": (
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not selected'}\n"
                    f"Selected mechanism: {mechanism}\nCountry: {session.country or 'Not selected'}\n"
                    f"Region: {session.region or 'Not selected'}\nSector: {session.sector or 'Not selected'}\n"
                    f"Affected profiles: {format_all_dgs(session)}\n\n"
                    f"Mapped policy context:\n{policy_context or 'None available'}"
                ),
            }],
            temperature=0.2,
            max_tokens=800,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        normalized = self._normalize_mitigation_mechanism_candidate(payload)
        if normalized:
            normalized["policy_title"] = self._grounded_mitigation_policy_title(
                session,
                str(normalized.get("policy_title") or ""),
                policies,
            )
            return normalized
        return {
            "mechanism": mechanism,
            "policy_title": "",
            "causal_linkage": "",
            "considerations": [],
            "mitigation_suggestions": [],
        }

    async def _start_guided_mitigation_flow(
        self,
        session_id: str,
        session: ChatSession,
        mitigation_measure: str,
        initial_reason: str = "",
    ) -> ChatResponse:
        selected_mechanism = str(session.selected_mitigation_mechanism or "").strip()
        selected_guidance = (
            session.mitigation_mechanism_guidance
            if isinstance(session.mitigation_mechanism_guidance, dict)
            else {}
        )
        session.pending_mitigation_measure = mitigation_measure.strip()
        session.pending_mitigation_reason = initial_reason.strip()
        session.pending_mitigation_evidence = ""
        if not selected_mechanism:
            session.mitigation_mechanisms = None
        session.mitigation_mechanism_reflection = None
        session.mitigation_policy_effects = None
        session.mitigation_policy_effect_index = 0
        session.mitigation_creation_summary = None
        session.mitigation_target_population = None
        session.mitigation_dg_evidence = None
        session.mitigation_dg_evidence_index = 0
        session.mitigation_equity = None
        session.mitigation_equity_skipped = False
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
        if selected_mechanism:
            session.mitigation_mechanisms = [selected_mechanism]
            session.pending_mitigation_reason = (
                str(selected_guidance.get("causal_linkage") or "").strip()
                or initial_reason.strip()
                or selected_mechanism
            )
            return await self._mitigation_mechanism_reflection_step(
                session_id, session
            )
        return self._mitigation_mechanism_confirmation_step(session_id, session, mechanisms)

    async def _assess_measure_policy_and_mechanisms(
        self, session: ChatSession, mitigation_measure: str
    ) -> tuple[dict[str, object], list[str]]:
        source_parts: list[str] = []
        if session.selected_mitigation_mechanism:
            source_parts.append(
                "User-selected mechanism: " + session.selected_mitigation_mechanism
            )
        if session.selected_mitigation_policy:
            source_parts.append("Mapped policy: " + session.selected_mitigation_policy)
        if isinstance(session.mitigation_mechanism_guidance, dict):
            linkage = str(
                session.mitigation_mechanism_guidance.get("causal_linkage") or ""
            ).strip()
            if linkage:
                source_parts.append("Policy-to-hazard causal linkage: " + linkage)
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

    async def _mitigation_mechanism_reflection_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        guidance = (
            session.mitigation_mechanism_guidance
            if isinstance(session.mitigation_mechanism_guidance, dict)
            else {}
        )
        response = await ask_llm_chat(
            context=load_prompt_file("mitigation_measure_mechanism_reflection.txt"),
            messages=[{
                "role": "user",
                "content": (
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or ''}\n"
                    f"Chosen mechanism: {'; '.join(session.mitigation_mechanisms or [])}\n"
                    f"Mapped policy: {session.selected_mitigation_policy or 'Not identified'}\n"
                    f"Policy-to-hazard linkage: {str(guidance.get('causal_linkage') or '')}\n"
                    f"Mitigation measure: {session.pending_mitigation_measure or ''}"
                ),
            }],
            temperature=0.2,
            max_tokens=450,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        reflection = (
            str(payload.get("reflection") or "").strip()
            if isinstance(payload, dict)
            else ""
        )
        if not reflection:
            reflection = (
                f"The proposed measure should be assessed for how its concrete actions change "
                f"the chosen mechanism: {'; '.join(session.mitigation_mechanisms or [])}."
            )
        session.mitigation_mechanism_reflection = reflection
        session.phase = "mitigation_mechanism_reflection_review"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_mechanism_reflection_review",
            bot_message=markdown_to_html(
                "### Mechanism to be mitigated\n\n"
                f"{reflection}\n\n"
                "Confirm this reflection or clarify how the measure will change the chosen mechanism."
            ),
            options=self._guided_options("Confirm reflection", "Clarify reflection"),
            session=session.summary(),
            error=False,
        )

    async def _handle_mitigation_mechanism_reflection_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        action = normalize(
            exact_option_label(
                message, self._guided_options("Confirm reflection", "Clarify reflection")
            )
            or message
        )
        if action == normalize("Confirm reflection"):
            return self._mitigation_evidence_decision_step(
                session_id,
                session,
                session.pending_mitigation_measure or "",
                session.pending_mitigation_reason or "",
                "",
            )
        if action == normalize("Clarify reflection"):
            session.phase = "mitigation_mechanism_reflection_input"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_mechanism_reflection_input",
                bot_message=(
                    "Describe specifically how the measure changes the chosen mechanism "
                    "and how that change reduces the selected hazard."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_mechanism_reflection_input(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        review = await self._guided_text_review(
            "how the mitigation measure changes the chosen mechanism", message, session
        )
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_mechanism_reflection_input", review
            )
        session.mitigation_mechanism_reflection = str(
            review.get("normalized_text") or message
        ).strip()
        return self._mitigation_evidence_decision_step(
            session_id,
            session,
            session.pending_mitigation_measure or "",
            session.pending_mitigation_reason or "",
            "",
        )

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
            return await self._mitigation_mechanism_reflection_step(
                session_id, session
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
        return await self._mitigation_mechanism_reflection_step(session_id, session)

    async def _guided_after_evidence(self, session_id, session, evidence_text: str) -> ChatResponse:
        if evidence_text:
            relevant = await self._guided_evidence_relevance(session, evidence_text)
            outcome = normalize_for_match(str(relevant.get("outcome") or ""))
            if not relevant.get("relevant") or outcome in {"irrelevant", "ambiguous", "unavailable"}:
                session.phase = "mitigation_evidence_input"
                return self._mitigation_evidence_input_step(
                    session_id,
                    session,
                    error=True,
                    message=str(
                        relevant.get("clarification_question")
                        or relevant.get("reason")
                        or "The evidence does not clearly support the mitigation measure. Please clarify the connection or provide different evidence."
                    ),
                )
        session.pending_mitigation_evidence = evidence_text
        return await self._start_mitigation_policy_effect_review(session_id, session)

    async def _guided_evidence_relevance(
        self, session: ChatSession, evidence_text: str
    ) -> dict[str, object]:
        context_text = await self._mitigation_evidence_context(
            session,
            session.pending_mitigation_measure or "",
            "",
            evidence_text,
            retrieval_query=session.pending_mitigation_measure or "",
        )
        response = await ask_llm_chat(
            context=load_prompt_file("mitigation_guided_evidence_relevance.txt"),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Measure: {session.pending_mitigation_measure or ''}\n"
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
                "outcome": "unavailable",
                "relevant": False,
                "reason": "Evidence relevance could not be established.",
                "clarification_question": (
                    "Please explain how this evidence supports the mitigation measure, provide "
                    "different evidence, or choose Skip."
                ),
            }
        )

    async def _start_mitigation_policy_effect_review(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        effects = await self._identify_mitigation_policy_effects(session)
        session.mitigation_policy_effects = effects
        session.mitigation_policy_effect_index = 0
        if not effects:
            return await self._guided_summary_step(
                session_id, session, "mitigation_summary_review"
            )
        return self._mitigation_policy_effect_review_step(session_id, session)

    async def _identify_mitigation_policy_effects(
        self, session: ChatSession
    ) -> list[dict[str, object]]:
        policy_context, _ = self._mitigation_mechanism_policy_context(session)
        guidance = (
            session.mitigation_mechanism_guidance
            if isinstance(session.mitigation_mechanism_guidance, dict)
            else {}
        )
        response = await ask_llm_chat(
            context=load_prompt_file("mitigation_policy_effects.txt"),
            messages=[{
                "role": "user",
                "content": (
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or ''}\n"
                    f"Chosen mechanism: {'; '.join(session.mitigation_mechanisms or [])}\n"
                    f"Mapped policy: {session.selected_mitigation_policy or 'Not identified'}\n"
                    f"Policy-to-hazard linkage: {str(guidance.get('causal_linkage') or '')}\n"
                    f"Mitigation measure: {session.pending_mitigation_measure or ''}\n"
                    f"Mechanism reflection: {session.mitigation_mechanism_reflection or ''}\n\n"
                    f"Mapped policy context:\n{policy_context or 'None available'}"
                ),
            }],
            temperature=0.2,
            max_tokens=1000,
            response_format="json",
        )
        payload = None if is_llm_unavailable_response(response) else parse_json_object(response)
        raw_effects = payload.get("effects") if isinstance(payload, dict) else None
        if not isinstance(raw_effects, list):
            return []
        effects: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in raw_effects[:4]:
            if not isinstance(item, dict):
                continue
            problem = str(item.get("problem") or "").strip()
            pathway = str(item.get("causal_pathway") or "").strip()
            key = normalize_for_match(problem)
            if not problem or not pathway or key in seen:
                continue
            seen.add(key)
            effects.append({
                "policy_aspect": str(item.get("policy_aspect") or "").strip(),
                "problem": problem,
                "causal_pathway": pathway,
                "basis": str(item.get("basis") or "").strip(),
                "user_position": "pending",
                "additional_mitigation": "",
                "disagreement_reason": "",
            })
        return effects

    def _mitigation_policy_effect_review_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        effects = session.mitigation_policy_effects or []
        index = session.mitigation_policy_effect_index
        if index >= len(effects):
            raise ValueError("Policy-effect review index is outside the available effects")
        effect = effects[index]
        prior = self._mitigation_policy_effect_decisions_markdown(effects[:index])
        session.phase = "mitigation_policy_effect_review"
        return ChatResponse(
            session_id=session_id,
            step="mitigation_policy_effect_review",
            bot_message=markdown_to_html(
                "### Possible effect on another policy aspect\n\n"
                f"**Policy aspect:** {effect.get('policy_aspect') or 'Related policy implementation'}\n\n"
                f"**Potential problem:** {effect.get('problem')}\n\n"
                f"**How it could arise:** {effect.get('causal_pathway')}\n\n"
                + (f"**Grounding basis:** {effect.get('basis')}\n\n" if effect.get("basis") else "")
                + (f"### Understanding recorded so far\n\n{prior}\n\n" if prior else "")
                + "Could your mitigation measure create this problem?"
            ),
            options=self._guided_options("Yes", "No"),
            session=session.summary(),
            error=False,
        )

    @staticmethod
    def _mitigation_policy_effect_decisions_markdown(
        effects: list[dict[str, object]]
    ) -> str:
        lines: list[str] = []
        for effect in effects:
            position = str(effect.get("user_position") or "pending")
            detail = str(
                effect.get("additional_mitigation")
                or effect.get("disagreement_reason")
                or ""
            ).strip()
            lines.append(
                f"- **{effect.get('problem')}:** {position}"
                + (f" — {detail}" if detail else "")
            )
        return "\n".join(lines)

    async def _handle_mitigation_policy_effect_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        options = self._guided_options("Yes", "No")
        action = normalize(exact_option_label(message, options) or message)
        effects = session.mitigation_policy_effects or []
        index = session.mitigation_policy_effect_index
        if index >= len(effects):
            return await self._guided_summary_step(
                session_id, session, "mitigation_summary_review"
            )
        if action == normalize("Yes"):
            effects[index]["user_position"] = "agreed"
            session.phase = "mitigation_policy_effect_mitigation"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_policy_effect_mitigation",
                bot_message=(
                    "Propose an additional mitigation that would prevent or reduce this "
                    f"problem: {effects[index].get('problem')}"
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        if action == normalize("No"):
            effects[index]["user_position"] = "disagreed"
            session.phase = "mitigation_policy_effect_disagreement"
            return ChatResponse(
                session_id=session_id,
                step="mitigation_policy_effect_disagreement",
                bot_message=(
                    "Explain why the proposed measure would not create this problem. "
                    "Refer to a concrete safeguard, design feature, or causal reason."
                ),
                options=[],
                session=session.summary(),
                input_mode="textarea",
                error=False,
            )
        return self._repeat_current_options(session_id, session, self.invalid_message, True)

    async def _handle_mitigation_policy_effect_mitigation(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        review = await self._guided_text_review(
            "additional mitigation for an agreed potential policy problem", message, session
        )
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_policy_effect_mitigation", review
            )
        effect = self._current_mitigation_policy_effect(session)
        if effect is not None:
            effect["additional_mitigation"] = str(
                review.get("normalized_text") or message
            ).strip()
        return await self._advance_mitigation_policy_effect(session_id, session)

    async def _handle_mitigation_policy_effect_disagreement(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        review = await self._guided_text_review(
            "reason the mitigation will not create the potential policy problem",
            message,
            session,
        )
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_policy_effect_disagreement", review
            )
        effect = self._current_mitigation_policy_effect(session)
        if effect is not None:
            effect["disagreement_reason"] = str(
                review.get("normalized_text") or message
            ).strip()
        return await self._advance_mitigation_policy_effect(session_id, session)

    @staticmethod
    def _current_mitigation_policy_effect(
        session: ChatSession,
    ) -> dict[str, object] | None:
        effects = session.mitigation_policy_effects or []
        index = session.mitigation_policy_effect_index
        return effects[index] if 0 <= index < len(effects) else None

    async def _advance_mitigation_policy_effect(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        session.mitigation_policy_effect_index += 1
        if session.mitigation_policy_effect_index < len(
            session.mitigation_policy_effects or []
        ):
            return self._mitigation_policy_effect_review_step(session_id, session)
        return await self._guided_summary_step(
            session_id, session, "mitigation_summary_review"
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
        linkage = (
            str((session.mitigation_mechanism_guidance or {}).get("causal_linkage") or "").strip()
            if isinstance(session.mitigation_mechanism_guidance, dict)
            else ""
        )
        effect_summary = self._mitigation_policy_effect_decisions_markdown(
            session.mitigation_policy_effects or []
        )
        return (
            f"- **Measure:** {session.pending_mitigation_measure or session.mitigation_measure or 'Not provided'}\n"
            f"- **Mechanisms mitigated:** {'; '.join(session.mitigation_mechanisms or []) or 'Not provided'}\n"
            f"- **Mapped policy:** {session.selected_mitigation_policy or 'Not identified'}\n"
            f"- **Policy-to-hazard linkage:** {linkage or 'Not available'}\n"
            f"- **How the measure mitigates the mechanism:** {session.mitigation_mechanism_reflection or 'Not confirmed'}\n"
            f"- **Measure evidence:** {evidence}\n"
            f"- **Other policy effects reviewed:**\n{effect_summary or '  - No distinct grounded problem identified'}\n"
            f"- **Open Labs inspiration decision:** {inspiration.get('action') or 'Not reviewed yet'}"
            f"{(': ' + str(inspiration.get('detail'))) if inspiration.get('detail') else ''}\n"
            f"- **Disadvantaged groups benefited:** {groups}\n"
            f"- **DG evidence:** {sum(bool(value) for value in dg_evidence.values())} of {len(dg_evidence)} group(s) supplied evidence\n"
            f"- **How the measure is equitable:** "
            f"{session.mitigation_equity or ('Not provided (skipped)' if session.mitigation_equity_skipped else 'Not described yet')}"
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
                if session.selected_mitigation_mechanism:
                    return await self._start_guided_mitigation_flow(
                        session_id, session, choices[index - 1]
                    )
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
        if session.selected_mitigation_mechanism:
            return await self._start_guided_mitigation_flow(
                session_id, session, session.pending_mitigation_measure
            )
        relevance, mechanisms = await self._assess_measure_policy_and_mechanisms(
            session, session.pending_mitigation_measure
        )
        return self._mitigation_mechanism_confirmation_step(session_id, session, mechanisms)

    async def _guided_dg_suggestion_step(self, session_id, session) -> ChatResponse:
        groups = self._mitigation_target_population_labels(session)
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
                bot_message="How is the proposed mitigation measure equitable for the different disadvantaged groups? Describe the concrete distribution, access, affordability, participation, or protection mechanism.",
                options=self._guided_options("Skip this"),
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
        action = normalize(
            exact_option_label(message, self._guided_options("Skip this")) or ""
        )
        if action == normalize("Skip this"):
            session.mitigation_equity = None
            session.mitigation_equity_skipped = True
            return await self._guided_summary_step(
                session_id, session, "mitigation_final_summary_review"
            )
        review = await self._guided_text_review("equity explanation", message, session)
        if not review.get("clear"):
            return self._guided_clarification_response(
                session_id, session, "mitigation_equity", review
            )
        session.mitigation_equity = str(review.get("normalized_text") or message).strip()
        session.mitigation_equity_skipped = False
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
            linkage = (
                str((session.mitigation_mechanism_guidance or {}).get("causal_linkage") or "").strip()
                if isinstance(session.mitigation_mechanism_guidance, dict)
                else ""
            )
            effect_details = "; ".join(
                " | ".join(
                    part
                    for part in (
                        str(effect.get("problem") or "").strip(),
                        str(effect.get("user_position") or "").strip(),
                        str(
                            effect.get("additional_mitigation")
                            or effect.get("disagreement_reason")
                            or ""
                        ).strip(),
                    )
                    if part
                )
                for effect in (session.mitigation_policy_effects or [])
                if isinstance(effect, dict)
            )
            reason = (
                "Mechanisms mitigated: "
                + "; ".join(session.mitigation_mechanisms or [])
                + (f". Mapped policy: {session.selected_mitigation_policy}" if session.selected_mitigation_policy else "")
                + (f". Policy-to-hazard linkage: {linkage}" if linkage else "")
                + (
                    f". Mechanism reflection: {session.mitigation_mechanism_reflection}"
                    if session.mitigation_mechanism_reflection
                    else ""
                )
                + (f". Other policy effects reviewed: {effect_details}" if effect_details else "")
                + ". Equity: "
                + (
                    session.mitigation_equity
                    or ("Not provided (skipped)" if session.mitigation_equity_skipped else "")
                )
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
        current_effect = self._current_mitigation_policy_effect(session)
        response = await ask_llm_chat(
            context=load_nested_prompt_file("llm/mitigation_guided_input_clarity.txt"),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Field: {field_name}\n"
                        f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or ''}\n"
                        f"Chosen mechanism: {'; '.join(session.mitigation_mechanisms or [])}\n"
                        f"Mitigation measure: {session.pending_mitigation_measure or session.mitigation_measure or ''}\n"
                        f"Current potential policy problem: {str((current_effect or {}).get('problem') or '')}\n"
                        f"Text: {value}"
                    ),
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
        options = (
            self._mitigation_mechanism_selection_options(session)
            if phase == "mitigation_mechanism_selection"
            else []
        )
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
            options=options,
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    @staticmethod
    def _split_guided_items(value: str) -> list[str]:
        items = [re.sub(r"^[-*\d.)\s]+", "", item).strip() for item in re.split(r"[\n;]+", value)]
        return list(dict.fromkeys(item for item in items if len(compact_for_match(item)) >= 8))[:8]
