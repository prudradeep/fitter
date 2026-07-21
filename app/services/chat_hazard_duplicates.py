import logging
from typing import Any

from sqlalchemy import and_, or_, select

from app.models import AdditionalHazard, CustomHazard, SystemHazard, UserHazard, UserSession
from app.services.chat_formatters import hazard_names
from app.services.chat_options import compact_for_match, fuzzy_score, normalize_for_match
from app.services.chat_session import ChatSession


logger = logging.getLogger(__name__)


def same_sector_hazard_names(db: Any, session: ChatSession) -> list[str]:
    names: list[object] = [
        *(session.hazards or []),
        *(session.custom_hazards or []),
        *(session.additional_hazards or []),
        *hazard_names(session),
    ]
    if session.sector_id is not None:
        try:
            names.extend(
                db.scalars(
                    select(SystemHazard.name).where(
                        SystemHazard.sector_id == session.sector_id
                    )
                ).all()
            )
            names.extend(
                db.scalars(
                    select(AdditionalHazard.name).where(
                        AdditionalHazard.sector_id == session.sector_id
                    )
                ).all()
            )
            names.extend(
                db.scalars(
                    select(CustomHazard.name).where(
                        CustomHazard.sector_id == session.sector_id
                    )
                ).all()
            )
            names.extend(
                db.scalars(
                    select(UserHazard.name).where(
                        UserHazard.sector_id == session.sector_id
                    )
                ).all()
            )
        except Exception:
            logger.exception("Failed to load same-sector hazards for duplicate check")

    return dedupe_hazard_names(names)


def same_scope_custom_hazard_names(
    db: Any,
    session: ChatSession,
    user_id: str | None,
) -> list[str]:
    names: list[object] = []
    if session.country_id is None or session.sector_id is None:
        return []

    region_scope_key = session.region_id or ""
    try:
        names.extend(
            db.scalars(
                select(CustomHazard.name).where(
                    CustomHazard.country_id == session.country_id,
                    CustomHazard.sector_id == session.sector_id,
                    CustomHazard.region_scope_key == region_scope_key,
                    or_(
                        CustomHazard.created_by_user_id == user_id,
                        and_(
                            CustomHazard.validation_mode == "strict",
                            CustomHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            ).all()
        )
        names.extend(
            db.scalars(
                select(UserHazard.name)
                .join(UserSession, UserSession.id == UserHazard.user_session_id)
                .where(
                    UserHazard.source == "custom",
                    UserHazard.sector_id == session.sector_id,
                    UserHazard.region_id.is_(None)
                    if session.region_id is None
                    else UserHazard.region_id == session.region_id,
                    UserSession.country_id == session.country_id,
                    UserSession.sector_id == session.sector_id,
                    UserSession.region_id.is_(None)
                    if session.region_id is None
                    else UserSession.region_id == session.region_id,
                    or_(
                        UserSession.user_id == user_id,
                        and_(
                            UserHazard.validation_mode == "strict",
                            UserHazard.is_crowd_sourced.is_(True),
                        ),
                    ),
                )
            ).all()
        )
    except Exception:
        logger.exception("Failed to load same-scope custom hazards for duplicate check")

    return dedupe_hazard_names(names)


def dedupe_hazard_names(names: list[object]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        label = str(name or "").strip()
        key = normalize_for_match(label)
        if label and key and key not in seen:
            seen.add(key)
            deduped.append(label)
    return deduped


def local_similar_hazards(hazard: str, existing_hazards: list[str]) -> list[str]:
    query = normalize_for_match(hazard)
    compact_query = compact_for_match(hazard)
    if not query or not compact_query:
        return []

    query_words = hazard_similarity_words(query)
    matches: list[str] = []
    for existing in existing_hazards:
        existing_normalized = normalize_for_match(existing)
        compact_existing = compact_for_match(existing)
        if not existing_normalized or not compact_existing:
            continue

        existing_words = hazard_similarity_words(existing_normalized)
        overlap = len(query_words & existing_words) / max(1, len(query_words))
        reverse_overlap = len(query_words & existing_words) / max(1, len(existing_words))
        is_contained = compact_query in compact_existing or compact_existing in compact_query
        if (
            is_contained
            or overlap >= 0.75
            or reverse_overlap >= 0.75
            or fuzzy_score(hazard, existing) >= 0.82
        ):
            matches.append(existing)

    return list(dict.fromkeys(matches))


def hazard_duplicate_payloads(hazard: str, matches: list[str]) -> list[dict[str, object]]:
    return [
        {
            "existing_hazard": match,
            "similarity_score": round(fuzzy_score(hazard, match) * 100),
            "reason": (
                "The proposed hazard is the same as, or very similar to, "
                "an existing hazard in the selected sector or context."
            ),
        }
        for match in matches[:3]
    ]


def hazard_similarity_words(value: str) -> set[str]:
    words: set[str] = set()
    for word in value.split():
        if len(word) <= 2:
            continue
        if len(word) > 3 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.add(word)
    return words
