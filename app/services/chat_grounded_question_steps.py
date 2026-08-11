import asyncio
from html import escape
import logging
import re

from app.llm import ask_llm_chat
from app.schemas import ChatResponse
from app.services.chat_formatters import format_all_dgs
from app.services.chat_options import STATS_DEEP_DIVE_OPTIONS, normalize_for_match
from app.services.chat_session import ChatSession
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    TEMPORARY_KB_SCOPE,
    VALIDATED_EVIDENCE_SCOPE,
    KnowledgeBaseService,
)
from app.services.message_renderer import markdown_to_html
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template
from app.services.question_intent import detect_user_question_intent
from app.services.sector_prompt_rag import SectorPromptRagService

logger = logging.getLogger(__name__)


class ChatGroundedQuestionStepsMixin:
    async def _stats_deep_dive(
        self,
        session_id: str,
        session: ChatSession,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        persist_history: bool = True,
    ) -> ChatResponse:
        context, messages = await self._build_stats_deep_dive_messages(session, user_message, history)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=900,
        )

        next_messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": answer},
        ]
        if persist_history:
            if session.stats_conversation is None:
                session.stats_conversation = []
            session.stats_conversation.extend(next_messages)
        else:
            if session.stats_dialog_conversation is None:
                session.stats_dialog_conversation = []
            session.stats_dialog_conversation.extend(next_messages)

        return ChatResponse(
            session_id=session_id,
            step="stats_deep_dive",
            bot_message=markdown_to_html(answer),
            options=STATS_DEEP_DIVE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _deep_dive(
        self, session_id: str, session: ChatSession, user_message: str
    ) -> ChatResponse:
        context, messages = await self._build_deep_dive_messages(session, user_message)
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.25,
            max_tokens=900,
        )
        return ChatResponse(
            session_id=session_id,
            step="complete",
            bot_message=markdown_to_html(answer),
            options=[],
            session=session.summary(),
            error=False,
        )

    async def _handle_anytime_grounded_question(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse | None:
        intent = await self._detect_user_question_intent(session, message)
        if not (
            bool(intent.get("is_question"))
            and str(intent.get("confidence") or "").casefold() in {"high", "medium"}
        ):
            return None
        if self._is_stats_related_question(message):
            return self._stats_deep_dive_dialog_step(
                session_id,
                session,
                initial_question=message,
            )
        answer, source_map = await self._answer_grounded_question(session_id, session, message)
        return self._repeat_current_options(
            session_id,
            session,
            self._grounded_answer_html(answer, source_map),
            error=False,
        )

    async def _answer_grounded_question(
        self, session_id: str, session: ChatSession, question: str
    ) -> tuple[str, dict[str, dict[str, str]]]:
        workflow_context = self._workflow_help_context(session)
        if self._is_workflow_help_question(session, question):
            return await self._answer_workflow_help_question(
                session,
                question,
                workflow_context,
            )
        else:
            (
                (knowledge_context, knowledge_sources),
                (stats_context, stats_sources),
            ) = await asyncio.gather(
                self._question_knowledge_context(session, question),
                self._question_stats_context(session, question),
            )
        if (
            not knowledge_context.strip()
            and not stats_context.strip()
            and not workflow_context.strip()
        ):
            return (
                "I do not have enough information in the Knowledge Base or loaded "
                "sector stats to answer that yet.",
                {},
            )

        context = render_prompt_template(
            "llm/grounded_question_answer.txt",
            scope_instruction=self._scope_instruction(session),
            knowledge_context=knowledge_context
            or "- No relevant Knowledge Base excerpts were found.",
            stats_context=stats_context or "- No relevant sector statistical context was found.",
            workflow_context=workflow_context
            or "- No relevant workflow help context is available.",
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/grounded_question_answer_user.txt",
                    country=session.country or "Not selected",
                    region=session.region or "Not selected",
                    sector=session.sector or "Not selected",
                    selected_hazard=session.selected_hazard
                    or session.accepted_custom_hazard
                    or "Not selected",
                    affected_groups=format_all_dgs(session) or "Not selected",
                    mitigation_measure=session.mitigation_measure
                    or session.pending_mitigation_measure
                    or "Not selected",
                    conversation_history=self._grounded_question_history(session_id, session),
                    question=question,
                ),
            }
        ]
        answer = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.1,
            max_tokens=800,
        )
        return answer, {**knowledge_sources, **stats_sources}

    async def _answer_workflow_help_question(
        self,
        session: ChatSession,
        question: str,
        workflow_context: str,
    ) -> tuple[str, dict[str, dict[str, str]]]:
        if not workflow_context.strip():
            return (
                "The available Workflow Help Context does not contain enough "
                "information to answer this question.",
                {},
            )
        answer = await ask_llm_chat(
            context=load_nested_prompt_file("workflow/answer.txt"),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Session context:\n"
                        f"- Country: {session.country or 'Not selected'}\n"
                        f"- Region: {session.region or 'Not selected'}\n"
                        f"- Sector: {session.sector or 'Not selected'}\n"
                        f"- Current workflow step: {session.phase or 'Not selected'}\n\n"
                        "Workflow Help Context:\n"
                        f"{workflow_context}\n\n"
                        "User question:\n"
                        f"{question}"
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=350,
        )
        return answer, {}

    def _workflow_help_context(self, session: ChatSession) -> str:
        phase = str(session.phase or "").strip()
        if phase in {"hazards", "stats_deep_dive"}:
            return load_nested_prompt_file("workflow/hazards.txt")
        if phase == "custom_hazard_input":
            return load_nested_prompt_file("workflow/custom_hazard_input.txt")
        if phase == "reason_confirmation":
            return load_nested_prompt_file("workflow/reason_confirmation.txt")
        return ""

    @staticmethod
    def _is_workflow_help_question(session: ChatSession, question: str) -> bool:
        normalized = normalize_for_match(question)
        if not normalized:
            return False
        phase = str(session.phase or "").strip()
        workflow_terms = {
            "option",
            "button",
            "workflow",
            "step",
            "add",
            "create",
            "start",
            "refresh",
            "later",
            "own",
        }
        if not any(term in normalized.split() for term in workflow_terms):
            return False
        if phase in {"hazards", "stats_deep_dive"} and any(
            phrase in normalized
            for phrase in (
                "add hazard",
                "add a hazard",
                "add new hazard",
                "add a new hazard",
                "add my own hazard",
                "own hazard",
                "create hazard",
                "create a hazard",
                "start mitigation",
                "start mitigation planning",
                "refresh hazards",
                "refresh dgs",
            )
        ):
            return True
        if phase == "custom_hazard_input" and any(
            phrase in normalized
            for phrase in (
                "go back",
                "list of hazards",
                "hazard description",
                "what should i type",
            )
        ):
            return True
        if phase == "reason_confirmation" and any(
            phrase in normalized
            for phrase in (
                "mitigation",
                "write my own",
                "adopt",
                "proposal",
            )
        ):
            return True
        return False

    def _grounded_question_history(
        self,
        session_id: str | None,
        session: ChatSession,
        limit: int = 6,
    ) -> str:
        history_sources = (
            self._recent_chat_messages_for_auto_user(session_id, limit=limit)
            if session_id
            else []
        )
        if not history_sources:
            history_sources = [
                *(session.stats_conversation or []),
                *(session.stats_dialog_conversation or []),
                *(session.mitigation_clarification_history or []),
            ]
        cleaned: list[dict[str, str]] = []
        for item in history_sources:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = " ".join(str(item.get("content") or "").split())
            content = re.sub(r"<[^>]+>", " ", content)
            content = " ".join(content.split())
            if role not in {"user", "assistant"} or not content:
                continue
            cleaned.append({"role": role, "content": self._source_excerpt(content, 500)})
        if not cleaned:
            return "- No recent conversation history available."
        return "\n".join(
            f"- {item['role'].title()}: {item['content']}"
            for item in cleaned[-limit:]
        )

    @staticmethod
    def _is_stats_related_question(message: str) -> bool:
        normalized = normalize_for_match(message)
        if not normalized:
            return False
        stats_terms = {
            "stat",
            "stats",
            "statistic",
            "statistics",
            "statistical",
            "data",
            "percentage",
            "percent",
            "average",
            "comparison",
            "compare",
            "population",
            "affected group",
            "affected groups",
            "profile",
            "profiles",
            "predictor",
            "predictors",
            "regional",
            "national",
        }
        return any(term in normalized for term in stats_terms)

    async def _question_knowledge_context(
        self, session: ChatSession, question: str
    ) -> tuple[str, dict[str, dict[str, str]]]:
        query = " ".join(
            item
            for item in [
                question,
                session.country or "",
                session.region or "",
                session.sector or "",
                session.selected_hazard or session.accepted_custom_hazard or "",
                format_all_dgs(session),
                session.mitigation_measure or session.pending_mitigation_measure or "",
            ]
            if item
        )
        contexts: list[str] = []
        try:
            main_results = await KnowledgeBaseService(self.db, None, scope=MAIN_KB_SCOPE).search(
                query,
                limit=6,
            )
        except Exception:
            logger.exception("Main knowledge-base lookup failed during anytime question")
            main_results = []
        sources: dict[str, dict[str, str]] = {}
        main_context, main_sources = self._format_grounded_question_sources(
            main_results,
            prefix="S",
            source_label="Knowledge Base",
            start_index=1,
        )
        sources.update(main_sources)
        next_index = len(main_sources) + 1
        if main_context:
            contexts.append("Main Knowledge Base:\n" + main_context)

        validated_results: list[dict[str, object]] = []
        if session.country_id is not None and session.sector_id is not None:
            try:
                validated_results = await KnowledgeBaseService(
                    self.db,
                    None,
                    scope=VALIDATED_EVIDENCE_SCOPE,
                    country_id=session.country_id,
                    region_id=session.region_id,
                    sector_id=session.sector_id,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Validated evidence lookup failed during anytime question")
                validated_results = []
            validated_context, validated_sources = self._format_grounded_question_sources(
                validated_results,
                prefix="S",
                source_label="Validated evidence",
                start_index=next_index,
            )
            sources.update(validated_sources)
            next_index += len(validated_sources)
            if validated_context:
                contexts.append("Validated evidence:\n" + validated_context)

        if session.session_key:
            try:
                temporary_results = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope=TEMPORARY_KB_SCOPE,
                    session_key=session.session_key,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Temporary knowledge-base lookup failed during anytime question")
                temporary_results = []
            temporary_context, temporary_sources = self._format_grounded_question_sources(
                temporary_results,
                prefix="S",
                source_label="Session evidence",
                start_index=next_index,
            )
            sources.update(temporary_sources)
            if temporary_context:
                contexts.append("Session evidence:\n" + temporary_context)

        return "\n\n".join(contexts), sources

    async def _question_stats_context(
        self, session: ChatSession, question: str
    ) -> tuple[str, dict[str, dict[str, str]]]:
        if not session.sector:
            return "", {}
        query = " ".join(
            item
            for item in [
                question,
                session.selected_hazard or "",
                format_all_dgs(session),
                session.mitigation_measure or session.pending_mitigation_measure or "",
            ]
            if item
        )
        try:
            results = await SectorPromptRagService(self.db).search(
                session.sector,
                query,
                limit=8,
            )
        except Exception:
            logger.exception("Sector-prompt RAG lookup failed")
            results = []
        context, sources = self._format_grounded_question_sources(
            results,
            prefix="SP",
            source_label="Sector stats",
            start_index=1,
        )
        if context:
            return context, sources
        return "- No relevant sector-prompt RAG excerpts were found.", {}

    @staticmethod
    def _format_grounded_question_sources(
        results: list[dict[str, object]],
        *,
        prefix: str,
        source_label: str,
        start_index: int = 1,
        content_limit: int = 900,
    ) -> tuple[str, dict[str, dict[str, str]]]:
        lines: list[str] = []
        sources: dict[str, dict[str, str]] = {}
        for offset, result in enumerate(results, start=start_index):
            source_id = f"{prefix}{offset}"
            title = str(result.get("title") or source_label or "Knowledge source").strip()
            source_type = str(result.get("source_type") or source_label or "").strip()
            source_uri = str(result.get("source_uri") or "").strip()
            page_number = result.get("page_number")
            page_label = f", page {page_number}" if page_number else ""
            score = result.get("score")
            score_label = f", score {score}" if score is not None else ""
            nli_label = result.get("nli_label")
            nli_score = result.get("nli_score")
            nli_score_label = (
                f", NLI {nli_label} {nli_score}"
                if nli_label is not None and nli_score is not None
                else ""
            )
            content = str(result.get("content") or "").strip()
            if not content:
                continue
            context_excerpt = ChatGroundedQuestionStepsMixin._source_excerpt(content, content_limit)
            tooltip_excerpt = ChatGroundedQuestionStepsMixin._source_excerpt(content, 360)
            lines.append(
                f"- [{source_id}] {title}{page_label}{score_label}{nli_score_label}: "
                f"{context_excerpt}"
            )
            sources[source_id] = {
                "id": source_id,
                "title": title,
                "source_type": source_type or source_label,
                "source_uri": source_uri,
                "page": str(page_number or ""),
                "excerpt": tooltip_excerpt,
            }
        return "\n".join(lines), sources

    @staticmethod
    def _source_excerpt(content: str, limit: int = 360) -> str:
        text = " ".join(str(content or "").split())
        if len(text) <= limit:
            return text
        truncated = text[:limit].rstrip()
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0].rstrip()
        return f"{truncated}..."

    @classmethod
    def _grounded_answer_html(
        cls,
        answer: str,
        source_map: dict[str, dict[str, str]],
    ) -> str:
        html = markdown_to_html(answer)
        if not source_map:
            return html
        pattern = re.compile(
            r"(?<![\w-])\[("
            + "|".join(re.escape(source_id) for source_id in sorted(source_map, key=len, reverse=True))
            + r")\](?![\w-])"
        )
        return pattern.sub(lambda match: cls._source_chip_html(match.group(1), source_map), html)

    @staticmethod
    def _source_chip_html(source_id: str, source_map: dict[str, dict[str, str]]) -> str:
        source = source_map.get(source_id) or {}
        title = source.get("title") or "Knowledge source"
        source_type = source.get("source_type") or "Source"
        source_uri = source.get("source_uri") or ""
        page = source.get("page") or ""
        excerpt = source.get("excerpt") or ""
        meta_parts = [source_type]
        if page:
            meta_parts.append(f"page {page}")
        if source_uri:
            meta_parts.append(source_uri.replace("sector-prompt://", ""))
        aria_label = f"{source_id}: {title}. {'; '.join(meta_parts)}. {excerpt}"
        tooltip = (
            '<span class="source-citation-tooltip" aria-hidden="true">'
            f"<strong>{escape(title)}</strong>"
            f"<small>{escape(' · '.join(meta_parts))}</small>"
            f"<span>{escape(excerpt)}</span>"
            "</span>"
        )
        label = f"<span aria-hidden=\"true\">{escape(source_id)}</span>"
        if source_uri.startswith(("http://", "https://")):
            return (
                f'<a class="source-citation" href="{escape(source_uri, quote=True)}" '
                'target="_blank" rel="noopener noreferrer" '
                f'aria-label="{escape(aria_label, quote=True)}">'
                f"{label}{tooltip}</a>"
            )
        return (
            '<span class="source-citation" tabindex="0" '
            f'aria-label="{escape(aria_label, quote=True)}" '
            f'title="{escape(aria_label, quote=True)}">'
            f"{label}{tooltip}</span>"
        )

    @staticmethod
    def _looks_like_user_question(message: str) -> bool:
        value = str(message or "").strip()
        if not value:
            return False
        if "?" in value:
            return True
        normalized = normalize_for_match(value)
        question_starts = (
            "what ",
            "why ",
            "how ",
            "when ",
            "where ",
            "which ",
            "who ",
            "whose ",
            "can ",
            "could ",
            "should ",
            "would ",
            "is ",
            "are ",
            "do ",
            "does ",
            "did ",
            "explain ",
            "tell me ",
        )
        return any(normalized.startswith(prefix) for prefix in question_starts)

    async def _detect_user_question_intent(
        self,
        session: ChatSession,
        message: str,
    ) -> dict[str, bool | str]:
        return await detect_user_question_intent(
            message,
            context={
                "country": session.country,
                "region": session.region,
                "sector": session.sector,
                "phase": session.phase,
                "selected_hazard": session.selected_hazard or session.accepted_custom_hazard,
                "available_countries": self._available_country_names()
                if session.country is None
                else [],
                "available_regions": self._available_region_names(session)
                if session.country is not None and session.region is None
                else [],
                "available_sectors": self._available_sector_names(session)
                if session.country is not None and session.sector is None
                else [],
            },
            fallback=self._looks_like_user_question,
        )

    async def _build_deep_dive_messages(
        self, session: ChatSession, user_message: str
    ) -> tuple[str, list[dict[str, str]]]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"{session.selected_hazard or ''} {format_all_dgs(session)} {user_message}",
            limit=8,
        )
        context = render_prompt_template(
            "llm/deep_dive_context.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/deep_dive_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    user_message=user_message,
                ),
            }
        ]
        return context, messages

    async def _build_stats_deep_dive_messages(
        self,
        session: ChatSession,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        sector_context = await self._sector_prompt_rag_context(
            session,
            f"{session.selected_hazard or ''} {format_all_dgs(session)} {user_message}",
            limit=8,
        )
        context = render_prompt_template(
            "llm/stats_deep_dive_context.txt",
            scope_instruction=self._scope_instruction(session),
            sector_context=sector_context,
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/stats_deep_dive_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                    user_message=user_message,
                ),
            }
        ]
        history = list((session.stats_conversation or []) if history is None else history)
        if not history:
            return context, messages

        current_message = messages[-1]
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/stats_deep_dive_history_user.txt",
                    country=session.country,
                    region=session.region,
                    sector=session.sector,
                ),
            },
            *history[-10:],
            current_message,
        ]
        return context, messages
