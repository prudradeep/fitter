from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CountrySector(Base):
    __tablename__ = "country_sectors"
    __table_args__ = (UniqueConstraint("country_id", "sector_id", name="uq_country_sector"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id", ondelete="CASCADE"), index=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"), index=True)


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    map_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    map_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    regions: Mapped[list["Region"]] = relationship(
        back_populates="country",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    sectors: Mapped[list["Sector"]] = relationship(
        secondary="country_sectors",
        back_populates="countries",
        lazy="selectin",
    )


class Region(Base):
    __tablename__ = "regions"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_country_region"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    country: Mapped["Country"] = relationship(back_populates="regions")


class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)

    countries: Mapped[list["Country"]] = relationship(
        secondary="country_sectors",
        back_populates="sectors",
        lazy="selectin",
    )


class EvaluationQuestion(Base):
    __tablename__ = "evaluation_questions"
    __table_args__ = (UniqueConstraint("category", "sort_order", name="uq_eval_category_sort"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    category: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    options: Mapped[list["QuestionOption"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class QuestionOption(Base):
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint("questionId", "option", name="uq_question_option"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    question_id: Mapped[int] = mapped_column(
        "questionId",
        ForeignKey("evaluation_questions.id", ondelete="CASCADE"),
        index=True,
    )
    option: Mapped[str] = mapped_column(String(255), nullable=False)

    question: Mapped["EvaluationQuestion"] = relationship(back_populates="options")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(220), index=True)
    session_data: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), index=True
    )
    country_id: Mapped[int | None] = mapped_column(
        ForeignKey("countries.id", ondelete="SET NULL"), index=True
    )
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), index=True
    )
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserChatMessage(Base):
    __tablename__ = "user_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_session_id: Mapped[int] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    designation: Mapped[str] = mapped_column(String(160), nullable=False)
    organisation_type: Mapped[str] = mapped_column(String(160), nullable=False)
    organisation_name: Mapped[str] = mapped_column(String(220), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SystemHazard(Base):
    __tablename__ = "system_hazards"
    __table_args__ = (UniqueConstraint("sector_id", "name", name="uq_system_hazard_sector_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserHazard(Base):
    __tablename__ = "user_hazards"
    __table_args__ = (UniqueConstraint("user_session_id", "name", name="uq_user_session_hazard"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True)
    system_hazard_id: Mapped[int | None] = mapped_column(ForeignKey("system_hazards.id", ondelete="SET NULL"), index=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id", ondelete="SET NULL"), index=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="custom")
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserHazardSocioDemographic(Base):
    __tablename__ = "user_hazard_socio_demographics"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_hazard_id: Mapped[int] = mapped_column(ForeignKey("user_hazards.id", ondelete="CASCADE"), index=True)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"), index=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id", ondelete="SET NULL"), index=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id", ondelete="SET NULL"), index=True)
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="llm")
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserMitigationMeasure(Base):
    __tablename__ = "user_mitigation_measures"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_hazard_id: Mapped[int] = mapped_column(ForeignKey("user_hazards.id", ondelete="CASCADE"), index=True)
    measure: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserQuestionResponse(Base):
    __tablename__ = "user_question_responses"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True)
    user_hazard_id: Mapped[int | None] = mapped_column(ForeignKey("user_hazards.id", ondelete="SET NULL"), index=True)
    mitigation_measure_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_mitigation_measures.id", ondelete="SET NULL"),
        index=True,
    )
    question_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_questions.id", ondelete="SET NULL"), index=True)
    question_option_id: Mapped[int | None] = mapped_column(ForeignKey("question_options.id", ondelete="SET NULL"), index=True)
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    response_text: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserActivity(Base):
    __tablename__ = "user_activities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    step: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
