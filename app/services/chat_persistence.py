import json
import logging
import re
from dataclasses import asdict

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import UserActivity, UserChatMessage, UserHazard, UserMitigationMeasure, UserSession
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
        except SQLAlchemyError:
            self.db.rollback()
            logger.exception("Failed to persist user session session_id=%s", session_id)
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
        self._record_chat_message(
            session_id,
            session,
            "bot",
            response.bot_message,
            is_error=response.error,
        )

    def _attach_persisted_session_counts(
        self,
        session_id: str,
        session: ChatSession,
        response: ChatResponse,
    ) -> None:
        response.session.mitigation_measure_count = max(
            int(response.session.mitigation_measure_count or 0),
            self._persisted_mitigation_measure_count(session_id, session),
        )

    def _persisted_mitigation_measure_count(
        self,
        session_id: str,
        session: ChatSession,
    ) -> int:
        _ = session_id
        if session.country_id is None or session.sector_id is None:
            return 0

        try:
            scope_filters = [
                UserSession.country_id == session.country_id,
                UserSession.region_id.is_(None)
                if session.region_id is None
                else UserSession.region_id == session.region_id,
                UserSession.sector_id == session.sector_id,
            ]
            visibility_filters = self._visible_mitigation_measure_filters()
            direct_count = self.db.scalar(
                select(func.count(UserMitigationMeasure.id))
                .join(UserSession, UserSession.id == UserMitigationMeasure.user_session_id)
                .where(
                    *scope_filters,
                    *visibility_filters,
                )
            ) or 0
            linked_count = self.db.scalar(
                select(func.count(UserMitigationMeasure.id))
                .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserMitigationMeasure.user_session_id.is_(None),
                    *scope_filters,
                    *visibility_filters,
                )
            ) or 0
            return int(direct_count) + int(linked_count)
        except SQLAlchemyError:
            logger.exception(
                "Failed to count persisted mitigation measures session_id=%s",
                session_id,
            )
            return 0

    def _visible_mitigation_measure_filters(self) -> list[object]:
        if self.user_id is None:
            return []
        return [
            or_(
                UserSession.user_id == self.user_id,
                and_(
                    UserMitigationMeasure.validation_mode == "strict",
                    UserMitigationMeasure.is_crowd_sourced.is_(True),
                ),
            )
        ]

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
        except SQLAlchemyError:
            self.db.rollback()
            logger.exception(
                "Failed to persist chat message session_id=%s role=%s",
                session_id,
                role,
            )

    def _chat_message_display_content(self, content: str) -> str:
        if content.strip().startswith("TARGET_POPULATION_BATCH:"):
            return "Quick Select Affected Population Group"
        if not bool(getattr(self, "is_admin", False)):
            return self._strip_profile_admin_details(content)
        return content

    @staticmethod
    def _strip_profile_admin_details(content: str) -> str:
        if not re.search(
            r"Reference:|Plain[- ]English:|Mapped target population:|Eurostat population lookup:",
            content,
            flags=re.IGNORECASE,
        ):
            return content
        admin_detail = re.compile(
            r"(?is)(?:<br\s*/?>\s*)?"
            r"(?:Reference|Plain[- ]English|Mapped target population|Eurostat population lookup):"
            r"\s*.*?"
            r"(?=(?:<br\s*/?>\s*)?"
            r"(?:Combined profiles|Selected options|Reference|Plain[- ]English|"
            r"Mapped target population|Eurostat population lookup):|</small>|</p>|</li>|</th>|$)"
        )
        return admin_detail.sub("", content)

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
        except SQLAlchemyError:
            logger.exception(
                "Failed to load chat messages for auto conversation session_id=%s",
                session_id,
            )
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
        except SQLAlchemyError:
            self.db.rollback()
            logger.exception(
                "Failed to persist user activity session_id=%s activity_type=%s",
                session_id,
                activity_type,
            )

    @staticmethod
    def _activity_step(session: ChatSession) -> str:
        if session.pending_fuzzy_option:
            return "fuzzy_confirmation"
        if session.pending_selection_confirmation or session.pending_selection_action:
            return "selection_confirmation"
        if session.country is None:
            return "country"
        if session.region is None:
            return "region"
        if session.sector is None:
            return "sector"
        return session.phase
