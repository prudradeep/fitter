import asyncio
import logging

from app.llm import ask_llm_chat
from app.schemas import ChatResponse
from app.services.chat_formatters import format_all_dgs
from app.services.chat_options import STATS_DEEP_DIVE_OPTIONS, normalize_for_match
from app.services.chat_session import ChatSession
from app.services.knowledge_base import KnowledgeBaseService
from app.services.message_renderer import markdown_to_html
from app.services.prompt_loader import render_prompt_template
from app.services.question_intent import detect_user_question_intent

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
        answer = await self._answer_grounded_question(session, message)
        return self._repeat_current_options(
            session_id,
            session,
            markdown_to_html(answer),
            error=False,
        )

    async def _answer_grounded_question(self, session: ChatSession, question: str) -> str:
        knowledge_context, stats_context = await asyncio.gather(
            self._question_knowledge_context(session, question),
            self._question_stats_context(session, question),
        )
        if not knowledge_context.strip() and not stats_context.strip():
            return (
                "I do not have enough information in the Knowledge Base or loaded "
                "sector stats to answer that yet."
            )

        context = render_prompt_template(
            "llm/grounded_question_answer.txt",
            scope_instruction=self._scope_instruction(session),
            knowledge_context=knowledge_context
            or "- No relevant Knowledge Base excerpts were found.",
            stats_context=stats_context or "- No relevant sector statistical context was found.",
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
                    question=question,
                ),
            }
        ]
        return await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.1,
            max_tokens=800,
        )

    async def _question_knowledge_context(self, session: ChatSession, question: str) -> str:
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
            main_results = await KnowledgeBaseService(self.db, self.user_id).search(
                query,
                limit=6,
            )
        except Exception:
            logger.exception("Main knowledge-base lookup failed during anytime question")
            main_results = []
        main_context = self._format_knowledge_results(main_results)
        if main_context:
            contexts.append("Main Knowledge Base:\n" + main_context)

        if session.session_key:
            try:
                temporary_results = await KnowledgeBaseService(
                    self.db,
                    self.user_id,
                    scope="temporary",
                    session_key=session.session_key,
                ).search(query, limit=4)
            except Exception:
                logger.exception("Temporary knowledge-base lookup failed during anytime question")
                temporary_results = []
            temporary_context = self._format_knowledge_results(temporary_results)
            if temporary_context:
                contexts.append("Session evidence:\n" + temporary_context)

        return "\n\n".join(contexts)

    async def _question_stats_context(self, session: ChatSession, question: str) -> str:
        if not session.sector:
            return ""
        return await self._sector_prompt_rag_context(
            session,
            " ".join(
                item
                for item in [
                    question,
                    session.selected_hazard or "",
                    format_all_dgs(session),
                    session.mitigation_measure or session.pending_mitigation_measure or "",
                ]
                if item
            ),
            limit=8,
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
        context, messages = await self._build_deep_dive_messages(session, user_message)
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
