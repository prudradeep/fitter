from sqlalchemy import distinct, func, select
from sqlalchemy.orm import selectinload

from app.db.session import SessionLocal
from app.models import Country, CountrySector, SystemHazard, UserSession
from app.services.hazard_salience import top_hazard_salience_by_country

COUNTRY_DISPLAY_ORDER = ["Germany", "Hungary", "Ireland", "Italy", "Spain", "Portugal"]


def get_coverage_rows() -> list[dict[str, object]]:
    order_index = {country: index for index, country in enumerate(COUNTRY_DISPLAY_ORDER)}
    salience_by_country = top_hazard_salience_by_country(limit=3)

    with SessionLocal() as db:
        countries = db.scalars(
            select(Country).options(selectinload(Country.sectors)).order_by(Country.name)
        ).all()
        hazard_counts = dict(
            db.execute(
                select(CountrySector.country_id, func.count(distinct(SystemHazard.id)))
                .join(SystemHazard, SystemHazard.sector_id == CountrySector.sector_id)
                .group_by(CountrySector.country_id)
            ).all()
        )
        analysis_counts = dict(
            db.execute(
                select(UserSession.country_id, func.count(distinct(UserSession.id)))
                .where(
                    UserSession.country_id.is_not(None),
                    UserSession.sector_id.is_not(None),
                )
                .group_by(UserSession.country_id)
            ).all()
        )

    sorted_countries = sorted(
        countries,
        key=lambda country: (order_index.get(country.name, len(order_index)), country.name),
    )

    return [
        {
            "coverage": country.name,
            "code": country.map_code or "",
            "map_path": country.map_path or "",
            "hazards": int(hazard_counts.get(country.id, 0)),
            "analyses": int(analysis_counts.get(country.id, 0)),
            "sectors": ", ".join(
                sector.name for sector in sorted(country.sectors, key=lambda item: item.name)
            )
            or "Not configured",
            "top_hazard_salience": salience_by_country.get(country.name, []),
        }
        for country in sorted_countries
    ]


def get_coverage_map_rows() -> list[dict[str, object]]:
    return [
        {
            "code": row["code"],
            "country": row["coverage"],
            "sectors": row["sectors"],
            "map_path": row["map_path"],
            "hazards": row["hazards"],
            "analyses": row["analyses"],
            "top_hazard_salience": row["top_hazard_salience"],
        }
        for row in get_coverage_rows()
        if row["code"]
    ]
