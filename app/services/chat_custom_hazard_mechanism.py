import re

from app.schemas import ChatResponse
from app.llm import ask_llm_chat
from app.services.chat_options import (
    CUSTOM_HAZARD_CAUSAL_LINKAGE_OPTIONS,
    CUSTOM_HAZARD_MECHANISM_CONFIRMATION_OPTIONS,
    CUSTOM_HAZARD_MECHANISM_INPUT_OPTIONS,
    CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION_OPTIONS,
    HAZARD_ENTRY_OPTIONS,
    exact_option_label,
    match_option_label,
    normalize,
)
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.custom_hazard_validation import (
    assess_custom_hazard_mechanism_clarity,
    custom_hazard_dimension_floor,
    policy_objective_for_sector,
    summarize_custom_hazard_supporting_policy,
    suggest_custom_hazard_mechanisms,
    validate_custom_hazard_mechanism_linkage,
)
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.message_renderer import markdown_to_html
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
)


class ChatCustomHazardMechanismMixin:
    async def _custom_hazard_mechanism_suggestion_step(
        self, session_id: str, session
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("resolved_hazard_text") or state.get("raw_text") or "").strip()
        evidence = str(
            state.get("evidence")
            or session.pending_hazard_evidence
            or session.accepted_custom_hazard_evidence
            or ""
        ).strip()
        mechanisms = await suggest_custom_hazard_mechanisms(
            hazard,
            session.sector or "",
            policy_objective_for_sector(session.sector or ""),
            evidence=evidence,
        )
        state["suggested_mechanisms"] = mechanisms
        if not mechanisms:
            state["message"] = "No sufficiently specific mechanism could be suggested."
            return self._custom_hazard_mechanism_input_step(
                session_id,
                session,
                detail=(
                    "I could not identify a sufficiently specific causal mechanism from "
                    "the hazard and objective. Please describe the process or change that "
                    "causes or worsens this hazard."
                ),
            )
        state["selected_mechanism"] = mechanisms[0]
        state["mechanism_source"] = "ai_suggestion"
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION)
        reflection = await self._custom_hazard_ai_reflection(
            session,
            hazard,
            str(state.get("reason") or state.get("objective_fit_reason") or "").strip(),
        )
        state["ai_reflection"] = reflection
        evidence_notice = str(state.pop("evidence_relationship_notice", "") or "").strip()
        return self._custom_hazard_mechanism_confirmation_step(
            session_id, session, evidence_notice=evidence_notice
        )

    def _custom_hazard_mechanism_confirmation_step(
        self, session_id: str, session, *, evidence_notice: str = ""
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        evidence_prefix = f"{evidence_notice}\n\n" if evidence_notice else ""
        objective = (state.get("dimension_scores") or {}).get("policy_objective_fit", {})
        objective_reason = (
            str(objective.get("reason") or "").strip()
            if isinstance(objective, dict)
            else ""
        )
        objective_score = objective.get("score") if isinstance(objective, dict) else None
        objective_prefix = (
            "## Sectoral Objective Fit\n\n"
            "**Supported**"
            + (f" (score: {objective_score}/10)" if objective_score is not None else "")
            + f"\n\n{objective_reason or 'The hazard is compatible with the selected policy objective.'}\n\n"
        )
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_mechanism_confirmation",
            bot_message=markdown_to_html(
                evidence_prefix
                + objective_prefix
                + "## AI reflection\n\n"
                f"{state.get('ai_reflection') or ''}\n\n"
                "Does this reflection accurately represent the information you provided?"
            ),
            options=CUSTOM_HAZARD_MECHANISM_CONFIRMATION_OPTIONS,
        )

    async def _custom_hazard_ai_reflection(
        self, session, hazard: str, reason: str
    ) -> str:
        """Summarize user-provided policy/hazard information without adding facts."""
        objective = policy_objective_for_sector(session.sector or "")
        response = await ask_llm_chat(
            context=(
                "You are a careful policy facilitator. Summarize only the information "
                "provided by the user and stated policy objective. Do not invent facts, "
                "evidence, statistics, laws, or affected groups. Write one concise reflective paragraph."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Selected policy objective: {objective}\n"
                    f"User hazard: {hazard}\n"
                    f"User explanation: {reason or 'No separate explanation provided.'}\n\n"
                    "Reflect on how the user's description frames a possible adverse consequence "
                    "of the objective."
                ),
            }],
            temperature=0.0,
            max_tokens=240,
        )
        if response.strip() and not is_llm_unavailable_response(response):
            return response.strip()
        return (
            f"You described **{hazard}** as a possible adverse consequence of pursuing "
            f"the objective **{objective}**. "
            f"{reason or 'Please confirm or refine the causal explanation in the next step.'}"
        )

    async def _handle_custom_hazard_mechanism_confirmation(
        self, session_id: str, session, message: str
    ) -> ChatResponse:
        label = exact_option_label(message, CUSTOM_HAZARD_MECHANISM_CONFIRMATION_OPTIONS)
        if label is None:
            label = match_option_label(message, CUSTOM_HAZARD_MECHANISM_CONFIRMATION_OPTIONS)
        action = normalize(label or message)
        if action == normalize("No, provide a mechanism") or action == normalize("No"):
            self._custom_hazard_state(session)["mechanism_input_exit_target"] = (
                "sectoral_objective_fit"
            )
            return self._custom_hazard_mechanism_input_step(session_id, session)
        if action != normalize("Yes"):
            return await self._custom_hazard_mechanism_suggestion_step(session_id, session)

        state = self._custom_hazard_state(session)
        hazard = str(state.get("resolved_hazard_text") or state.get("raw_text") or "").strip()
        mechanism = str(state.get("selected_mechanism") or "").strip()
        query = (
            f"{hazard} {mechanism} policy regulation provision requirement programme "
            f"{session.sector or ''} {session.country or ''}"
        )
        # Source priority is deliberate: a policy reference is the most direct
        # authority for the mechanism; main KB and validated evidence are fallbacks.
        document_ids = [str(value) for value in state.get("policy_reference_document_ids") or []]
        policy_context = str(state.get("policy_reference_context") or "").strip()
        if not policy_context and document_ids:
            policy_context = await self._policy_reference_context(session, document_ids, query=query)
        if not policy_context and session.selected_context_policy_id:
            policy_context = self._context_policy_document_context(
                session, self._stored_context_policy_document_ids(session)
            )
        if policy_context:
            policy = await summarize_custom_hazard_supporting_policy(hazard, mechanism, policy_context)
            if policy.get("supported") and policy.get("causal_linkage"):
                return self._accept_mechanism_policy_source(
                    session_id, session, policy, "policy_reference", [], policy_context
                )

        source_queries = [
            ("main_knowledge_base", KnowledgeBaseService(self.db, None, scope=MAIN_KB_SCOPE), 8),
            (
                "validated_evidence",
                KnowledgeBaseService(
                    self.db, None, scope=VALIDATED_EVIDENCE_SCOPE,
                    country_id=session.country_id, region_id=session.region_id, sector_id=session.sector_id,
                ),
                6,
            ),
        ]
        for source_name, service, limit in source_queries:
            try:
                results = await service.search(query, limit=limit)
            except Exception:
                results = []
            grounded = await self.grounding_models.ground_results(query, results)
            if not grounded:
                continue
            context = self._format_knowledge_results(grounded)
            policy = await summarize_custom_hazard_supporting_policy(hazard, mechanism, context)
            if policy.get("supported") and policy.get("causal_linkage"):
                return self._accept_mechanism_policy_source(
                    session_id, session, policy, source_name, grounded, context
                )
        state["message"] = "No policy, main knowledge-base, or validated-evidence source supported the mechanism."
        return self._custom_hazard_policy_reference_step(
            session_id, session,
            detail="Please provide a supporting policy URL or file for the confirmed mechanism.",
        )

    def _accept_mechanism_policy_source(
        self, session_id, session, policy: dict, source: str,
        grounded: list[dict], context: str,
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        state["mechanism_knowledge_context"] = context
        state["supporting_policy_summary"] = str(policy.get("summary") or "").strip()
        state["supporting_policy_details"] = str(policy.get("policy_details") or "").strip()
        state["supporting_policy_sources"] = [
            {"title": str(item.get("title") or "Knowledge source"), "source_uri": str(item.get("source_uri") or ""), "page_number": item.get("page_number")}
            for item in grounded[:6]
        ]
        state["pending_policy_linkage"] = policy
        state["mechanism_source"] = source
        # The policy source has already been checked. Do not ask users to
        # confirm it again; proceed directly to the causal-linkage review.
        return self._custom_hazard_causal_linkage_step(session_id, session, policy)

    def _custom_hazard_policy_details_confirmation_step(
        self, session_id: str, session
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        transition_custom_hazard(
            session, ChatPhase.CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION
        )
        source_lines = []
        for source in state.get("supporting_policy_sources") or []:
            if not isinstance(source, dict):
                continue
            title = str(source.get("title") or "Knowledge source").strip()
            page = source.get("page_number")
            source_lines.append(f"- {title}" + (f", page {page}" if page else ""))
        sources = "\n".join(source_lines[:6])
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_policy_details_confirmation",
            bot_message=markdown_to_html(
                "## Supporting policy details\n\n"
                "As I understand it, this is the policy supporting the suggested mechanism.\n\n"
                f"**Policy summary:** {state.get('supporting_policy_summary') or 'A relevant supporting policy was found.'}\n\n"
                f"**Relevant policy details:** {state.get('supporting_policy_details') or state.get('supporting_policy_summary')}\n\n"
                + (f"**Sources:**\n{sources}\n\n" if sources else "")
                + "Do you confirm that this is the appropriate supporting policy?"
            ),
            options=CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION_OPTIONS,
        )

    async def _handle_custom_hazard_policy_details_confirmation(
        self, session_id: str, session, message: str
    ) -> ChatResponse:
        label = exact_option_label(
            message, CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION_OPTIONS
        )
        if label is None:
            label = match_option_label(
                message, CUSTOM_HAZARD_POLICY_DETAILS_CONFIRMATION_OPTIONS
            )
        action = normalize(label or message)
        if action in {
            normalize("Provide a different policy"),
            normalize("No"),
        }:
            return self._custom_hazard_policy_reference_step(
                session_id,
                session,
                detail="Please provide the policy that supports the confirmed mechanism.",
            )
        if action not in {normalize("Confirm policy"), normalize("Yes")}:
            return self._custom_hazard_policy_details_confirmation_step(
                session_id, session
            )
        state = self._custom_hazard_state(session)
        linkage = state.get("pending_policy_linkage")
        if not isinstance(linkage, dict) or not linkage.get("causal_linkage"):
            return self._custom_hazard_policy_reference_step(
                session_id,
                session,
                error=True,
                detail="The policy linkage is no longer available. Please provide the policy again.",
            )
        state["policy_reference_available"] = True
        return self._custom_hazard_causal_linkage_step(session_id, session, linkage)

    def _custom_hazard_mechanism_input_step(
        self, session_id: str, session, *, detail: str = ""
    ) -> ChatResponse:
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_MECHANISM_INPUT)
        message = (
            "## Provide the mechanism\n\n"
            "Describe the specific process, intervention, rule, technology, or market change "
            "that causes or worsens the hazard."
        )
        if detail:
            message = f"{detail}\n\n{message}"
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_mechanism_input",
            bot_message=markdown_to_html(message),
            options=CUSTOM_HAZARD_MECHANISM_INPUT_OPTIONS,
            input_mode="textarea",
        )

    async def _handle_custom_hazard_mechanism_input(
        self, session_id: str, session, message: str
    ) -> ChatResponse:
        if normalize(message) == normalize("Exit from this step"):
            state = self._custom_hazard_state(session)
            if state.get("mechanism_input_exit_target") == "sectoral_objective_fit":
                transition_custom_hazard(
                    session, ChatPhase.CUSTOM_HAZARD_MECHANISM_CONFIRMATION
                )
                return self._custom_hazard_mechanism_confirmation_step(session_id, session)
            return self._custom_hazard_causal_linkage_step(
                session_id,
                session,
                {"causal_linkage": state.get("mechanism_causal_linkage") or ""},
            )
        if normalize(message) == normalize("Go back to list of hazards"):
            self._discard_temporary_policy_references(session)
            session.custom_hazard = None
            transition_custom_hazard(session, ChatPhase.HAZARDS)
            return self._hazards_step(session_id, session)
        state = self._custom_hazard_state(session)
        hazard = str(state.get("resolved_hazard_text") or state.get("raw_text") or "").strip()
        mechanism = re.sub(r"\s+", " ", str(message or "")).strip()
        clarity = await assess_custom_hazard_mechanism_clarity(hazard, mechanism)
        if not clarity.get("clear"):
            return self._custom_hazard_mechanism_input_step(
                session_id,
                session,
                detail=str(clarity.get("reason") or "Please clarify the mechanism."),
            )
        existing_source = str(state.get("mechanism_source") or "")
        knowledge_context = str(state.get("mechanism_knowledge_context") or "").strip()
        state["selected_mechanism"] = mechanism
        state["mechanism_source"] = "user"
        state["mechanism_confirmed"] = False
        state["causal_linkage_confirmed"] = False
        if state.get("policy_reference_available"):
            return await self._validate_current_custom_hazard_mechanism_source(
                session_id, session
            )
        if existing_source == "knowledge_base" and knowledge_context:
            linkage = await validate_custom_hazard_mechanism_linkage(
                hazard, mechanism, knowledge_context, "knowledge-base"
            )
            if linkage.get("supported") and linkage.get("causal_linkage"):
                state["mechanism_source"] = "knowledge_base"
                return self._custom_hazard_causal_linkage_step(session_id, session, linkage)
        return self._custom_hazard_policy_reference_step(
            session_id,
            session,
            detail="A policy source is needed to validate the mechanism you provided.",
        )

    async def _validate_current_custom_hazard_mechanism_source(
        self, session_id: str, session
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        hazard = str(state.get("resolved_hazard_text") or state.get("raw_text") or "").strip()
        mechanism = str(state.get("selected_mechanism") or "").strip()
        document_ids = [
            str(value)
            for value in (
                state.get("pending_policy_reference_document_ids")
                or state.get("policy_reference_document_ids")
                or []
            )
        ]
        policy_context = str(state.get("pending_policy_reference_context") or "").strip()
        if not policy_context:
            policy_context = await self._policy_reference_context(
                session,
                document_ids,
                query=f"{hazard} {mechanism} {session.sector or ''} {session.country or ''} {session.region or ''}",
            )
        state["policy_reference_context"] = policy_context
        policy = await summarize_custom_hazard_supporting_policy(
            hazard, mechanism, policy_context
        )
        if not policy.get("supported") or not policy.get("causal_linkage"):
            state["policy_reference_relevance_pending"] = True
            state["message"] = str(
                policy.get("reason") or "The policy does not support the mechanism."
            )
            return self._custom_hazard_policy_reference_step(
                session_id,
                session,
                error=True,
                detail=(
                    f"The provided policy does not clearly support the mechanism **{mechanism}**. "
                    f"{policy.get('reason') or 'The relevant provision or causal connection is missing.'}"
                ),
                retry=True,
            )
        state["supporting_policy_summary"] = str(policy.get("summary") or "").strip()
        state["supporting_policy_details"] = str(policy.get("policy_details") or "").strip()
        state["policy_reference_context"] = policy_context
        state["policy_reference_relevance_pending"] = False
        state["mechanism_source"] = "policy"
        self._promote_pending_custom_hazard_policy_reference(session, state)
        state["policy_summary_notice"] = (
            "## Supporting policy accepted\n\n"
            "The policy is relevant to the confirmed mechanism.\n\n"
            f"**Policy summary:** {state.get('supporting_policy_summary') or policy.get('reason')}\n\n"
            f"**Relevant policy details:** {state.get('supporting_policy_details') or state.get('supporting_policy_summary')}"
        )
        return self._custom_hazard_causal_linkage_step(session_id, session, policy)

    def _promote_pending_custom_hazard_policy_reference(
        self, session, state: dict[str, object]
    ) -> None:
        pending_ids = [
            str(value).strip()
            for value in state.get("pending_policy_reference_document_ids") or []
            if str(value).strip()
        ]
        if not pending_ids:
            state["policy_reference_available"] = bool(
                state.get("policy_reference_document_ids")
            )
            return
        old_ids = [
            str(value).strip()
            for value in state.get("policy_reference_document_ids") or []
            if str(value).strip() and str(value).strip() not in pending_ids
        ]
        if state.get("replacing_policy_reference") and old_ids:
            retained_pending = list(state.get("pending_policy_reference_document_ids") or [])
            state["policy_reference_document_ids"] = old_ids
            self._discard_temporary_policy_references(session)
            state["pending_policy_reference_document_ids"] = retained_pending
            self._clear_replaced_policy_reference_validation(session, state)
        state["policy_reference"] = str(
            state.get("pending_policy_reference") or "Provided policy document"
        ).strip()
        state["policy_reference_document_ids"] = pending_ids
        state["policy_reference_context"] = str(
            state.get("pending_policy_reference_context") or ""
        ).strip()
        state["pending_policy_reference"] = ""
        state["pending_policy_reference_document_ids"] = []
        state["pending_policy_reference_context"] = ""
        state["policy_reference_available"] = True
        state["replacing_policy_reference"] = False

    def _custom_hazard_policy_relevance_clarification_step(
        self, session_id: str, session
    ) -> ChatResponse:
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CLARIFICATION)
        state = self._custom_hazard_state(session)
        state["awaiting_policy_relevance_clarification"] = True
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_policy_relevance_clarification",
            bot_message=markdown_to_html(
                "## Clarify policy relevance\n\n"
                f"Explain which provision in the supplied policy supports the mechanism "
                f"**{state.get('selected_mechanism') or 'under review'}**, and how it does so. "
                "Your explanation will be checked against the policy text before it is accepted."
            ),
            options=HAZARD_ENTRY_OPTIONS,
            input_mode="textarea",
        )

    def _custom_hazard_causal_linkage_step(
        self, session_id: str, session, linkage: dict[str, object]
    ) -> ChatResponse:
        state = self._custom_hazard_state(session)
        causal_linkage = str(linkage.get("causal_linkage") or "").strip()
        state["mechanism_causal_linkage"] = causal_linkage
        state["mechanism_confirmed"] = True
        state["causal_linkage_confirmed"] = False
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_CAUSAL_LINKAGE_CONFIRMATION)
        policy_notice = str(state.pop("policy_summary_notice", "") or "").strip()
        policy_prefix = f"{policy_notice}\n\n" if policy_notice else ""
        return self._custom_hazard_response(
            session_id=session_id,
            session=session,
            step="custom_hazard_causal_linkage_confirmation",
            bot_message=markdown_to_html(
                policy_prefix
                + "## Possible factors contributing to the hazard\n\n"
                f"**Mechanism:** {state.get('selected_mechanism')}\n\n"
                f"{self._short_linkage_bullets(causal_linkage)}\n\n"
                "Is the described mechanism appropriately connected to the hazard?"
            ),
            options=CUSTOM_HAZARD_CAUSAL_LINKAGE_OPTIONS,
        )

    async def _handle_custom_hazard_causal_linkage_confirmation(
        self, session_id: str, session, message: str
    ) -> ChatResponse:
        label = exact_option_label(message, CUSTOM_HAZARD_CAUSAL_LINKAGE_OPTIONS)
        if label is None:
            label = match_option_label(message, CUSTOM_HAZARD_CAUSAL_LINKAGE_OPTIONS)
        action = normalize(label or message)
        if action == normalize("No, revise the linkage") or action == normalize("No"):
            self._custom_hazard_state(session)["mechanism_input_exit_target"] = (
                "causal_linkage"
            )
            return self._custom_hazard_mechanism_input_step(
                session_id,
                session,
                detail=(
                    "Please revise the mechanism or explain the causal relationship "
                    "that should be checked."
                ),
            )
        if action != normalize("Yes"):
            state = self._custom_hazard_state(session)
            return self._custom_hazard_causal_linkage_step(
                session_id,
                session,
                {"causal_linkage": state.get("mechanism_causal_linkage") or ""},
            )
        state = self._custom_hazard_state(session)
        state["causal_linkage_confirmed"] = True
        state["mechanism_confirmed"] = True
        existing_reason = str(
            state.get("objective_fit_reason") or state.get("reason") or ""
        ).split(" Mechanism:", 1)[0].strip()
        mechanism_reason = (
            f"Mechanism: {state.get('selected_mechanism')}. "
            f"Causal linkage: {state.get('mechanism_causal_linkage')}."
        )
        state["reason"] = " ".join(
            part for part in (existing_reason, mechanism_reason) if part
        ).strip()
        session.pending_hazard_reason = str(state["reason"])
        dimensions = state.setdefault("dimension_scores", {})
        dimensions["mechanism_fit"] = {
            "score": max(8, custom_hazard_dimension_floor(state.get("validation_mode"))),
            "reason": "The user confirmed a source-supported causal mechanism and linkage.",
            "causal_linkage": state.get("mechanism_causal_linkage") or "",
            "confidence": "high",
            "needs_clarification": False,
            "clarification_question": "",
            "status": "SUPPORTED",
        }
        state["active_validation_dimension"] = "hazard_definition_fit"
        state["active_validation_dimensions"] = ["hazard_definition_fit"]
        state["next_action"] = CustomHazardAction.VALIDATE.value
        state["status"] = CustomHazardStatus.DRAFT.value
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_DIMENSION_CHECK)
        return await self._run_custom_hazard_dimension_check(session_id, session)
