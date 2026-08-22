#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
import uuid
from pathlib import Path

NAMESPACE = uuid.UUID("5bb9c36d-4ea6-46d1-b951-0d9632ca6284")

def stable_id(kind: str, *parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, kind + ":" + "|".join(str(p) for p in parts)))

COUNTRIES = [['Germany', 'DE', 'countries/de/de-all.geo.json'],
 ['Hungary', 'HU', 'countries/hu/hu-all.geo.json'],
 ['Ireland', 'IE', 'countries/ie/ie-all.geo.json'],
 ['Italy', 'IT', 'countries/it/it-all.geo.json'],
 ['Portugal', 'PT', 'countries/pt/pt-all.geo.json'],
 ['Spain', 'ES', 'countries/es/es-all.geo.json']]

SECTORS = [['Energy'], ['Housing'], ['Transport']]

REGIONS = [['Germany', 'Baden-Württemberg'],
 ['Germany', 'Bavaria'],
 ['Germany', 'Berlin'],
 ['Germany', 'Brandenburg'],
 ['Germany', 'Bremen'],
 ['Germany', 'Hamburg'],
 ['Germany', 'Hesse'],
 ['Germany', 'Lower Saxony'],
 ['Germany', 'Mecklenburg-Vorpommern'],
 ['Germany', 'North Rhine-Westphalia'],
 ['Germany', 'Rhineland-Palatinate'],
 ['Germany', 'Saarland'],
 ['Germany', 'Saxony'],
 ['Germany', 'Saxony-Anhalt'],
 ['Germany', 'Schleswig-Holstein'],
 ['Germany', 'Thuringia'],
 ['Hungary', 'Baranya'],
 ['Hungary', 'Borsod-Abaúj-Zemplén'],
 ['Hungary', 'Budapest'],
 ['Hungary', 'Csongrád-Csanád'],
 ['Hungary', 'Fejér'],
 ['Hungary', 'Győr-Moson-Sopron'],
 ['Hungary', 'Hajdú-Bihar'],
 ['Hungary', 'Heves'],
 ['Hungary', 'Jász-Nagykun-Szolnok'],
 ['Hungary', 'Komárom-Esztergom'],
 ['Hungary', 'Nógrád'],
 ['Hungary', 'Pest'],
 ['Hungary', 'Somogy'],
 ['Hungary', 'Szabolcs-Szatmár-Bereg'],
 ['Hungary', 'Tolna'],
 ['Hungary', 'Vas'],
 ['Hungary', 'Veszprém'],
 ['Hungary', 'Zala'],
 ['Ireland', 'Clare'],
 ['Ireland', 'Connacht'],
 ['Ireland', 'Cork'],
 ['Ireland', 'Dublin'],
 ['Ireland', 'Galway'],
 ['Ireland', 'Kerry'],
 ['Ireland', 'Kilkenny'],
 ['Ireland', 'Leinster'],
 ['Ireland', 'Limerick'],
 ['Ireland', 'Mayo'],
 ['Ireland', 'Munster'],
 ['Ireland', 'Sligo'],
 ['Ireland', 'Tipperary'],
 ['Ireland', 'Ulster (ROI)'],
 ['Ireland', 'Waterford'],
 ['Ireland', 'Wicklow'],
 ['Italy', 'Abruzzo'],
 ['Italy', 'Aosta Valley'],
 ['Italy', 'Basilicata'],
 ['Italy', 'Calabria'],
 ['Italy', 'Campania'],
 ['Italy', 'Emilia-Romagna'],
 ['Italy', 'Friuli-Venezia Giulia'],
 ['Italy', 'Lazio'],
 ['Italy', 'Liguria'],
 ['Italy', 'Lombardy'],
 ['Italy', 'Marche'],
 ['Italy', 'Molise'],
 ['Italy', 'Piedmont'],
 ['Italy', 'Puglia'],
 ['Italy', 'Sardinia'],
 ['Italy', 'Sicily'],
 ['Italy', 'Trentino-Alto Adige'],
 ['Italy', 'Tuscany'],
 ['Italy', 'Umbria'],
 ['Italy', 'Veneto'],
 ['Spain', 'Andalusia'],
 ['Spain', 'Aragon'],
 ['Spain', 'Asturias'],
 ['Spain', 'Balearic Islands'],
 ['Spain', 'Basque Country'],
 ['Spain', 'Canary Islands'],
 ['Spain', 'Cantabria'],
 ['Spain', 'Castile and León'],
 ['Spain', 'Castile-La Mancha'],
 ['Spain', 'Catalonia'],
 ['Spain', 'Extremadura'],
 ['Spain', 'Galicia'],
 ['Spain', 'La Rioja'],
 ['Spain', 'Madrid'],
 ['Spain', 'Murcia'],
 ['Spain', 'Navarre'],
 ['Spain', 'Valencia'],
 ['Portugal', 'Alentejo'],
 ['Portugal', 'Algarve'],
 ['Portugal', 'Azores'],
 ['Portugal', 'Centro'],
 ['Portugal', 'Lisbon Metropolitan Area'],
 ['Portugal', 'Madeira'],
 ['Portugal', 'Norte']]

