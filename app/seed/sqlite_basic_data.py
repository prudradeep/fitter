from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.db.session import engine

logger = logging.getLogger(__name__)
_NAMESPACE = uuid.UUID("e7607d55-9c8d-4d64-a949-388b44b1ee8e")

COUNTRIES = [['Germany', 'DE', 'countries/de/de-all.geo.json'], ['Hungary', 'HU', 'countries/hu/hu-all.geo.json'], ['Ireland', 'IE', 'countries/ie/ie-all.geo.json'], ['Italy', 'IT', 'countries/it/it-all.geo.json'], ['Portugal', 'PT', 'countries/pt/pt-all.geo.json'], ['Spain', 'ES', 'countries/es/es-all.geo.json']]

SECTORS = [['Energy'], ['Housing'], ['Transport']]

REGIONS = [['Germany', 'Baden-Württemberg'], ['Germany', 'Bavaria'], ['Germany', 'Berlin'], ['Germany', 'Brandenburg'], ['Germany', 'Bremen'], ['Germany', 'Hamburg'], ['Germany', 'Hesse'], ['Germany', 'Lower Saxony'], ['Germany', 'Mecklenburg-Vorpommern'], ['Germany', 'North Rhine-Westphalia'], ['Germany', 'Rhineland-Palatinate'], ['Germany', 'Saarland'], ['Germany', 'Saxony'], ['Germany', 'Saxony-Anhalt'], ['Germany', 'Schleswig-Holstein'], ['Germany', 'Thuringia'], ['Hungary', 'Baranya'], ['Hungary', 'Borsod-Abaúj-Zemplén'], ['Hungary', 'Budapest'], ['Hungary', 'Csongrád-Csanád'], ['Hungary', 'Fejér'], ['Hungary', 'Győr-Moson-Sopron'], ['Hungary', 'Hajdú-Bihar'], ['Hungary', 'Heves'], ['Hungary', 'Jász-Nagykun-Szolnok'], ['Hungary', 'Komárom-Esztergom'], ['Hungary', 'Nógrád'], ['Hungary', 'Pest'], ['Hungary', 'Somogy'], ['Hungary', 'Szabolcs-Szatmár-Bereg'], ['Hungary', 'Tolna'], ['Hungary', 'Vas'], ['Hungary', 'Veszprém'], ['Hungary', 'Zala'], ['Ireland', 'Clare'], ['Ireland', 'Connacht'], ['Ireland', 'Cork'], ['Ireland', 'Dublin'], ['Ireland', 'Galway'], ['Ireland', 'Kerry'], ['Ireland', 'Kilkenny'], ['Ireland', 'Leinster'], ['Ireland', 'Limerick'], ['Ireland', 'Mayo'], ['Ireland', 'Munster'], ['Ireland', 'Sligo'], ['Ireland', 'Tipperary'], ['Ireland', 'Ulster (ROI)'], ['Ireland', 'Waterford'], ['Ireland', 'Wicklow'], ['Italy', 'Abruzzo'], ['Italy', 'Aosta Valley'], ['Italy', 'Basilicata'], ['Italy', 'Calabria'], ['Italy', 'Campania'], ['Italy', 'Emilia-Romagna'], ['Italy', 'Friuli-Venezia Giulia'], ['Italy', 'Lazio'], ['Italy', 'Liguria'], ['Italy', 'Lombardy'], ['Italy', 'Marche'], ['Italy', 'Molise'], ['Italy', 'Piedmont'], ['Italy', 'Puglia'], ['Italy', 'Sardinia'], ['Italy', 'Sicily'], ['Italy', 'Trentino-Alto Adige'], ['Italy', 'Tuscany'], ['Italy', 'Umbria'], ['Italy', 'Veneto'], ['Spain', 'Andalusia'], ['Spain', 'Aragon'], ['Spain', 'Asturias'], ['Spain', 'Balearic Islands'], ['Spain', 'Basque Country'], ['Spain', 'Canary Islands'], ['Spain', 'Cantabria'], ['Spain', 'Castile and León'], ['Spain', 'Castile-La Mancha'], ['Spain', 'Catalonia'], ['Spain', 'Extremadura'], ['Spain', 'Galicia'], ['Spain', 'La Rioja'], ['Spain', 'Madrid'], ['Spain', 'Murcia'], ['Spain', 'Navarre'], ['Spain', 'Valencia'], ['Portugal', 'Alentejo'], ['Portugal', 'Algarve'], ['Portugal', 'Azores'], ['Portugal', 'Centro'], ['Portugal', 'Lisbon Metropolitan Area'], ['Portugal', 'Madeira'], ['Portugal', 'Norte']]

