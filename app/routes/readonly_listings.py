from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import require_current_user
from app.db.session import get_db
from app.models import (
    AdditionalHazard,
    AdditionalHazardProfile,
    AdditionalHazardProfileTargetPopulation,
    AppUser,
    Country,
    CountrySector,
    EurostatPopulationCache,
    CustomHazard,
    MitigationMeasurePolicy,
    MitigationMeasurePolicyAdditionalHazard,
    MitigationMeasurePolicySystemHazard,
    MitigationMeasureTargetGroup,
    OriginalPolicy,
    OriginalPolicyCode,
    OriginalPolicySector,
    OriginalPolicySystemHazard,
    Policy,
    PolicyHazardLink,
    QuestionOption,
    Region,
    Sector,
    SystemHazard,
    SystemHazardSocioDemographic,
    SystemHazardSocioDemographicTargetPopulation,
    UserHazardSocioDemographic,
    UserSession,
)
from app.services.eurostat_service import population_percentages
router = APIRouter(
    prefix="/api/v1/listings",
    tags=["Read-only listings"],
    dependencies=[Depends(require_current_user)],
)


def _page(limit: int, offset: int) -> dict[str, int]:
    return {"limit": limit, "offset": offset}


@router.get("/countries", summary="List countries", description="Lists available countries. Use the returned country IDs to filter regions, sectors, policies, hazards, and Eurostat data.")
def list_countries(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    rows = db.scalars(
        select(Country).order_by(Country.name, Country.id).offset(offset).limit(limit)
    ).all()
    return {
        **_page(limit, offset),
        "items": [
            {"id": row.id, "name": row.name, "map_code": row.map_code}
            for row in rows
        ],
    }


@router.get("/regions", summary="List regions", description="Lists regions, optionally filtered by country_id.")
def list_regions(
    country_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(Region, Country.name).join(Country, Country.id == Region.country_id)
    if country_id:
        query = query.where(Region.country_id == country_id)
    rows = db.execute(
        query.order_by(Country.name, Region.name, Region.id).offset(offset).limit(limit)
    ).all()
    return {
        **_page(limit, offset),
        "items": [
            {
                "id": region.id,
                "name": region.name,
                "country_id": region.country_id,
                "country": country_name,
            }
            for region, country_name in rows
        ],
    }


@router.get("/sectors", summary="List sectors", description="Lists sectors, optionally filtered to those assigned to country_id.")
def list_sectors(
    country_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(Sector)
    if country_id:
        query = query.join(CountrySector, CountrySector.sector_id == Sector.id).where(
            CountrySector.country_id == country_id
        )
    rows = db.scalars(query.order_by(Sector.name, Sector.id).offset(offset).limit(limit)).all()
    return {
        **_page(limit, offset),
        "items": [{"id": row.id, "name": row.name} for row in rows],
    }


@router.get("/policies", summary="List policies", description="Lists standard and original policies, optionally filtered by country_id and sector_id. Policies created by other users are excluded.")
def list_policies(
    country_id: str | None = Query(default=None, min_length=1),
    sector_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    policy_query = (
        select(Policy, Country.name, Sector.name)
        .join(Country, Country.id == Policy.country_id)
        .join(Sector, Sector.id == Policy.sector_id)
        .where(or_(Policy.created_by_user_id.is_(None), Policy.created_by_user_id == current_user.id))
    )
    if country_id:
        policy_query = policy_query.where(Policy.country_id == country_id)
    if sector_id:
        policy_query = policy_query.where(Policy.sector_id == sector_id)
    policy_rows = db.execute(
        policy_query.order_by(Country.name, Sector.name, Policy.policy, Policy.id)
        .offset(offset)
        .limit(limit)
    ).all()

    original_query = (
        select(OriginalPolicy, Country.name)
        .join(Country, Country.id == OriginalPolicy.country_id)
    )
    if sector_id:
        original_query = original_query.join(
            OriginalPolicySector,
            OriginalPolicySector.original_policy_id == OriginalPolicy.id,
        ).where(OriginalPolicySector.sector_id == sector_id)
    if country_id:
        original_query = original_query.where(OriginalPolicy.country_id == country_id)
    original_rows = db.execute(
        original_query.order_by(Country.name, OriginalPolicy.policy_name, OriginalPolicy.id)
        .offset(offset)
        .limit(limit)
    ).all()
    original_ids = [row.id for row, _ in original_rows]
    codes_by_policy: dict[str, list[str]] = {policy_id: [] for policy_id in original_ids}
    sectors_by_policy: dict[str, list[dict[str, str]]] = {policy_id: [] for policy_id in original_ids}
    if original_ids:
        for policy_id, code in db.execute(
            select(OriginalPolicyCode.original_policy_id, OriginalPolicyCode.code)
            .where(OriginalPolicyCode.original_policy_id.in_(original_ids))
            .order_by(OriginalPolicyCode.code)
        ):
            codes_by_policy[policy_id].append(code)
        for policy_id, sid, sector_name in db.execute(
            select(OriginalPolicySector.original_policy_id, Sector.id, Sector.name)
            .join(Sector, Sector.id == OriginalPolicySector.sector_id)
            .where(OriginalPolicySector.original_policy_id.in_(original_ids))
            .order_by(Sector.name)
        ):
            sectors_by_policy[policy_id].append({"id": sid, "name": sector_name})

    return {
        **_page(limit, offset),
        "policies": [
            {
                "id": row.id,
                "name": row.policy,
                "country_id": row.country_id,
                "country": country_name,
                "sector_id": row.sector_id,
                "sector": sector_name,
                "url": row.policy_url,
                "language": row.language,
                "policy_type": row.policy_type,
                "source": row.source,
                "is_crowd_sourced": bool(row.is_crowd_sourced),
            }
            for row, country_name, sector_name in policy_rows
        ],
        "original_policies": [
            {
                "id": row.id,
                "name": row.policy_name,
                "country_id": row.country_id,
                "country": country_name,
                "objective": row.objective,
                "policy_document": row.policy_document,
                "sectors": sectors_by_policy.get(row.id, []),
                "codes": codes_by_policy.get(row.id, []),
            }
            for row, country_name in original_rows
        ],
    }


@router.get("/hazards", summary="List hazards", description="Lists system, additional, and owner-visible custom hazards. Use policy_id to return hazards linked to a policy.")
def list_hazards(
    country_id: str | None = Query(default=None, min_length=1),
    sector_id: str | None = Query(default=None, min_length=1),
    region_id: str | None = Query(default=None, min_length=1),
    policy_id: str | None = Query(default=None, min_length=1),
    include_additional: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    system_query = select(SystemHazard, Sector.name).join(Sector, Sector.id == SystemHazard.sector_id)
    if policy_id:
        linked_system_hazards = select(PolicyHazardLink.system_hazard_id).where(
            PolicyHazardLink.policy_id == policy_id,
            PolicyHazardLink.system_hazard_id.is_not(None),
        )
        linked_original_system_hazards = (
            select(OriginalPolicySystemHazard.system_hazard_id)
            .join(
                OriginalPolicyCode,
                OriginalPolicyCode.id == OriginalPolicySystemHazard.original_policy_code_id,
            )
            .where(OriginalPolicyCode.original_policy_id == policy_id)
        )
        system_query = system_query.where(
            or_(
                SystemHazard.id.in_(linked_system_hazards),
                SystemHazard.id.in_(linked_original_system_hazards),
            )
        )
    if sector_id:
        system_query = system_query.where(SystemHazard.sector_id == sector_id)
    if country_id:
        system_query = system_query.join(CountrySector, CountrySector.sector_id == Sector.id).where(
            CountrySector.country_id == country_id
        )
    system_rows = db.execute(system_query.order_by(Sector.name, SystemHazard.name, SystemHazard.id)).all()
    items: list[dict[str, object]] = [
        {
            "id": row.id,
            "name": row.name,
            "type": "system",
            "country_id": country_id,
            "sector_id": row.sector_id,
            "sector": sector_name,
            "source": "system",
        }
        for row, sector_name in system_rows
    ]
    if include_additional:
        additional_query = (
            select(AdditionalHazard, Country.name, Sector.name)
            .join(Country, Country.id == AdditionalHazard.country_id)
            .join(Sector, Sector.id == AdditionalHazard.sector_id)
        )
        if country_id:
            additional_query = additional_query.where(AdditionalHazard.country_id == country_id)
        if sector_id:
            additional_query = additional_query.where(AdditionalHazard.sector_id == sector_id)
        if policy_id:
            linked_additional_hazards = select(PolicyHazardLink.additional_hazard_id).where(
                PolicyHazardLink.policy_id == policy_id,
                PolicyHazardLink.additional_hazard_id.is_not(None),
            )
            additional_query = additional_query.where(AdditionalHazard.id.in_(linked_additional_hazards))
        items.extend(
            {
                "id": row.id,
                "name": row.name,
                "type": "additional",
                "country_id": row.country_id,
                "country": country_name,
                "sector_id": row.sector_id,
                "sector": sector_name,
                "source": row.source,
            }
            for row, country_name, sector_name in additional_query.order_by(
                Country.name, Sector.name, AdditionalHazard.name, AdditionalHazard.id
            ).all()
        )
    custom_query = (
        select(CustomHazard, Country.name, Sector.name)
        .join(Country, Country.id == CustomHazard.country_id)
        .join(Sector, Sector.id == CustomHazard.sector_id)
        .where(
            or_(
                CustomHazard.created_by_user_id.is_(None),
                CustomHazard.created_by_user_id == current_user.id,
            )
        )
    )
    if country_id:
        custom_query = custom_query.where(CustomHazard.country_id == country_id)
    if sector_id:
        custom_query = custom_query.where(CustomHazard.sector_id == sector_id)
    if region_id:
        custom_query = custom_query.where(CustomHazard.region_id == region_id)
    if policy_id:
        custom_query = custom_query.where(CustomHazard.policy_id == policy_id)
    items.extend(
        {
            "id": row.id,
            "name": row.name,
            "type": "custom",
            "country_id": row.country_id,
            "country": country_name,
            "sector_id": row.sector_id,
            "sector": sector_name,
            "region_id": row.region_id,
            "source": row.source,
            "reason": row.reason,
            "evidence": row.evidence,
        }
        for row, country_name, sector_name in custom_query.order_by(
            Country.name, Sector.name, CustomHazard.name, CustomHazard.id
        ).all()
    )
    items.sort(key=lambda item: (str(item.get("country") or ""), str(item.get("sector") or ""), str(item["name"])))
    return {**_page(limit, offset), "items": items[offset : offset + limit]}


@router.get("/socio-demographics", summary="List socio-demographic profiles", description="Lists matching system and additional-hazard profiles. Include an owned session_key to add that session's profiles; at least one filter is required.")
def list_socio_demographics(
    country_id: str | None = Query(default=None, min_length=1),
    region_id: str | None = Query(default=None, min_length=1),
    sector_id: str | None = Query(default=None, min_length=1),
    hazard_id: str | None = Query(default=None, min_length=1),
    session_key: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AppUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not any((country_id, region_id, sector_id, hazard_id, session_key)):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of country_id, region_id, sector_id, hazard_id, or session_key.",
        )
    if region_id and not session_key:
        raise HTTPException(
            status_code=422,
            detail="region_id can only be used with an owned session_key for session-specific profiles.",
        )
    user_session = None
    if session_key:
        user_session = db.scalar(
            select(UserSession).where(
                UserSession.session_key == session_key,
                UserSession.user_id == current_user.id,
            )
        )
        if user_session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        country_id = country_id or user_session.country_id
        region_id = region_id or user_session.region_id
        sector_id = sector_id or user_session.sector_id
    system_query = (
        select(SystemHazardSocioDemographic, SystemHazard.name, Sector.name)
        .join(SystemHazard, SystemHazard.id == SystemHazardSocioDemographic.system_hazard_id)
        .join(Sector, Sector.id == SystemHazard.sector_id)
    )
    if sector_id:
        system_query = system_query.where(SystemHazard.sector_id == sector_id)
    if hazard_id:
        system_query = system_query.where(SystemHazardSocioDemographic.system_hazard_id == hazard_id)
    if country_id:
        system_query = system_query.join(CountrySector, CountrySector.sector_id == Sector.id).where(
            CountrySector.country_id == country_id
        )
    system_rows = db.execute(
        system_query.order_by(Sector.name, SystemHazard.name, SystemHazardSocioDemographic.profile)
    ).all()
    system_profile_ids = [profile.id for profile, *_ in system_rows]
    system_target_populations: dict[str, list[str]] = {
        profile_id: [] for profile_id in system_profile_ids
    }
    if system_profile_ids:
        for profile_id, option in db.execute(
            select(
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id == SystemHazardSocioDemographicTargetPopulation.question_option_id,
            )
            .where(
                SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id.in_(
                    system_profile_ids
                )
            )
            .order_by(QuestionOption.option)
        ):
            system_target_populations[profile_id].append(option)
    items: list[dict[str, object]] = [
        {
            "id": profile.id,
            "type": "system",
            "hazard_id": profile.system_hazard_id,
            "hazard": hazard_name,
            "country_id": country_id,
            "sector_id": profile.sector_id or sector_id,
            "sector": sector_name,
            "variable_name": profile.variable_name,
            "variable_type": profile.variable_type,
            "profile": profile.profile,
            "explanation": profile.explanation,
            "statistical_basis": profile.statistical_basis,
            "target_populations": system_target_populations.get(profile.id, []),
            "source": profile.source,
        }
        for profile, hazard_name, sector_name in system_rows
    ]

    additional_query = (
        select(AdditionalHazardProfile, AdditionalHazard, Country.name, Sector.name)
        .join(AdditionalHazard, AdditionalHazard.id == AdditionalHazardProfile.additional_hazard_id)
        .join(Country, Country.id == AdditionalHazard.country_id)
        .join(Sector, Sector.id == AdditionalHazard.sector_id)
    )
    if country_id:
        additional_query = additional_query.where(AdditionalHazard.country_id == country_id)
    if sector_id:
        additional_query = additional_query.where(AdditionalHazard.sector_id == sector_id)
    if hazard_id:
        additional_query = additional_query.where(AdditionalHazardProfile.additional_hazard_id == hazard_id)
    additional_rows = additional_query.order_by(
        Country.name, Sector.name, AdditionalHazard.name, AdditionalHazardProfile.profile
    ).all()
    additional_profile_ids = [profile.id for profile, *_ in additional_rows]
    target_populations: dict[str, list[str]] = {profile_id: [] for profile_id in additional_profile_ids}
    if additional_profile_ids:
        for profile_id, option in db.execute(
            select(AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id, QuestionOption.option)
            .join(QuestionOption, QuestionOption.id == AdditionalHazardProfileTargetPopulation.question_option_id)
            .where(AdditionalHazardProfileTargetPopulation.additional_hazard_profile_id.in_(additional_profile_ids))
            .order_by(QuestionOption.option)
        ):
            target_populations[profile_id].append(option)
    items.extend(
        {
            "id": profile.id,
            "type": "additional",
            "hazard_id": hazard.id,
            "hazard": hazard.name,
            "country_id": hazard.country_id,
            "country": country_name,
            "sector_id": hazard.sector_id,
            "sector": sector_name,
            "profile": profile.profile,
            "evidence": profile.evidence,
            "reference": profile.reference,
            "target_populations": target_populations.get(profile.id, []),
            "source": profile.source,
        }
        for profile, hazard, country_name, sector_name in additional_rows
    )

    if user_session is not None:
        user_query = select(UserHazardSocioDemographic).where(
            UserHazardSocioDemographic.user_session_id == user_session.id
        )
        if sector_id:
            user_query = user_query.where(UserHazardSocioDemographic.sector_id == sector_id)
        if country_id:
            user_query = user_query.where(UserHazardSocioDemographic.country_id == country_id)
        if region_id:
            user_query = user_query.where(UserHazardSocioDemographic.region_id == region_id)
        if hazard_id:
            user_query = user_query.where(
                or_(
                    UserHazardSocioDemographic.system_hazard_id == hazard_id,
                    UserHazardSocioDemographic.additional_hazard_id == hazard_id,
                    UserHazardSocioDemographic.custom_hazard_id == hazard_id,
                )
            )
        items.extend(
            {
                "id": row.id,
                "type": "session",
                "hazard_id": row.system_hazard_id or row.additional_hazard_id or row.custom_hazard_id,
                "country_id": row.country_id,
                "region_id": row.region_id,
                "sector_id": row.sector_id,
                "variable_name": row.variable_name,
                "profile": row.profile,
                "explanation": row.explanation,
                "statistical_basis": row.statistical_basis,
                "source": row.source,
                "reason": row.reason,
                "evidence": row.evidence,
            }
            for row in user_query.order_by(UserHazardSocioDemographic.created_at, UserHazardSocioDemographic.id).all()
        )
    items.sort(key=lambda item: (str(item.get("sector") or ""), str(item.get("hazard") or ""), str(item.get("profile") or "")))
    return {**_page(limit, offset), "items": items[offset : offset + limit]}


@router.get("/eurostat", summary="List cached Eurostat population data", description="Returns matching cached population-analysis data without calling the external API. At least one context filter is required.")
def list_eurostat_data(
    country_id: str | None = Query(default=None, min_length=1),
    region_id: str | None = Query(default=None, min_length=1),
    sector_id: str | None = Query(default=None, min_length=1),
    hazard_id: str | None = Query(default=None, min_length=1),
    profile: str | None = Query(default=None, min_length=1, max_length=255),
    include_expired: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not any((country_id, region_id, sector_id, hazard_id, profile)):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of country_id, region_id, sector_id, hazard_id, or profile.",
        )
    query = select(EurostatPopulationCache)
    for column, value in (
        (EurostatPopulationCache.country_id, country_id),
        (EurostatPopulationCache.region_id, region_id),
        (EurostatPopulationCache.sector_id, sector_id),
        (EurostatPopulationCache.system_hazard_id, hazard_id),
        (EurostatPopulationCache.profile, profile),
    ):
        if value:
            query = query.where(column == value)
    if not include_expired:
        query = query.where(EurostatPopulationCache.expires_at > datetime.now(timezone.utc).replace(tzinfo=None))
    rows = db.scalars(
        query.order_by(EurostatPopulationCache.country, EurostatPopulationCache.region,
                       EurostatPopulationCache.profile, EurostatPopulationCache.id)
        .offset(offset)
        .limit(limit)
    ).all()
    items: list[dict[str, object]] = []
    for row in rows:
        try:
            payload = json.loads(row.response_json)
            if not isinstance(payload, dict):
                payload = {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        eurostat = payload.get("data_set", {}).get("eurostat", {}) if isinstance(payload.get("data_set"), dict) else {}
        items.append(
            {
                "id": row.id,
                "country_id": row.country_id,
                "country": row.country,
                "region_id": row.region_id,
                "region": row.region,
                "sector_id": row.sector_id,
                "hazard_id": row.system_hazard_id,
                "profile": row.profile,
                "regional_population_pct": population_percentages(payload)["regional_pct"],
                "national_population_pct": population_percentages(payload)["national_pct"],
                "dataset_code": eurostat.get("dataset_code") if isinstance(eurostat, dict) else None,
                "indicator_label": eurostat.get("indicator_label") if isinstance(eurostat, dict) else None,
                "data": eurostat,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return {**_page(limit, offset), "items": items}


@router.get("/mitigation-measures", summary="List mitigation measures", description="Lists measures with their target groups and hazard links, optionally filtered by country_id, sector_id, and hazard_id.")
def list_mitigation_measures(
    country_id: str | None = Query(default=None, min_length=1),
    sector_id: str | None = Query(default=None, min_length=1),
    hazard_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(MitigationMeasurePolicy)
    if country_id:
        query = query.where(MitigationMeasurePolicy.country_id == country_id)
    if sector_id:
        query = query.where(MitigationMeasurePolicy.sector_id == sector_id)
    if hazard_id:
        query = query.where(
            or_(
                MitigationMeasurePolicy.id.in_(
                    select(MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id).where(
                        MitigationMeasurePolicySystemHazard.system_hazard_id == hazard_id
                    )
                ),
                MitigationMeasurePolicy.id.in_(
                    select(MitigationMeasurePolicyAdditionalHazard.mitigation_measure_policy_id).where(
                        MitigationMeasurePolicyAdditionalHazard.additional_hazard_id == hazard_id
                    )
                ),
            )
        )
    rows = db.scalars(
        query.order_by(MitigationMeasurePolicy.policy_title, MitigationMeasurePolicy.id)
        .offset(offset)
        .limit(limit)
    ).all()
    ids = [row.id for row in rows]
    target_groups: dict[str, list[str]] = {measure_id: [] for measure_id in ids}
    system_hazards: dict[str, list[dict[str, str | None]]] = {measure_id: [] for measure_id in ids}
    additional_hazards: dict[str, list[dict[str, str]]] = {measure_id: [] for measure_id in ids}
    if ids:
        for measure_id, option in db.execute(
            select(MitigationMeasureTargetGroup.mitigation_measure_policy_id, QuestionOption.option)
            .join(QuestionOption, QuestionOption.id == MitigationMeasureTargetGroup.question_option_id)
            .where(MitigationMeasureTargetGroup.mitigation_measure_policy_id.in_(ids))
            .order_by(QuestionOption.option)
        ):
            target_groups[measure_id].append(option)
        for measure_id, hazard_id_row, hazard_name, mitigation_effect in db.execute(
            select(
                MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id,
                SystemHazard.id,
                SystemHazard.name,
                MitigationMeasurePolicySystemHazard.mitigation_effect,
            )
            .join(SystemHazard, SystemHazard.id == MitigationMeasurePolicySystemHazard.system_hazard_id)
            .where(MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id.in_(ids))
            .order_by(SystemHazard.name)
        ):
            system_hazards[measure_id].append(
                {"id": hazard_id_row, "name": hazard_name, "mitigation_effect": mitigation_effect}
            )
        for measure_id, hazard_id_row, hazard_name in db.execute(
            select(
                MitigationMeasurePolicyAdditionalHazard.mitigation_measure_policy_id,
                AdditionalHazard.id,
                AdditionalHazard.name,
            )
            .join(AdditionalHazard, AdditionalHazard.id == MitigationMeasurePolicyAdditionalHazard.additional_hazard_id)
            .where(MitigationMeasurePolicyAdditionalHazard.mitigation_measure_policy_id.in_(ids))
            .order_by(AdditionalHazard.name)
        ):
            additional_hazards[measure_id].append({"id": hazard_id_row, "name": hazard_name})
    return {
        **_page(limit, offset),
        "items": [
            {
                "id": row.id,
                "policy_code": row.policy_code,
                "title": row.policy_title,
                "country_id": row.country_id,
                "sector_id": row.sector_id,
                "policy_type": row.policy_type,
                "description": row.short_description,
                "source": row.source,
                "target_groups": target_groups.get(row.id, []),
                "system_hazards": system_hazards.get(row.id, []),
                "additional_hazards": additional_hazards.get(row.id, []),
            }
            for row in rows
        ],
    }