COUNTRY_SECTORS = [['Germany', 'Energy'],
 ['Germany', 'Housing'],
 ['Hungary', 'Energy'],
 ['Hungary', 'Housing'],
 ['Ireland', 'Energy'],
 ['Ireland', 'Housing'],
 ['Italy', 'Energy'],
 ['Italy', 'Transport'],
 ['Spain', 'Energy'],
 ['Spain', 'Transport'],
 ['Portugal', 'Energy'],
 ['Portugal', 'Housing']]

EVALUATION_QUESTIONS = [['target_population', 1, '', 'Age range', 1],
 ['target_population', 2, '', 'Living in a house with low energy efficiency', 1],
 ['target_population', 3, '', 'Gender', 1],
 ['target_population', 4, '', 'Need of a car to perform daily activities', 1],
 ['target_population', 5, '', 'Level of education', 1],
 ['target_population', 6, '', 'Location of residency', 1],
 ['target_population', 7, '', 'Economic status', 1],
 ['target_population', 8, '', 'Care responsibility as the main activity', 1],
 ['target_population', 9, '', 'EU citizenship', 1],
 ['target_population', 10, '', 'Disability of long-term condition', 1],
 ['target_population', 11, '', 'Level of income', 1],
 ['target_population', 12, '', 'Tenancy status', 1],
 ['The transformative impact',
  1,
  'Direct effect',
  '## 1. Direct Effect on Identified Negative Impacts (Weight: ~40%)\n'
  '\n'
  'To what extent does the measure directly address the previously identified negative impacts?\n'
  '\n'
  '- **1** = No clear relevance to the identified problems\n'
  '- **5** = Partially relevant; addresses some aspects\n'
  '- **10** = Strong, direct alignment with the defined problems',
  1],
 ['The transformative impact',
  2,
  'Systemic & Structural Impact',
  '## 2. Systemic & Structural Impact (Weight: ~35%)\n'
  '\n'
  'To what extent does the initiative generate broader systemic change (across sectors, institutions, or policies)?\n'
  '\n'
  '- **1** = Isolated impact; no broader systemic or institutional change\n'
  '- **5** = Moderate spillovers or incremental institutional adjustments\n'
  '- **10** = Strong cross-sector impact and/or significant changes to governance, regulation, or institutional '
  'behavior',
  1],
 ['The transformative impact',
  3,
  'Societal Transformation & Equity',
  '## 3. Societal Transformation & Equity (Weight: ~25%)\n'
  '\n'
  'To what extent does the initiative influence societal attitudes and reduce inequalities?\n'
  '\n'
  '- **1** = No influence on attitudes; may worsen inequalities\n'
  '- **5** = Some influence on discourse or limited/mixed equity effects\n'
  '- **10** = Strong shift in narratives/priorities and clear reduction of inequalities (e.g., accessibility, '
  'fairness)',
  1],
 ['Feasibility and Implementation',
  1,
  'Accessibility',
  '## 4.1 Barriers in terms of Accessibility\n'
  '\n'
  'Are there technical, administrative, geographic, digital, or social barriers that may prevent certain groups from '
  'accessing the measure on equal terms?\n'
  '\n'
  '- **1** = Severe technical, administrative, geographic, digital, or social barriers prevent equitable access\n'
  '- **5** = Moderate accessibility barriers that require adaptation, support, or targeted interventions\n'
  '- **10** = Very few accessibility barriers; the measure is broadly accessible to all affected groups',
  1],
 ['Feasibility and Implementation',
  2,
  'Affordability',
  '## 4.2 Barriers in terms of Affordability\n'
  '\n'
  'Is the measure economically viable for all affected groups, including low-income households or other disadvantaged '
  'populations? Are costs, co-payments, or hidden burdens equitably distributed?\n'
  '\n'
  '- **1** = Severe affordability barriers; costs or financial burdens exclude significant groups\n'
  '- **5** = Moderate affordability challenges; support mechanisms or adjustments may be needed\n'
  '- **10** = Very few affordability barriers; costs and financial burdens are equitably distributed and broadly '
  'manageable',
  1],
 ['Feasibility and Implementation',
  3,
  'Acceptability',
  '## 4.3 Barriers in terms of Acceptability\n'
  '\n'
  'Is the measure socially and politically acceptable to the communities and stakeholders affected? Does it align with '
  'local norms, trust levels, and stakeholder expectations?\n'
  '\n'
  '- **1** = Severe political, cultural, financial, or administrative barriers\n'
  '- **5** = Moderate obstacles that require negotiation or adaptation\n'
  '- **10** = Very few obstacles; high political, cultural, and financial compatibility',
  1],
 ['Feasibility and Implementation',
  4,
  'Availability & timing',
  '## 5. Barriers in terms of Availability / Timing\n'
  '\n'
  'Is the measure legally, institutionally, and logistically in place for the target population? Are the necessary '
  'infrastructures, services, or delivery mechanisms present?\n'
  '\n'
  'How suitable is the current moment for implementing this initiative (in terms of readiness, preconditions, and '
  'alignment with policy cycles)?\n'
  '\n'
  '- **1** = Cannot be implemented under current conditions; major prerequisites missing\n'
  '- **5** = Some prerequisites missing; partial readiness\n'
  '- **10** = Fully implementable immediately with no major preconditions',
  1]]

