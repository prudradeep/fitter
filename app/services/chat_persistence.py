import json
import logging
from dataclasses import asdict

from sqlalchemy import desc, select

from app.models import UserActivity, UserChatMessage, UserSession
from app.schemas import ChatResponse
from app.services.chat_session import ChatSession, session_store

logger = logging.getLogger(__name__)


class ChatPersistenceMixin:
    def _ensure_user_session(self, session_id: str, session: ChatSession) -> UserSession | None:
        try:
            user_session = self.db.scalar(
                select(UserSession).where(UserSession.session_key == session_id)
            )
            if user_session is None:
                user_session = UserSession(session_key=session_id)
                self.db.add(user_session)
            if self.user_id is not None:
                user_session.user_id = self.user_id
            user_session.country_id = session.country_id
            user_session.region_id = session.region_id
            user_session.sector_id = session.sector_id
            if not user_session.title_is_manual:
                user_session.title = self._session_title(session)
            session_data = asdict(session)
            session_data["stats_dialog_conversation"] = None
            user_session.session_data = json.dumps(session_data, default=str)
            self.db.commit()
            self.db.refresh(user_session)
            return user_session
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist user session")
            return None

    def _session_belongs_to_current_user(self, session_id: str | None) -> bool:
        if not session_id or self.user_id is None:
            return True
        user_session = self.db.scalar(
            select(UserSession).where(UserSession.session_key == session_id)
        )
        if user_session is None or user_session.user_id is None:
            return True
        return user_session.user_id == self.user_id

    def _hydrate_session_from_db(self, session_id: str | None) -> None:
        if not session_id:
            return
        user_session = self.db.scalar(
            select(UserSession).where(UserSession.session_key == session_id)
        )
        if not user_session or not user_session.session_data:
            return
        try:
            session_store.put(session_id, json.loads(user_session.session_data))
        except json.JSONDecodeError:
            logger.warning("Could not restore invalid session snapshot for %s", session_id)

    def _finalize_chat_response(
        self, session_id: str, session: ChatSession, response: ChatResponse
    ) -> None:
        self._ensure_user_session(session_id, session)
        if response.error:
            return
        self._record_chat_message(
            session_id,
            session,
            "bot",
            response.bot_message,
            is_error=response.error,
        )

    def _record_chat_message(
        self,
        session_id: str,
        session: ChatSession,
        role: str,
        content: str,
        is_error: bool = False,
    ) -> None:
        if not content.strip():
            return
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            self.db.add(
                UserChatMessage(
                    user_session_id=user_session.id,
                    role=role,
                    content=content,
                    is_error=is_error,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist chat message")

    @staticmethod
    def _chat_message_display_content(content: str) -> str:
        if content.strip().startswith("TARGET_POPULATION_BATCH:"):
            return "Quick Select Affected Population Group"
        return content

    def _recent_chat_messages_for_auto_user(
        self, session_id: str, limit: int = 10
    ) -> list[dict[str, str]]:
        try:
            user_session = self.db.scalar(
                select(UserSession).where(UserSession.session_key == session_id)
            )
            if user_session is None:
                return []
            rows = self.db.scalars(
                select(UserChatMessage)
                .where(UserChatMessage.user_session_id == user_session.id)
                .order_by(desc(UserChatMessage.created_at), desc(UserChatMessage.id))
                .limit(limit)
            ).all()
        except Exception:
            logger.exception("Failed to load chat messages for auto conversation")
            return []
        return [
            {"role": row.role, "content": row.content}
            for row in reversed(rows)
            if str(row.content or "").strip()
        ]

    @staticmethod
    def _session_title(session: ChatSession) -> str:
        parts = [item for item in [session.country, session.region, session.sector] if item]
        if session.selected_hazard:
            parts.append(session.selected_hazard)
        return " / ".join(parts[:4]) or "New policy session"

    def _record_activity(
        self,
        session_id: str,
        session: ChatSession,
        activity_type: str,
        details: str | None = None,
        step: str | None = None,
    ) -> None:
        try:
            user_session = self._ensure_user_session(session_id, session)
            if user_session is None:
                return
            self.db.add(
                UserActivity(
                    user_session_id=user_session.id,
                    activity_type=activity_type,
                    step=step or self._activity_step(session),
                    details=details,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to persist user activity")

    @staticmethod
    def _activity_step(session: ChatSession) -> str:
        if session.pending_fuzzy_option:
            return "fuzzy_confirmation"
        if session.country is None:
            return "country"
        if session.region is None:
            return "region"
        if session.sector is None:
            return "sector"
        return session.phase
