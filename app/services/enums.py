from enum import StrEnum


class AppStrEnum(StrEnum):
    @classmethod
    def coerce(cls, value, default=None):
        if isinstance(value, cls):
            return value
        text = str(value or "").strip()
        for member in cls:
            if text == member.value or text == member.name:
                return member
            if text.casefold() in {member.value.casefold(), member.name.casefold()}:
                return member
        if default is not None:
            return default
        raise ValueError(f"Unknown {cls.__name__}: {value!r}")

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]


class ConfidenceLevel(AppStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CustomHazardAction(AppStrEnum):
    ASK_CLARIFICATION = "ask_clarification"
    ASK_DUPLICATE_CONFIRMATION = "ask_duplicate_confirmation"
    REVIEW_GROUPS = "review_groups"
    VALIDATE = "validate"
    REJECT = "reject"


class CustomHazardStatus(AppStrEnum):
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_DUPLICATE_CONFIRMATION = "needs_duplicate_confirmation"
    NEEDS_GROUP_REVIEW = "needs_group_review"
    READY = "ready"
    REJECTED = "rejected"


class GroundingStatus(AppStrEnum):
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    WARNING = "WARNING"
    CONFIRMED = "CONFIRMED"
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS CLARIFICATION"
    INSUFFICIENT_INFO = "INSUFFICIENT INFO"


class CustomHazardDimension(AppStrEnum):
    HAZARD_DEFINITION_FIT = "hazard_definition_fit"
    TWIN_TRANSITION_POLICY_FIT = "twin_transition_policy_fit"
    SELECTED_SECTOR_FIT = "selected_sector_fit"
    COUNTRY_REGION_FIT = "country_region_fit"
    AFFECTED_GROUPS_FIT = "affected_groups_fit"


class ChatPhase(AppStrEnum):
    WIZARD = "wizard"
    HAZARDS = "hazards"
    STATS_DEEP_DIVE = "stats_deep_dive"
    TARGET_POPULATION_QUESTION = "target_population_question"
    HAZARD_PROFILE_SELECTION = "hazard_profile_selection"
    SOCIO_DEMOGRAPHIC_REVIEW = "socio_demographic_review"
    REASON_CONFIRMATION = "reason_confirmation"
    OTHER_ACTIONS = "other_actions"
    ADD_DGS = "add_dgs"
    DG_REASON_EVIDENCE = "dg_reason_evidence"
    MITIGATION = "mitigation"
    MITIGATION_MEASURE = "mitigation_measure"
    MITIGATION_DUPLICATE_SUGGESTION = "mitigation_duplicate_suggestion"
    MITIGATION_DUPLICATE_REPORT = "mitigation_duplicate_report"
    MITIGATION_REASON = "mitigation_reason"
    MITIGATION_CLARITY = "mitigation_clarity"
    MITIGATION_EVIDENCE_DECISION = "mitigation_evidence_decision"
    MITIGATION_EVIDENCE_INPUT = "mitigation_evidence_input"
    MITIGATION_TARGET_POPULATION = "mitigation_target_population"
    MITIGATION_TARGET_POPULATION_REVIEW = "mitigation_target_population_review"
    MITIGATION_REVIEW = "mitigation_review"
    IMPLEMENTATION_CHALLENGE_DISCUSSION = "implementation_challenge_discussion"
    IMPLEMENTATION_READINESS_ASSESSMENT = "implementation_readiness_assessment"
    EVALUATION_QUESTION = "evaluation_question"
    EVALUATION_COMPLETE = "evaluation_complete"
    SYSTEM_INQUIRY_INTRO = "system_inquiry_intro"
    SYSTEM_INQUIRY_OBSERVATION = "system_inquiry_observation"
    SYSTEM_INQUIRY_FOLLOWUP = "system_inquiry_followup"
    SYSTEM_INQUIRY_COMPLETE = "system_inquiry_complete"
    ADD_HAZARD = "add_hazard"
    ADD_HAZARD_REASON = "add_hazard_reason"
    ADD_HAZARD_EVIDENCE_DECISION = "add_hazard_evidence_decision"
    ADD_HAZARD_EVIDENCE_INPUT = "add_hazard_evidence_input"
    ADD_HAZARD_EVIDENCE = "add_hazard_evidence"
    ADD_HAZARD_CLARIFICATION = "add_hazard_clarification"
    HAZARD_DUPLICATE_SUGGESTION = "hazard_duplicate_suggestion"
    CUSTOM_HAZARD_INPUT = "custom_hazard_input"
    CUSTOM_HAZARD_TITLE_CLARIFICATION = "custom_hazard_title_clarification"
    CUSTOM_HAZARD_VALIDATION = "custom_hazard_validation"
    CUSTOM_HAZARD_DIMENSION_CHECK = "custom_hazard_dimension_check"
    CUSTOM_HAZARD_CLARIFICATION = "custom_hazard_clarification"
    CUSTOM_HAZARD_DUPLICATE_CONFIRMATION = "custom_hazard_duplicate_confirmation"
    CUSTOM_HAZARD_GROUP_REVIEW = "custom_hazard_group_review"
    CUSTOM_HAZARD_POPULATION_REVIEW = "custom_hazard_population_review"
    CUSTOM_HAZARD_PROFILE_REASON = "custom_hazard_profile_reason"
    CUSTOM_HAZARD_SUMMARY_REVIEW = "custom_hazard_summary_review"
    HAZARD_POPULATION_REGION_COMPARISON = "hazard_population_region_comparison"