QUESTION_OPTIONS = [['Age range', '<18'],
 ['Age range', '18-25'],
 ['Age range', '25-35'],
 ['Age range', '35-65'],
 ['Age range', '>65'],
 ['Living in a house with low energy efficiency', 'Yes'],
 ['Living in a house with low energy efficiency', 'No'],
 ['Gender', 'Woman'],
 ['Gender', 'Male'],
 ['Gender', 'Non-binary'],
 ['Gender', 'Other'],
 ['Need of a car to perform daily activities', 'Yes'],
 ['Need of a car to perform daily activities', 'No'],
 ['Level of education', 'No formal education'],
 ['Level of education', 'Primary'],
 ['Level of education', 'Secondary'],
 ['Level of education', 'Further normal education'],
 ['Location of residency', 'Urban area'],
 ['Location of residency', 'Suburban area'],
 ['Location of residency', 'Rural area'],
 ['Economic status', 'Employed'],
 ['Economic status', 'Unemployed'],
 ['Economic status', 'Retired'],
 ['Care responsibility as the main activity', 'Yes, remunerated'],
 ['Care responsibility as the main activity', 'Yes, Non-remunerated'],
 ['Care responsibility as the main activity', 'No'],
 ['EU citizenship', 'Yes'],
 ['EU citizenship', 'No'],
 ['Disability of long-term condition', 'Yes'],
 ['Disability of long-term condition', 'No'],
 ['Level of income', 'Low income'],
 ['Level of income', 'Medium income'],
 ['Level of income', 'High income'],
 ['Tenancy status', 'Homeowner'],
 ['Tenancy status', 'Tenant']]