COUNTRY_SECTORS = [['Germany', 'Energy'], ['Germany', 'Housing'], ['Hungary', 'Energy'], ['Hungary', 'Housing'], ['Ireland', 'Energy'], ['Ireland', 'Housing'], ['Italy', 'Energy'], ['Italy', 'Transport'], ['Spain', 'Energy'], ['Spain', 'Transport'], ['Portugal', 'Energy'], ['Portugal', 'Housing']]

EVALUATION_QUESTIONS = [['target_population', 1, '', 'Age range', 1], ['target_population', 2, '', 'Living in a house with low energy efficiency', 1], ['target_population', 3, '', 'Gender', 1], ['target_population', 4, '', 'Need of a car to perform daily activities', 1], ['target_population', 5, '', 'Level of education', 1], ['target_population', 6, '', 'Location of residency', 1], ['target_population', 7, '', 'Economic status', 1], ['target_population', 8, '', 'Care responsibility as the main activity', 1], ['target_population', 9, '', 'EU citizenship', 1], ['target_population', 10, '', 'Disability of long-term condition', 1], ['target_population', 11, '', 'Level of income', 1], ['target_population', 12, '', 'Tenancy status', 1], ['The transformative impact', 1, 'Direct effect', '## 1. Direct Effect on Identified Negative Impacts (Weight: ~40%)\n\nTo what extent does the measure directly address the previously identified negative impacts?\n\n- **1** = No clear relevance to the identified problems\n- **5** = Partially relevant; addresses some aspects\n- **10** = Strong, direct alignment with the defined problems', 1], ['The transformative impact', 2, 'Systemic & Structural Impact', '## 2. Systemic & Structural Impact (Weight: ~35%)\n\nTo what extent does the initiative generate broader systemic change (across sectors, institutions, or policies)?\n\n- **1** = Isolated impact; no broader systemic or institutional change\n- **5** = Moderate spillovers or incremental institutional adjustments\n- **10** = Strong cross-sector impact and/or significant changes to governance, regulation, or institutional behavior', 1], ['The transformative impact', 3, 'Societal Transformation & Equity', '## 3. Societal Transformation & Equity (Weight: ~25%)\n\nTo what extent does the initiative influence societal attitudes and reduce inequalities?\n\n- **1** = No influence on attitudes; may worsen inequalities\n- **5** = Some influence on discourse or limited/mixed equity effects\n- **10** = Strong shift in narratives/priorities and clear reduction of inequalities (e.g., accessibility, fairness)', 1], ['Feasibility and Implementation', 1, 'Accessibility', '## 4.1 Barriers in terms of Accessibility\n\nAre there technical, administrative, geographic, digital, or social barriers that may prevent certain groups from accessing the measure on equal terms?\n\n- **1** = Severe technical, administrative, geographic, digital, or social barriers prevent equitable access\n- **5** = Moderate accessibility barriers that require adaptation, support, or targeted interventions\n- **10** = Very few accessibility barriers; the measure is broadly accessible to all affected groups', 1], ['Feasibility and Implementation', 2, 'Affordability', '## 4.2 Barriers in terms of Affordability\n\nIs the measure economically viable for all affected groups, including low-income households or other disadvantaged populations? Are costs, co-payments, or hidden burdens equitably distributed?\n\n- **1** = Severe affordability barriers; costs or financial burdens exclude significant groups\n- **5** = Moderate affordability challenges; support mechanisms or adjustments may be needed\n- **10** = Very few affordability barriers; costs and financial burdens are equitably distributed and broadly manageable', 1], ['Feasibility and Implementation', 3, 'Acceptability', '## 4.3 Barriers in terms of Acceptability\n\nIs the measure socially and politically acceptable to the communities and stakeholders affected? Does it align with local norms, trust levels, and stakeholder expectations?\n\n- **1** = Severe political, cultural, financial, or administrative barriers\n- **5** = Moderate obstacles that require negotiation or adaptation\n- **10** = Very few obstacles; high political, cultural, and financial compatibility', 1], ['Feasibility and Implementation', 4, 'Availability & timing', '## 5. Barriers in terms of Availability / Timing\n\nIs the measure legally, institutionally, and logistically in place for the target population? Are the necessary infrastructures, services, or delivery mechanisms present?\n\nHow suitable is the current moment for implementing this initiative (in terms of readiness, preconditions, and alignment with policy cycles)?\n\n- **1** = Cannot be implemented under current conditions; major prerequisites missing\n- **5** = Some prerequisites missing; partial readiness\n- **10** = Fully implementable immediately with no major preconditions', 1]]

