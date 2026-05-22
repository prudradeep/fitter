from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import Country

COUNTRY_DISPLAY_ORDER = ["Germany", "Hungary", "Ireland", "Italy", "Spain", "Portugal"]
COUNTRY_ISO2 = {
    "Germany": "DE",
    "Hungary": "HU",
    "Ireland": "IE",
    "Italy": "IT",
    "Portugal": "PT",
    "Spain": "ES",
}


def get_coverage_rows() -> list[dict[str, str]]:
    order_index = {country: index for index, country in enumerate(COUNTRY_DISPLAY_ORDER)}

    with SessionLocal() as db:
        countries = db.scalars(
            select(Country).options(selectinload(Country.sectors)).order_by(Country.name)
        ).all()

    sorted_countries = sorted(
        countries,
        key=lambda country: (order_index.get(country.name, len(order_index)), country.name),
    )

    return [
        {
            "coverage": country.name,
            "sectors": ", ".join(
                sector.name for sector in sorted(country.sectors, key=lambda item: item.name)
            )
            or "Not configured",
        }
        for country in sorted_countries
    ]


def get_coverage_map_rows() -> list[dict[str, str]]:
    return [
        {
            "code": COUNTRY_ISO2[row["coverage"]],
            "country": row["coverage"],
            "sectors": row["sectors"],
        }
        for row in get_coverage_rows()
        if row["coverage"] in COUNTRY_ISO2
    ]
