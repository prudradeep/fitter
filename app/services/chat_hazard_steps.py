import re
from difflib import SequenceMatcher

from sqlalchemy import and_, or_, select

from app.models import (
    AdditionalHazard,
    KnowledgeChunk,
    KnowledgeDocument,
    MitigationMeasurePolicy,
    MitigationMeasurePolicyAdditionalHazard,
    MitigationMeasurePolicySystemHazard,
    Policy,
    SystemHazard,
)
from app.schemas import ChatResponse, Option
from app.llm import ask_llm_chat
from app.services.chat_formatters import format_hazards
from app.services.chat_json import parse_json_object
from app.services.chat_options import (
    HAZARD_ENTRY_OPTIONS,
    POST_SECTOR_OPTIONS,
    REGIONAL_POPULATION_COMPARISON_LABEL,
    SOCIO_DEMOGRAPHIC_OPTIONS,
    STATS_DEEP_DIVE_OPTIONS,
    best_fuzzy_label,
    exact_option_label,
    match_option_label,
    normalize,
    normalize_for_match,
)
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    default_custom_hazard_state,
    validate_policy_reference_twin_transition,
)
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.enums import ChatPhase
from app.services.hazard_salience import survey_respondent_count
from app.services.knowledge_base import MAIN_KB_SCOPE
from app.services.message_renderer import markdown_to_html, render_message


def is_hazard_action_label(label: str) -> bool:
    return normalize_for_match(label) in {
        "show hazards added by experts",
        "show co created hazards",
        "show listed hazards",
    }