QUESTION_OPTIONS = [['Age range', '<18'], ['Age range', '18-25'], ['Age range', '25-35'], ['Age range', '35-65'], ['Age range', '>65'], ['Living in a house with low energy efficiency', 'Yes'], ['Living in a house with low energy efficiency', 'No'], ['Gender', 'Woman'], ['Gender', 'Male'], ['Gender', 'Non-binary'], ['Gender', 'Other'], ['Need of a car to perform daily activities', 'Yes'], ['Need of a car to perform daily activities', 'No'], ['Level of education', 'No formal education'], ['Level of education', 'Primary'], ['Level of education', 'Secondary'], ['Level of education', 'Further normal education'], ['Location of residency', 'Urban area'], ['Location of residency', 'Suburban area'], ['Location of residency', 'Rural area'], ['Economic status', 'Employed'], ['Economic status', 'Unemployed'], ['Economic status', 'Retired'], ['Care responsibility as the main activity', 'Yes, remunerated'], ['Care responsibility as the main activity', 'Yes, Non-remunerated'], ['Care responsibility as the main activity', 'No'], ['EU citizenship', 'Yes'], ['EU citizenship', 'No'], ['Disability of long-term condition', 'Yes'], ['Disability of long-term condition', 'No'], ['Level of income', 'Low income'], ['Level of income', 'Medium income'], ['Level of income', 'High income'], ['Tenancy status', 'Homeowner'], ['Tenancy status', 'Tenant']]


def _stable_id(kind: str, *parts: object) -> str:
    key = "|".join(str(part).strip().casefold() for part in parts)
    return str(uuid.uuid5(_NAMESPACE, f"{kind}|{key}"))


