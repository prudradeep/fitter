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
    chart_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
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


class AdditionalHazard(Base):
    __tablename__ = "additional_hazards"
    __table_args__ = (
        UniqueConstraint("country_id", "sector_id", "name", name="uq_additional_hazard_scope_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id", ondelete="CASCADE"), index=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="csv")
    csv_row_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AdditionalHazardProfile(Base):
    __tablename__ = "additional_hazard_profiles"
    __table_args__ = (
        UniqueConstraint("additional_hazard_id", "profile", name="uq_additional_hazard_profile"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    additional_hazard_id: Mapped[int] = mapped_column(
        ForeignKey("additional_hazards.id", ondelete="CASCADE"), index=True
    )
    profile: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="d4_2_pdf")
    csv_row_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AdditionalHazardProfileTargetPopulation(Base):
    __tablename__ = "additional_hazard_profile_target_populations"
    __table_args__ = (
        UniqueConstraint(
            "additional_hazard_profile_id",
            "question_option_id",
            name="uq_additional_hazard_profile_target_option",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    additional_hazard_profile_id: Mapped[int] = mapped_column(
        ForeignKey("additional_hazard_profiles.id", ondelete="CASCADE"), index=True
    )
    question_option_id: Mapped[int] = mapped_column(
        ForeignKey("question_options.id", ondelete="CASCADE"), index=True
    )
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
    variable_name: Mapped[str | None] = mapped_column(String(160))
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    statistical_basis: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="llm")
    metadata_json: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SystemHazardSocioDemographic(Base):
    __tablename__ = "system_hazard_socio_demographics"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    system_hazard_id: Mapped[int] = mapped_column(ForeignKey("system_hazards.id", ondelete="CASCADE"), index=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id", ondelete="SET NULL"), index=True)
    variable_name: Mapped[str | None] = mapped_column(String(160))
    variable_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="individual", server_default="individual"
    )
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    statistical_basis: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="sector_prompt")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SystemHazardSocioDemographicTargetPopulation(Base):
    __tablename__ = "system_hazard_socio_demographic_target_populations"
    __table_args__ = (
        UniqueConstraint(
            "system_hazard_socio_demographic_id",
            "question_option_id",
            name="uq_system_dg_target_population_option",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    system_hazard_socio_demographic_id: Mapped[int] = mapped_column(
        ForeignKey("system_hazard_socio_demographics.id", ondelete="CASCADE"), index=True
    )
    question_option_id: Mapped[int] = mapped_column(
        ForeignKey("question_options.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserMitigationMeasure(Base):
    __tablename__ = "user_mitigation_measures"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_hazard_id: Mapped[int] = mapped_column(ForeignKey("user_hazards.id", ondelete="CASCADE"), index=True)
    measure: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    target_population: Mapped[str | None] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(Text)
    target_groups_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class MitigationMeasureExample(Base):
    __tablename__ = "mitigation_measure_examples"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"), index=True)
    system_hazard_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_hazards.id", ondelete="SET NULL"), index=True
    )
    system_hazard_socio_demographic_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_hazard_socio_demographics.id", ondelete="SET NULL"), index=True
    )
    profile_label: Mapped[str | None] = mapped_column(String(255))
    measure: Mapped[str] = mapped_column(Text, nullable=False)
    policy_case_study: Mapped[str | None] = mapped_column(Text)
    country_city: Mapped[str | None] = mapped_column(String(255))
    implementation_summary: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    reference_links: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="seed", server_default="seed")
    csv_row_number: Mapped[int | None] = mapped_column(Integer)
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


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="main", index=True)
    session_key: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class EurostatPopulationCache(Base):
    __tablename__ = "eurostat_population_cache"
    __table_args__ = (
        UniqueConstraint(
            "country_id",
            "region_id",
            "sector_id",
            "system_hazard_id",
            "profile",
            name="uq_eurostat_population_lookup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    country: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    country_id: Mapped[int | None] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True, index=True
    )
    system_hazard_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_hazards.id", ondelete="CASCADE"), nullable=True, index=True
    )
    profile: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SystemHazardSocioDemographicPopulationMatch(Base):
    __tablename__ = "system_hazard_socio_demographic_population_matches"
    __table_args__ = (
        UniqueConstraint(
            "system_hazard_socio_demographic_id",
            "eurostat_population_cache_id",
            name="uq_system_dg_eurostat_cache_match",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    system_hazard_socio_demographic_id: Mapped[int] = mapped_column(
        ForeignKey("system_hazard_socio_demographics.id", ondelete="CASCADE"),
        index=True,
    )
    eurostat_population_cache_id: Mapped[int | None] = mapped_column(
        ForeignKey("eurostat_population_cache.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    match_status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserActivity(Base):
    __tablename__ = "user_activities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    step: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