class ChatHazardStepsMixin:
    def _policy_rows_for_selected_context(
        self, session: ChatSession
    ) -> list[tuple[str, str]]:
        """Return policy IDs and titles scoped to the selected country and sector."""
        if not (hasattr(self, "db") and session.country_id and session.sector_id):
            return []

        rows = self.db.execute(
            select(Policy.id, Policy.policy)
            .where(
                Policy.country_id == session.country_id,
                Policy.sector_id == session.sector_id,
            )
            .order_by(Policy.policy, Policy.id)
        ).all()
        policies: list[tuple[str, str]] = []
        seen: set[str] = set()
        for policy_id, title_value in rows:
            title = str(title_value or "").strip()
            key = " ".join(title.casefold().split())
            if title and key not in seen:
                seen.add(key)
                policies.append((str(policy_id), title))
        return policies

    def _policies_for_selected_context(self, session: ChatSession) -> list[str]:
        """Return the reference policies available for the current policy context."""
        return [title for _, title in self._policy_rows_for_selected_context(session)]

    def _policy_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        policies = self._policy_rows_for_selected_context(session)
        session.phase = "policy"
        if not policies:
            return ChatResponse(
                session_id=session_id,
                step="policy",
                bot_message=(
                    f"No policies are available for **{session.country}**, "
                    f"**{session.region}**, and **{session.sector}**."
                ),
                session=session.summary(),
                error=False,
            )
        policy_document_ids = {
            str(policy_id)
            for policy_id in self.db.scalars(
                select(KnowledgeDocument.policy_id).where(
                    KnowledgeDocument.policy_id.in_(
                        [policy_id for policy_id, _ in policies]
                    ),
                    KnowledgeDocument.scope == MAIN_KB_SCOPE,
                )
            ).all()
            if policy_id
        }
        return ChatResponse(
            session_id=session_id,
            step="policy",
            bot_message=render_message(
                "policy_selection.md",
                country=session.country,
                region=session.region,
                sector=session.sector,
                policies=[
                    {
                        "title": title,
                        "description": "",
                        "document_available": policy_id in policy_document_ids,
                    }
                    for policy_id, title in policies
                ],
            ),
            options=[
                Option(id=index, label=title)
                for index, (_, title) in enumerate(policies, start=1)
            ],
            session=session.summary(),
            error=False,
        )

    async def _select_context_policy(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        policies = self._policy_rows_for_selected_context(session)
        matched: tuple[str, str] | None = None
        cleaned = normalize_for_match(message)
        selected_number = re.search(r"\b(\d+)\b", str(message or ""))
        if selected_number:
            index = int(selected_number.group(1))
            if 1 <= index <= len(policies):
                matched = policies[index - 1]
        for index, policy in enumerate(policies, start=1):
            title = normalize_for_match(policy[1])
            if matched is None and (
                message.strip() == str(index)
                or normalize(message) == normalize(policy[1])
                or (cleaned and (cleaned in title or title in cleaned))
            ):
                matched = policy
                break
        if matched is None and cleaned:
            fuzzy_title = best_fuzzy_label(cleaned, [title for _, title in policies], threshold=0.35)
            if fuzzy_title:
                matched = next(policy for policy in policies if policy[1] == fuzzy_title)
        if matched is None:
            return self._policy_step(session_id, session)
        session.selected_context_policy_id, session.selected_context_policy = matched
        if session.pending_mitigation_policy_selection:
            session.pending_mitigation_policy_selection = False
            session.selected_mitigation_policy = session.selected_context_policy
            return await self._create_mitigation_measure_step(session_id, session)
        return await self._context_policy_details_step(session_id, session)

    def _context_policy_document_context(
        self, session: ChatSession, document_ids: list[str]
    ) -> str:
        if not document_ids:
            return ""
        rows = self.db.scalars(
            select(KnowledgeChunk.content)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.id.in_(document_ids))
            .order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index)
        ).all()
        return "\n\n".join(str(row).strip() for row in rows if str(row).strip())[:24000]

    def _stored_context_policy_document_ids(self, session: ChatSession) -> list[str]:
        if not session.selected_context_policy_id:
            return []
        return list(self.db.scalars(
            select(KnowledgeDocument.id).where(
                or_(
                    and_(
                        KnowledgeDocument.policy_id == session.selected_context_policy_id,
                        KnowledgeDocument.scope == MAIN_KB_SCOPE,
                    ),
                    and_(
                        KnowledgeDocument.user_id == self.user_id,
                        KnowledgeDocument.scope == "policy_reference",
                        KnowledgeDocument.title == f"Policy: {session.selected_context_policy}",
                        KnowledgeDocument.country_id == session.country_id,
                        KnowledgeDocument.sector_id == session.sector_id,
                    ),
                )
            )
        ).all())

    async def _context_policy_details_step(
        self, session_id: str, session: ChatSession, document_ids: list[str] | None = None
    ) -> ChatResponse:
        policy = self.db.get(Policy, session.selected_context_policy_id)
        document_context = self._context_policy_document_context(
            session, document_ids or self._stored_context_policy_document_ids(session)
        )
        details = str(policy.policy or "").strip() if policy else ""
        if not details and not document_context:
            session.phase = "policy_reference"
            return ChatResponse(
                session_id=session_id,
                step="policy_reference",
                bot_message=markdown_to_html(
                    "## Policy details needed\n\n"
                    "No policy details or reusable document were found for this policy. "
                    "Please provide the policy URL or attach a PDF, DOCX, MD, or TXT file."
                ),
                session=session.summary(),
                input_mode="policy_reference",
                error=False,
            )

        source_text = document_context or details
        summary = await self._summarize_context_policy(session, source_text, details)
        session.selected_context_policy_summary = summary
        session.phase = "policy_summary"
        return ChatResponse(
            session_id=session_id,
            step="policy_summary",
            bot_message=markdown_to_html(
                f"## {session.selected_context_policy}\n\n{summary}\n\n"
                "If you would like to know about the hazards related to the policy or create mitigation measures for the hazards, "
                "Choose **Continue to hazards** when you are ready."
            ),
            options=[Option(id=1, label="Continue to hazards")],
            session=session.summary(),
            error=False,
        )

    async def _summarize_context_policy(
        self, session: ChatSession, source_text: str, catalog_details: str
    ) -> str:
        prompt = (
            "Summarize this policy using only the supplied text. Use concise headings: "
            "Policy details, Mechanisms, and Intended benefits. State when a detail is not available.\n\n"
            f"Policy: {session.selected_context_policy}\nContext: {session.country}, {session.region}, {session.sector}\n\n"
            f"Source text:\n{source_text[:12000]}"
        )
        response = await ask_llm_chat(
            context="You are a careful policy analyst. Do not invent policy details.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=450,
        )
        if not is_llm_unavailable_response(response) and response.strip():
            return response.strip()
        return (
            f"**Policy details:** {catalog_details or source_text[:1200]}\n\n"
            "**Mechanisms:** Not separately stated in the available policy material.\n\n"
            "**Intended benefits:** Not separately stated in the available policy material."
        )

    async def _handle_context_policy_reference(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        document_ids = re.findall(
            r"^Policy reference document ID:\s*(\S+)", message, re.IGNORECASE | re.MULTILINE
        )
        if not document_ids:
            return await self._context_policy_details_step(session_id, session)
        context = await self._policy_reference_context(session, document_ids, query=session.selected_context_policy or "")
        verification = await validate_policy_reference_twin_transition(context)
        if not verification or not verification.get("related"):
            session.phase = "policy_reference"
            return ChatResponse(
                session_id=session_id, step="policy_reference",
                bot_message=markdown_to_html(
                    "## Policy document could not be verified\n\n"
                    f"{(verification or {}).get('reason') or 'Please provide a relevant policy document.'}"
                ), session=session.summary(), input_mode="policy_reference", error=True,
            )
        documents = self.db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids))).all()
        for document in documents:
            document.scope = "policy_reference"
            document.title = f"Policy: {session.selected_context_policy}"
            document.country_id = session.country_id
            document.region_id = session.region_id
            document.sector_id = session.sector_id
        self.db.commit()
        return await self._context_policy_details_step(session_id, session, document_ids)

    def _limit_hazards_to_selected_policy(self, session: ChatSession) -> None:
        """Apply the selected reference policy's hazard-listing rule."""
        if not (hasattr(self, "db") and session.selected_context_policy_id):
            return
        policy = self.db.get(Policy, session.selected_context_policy_id)
        if policy is None:
            return
        policy_type = normalize_for_match(policy.policy_type or "")
        if policy_type == normalize_for_match("Survey policy case study"):
            self._set_all_system_hazards_for_selected_sector(session)
            session.additional_hazards = []
            return
        if policy_type != normalize_for_match("Adjustment to existing policy"):
            return

        mitigation_policy = self._matching_mitigation_measure_policy(policy)
        if mitigation_policy is None:
            session.hazards = []
            session.additional_hazards = []
            session.custom_hazards = []
            return
        system_hazard_names = {
            normalize(name)
            for name in self.db.scalars(
                select(SystemHazard.name)
                .join(
                    MitigationMeasurePolicySystemHazard,
                    MitigationMeasurePolicySystemHazard.system_hazard_id == SystemHazard.id,
                )
                .where(
                    MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id
                    == mitigation_policy.id
                )
            ).all()
        }
        additional_hazard_names = {
            normalize(name)
            for name in self.db.scalars(
                select(AdditionalHazard.name)
                .join(
                    MitigationMeasurePolicyAdditionalHazard,
                    MitigationMeasurePolicyAdditionalHazard.additional_hazard_id
                    == AdditionalHazard.id,
                )
                .where(
                    MitigationMeasurePolicyAdditionalHazard.mitigation_measure_policy_id
                    == mitigation_policy.id
                )
            ).all()
        }
        session.hazards = [
            hazard for hazard in (session.hazards or []) if normalize(hazard) in system_hazard_names
        ]
        session.additional_hazards = [
            hazard
            for hazard in (session.additional_hazards or [])
            if normalize(hazard) in additional_hazard_names
        ]
        session.custom_hazards = []

    def _set_all_system_hazards_for_selected_sector(self, session: ChatSession) -> None:
        """Restore the sector catalogue for survey case-study policies."""
        items = self._stored_hazard_items_for_context(session.session_key, session)
        session.hazards = [str(item["hazard"]) for item in items]
        existing_profiles = session.hazard_profiles or {}
        session.hazard_profiles = {
            str(item["hazard"]): list(
                existing_profiles.get(str(item["hazard"])) or item.get("profiles") or []
            )
            for item in items
        }

    async def _enrich_policy_hazards_with_population_context(
        self, session: ChatSession
    ) -> None:
        """Rank policy-selected system hazards without hiding unranked catalogue items."""
        if not session.selected_context_policy_id or not session.hazards:
            return
        policy = self.db.get(Policy, session.selected_context_policy_id)
        if policy is None or normalize_for_match(policy.policy_type or "") not in {
            normalize_for_match("Survey policy case study"),
            normalize_for_match("Adjustment to existing policy"),
        }:
            return

        listed_hazards = list(session.hazards)
        listed_profiles = dict(session.hazard_profiles or {})
        await self._rank_session_hazards(session)
        enriched_profiles = dict(session.hazard_profiles or {})
        session.hazards = listed_hazards
        session.hazard_profiles = {
            hazard: enriched_profiles.get(hazard, listed_profiles.get(hazard, []))
            for hazard in listed_hazards
        }

    def _matching_mitigation_measure_policy(
        self, policy: Policy
    ) -> MitigationMeasurePolicy | None:
        """Return one unambiguous same-context mitigation policy title match."""
        selected_key = normalize_for_match(policy.policy or "")
        if not selected_key:
            return None
        candidates = self.db.scalars(
            select(MitigationMeasurePolicy).where(
                MitigationMeasurePolicy.sector_id == policy.sector_id,
                or_(
                    MitigationMeasurePolicy.country_id == policy.country_id,
                    MitigationMeasurePolicy.country_id.is_(None),
                ),
            )
        ).all()
        scored: list[tuple[float, MitigationMeasurePolicy]] = []
        selected_tokens = set(selected_key.split())
        for candidate in candidates:
            candidate_key = normalize_for_match(candidate.policy_title or "")
            if not candidate_key:
                continue
            if candidate_key == selected_key:
                score = 1.0
            elif candidate_key in selected_key or selected_key in candidate_key:
                score = 0.9
            else:
                overlap = len(selected_tokens & set(candidate_key.split())) / max(
                    len(selected_tokens | set(candidate_key.split())), 1
                )
                score = max(overlap, SequenceMatcher(None, selected_key, candidate_key).ratio())
            if score >= 0.58:
                scored.append((score, candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].id))
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
            return None
        return scored[0][1]

    async def _hazards_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        if (
            (
                session.custom_hazard_evidence is None
                or session.custom_hazard_summaries is None
            )
            and session.country_id is not None
            and session.sector_id is not None
            and hasattr(self, "db")
        ):
            session.custom_hazards = self._saved_custom_hazards_for_context(session)
        self._hydrate_custom_hazard_profiles(session)
        self._limit_hazards_to_selected_policy(session)
        self._filter_session_hazards_without_profiles(session)
        await self._enrich_policy_hazards_with_population_context(session)
        session.phase = "hazards"
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message(
                "hazards_overview.md",
                country=session.country,
                region=session.region,
                sector=session.sector,
                selected_policy=session.selected_context_policy,
                policies=self._policies_for_selected_context(session),
                survey_count=survey_respondent_count(sector=session.sector or ""),
                has_linked_hazards=bool(session.hazards or session.additional_hazards),
                hazards=format_hazards(
                    session,
                    show_admin_details=bool(getattr(self, "is_admin", False)),
                ),
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_hazards_action(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        if (
            normalize(message) == normalize(REGIONAL_POPULATION_COMPARISON_LABEL)
            and session.accepted_custom_hazard
        ):
            return self._custom_hazard_population_region_comparison_step(
                session_id,
                session,
            )
        open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
        if open_selection_handler is not None:
            open_selection_response = await open_selection_handler(
                session_id,
                session,
                message,
                current_phase="sector",
            )
            if open_selection_response is not None:
                return open_selection_response
        else:
            navigation_handler = getattr(self, "_open_selection_navigation_response", None)
            if navigation_handler is not None:
                navigation_response = await navigation_handler(
                    session_id,
                    session,
                    message,
                    "sector",
                )
                if navigation_response is not None:
                    return navigation_response

            selection = self._post_sector_selection_from_open_text(session, message)
            if selection is not None:
                apply_selection = getattr(self, "_apply_pending_selection", None)
                if apply_selection is not None:
                    return await apply_selection(session_id, session, selection)

        exact_label = exact_option_label(message, POST_SECTOR_OPTIONS)
        if exact_label is None:
            question_handler = getattr(self, "_handle_anytime_grounded_question", None)
            if question_handler is not None:
                question_response = await question_handler(session_id, session, message)
                if question_response is not None:
                    return question_response
            exact_label = self._post_sector_label_from_open_text(message)
        if exact_label is None:
            exact_label = await self._post_sector_label_from_llm(session, message)
        if exact_label is None:
            fuzzy_label = match_option_label(message, POST_SECTOR_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            return self._custom_hazard_input_step(session_id, session)

        if action == normalize("Refresh hazards and DGs"):
            await self._refresh_session_hazards(session_id, session)
            return await self._hazards_step(session_id, session)

        if action == normalize("Dive deeper into statistical findings"):
            return self._stats_deep_dive_dialog_step(session_id, session)

        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=(
                "Your country, region, and sector are already selected. "
                "Please choose one of the available actions."
            ),
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _custom_hazard_input_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        self._discard_temporary_policy_references(session)
        self._clear_selected_hazard_context(session)
        transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_INPUT)
        session.custom_hazard = default_custom_hazard_state()
        session.custom_hazard_input_history = []
        session.generated_custom_hazard_title = None
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=render_message("add_hazard.md", selected_policy=session.selected_context_policy),
            options=HAZARD_ENTRY_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=False,
        )

    async def _refresh_session_hazards(
        self, session_id: str, session: ChatSession
    ) -> None:
        hazard_items = await self._refresh_hazards_and_profiles_from_llm(
            session_id,
            session,
            replace_sector_hazards=True,
        )
        session.hazards = [str(item["hazard"]) for item in hazard_items]
        session.hazard_profiles = {
            str(item["hazard"]): [
                profile
                for profile in item.get("profiles", [])
                if (
                    isinstance(profile, dict)
                    and str(profile.get("name") or "").strip()
                )
                or (isinstance(profile, str) and profile.strip())
            ]
            for item in hazard_items
            if item.get("profiles")
        }
        session.custom_hazards = self._saved_custom_hazards_for_context(session)
        session.additional_hazards = self._additional_hazards_for_context(session)
        self._hydrate_custom_hazard_profiles(session)
        self._filter_session_hazards_without_profiles(session)
        await self._rank_session_hazards(session)
        cache_store = getattr(self, "_store_hazard_listing_cache", None)
        if cache_store is not None:
            cache_store(session)
        self._record_activity(
            session_id,
            session,
            "hazards_refreshed",
            session.sector or "",
        )

    def _stats_deep_dive_dialog_step(
        self,
        session_id: str,
        session: ChatSession,
        initial_question: str | None = None,
    ) -> ChatResponse:
        session.phase = "stats_deep_dive"
        return ChatResponse(
            session_id=session_id,
            step="stats_deep_dive_dialog",
            bot_message="",
            options=POST_SECTOR_OPTIONS,
            session=session.summary(),
            input_values={"stats_question": str(initial_question or "").strip()},
            error=False,
        )

    async def _handle_stats_deep_dive(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, STATS_DEEP_DIVE_OPTIONS)
        if exact_label is None:
            exact_label = self._post_sector_label_from_open_text(message)
        if exact_label is None:
            exact_label = await self._post_sector_label_from_llm(session, message)
        if exact_label is None:
            fuzzy_label = match_option_label(message, STATS_DEEP_DIVE_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Start Mitigation Planning"):
            return self._hazard_profile_step(session_id, session)

        if action == normalize("Add a new Hazard"):
            return self._custom_hazard_input_step(session_id, session)

        if action == normalize("Refresh hazards and DGs"):
            await self._refresh_session_hazards(session_id, session)
            return await self._hazards_step(session_id, session)

        if not message:
            return ChatResponse(
                session_id=session_id,
                step="stats_deep_dive",
                bot_message=await self._sector_briefing(session),
                options=STATS_DEEP_DIVE_OPTIONS,
                session=session.summary(),
                error=False,
            )

        return await self._stats_deep_dive(session_id, session, message)

    def _post_sector_label_from_open_text(self, message: str) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        if normalized == "other options":
            return None
        if self._looks_like_post_sector_question(message):
            return None
        if normalized in {
            "next",
            "next step",
            "continue",
            "can we continue",
            "continue flow",
            "continue the flow",
            "go ahead",
            "proceed",
            "move forward",
            "start mitigation",
            "start mitigation planning",
            "create mitigation",
            "create a mitigation",
            "create mitigation measure",
            "create a mitigation measure",
            "start creating mitigation",
            "make mitigation measure",
            "new mitigation measure",
        }:
            return "Start Mitigation Planning"
        if "mitigation" in normalized and any(
            token in normalized
            for token in (
                "start",
                "create",
                "make",
                "build",
                "develop",
                "plan",
                "prepare",
            )
        ):
            return "Start Mitigation Planning"
        if normalized in {
            "add hazard",
            "add a hazard",
            "add new hazard",
            "add a new hazard",
            "create hazard",
            "create a hazard",
            "create a new hazard",
            "new hazard",
            "start a new hazard",
        }:
            return "Add a new Hazard"
        if (
            "hazard" in normalized
            and any(
                phrase in normalized
                for phrase in (
                    "dont make sense",
                    "do not make sense",
                    "doesnt make sense",
                    "does not make sense",
                    "not make sense",
                    "none fit",
                    "none of these fit",
                    "none of them fit",
                    "not fit",
                    "does not fit",
                    "dont fit",
                    "do not fit",
                    "missing",
                    "not listed",
                    "not shown",
                )
            )
        ):
            return "Add a new Hazard"
        if any(
            phrase in normalized
            for phrase in (
                "want to add one",
                "add one",
                "add my own",
                "create my own",
                "write my own hazard",
                "custom hazard",
                "own hazard",
                "another hazard",
                "different hazard",
                "new risk",
                "missing risk",
            )
        ):
            return "Add a new Hazard"
        if "hazard" in normalized and any(
            token in normalized
            for token in ("add", "create", "new")
        ):
            return "Add a new Hazard"
        if normalized in {
            "refresh",
            "refresh hazards",
            "refresh dgs",
            "refresh hazards and dgs",
            "reload hazards",
            "regenerate hazards",
            "update hazards",
        }:
            return "Refresh hazards and DGs"
        if "hazard" in normalized and any(
            token in normalized
            for token in ("refresh", "reload", "regenerate", "update")
        ):
            return "Refresh hazards and DGs"

        ordinal_parser = getattr(self, "_ordinal_index_from_text", None)
        if ordinal_parser is None:
            return None
        ordinal = ordinal_parser(message)
        if ordinal is None:
            return None
        labels = [option.label for option in POST_SECTOR_OPTIONS]
        index = ordinal if ordinal >= 0 else len(labels) + ordinal
        if index < 0 or index >= len(labels):
            return None
        return labels[index]

    @staticmethod
    def _looks_like_post_sector_question(message: str) -> bool:
        text = str(message or "").strip()
        if "?" in text:
            return True
        normalized = normalize_for_match(text)
        return any(
            normalized.startswith(prefix)
            for prefix in (
                "what ",
                "why ",
                "how ",
                "when ",
                "where ",
                "which ",
                "who ",
                "can ",
                "could ",
                "should ",
                "would ",
                "is ",
                "are ",
                "do ",
                "does ",
            )
        )

    async def _post_sector_label_from_llm(
        self,
        session: ChatSession,
        message: str,
    ) -> str | None:
        value = str(message or "").strip()
        if not value:
            return None
        if not self._looks_like_post_sector_action_request(value):
            return None

        prompt = (
            "Classify a user message shown after the app has listed hazards for a "
            "selected country, region, and sector.\n\n"
            "Return one valid JSON object only:\n"
            '{"action":"start_mitigation_planning|add_new_hazard|refresh_hazards|'
            'dive_deeper|none","confidence":"high|medium|low","reason":"Brief reason."}\n\n'
            "Use add_new_hazard when the user wants to add, create, write, provide, "
            "or define their own hazard, or says the listed hazards do not fit, do "
            "not make sense, are missing something, or are not the hazard they want. "
            "The wording may be informal or indirect.\n"
            "Use start_mitigation_planning only when they want to move on to "
            "mitigation planning. Use refresh_hazards only when they want to reload "
            "or regenerate the hazard list. Use dive_deeper only when they ask for "
            "statistical details. Use none for questions, navigation, or unrelated text."
        )
        response = await ask_llm_chat(
            context=prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Country: {session.country or ''}\n"
                        f"Region: {session.region or ''}\n"
                        f"Sector: {session.sector or ''}\n"
                        f"Message: {value}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=120,
            response_format="json",
        )
        if is_llm_unavailable_response(response):
            return None
        parsed = parse_json_object(response)
        if not isinstance(parsed, dict):
            return None
        action = str(parsed.get("action") or "").strip().casefold()
        confidence = str(parsed.get("confidence") or "").strip().casefold()
        if confidence not in {"high", "medium"}:
            return None
        return {
            "start_mitigation_planning": "Start Mitigation Planning",
            "add_new_hazard": "Add a new Hazard",
            "refresh_hazards": "Refresh hazards and DGs",
            "dive_deeper": "Dive deeper into statistical findings",
        }.get(action)

    @staticmethod
    def _looks_like_post_sector_action_request(message: str) -> bool:
        """Require an action signal before delegating post-sector intent to an LLM."""
        normalized = normalize_for_match(message)
        if not normalized:
            return False
        action_terms = (
            "hazard",
            "mitigation",
            "refresh",
            "reload",
            "regenerate",
            "update",
            "dive",
            "statistical",
            "findings",
            "analysis",
            "next step",
        )
        if any(term in normalized for term in action_terms):
            return True
        return "fit" in normalized and any(
            phrase in normalized
            for phrase in ("none fit", "not fit", "do not fit", "dont fit")
        )

    def _post_sector_selection_from_open_text(
        self,
        session: ChatSession,
        message: str,
    ) -> dict[str, str | None] | None:
        selector = getattr(self, "_deterministic_selection_from_text", None)
        if selector is None:
            return None
        selection = selector(session, message)
        if selection is None:
            return None
        return selection

    def _hazard_profile_step(self, session_id: str, session: ChatSession) -> ChatResponse:
        self._filter_session_hazards_without_profiles(session)
        session.phase = "hazard_profile_selection"
        return ChatResponse(
            session_id=session_id,
            step="hazard_profile_selection",
            bot_message=render_message(
                "mitigation_next.md",
                selected_policy=session.selected_context_policy,
            ),
            options=self._hazard_options(session),
            session=session.summary(),
            error=False,
        )

    async def _handle_hazard_profile_selection(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
        if open_selection_handler is not None:
            open_selection_response = await open_selection_handler(
                session_id,
                session,
                message,
                current_phase="sector",
            )
            if open_selection_response is not None:
                return open_selection_response
        else:
            navigation_handler = getattr(self, "_open_selection_navigation_response", None)
            if navigation_handler is not None:
                navigation_response = await navigation_handler(
                    session_id,
                    session,
                    message,
                    "sector",
                )
                if navigation_response is not None:
                    return navigation_response

        action = normalize(message)
        if action in {
            normalize("Show additional hazards"),
            normalize("Show hazards added by experts"),
        }:
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=(
                    "Choose one of the hazards added by experts from the selected "
                    "country-sector evidence."
                ),
                options=self._additional_hazard_selection_options(session),
                session=session.summary(),
                error=False,
            )
        if action == normalize("Show co-created hazards"):
            self._hydrate_custom_hazard_profiles(session)
            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message="Choose one of the co-created hazards added by users.",
                options=self._custom_hazard_selection_options(session),
                session=session.summary(),
                error=False,
            )
        if action == normalize("Show listed hazards"):
            return self._hazard_profile_step(session_id, session)

        hazard = self._open_hazard_selection_from_text(session, message)
        if hazard is None:
            hazard = self._match_hazard(message, session)
        if hazard is None:
            fuzzy_hazard = self._fuzzy_hazard(message, session)
            if fuzzy_hazard is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_hazard)

            question_handler = getattr(self, "_handle_anytime_grounded_question", None)
            if question_handler is not None:
                question_response = await question_handler(session_id, session, message)
                if question_response is not None:
                    return question_response

            return ChatResponse(
                session_id=session_id,
                step="hazard_profile_selection",
                bot_message=self.invalid_message,
                options=self._hazard_options(session),
                session=session.summary(),
                error=True,
            )

        self._discard_temporary_policy_references(session)
        self._clear_selected_hazard_context(session)
        session.selected_hazard = hazard
        is_saved_custom_hazard = self._is_saved_custom_hazard(session, hazard)
        self._record_activity(session_id, session, "hazard_selected", hazard)
        session.phase = "socio_demographic_review"

        if is_saved_custom_hazard:
            session.accepted_custom_hazard = hazard
            session.accepted_custom_hazard_id = self._custom_hazard_id_for_context(session, hazard)
            session.saved_target_population_answers = self._target_population_answers_for_saved_hazard(
                session,
                hazard,
            )
            self._hydrate_custom_hazard_profiles(session)
            return await self._hazard_profiles_response(session_id, session, hazard)

        return await self._hazard_profiles_response(session_id, session, hazard)

    def _open_hazard_selection_from_text(
        self,
        session: ChatSession,
        message: str,
    ) -> str | None:
        normalized_message = normalize_for_match(message)
        if not normalized_message:
            return None
        hazard_labels = [
            option.label
            for option in self._hazard_options(session)
            if not is_hazard_action_label(option.label)
        ]
        if not hazard_labels:
            return None

        ordinal_parser = getattr(self, "_ordinal_index_from_text", None)
        if ordinal_parser is not None:
            ordinal = ordinal_parser(message)
            if ordinal is not None:
                index = ordinal if ordinal >= 0 else len(hazard_labels) + ordinal
                if 0 <= index < len(hazard_labels):
                    return hazard_labels[index]

        normalized_hazards = [
            (hazard, normalize_for_match(hazard))
            for hazard in hazard_labels
            if normalize_for_match(hazard)
        ]
        exact_matches = [
            hazard
            for hazard, normalized_hazard in normalized_hazards
            if normalized_hazard == normalized_message
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        contained_matches = [
            hazard
            for hazard, normalized_hazard in normalized_hazards
            if self._normalized_phrase_contains(normalized_message, normalized_hazard)
        ]
        if len(contained_matches) == 1:
            return contained_matches[0]
        return None

    @staticmethod
    def _normalized_phrase_contains(text: str, phrase: str) -> bool:
        if not text or not phrase:
            return False
        text_tokens = text.split()
        phrase_tokens = phrase.split()
        if not text_tokens or not phrase_tokens or len(phrase_tokens) > len(text_tokens):
            return False
        window_size = len(phrase_tokens)
        return any(
            text_tokens[index : index + window_size] == phrase_tokens
            for index in range(0, len(text_tokens) - window_size + 1)
        )

    async def _handle_socio_demographic_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SOCIO_DEMOGRAPHIC_OPTIONS)
        if exact_label is None:
            option_matcher = getattr(self, "_open_option_label_from_text", None)
            if option_matcher is not None:
                exact_label = option_matcher(message, SOCIO_DEMOGRAPHIC_OPTIONS)
        if exact_label is None:
            exact_label = self._socio_demographic_label_from_open_text(message)
        if exact_label is None:
            open_selection_handler = getattr(self, "_open_selection_response_from_any_step", None)
            if open_selection_handler is not None:
                open_selection_response = await open_selection_handler(
                    session_id,
                    session,
                    message,
                    current_phase="sector",
                )
                if open_selection_response is not None:
                    return open_selection_response
        if exact_label is None:
            fuzzy_label = match_option_label(message, SOCIO_DEMOGRAPHIC_OPTIONS)
            if fuzzy_label is not None:
                return self._fuzzy_confirmation_step(session_id, session, fuzzy_label)
        action = normalize(exact_label or message)

        if action == normalize("Add more DGs"):
            return self._start_additional_dg_questions(session_id, session)

        if action == normalize("Create a new mitigation proposal"):
            session.mitigation_proposal_type = "new_policy"
            return await self._create_mitigation_measure_step(session_id, session)

        if action == normalize("Propose adaptations in the existing policy"):
            session.mitigation_proposal_type = "existing_policy"
            if not session.selected_context_policy:
                session.pending_mitigation_policy_selection = True
                return self._policy_step(session_id, session)
            session.selected_mitigation_policy = session.selected_context_policy
            return await self._create_mitigation_measure_step(session_id, session)

        return ChatResponse(
            session_id=session_id,
            step="socio_demographic_review",
            bot_message=self.invalid_message,
            options=SOCIO_DEMOGRAPHIC_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _socio_demographic_label_from_open_text(self, message: str) -> str | None:
        normalized = normalize_for_match(message)
        if not normalized:
            return None
        if normalized in {
            "create mitigation",
            "create a mitigation",
            "create mitigation measure",
            "create a mitigation measure",
            "create a new mitigation proposal",
            "new mitigation proposal",
            "start mitigation",
            "start mitigation measure",
            "make mitigation measure",
            "new mitigation measure",
        }:
            return "Create a new mitigation proposal"
        if normalized in {
            "propose changes in the existing policy",
            "propose adaptations in the existing policy",
            "propose changes to the existing policy",
            "propose policy changes",
            "change existing policy",
            "modify existing policy",
        }:
            return "Propose adaptations in the existing policy"
        if "mitigation" in normalized and any(
            token in normalized
            for token in ("start", "create", "make", "build", "develop", "prepare")
        ):
            return "Create a new mitigation proposal"
        if "policy" in normalized and any(
            token in normalized for token in ("change", "modify", "amend", "propose")
        ):
            return "Propose adaptations in the existing policy"
        if normalized in {"add dgs", "add more dgs", "add demographic groups"}:
            return "Add more DGs"
        if "dg" in normalized and "add" in normalized:
            return "Add more DGs"
        return None