def seed_sqlite_basic_data() -> dict[str, int]:
    """Seed the canonical bootstrap/reference rows required by an offline SQLite DB.

    This is intentionally idempotent and uses stable UUIDs for rows that do not
    already exist. Existing rows are preserved and updated where appropriate.
    """
    if engine.dialect.name != "sqlite":
        raise RuntimeError("seed_sqlite_basic_data() is only valid for SQLite")

    counts: dict[str, int] = {}
    with engine.begin() as connection:
        country_ids: dict[str, str] = {}
        for name, map_code, map_path in COUNTRIES:
            row = connection.execute(
                text("SELECT id FROM countries WHERE name = :name LIMIT 1"),
                {"name": name},
            ).scalar()
            country_id = str(row) if row else _stable_id("country", name)
            if row:
                connection.execute(
                    text("UPDATE countries SET map_code=:map_code, map_path=:map_path WHERE id=:id"),
                    {"map_code": map_code, "map_path": map_path, "id": country_id},
                )
            else:
                connection.execute(
                    text("INSERT INTO countries (id,name,map_code,map_path) VALUES (:id,:name,:map_code,:map_path)"),
                    {"id": country_id, "name": name, "map_code": map_code, "map_path": map_path},
                )
            country_ids[name.casefold()] = country_id

        sector_ids: dict[str, str] = {}
        for (name,) in SECTORS:
            row = connection.execute(
                text("SELECT id FROM sectors WHERE name = :name LIMIT 1"), {"name": name}
            ).scalar()
            sector_id = str(row) if row else _stable_id("sector", name)
            if not row:
                connection.execute(
                    text("INSERT INTO sectors (id,name) VALUES (:id,:name)"),
                    {"id": sector_id, "name": name},
                )
            sector_ids[name.casefold()] = sector_id

        for country_name, region_name in REGIONS:
            country_id = country_ids[country_name.casefold()]
            existing = connection.execute(
                text("SELECT id FROM regions WHERE country_id=:country_id AND name=:name LIMIT 1"),
                {"country_id": country_id, "name": region_name},
            ).scalar()
            if not existing:
                connection.execute(
                    text("INSERT INTO regions (id,country_id,name) VALUES (:id,:country_id,:name)"),
                    {"id": _stable_id("region", country_name, region_name), "country_id": country_id, "name": region_name},
                )

        for country_name, sector_name in COUNTRY_SECTORS:
            country_id = country_ids[country_name.casefold()]
            sector_id = sector_ids[sector_name.casefold()]
            existing = connection.execute(
                text("SELECT id FROM country_sectors WHERE country_id=:country_id AND sector_id=:sector_id LIMIT 1"),
                {"country_id": country_id, "sector_id": sector_id},
            ).scalar()
            if not existing:
                connection.execute(
                    text("INSERT INTO country_sectors (id,country_id,sector_id) VALUES (:id,:country_id,:sector_id)"),
                    {"id": _stable_id("country-sector", country_name, sector_name), "country_id": country_id, "sector_id": sector_id},
                )

        question_ids: dict[str, str] = {}
        for category, sort_order, chart_title, question, active in EVALUATION_QUESTIONS:
            row = connection.execute(
                text("SELECT id FROM evaluation_questions WHERE category=:category AND sort_order=:sort_order LIMIT 1"),
                {"category": category, "sort_order": int(sort_order)},
            ).scalar()
            question_id = str(row) if row else _stable_id("evaluation-question", category, sort_order, question)
            values = {
                "id": question_id,
                "category": category,
                "chart_title": chart_title or None,
                "question": question,
                "sort_order": int(sort_order),
                "active": bool(active),
            }
            if row:
                connection.execute(
                    text("UPDATE evaluation_questions SET chart_title=:chart_title, question=:question, active=:active WHERE id=:id"),
                    values,
                )
            else:
                connection.execute(
                    text("INSERT INTO evaluation_questions (id,category,chart_title,question,sort_order,active) VALUES (:id,:category,:chart_title,:question,:sort_order,:active)"),
                    values,
                )
            question_ids[question.casefold()] = question_id

        for question, option in QUESTION_OPTIONS:
            question_id = question_ids.get(question.casefold())
            if not question_id:
                raise RuntimeError(f"Question option references unknown question: {question!r}")
            existing = connection.execute(
                text('SELECT id FROM question_options WHERE questionId=:question_id AND "option"=:option LIMIT 1'),
                {"question_id": question_id, "option": option},
            ).scalar()
            if not existing:
                connection.execute(
                    text('INSERT INTO question_options (id,questionId,"option") VALUES (:id,:question_id,:option)'),
                    {"id": _stable_id("question-option", question, option), "question_id": question_id, "option": option},
                )

        for table in (
            "countries", "sectors", "regions", "country_sectors",
            "evaluation_questions", "question_options",
        ):
            counts[table] = int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())

    logger.info("SQLite basic data ready: %s", counts)
    return counts


def validate_sqlite_offline_seed(*, require_reference_data: bool = True) -> dict[str, int]:
    if engine.dialect.name != "sqlite":
        return {}
    minimums = {
        "countries": 6,
        "sectors": 3,
        "regions": 94,
        "country_sectors": 12,
        "evaluation_questions": 19,
        "question_options": 35,
    }
    if require_reference_data:
        minimums.update(
            {
                "additional_hazards": 1,
                "additional_hazard_profiles": 1,
                "system_hazards": 1,
                "mitigation_measure_examples": 1,
                "mitigation_measure_policies": 1,
                "mitigation_measure_target_groups": 1,
                "mitigation_measure_policy_system_hazards": 1,
            }
        )
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table, minimum in minimums.items():
            count = int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
            counts[table] = count
            if count < minimum:
                raise RuntimeError(
                    f"Offline SQLite seed validation failed: {table} has {count} row(s); expected at least {minimum}."
                )
    return counts