REQUIRED_TABLES = (
    "countries",
    "sectors",
    "regions",
    "country_sectors",
    "evaluation_questions",
    "question_options",
)


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_path = os.environ.get("SQLITE_DATABASE_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    url = os.environ.get("DATABASE_URL", "").strip()
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        # Handle Windows sqlite:///C:/... and regular absolute/relative paths.
        return Path(raw).expanduser().resolve()
    raise RuntimeError("SQLite database path was not provided. Use --database or SQLITE_DATABASE_PATH.")


def require_tables(conn: sqlite3.Connection) -> None:
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [name for name in REQUIRED_TABLES if name not in existing]
    if missing:
        raise RuntimeError("SQLite schema is missing required tables: " + ", ".join(missing))


def get_id(conn: sqlite3.Connection, table: str, where: str, params: tuple) -> str | None:
    row = conn.execute(f"SELECT id FROM {table} WHERE {where} LIMIT 1", params).fetchone()
    return row[0] if row else None


def seed(conn: sqlite3.Connection) -> dict[str, int]:
    require_tables(conn)
    conn.execute("PRAGMA foreign_keys = ON")

    country_ids: dict[str, str] = {}
    for name, map_code, map_path in COUNTRIES:
        row_id = get_id(conn, "countries", "name = ?", (name,))
        if row_id:
            conn.execute("UPDATE countries SET map_code = ?, map_path = ? WHERE id = ?", (map_code, map_path, row_id))
        else:
            row_id = stable_id("country", name)
            conn.execute(
                "INSERT INTO countries (id, name, map_code, map_path) VALUES (?, ?, ?, ?)",
                (row_id, name, map_code, map_path),
            )
        country_ids[name] = row_id

    sector_ids: dict[str, str] = {}
    for (name,) in SECTORS:
        row_id = get_id(conn, "sectors", "name = ?", (name,))
        if not row_id:
            row_id = stable_id("sector", name)
            conn.execute("INSERT INTO sectors (id, name) VALUES (?, ?)", (row_id, name))
        sector_ids[name] = row_id

    for country_name, region_name in REGIONS:
        country_id = country_ids[country_name]
        row_id = get_id(conn, "regions", "country_id = ? AND name = ?", (country_id, region_name))
        if not row_id:
            conn.execute(
                "INSERT INTO regions (id, country_id, name) VALUES (?, ?, ?)",
                (stable_id("region", country_name, region_name), country_id, region_name),
            )

    for country_name, sector_name in COUNTRY_SECTORS:
        country_id = country_ids[country_name]
        sector_id = sector_ids[sector_name]
        row_id = get_id(conn, "country_sectors", "country_id = ? AND sector_id = ?", (country_id, sector_id))
        if not row_id:
            conn.execute(
                "INSERT INTO country_sectors (id, country_id, sector_id) VALUES (?, ?, ?)",
                (stable_id("country-sector", country_name, sector_name), country_id, sector_id),
            )

    question_ids: dict[tuple[str, int], str] = {}
    target_question_ids_by_text: dict[str, str] = {}
    for category, sort_order, chart_title, question, active in EVALUATION_QUESTIONS:
        row = conn.execute(
            "SELECT id FROM evaluation_questions WHERE category = ? AND sort_order = ? LIMIT 1",
            (category, sort_order),
        ).fetchone()
        if row:
            qid = row[0]
            conn.execute(
                "UPDATE evaluation_questions SET chart_title = ?, question = ?, active = ? WHERE id = ?",
                (chart_title, question, int(bool(active)), qid),
            )
        else:
            qid = stable_id("evaluation-question", category, sort_order)
            conn.execute(
                "INSERT INTO evaluation_questions (id, category, chart_title, question, sort_order, active) VALUES (?, ?, ?, ?, ?, ?)",
                (qid, category, chart_title, question, sort_order, int(bool(active))),
            )
        question_ids[(category, int(sort_order))] = qid
        if category == "target_population":
            target_question_ids_by_text[question] = qid

    for question_text, option_label in QUESTION_OPTIONS:
        qid = target_question_ids_by_text.get(question_text)
        if not qid:
            raise RuntimeError(f"Question option references unknown target_population question: {question_text}")
        exists = conn.execute(
            'SELECT 1 FROM question_options WHERE questionId = ? AND "option" = ? LIMIT 1',
            (qid, option_label),
        ).fetchone()
        if not exists:
            conn.execute(
                'INSERT INTO question_options (id, questionId, "option") VALUES (?, ?, ?)',
                (stable_id("question-option", question_text, option_label), qid, option_label),
            )

    counts = {}
    for table in REQUIRED_TABLES:
        counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def validate_counts(counts: dict[str, int]) -> None:
    expected_minimums = {
        "countries": len(COUNTRIES),
        "sectors": len(SECTORS),
        "regions": len(REGIONS),
        "country_sectors": len(COUNTRY_SECTORS),
        "evaluation_questions": len(EVALUATION_QUESTIONS),
        "question_options": len(QUESTION_OPTIONS),
    }
    failures = [f"{t}={counts.get(t, 0)} (expected >= {n})" for t, n in expected_minimums.items() if counts.get(t, 0) < n]
    if failures:
        raise RuntimeError("Required SQLite reference data validation failed: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed required Dr Transition reference data into SQLite.")
    parser.add_argument("--database", help="Path to dr_transition.db. Defaults to SQLITE_DATABASE_PATH/DATABASE_URL.")
    args = parser.parse_args()
    db_path = resolve_db_path(args.database)
    if not db_path.exists():
        raise RuntimeError(f"SQLite database does not exist yet: {db_path}. Run schema/migrations first.")
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        with conn:
            counts = seed(conn)
            validate_counts(counts)
    finally:
        conn.close()
    print("Required SQLite reference data seeded successfully.")
    for table in REQUIRED_TABLES:
        print(f"{table}: {counts[table]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SQLite reference seed failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
