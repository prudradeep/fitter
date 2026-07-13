import csv
import logging
import re
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine
from app.seed.xlsx_readers import (
    _read_xlsx_first_sheet_rows,
    _xlsx_cell,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MM_CSV_PATH = PROJECT_ROOT / "mm.csv"
MM_TARGET_GROUP_XLSX_PATH = PROJECT_ROOT / "MM Target group.xlsx"
SECTORAL_CHALLENGES_XLSX_PATH = PROJECT_ROOT / "sectoral_challenges.xlsx"
HAZARDS_XLSX_PATH = PROJECT_ROOT / "hazards.xlsx"
ADDITIONAL_HAZARDS_CSV_PATH = PROJECT_ROOT / "additionalHazards.csv"
ADDITIONAL_HAZARD_PROFILES_CSV_PATH = PROJECT_ROOT / "additionalHazardProfiles.csv"


def seed_reference_data(*, apply_schema: bool = True) -> None:
    """Apply migrations and reload reference data from local CSV/XLSX files."""
    from app.db.migrations_runtime import run_runtime_migrations

    run_runtime_migrations(apply_base_schema=apply_schema, seed_reference_data=True)
    logger.info("Reference data seeded")


def _normalize_mitigation_example_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _read_mm_csv_rows() -> list[dict[str, str]]:
    if not MM_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with MM_CSV_PATH.open(encoding=encoding, newline="") as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with MM_CSV_PATH.open(encoding="utf-8", errors="replace", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_mm_target_group_xlsx_rows() -> list[dict[str, object]]:
    if not MM_TARGET_GROUP_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(MM_TARGET_GROUP_XLSX_PATH)
    if len(rows) < 3:
        return []

    category_row = rows[0]
    header_row = rows[1]
    category_by_column: dict[int, str] = {}
    current_category = ""
    for column_index, raw_category in enumerate(category_row):
        category = str(raw_category or "").strip()
        if category:
            current_category = category
        if column_index >= 5:
            category_by_column[column_index] = current_category

    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[2:], start=3):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        sector_name = _xlsx_cell(row, 2)
        if not policy_code and not policy_title:
            continue
        for column_index in range(5, len(header_row)):
            target_group = _xlsx_cell(header_row, column_index)
            if not target_group:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "sector_name": sector_name,
                    "policy_type": _xlsx_cell(row, 3),
                    "short_description": _xlsx_cell(row, 4),
                    "target_group_category": category_by_column.get(column_index, ""),
                    "target_group": target_group,
                    "match_value": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows



def _read_sectoral_challenges_xlsx_rows() -> list[dict[str, object]]:
    if not SECTORAL_CHALLENGES_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(SECTORAL_CHALLENGES_XLSX_PATH)
    if len(rows) < 2:
        return []

    header_row = rows[0]
    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[1:], start=2):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        if not policy_code and not policy_title:
            continue
        for column_index in range(2, len(header_row)):
            challenge = _xlsx_cell(header_row, column_index)
            if not challenge:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "additional_hazard": challenge,
                    "match_value": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows


def _read_hazards_xlsx_rows() -> list[dict[str, object]]:
    if not HAZARDS_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(HAZARDS_XLSX_PATH)
    if len(rows) < 3:
        return []

    sector_row = rows[0]
    header_row = rows[1]
    sector_by_column: dict[int, str] = {}
    current_sector = ""
    for column_index, raw_sector in enumerate(sector_row):
        sector = _hazards_xlsx_sector_name(str(raw_sector or ""))
        if sector:
            current_sector = sector
        if column_index >= 2:
            sector_by_column[column_index] = current_sector

    parsed_rows: list[dict[str, object]] = []
    for excel_row_number, row in enumerate(rows[2:], start=3):
        policy_code = _xlsx_cell(row, 0)
        policy_title = _xlsx_cell(row, 1)
        if not policy_code and not policy_title:
            continue
        for column_index in range(2, len(header_row)):
            hazard_label = _hazards_xlsx_hazard_label(_xlsx_cell(header_row, column_index))
            hazard_sector = sector_by_column.get(column_index, "")
            if not hazard_label or not hazard_sector:
                continue
            parsed_rows.append(
                {
                    "policy_code": policy_code,
                    "policy_title": policy_title,
                    "hazard_sector": hazard_sector,
                    "hazard_label": hazard_label,
                    "mitigation_effect": _xlsx_cell(row, column_index),
                    "excel_row_number": excel_row_number,
                    "excel_column_number": column_index + 1,
                }
            )
    return parsed_rows


def _hazards_xlsx_sector_name(value: str) -> str:
    normalized = value.casefold()
    if "energy" in normalized:
        return "Energy"
    if "transport" in normalized:
        return "Transport"
    if "housing" in normalized:
        return "Housing"
    return ""


def _hazards_xlsx_hazard_label(value: str) -> str:
    cleaned = str(value or "").strip().strip("[]")
    cleaned = re.sub(r"(?i)^hazard\s+\d+\s*\W+\s*", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _hazards_xlsx_system_hazard_lookup_key(
    hazard_sector: str,
    hazard_label: str,
) -> tuple[str, str] | None:
    sector_key = _normalize_mitigation_example_key(hazard_sector)
    label_key = _normalize_mitigation_example_key(hazard_label)
    aliases = {
        ("energy", "higherelectricitybills"): "higherelectricitybills",
        ("energy", "increasedheatingcosts"): "heatingandcoolingcostsincrease",
        ("energy", "exposuretoenergypoverty"): "strugglingtopaybillseachmonth",
        ("energy", "homelosesmarketvalue"): "housevaluedecreasenosolar",
        ("energy", "loseincomeduetotheproductionofsolarenergy"): "missingoutonsolarsavings",
        (
            "energy",
            "facingpressureorpenaltiesinthefutureifthehomedoesnotmeetnewenergyefficiencystandardsorregulations",
        ): "newtaxesorfinesforinefficiency",
        ("energy", "morefrequentpoweroutages"): "morefrequentpowercuts",
        ("transport", "higherfuelandmaintenancecosts"): "higherfuelrepaircostsice",
        ("transport", "losingresalevalue"): "carlosesresalevalue",
        ("transport", "penaltiesassociatedtopetroldieselcar"): "newtaxesfinesforice",
        (
            "transport",
            "drivingrestrictioninspecificemissionzones",
        ): "restrictedfromtowncitycentreszezrestrictions",
        ("transport", "reducedtravelefficiency"): "longerormorecomplexjourneys",
        ("transport", "exposuretomorepollution"): "morepollutionexposure",
        ("housing", "higherelectricitybills"): "higherelectricitybills",
        ("housing", "increasedheatingcosts"): "heatingandcoolingcostsincrease",
        ("housing", "exposuretoenergypoverty"): "strugglingtopaybillseachmonth",
        ("housing", "homelosesmarketvalue"): "housevaluedecreasenosolar",
        ("housing", "loseincomeduetotheproductionofsolarenergy"): "missingoutonsolarsavings",
        ("housing", "higherhouseinsurancecosts"): "homeinsurancemoreexpensive",
        (
            "housing",
            "facingpressureorpenaltiesinthefutureifthehomedoesnotmeetnewenergyefficiencystandardsorregulations",
        ): "newtaxesorfinesforinefficiency",
        (
            "housing",
            "lawsforbiddingsellingorrentinghouseswithnoretrofittingorrenovations",
        ): "lawsforbidsellingrentingnonrenovated",
        ("housing", "morefrequentpoweroutages"): "morefrequentpowercuts",
        ("housing", "presenceofdampormold"): "homemoredampormould",
        (
            "housing",
            "moreriskedperceivedbyinsurancecompaniesofthehousewithnorenovationorretrofitting",
        ): "insurersclassifyhomeashighrisk",
        ("housing", "strongereffectsofextremeweatherevents"): "increasedsevereweatherimpacts",
        ("housing", "diseasesandhealthproblems"): "colddampleadstohealthproblems",
    }
    hazard_name_key = aliases.get((sector_key, label_key))
    if not hazard_name_key:
        return None
    return sector_key, hazard_name_key


def _read_additional_hazards_csv_rows() -> list[dict[str, str]]:
    if not ADDITIONAL_HAZARDS_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with ADDITIONAL_HAZARDS_CSV_PATH.open(encoding=encoding, newline="") as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with ADDITIONAL_HAZARDS_CSV_PATH.open(
        encoding="utf-8", errors="replace", newline=""
    ) as csv_file:
        return list(csv.DictReader(csv_file))


def _read_additional_hazard_profiles_csv_rows() -> list[dict[str, str]]:
    if not ADDITIONAL_HAZARD_PROFILES_CSV_PATH.exists():
        return []

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with ADDITIONAL_HAZARD_PROFILES_CSV_PATH.open(
                encoding=encoding, newline=""
            ) as csv_file:
                return list(csv.DictReader(csv_file))
        except UnicodeDecodeError:
            continue
    with ADDITIONAL_HAZARD_PROFILES_CSV_PATH.open(
        encoding="utf-8", errors="replace", newline=""
    ) as csv_file:
        return list(csv.DictReader(csv_file))


def ensure_additional_hazards() -> None:
    with engine.begin() as connection:
        _seed_additional_hazards(connection)
        _seed_additional_hazard_profiles(connection)
        _seed_additional_hazard_profile_target_populations(connection)


def _seed_additional_hazards(connection) -> None:
    rows = _read_additional_hazards_csv_rows()
    if not rows:
        return

    country_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM countries")).mappings()
    }
    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }

    connection.execute(text("DELETE FROM additional_hazards WHERE source = 'csv'"))

    inserted = 0
    skipped = 0
    seen: set[tuple[int, int, str]] = set()
    for csv_index, row in enumerate(rows, start=2):
        country_name = (row.get("country") or "").strip()
        sector_name = (row.get("sector") or "").strip()
        hazard_name = (row.get("hazard name") or "").strip()
        country_id = country_by_key.get(_normalize_mitigation_example_key(country_name))
        sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
        hazard_key = _normalize_mitigation_example_key(hazard_name)
        if not country_id or not sector_id or not hazard_name:
            skipped += 1
            continue
        scope_key = (int(country_id), int(sector_id), hazard_key)
        if scope_key in seen:
            skipped += 1
            continue
        seen.add(scope_key)
        connection.execute(
            text(
                """
                INSERT INTO additional_hazards (
                    country_id,
                    sector_id,
                    name,
                    source,
                    csv_row_number
                )
                VALUES (
                    :country_id,
                    :sector_id,
                    :name,
                    'csv',
                    :csv_row_number
                )
                """
            ),
            {
                "country_id": country_id,
                "sector_id": sector_id,
                "name": hazard_name,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s additional hazards from additionalHazards.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_additional_hazard_profiles(connection) -> None:
    rows = _read_additional_hazard_profiles_csv_rows()
    if not rows:
        return

    country_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM countries")).mappings()
    }
    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    hazard_by_scope = {
        (
            int(row["country_id"]),
            int(row["sector_id"]),
            _normalize_mitigation_example_key(row["name"]),
        ): int(row["id"])
        for row in connection.execute(
            text("SELECT id, country_id, sector_id, name FROM additional_hazards")
        ).mappings()
    }

    connection.execute(
        text("DELETE FROM additional_hazard_profiles WHERE source = 'd4_2_pdf'")
    )

    inserted = 0
    skipped = 0
    seen: set[tuple[int, str]] = set()
    for csv_index, row in enumerate(rows, start=2):
        country_id = country_by_key.get(
            _normalize_mitigation_example_key((row.get("country") or "").strip())
        )
        sector_id = sector_by_key.get(
            _normalize_mitigation_example_key((row.get("sector") or "").strip())
        )
        hazard_key = _normalize_mitigation_example_key(
            (row.get("hazard name") or "").strip()
        )
        profile = (row.get("profile") or "").strip()
        if not country_id or not sector_id or not hazard_key or not profile:
            skipped += 1
            continue
        additional_hazard_id = hazard_by_scope.get((int(country_id), int(sector_id), hazard_key))
        if additional_hazard_id is None:
            skipped += 1
            continue
        scope_key = (additional_hazard_id, _normalize_mitigation_example_key(profile))
        if scope_key in seen:
            skipped += 1
            continue
        seen.add(scope_key)
        connection.execute(
            text(
                """
                INSERT INTO additional_hazard_profiles (
                    additional_hazard_id,
                    profile,
                    evidence,
                    reference,
                    source,
                    csv_row_number
                )
                VALUES (
                    :additional_hazard_id,
                    :profile,
                    :evidence,
                    :reference,
                    'd4_2_pdf',
                    :csv_row_number
                )
                """
            ),
            {
                "additional_hazard_id": additional_hazard_id,
                "profile": profile,
                "evidence": (row.get("evidence") or "").strip() or None,
                "reference": (row.get("reference") or "").strip() or None,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s additional hazard profiles from additionalHazardProfiles.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_additional_hazard_profile_target_populations(connection) -> None:
    option_by_key = {
        (
            _normalize_mitigation_example_key(row["question"]),
            _normalize_mitigation_example_key(row["option"]),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT question_options.id, evaluation_questions.question, question_options.`option`
                FROM question_options
                JOIN evaluation_questions
                  ON evaluation_questions.id = question_options.questionId
                WHERE evaluation_questions.category = 'target_population'
                  AND evaluation_questions.active = TRUE
                """
            )
        ).mappings()
    }
    profile_rows = list(
        connection.execute(
            text("SELECT id, profile FROM additional_hazard_profiles")
        ).mappings()
    )
    connection.execute(text("DELETE FROM additional_hazard_profile_target_populations"))

    inserted = 0
    for row in profile_rows:
        option_ids: set[int] = set()
        for question, option in _target_population_pairs_for_profile(str(row["profile"] or "")):
            option_id = option_by_key.get(
                (
                    _normalize_mitigation_example_key(question),
                    _normalize_mitigation_example_key(option),
                )
            )
            if option_id is not None:
                option_ids.add(option_id)
        for option_id in sorted(option_ids):
            connection.execute(
                text(
                    """
                    INSERT INTO additional_hazard_profile_target_populations (
                        additional_hazard_profile_id,
                        question_option_id
                    )
                    VALUES (:profile_id, :option_id)
                    """
                ),
                {"profile_id": int(row["id"]), "option_id": option_id},
            )
            inserted += 1

    logger.info(
        "Mapped %s additional hazard profile target-population option links",
        inserted,
    )


def _target_population_pairs_for_profile(profile: str) -> list[tuple[str, str]]:
    text_key = _normalize_profile_phrase(profile)
    pairs: list[tuple[str, str]] = []

    def add(question: str, option: str) -> None:
        pair = (question, option)
        if pair not in pairs:
            pairs.append(pair)

    if any(
        term in text_key
        for term in (
            "low income",
            "lower income",
            "poorer",
            "financially fragile",
            "financial insecurity",
            "financially vulnerable",
            "energy poor",
            "energy poverty",
            "vulnerable households",
            "disadvantaged groups",
            "poverty",
            "expensive electricity",
            "price fluctuations",
            "upfront retrofit costs",
        )
    ):
        add("Level of income", "Low income")
    if any(term in text_key for term in ("middle income", "middle to low")):
        add("Level of income", "Medium income")
    if any(term in text_key for term in ("higher income", "high income")):
        add("Level of income", "High income")
    if any(term in text_key for term in ("tenant", "renting", "rental", "renters")):
        add("Tenancy status", "Tenant")
    if any(term in text_key for term in ("homeowner", "home owner", "home ownership")):
        add("Tenancy status", "Homeowner")
    if "rural" in text_key or "peripheral" in text_key or "small municipalities" in text_key:
        add("Location of residency", "Rural area")
    if "suburban" in text_key:
        add("Location of residency", "Suburban area")
    if "urban" in text_key and "suburban" not in text_key:
        add("Location of residency", "Urban area")
    if any(term in text_key for term in ("older", "elderly", "seniors", "ageing", "aging")):
        add("Age range", ">65")
    if any(term in text_key for term in ("young", "younger")):
        add("Age range", "25-35")
    if any(term in text_key for term in ("disabil", "reduced mobility", "special needs")):
        add("Disability of long-term condition", "Yes")
    if "women" in text_key:
        add("Gender", "Woman")
    if any(term in text_key for term in ("unemployed", "lost jobs", "lost their jobs")):
        add("Economic status", "Unemployed")
    if any(term in text_key for term in ("workers", "worker", "commuters", "precarious work")):
        add("Economic status", "Employed")
    if any(term in text_key for term in ("car dependent", "car dependency", "commuters")):
        add("Need of a car to perform daily activities", "Yes")
    if "displaced far from employment" in text_key:
        add("Need of a car to perform daily activities", "Yes")
    if any(term in text_key for term in ("public transport users", "public transport dependent")):
        add("Need of a car to perform daily activities", "No")
    if any(term in text_key for term in ("low educated", "low education")):
        add("Level of education", "Primary")
    if any(term in text_key for term in ("limited digital literacy", "low digital literacy", "low tech literacy")):
        add("Level of education", "Primary")
    if any(term in text_key for term in ("migrant", "migrants", "non eu")):
        add("EU citizenship", "No")
    if any(term in text_key for term in ("inefficient homes", "inefficient housing", "inefficient buildings")):
        add("Living in a house with low energy efficiency", "Yes")

    return pairs


def _normalize_profile_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


def _resolve_mitigation_profile_id(
    profile_label: str,
    system_hazard_id: int | None,
    profile_rows: list[dict[str, object]],
) -> int | None:
    if system_hazard_id is None:
        return None

    profile_key = _normalize_mitigation_example_key(profile_label)
    if not profile_key:
        return None

    same_hazard_rows = [
        row for row in profile_rows if row.get("system_hazard_id") == system_hazard_id
    ]
    exact_matches: list[int] = []
    fallback_matches: list[int] = []
    for row in same_hazard_rows:
        row_id = row.get("id")
        if not isinstance(row_id, int):
            continue
        row_keys = {
            _normalize_mitigation_example_key(str(row.get("profile") or "")),
            _normalize_mitigation_example_key(str(row.get("variable_name") or "")),
        }
        if profile_key in row_keys:
            exact_matches.append(row_id)
            continue
        if any(profile_key and profile_key in row_key for row_key in row_keys):
            fallback_matches.append(row_id)

    if exact_matches:
        return exact_matches[0]
    if len(fallback_matches) == 1:
        return fallback_matches[0]
    return None


def _seed_mm_csv_mitigation_measure_examples(connection) -> None:
    rows = _read_mm_csv_rows()
    if not rows:
        return

    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    hazard_by_key = {
        (row["sector_id"], _normalize_mitigation_example_key(row["name"])): row["id"]
        for row in connection.execute(
            text("SELECT id, sector_id, name FROM system_hazards")
        ).mappings()
    }
    profile_rows = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, system_hazard_id, sector_id, variable_name, profile
                FROM system_hazard_socio_demographics
                """
            )
        ).mappings()
    ]

    connection.execute(
        text("DELETE FROM mitigation_measure_examples WHERE source = 'mm_csv'")
    )

    inserted = 0
    skipped = 0
    for csv_index, row in enumerate(rows, start=2):
        sector_name = (row.get("Sector") or "").strip()
        hazard_name = (row.get("Hazard") or "").strip()
        profile_label = (
            row.get("affected predictor / indicator categories") or ""
        ).strip()
        measure = (row.get("Twin-transition mitigation measure") or "").strip()
        sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
        if not sector_id or not measure:
            skipped += 1
            continue

        system_hazard_id = hazard_by_key.get(
            (sector_id, _normalize_mitigation_example_key(hazard_name))
        )
        profile_id = _resolve_mitigation_profile_id(
            profile_label,
            system_hazard_id if isinstance(system_hazard_id, int) else None,
            profile_rows,
        )
        connection.execute(
            text(
                """
                INSERT INTO mitigation_measure_examples (
                    sector_id,
                    system_hazard_id,
                    system_hazard_socio_demographic_id,
                    profile_label,
                    measure,
                    policy_case_study,
                    country_city,
                    implementation_summary,
                    evidence,
                    reference_links,
                    source,
                    csv_row_number
                )
                VALUES (
                    :sector_id,
                    :system_hazard_id,
                    :profile_id,
                    :profile_label,
                    :measure,
                    :policy_case_study,
                    :country_city,
                    :implementation_summary,
                    :evidence,
                    :reference_links,
                    'mm_csv',
                    :csv_row_number
                )
                """
            ),
            {
                "sector_id": sector_id,
                "system_hazard_id": system_hazard_id,
                "profile_id": profile_id,
                "profile_label": profile_label or None,
                "measure": measure,
                "policy_case_study": (
                    row.get("Policy case study across Europe only") or ""
                ).strip()
                or None,
                "country_city": (row.get("Country / city") or "").strip() or None,
                "implementation_summary": (
                    row.get("Policy implementation summary") or ""
                ).strip()
                or None,
                "evidence": (
                    row.get("Evidence of success / why credible") or ""
                ).strip()
                or None,
                "reference_links": (row.get("Reference links") or "").strip()
                or None,
                "csv_row_number": csv_index,
            },
        )
        inserted += 1

    logger.info(
        "Loaded %s mitigation measure examples from mm.csv; skipped %s rows",
        inserted,
        skipped,
    )


def _seed_mm_target_group_xlsx(connection) -> None:
    rows = _read_mm_target_group_xlsx_rows()
    if not rows:
        return

    _ensure_mm_target_group_question_options(connection)

    sector_by_key = {
        _normalize_mitigation_example_key(row["name"]): row["id"]
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    country_by_map_code = {
        str(row["map_code"] or "").casefold(): int(row["id"])
        for row in connection.execute(
            text("SELECT id, map_code FROM countries WHERE map_code IS NOT NULL")
        ).mappings()
    }
    option_by_group = _mm_target_group_option_map(connection)

    connection.execute(
        text("DELETE FROM mitigation_measure_target_groups WHERE source = 'xlsx'")
    )
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_additional_hazards WHERE source = 'xlsx'")
    )
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_system_hazards WHERE source = 'xlsx'")
    )
    connection.execute(text("DELETE FROM mitigation_measure_policies WHERE source = 'xlsx'"))

    policy_ids: dict[tuple[str, int | None], int] = {}
    policy_rows: dict[tuple[str, int | None], dict[str, object]] = {}
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        if not policy_code:
            continue
        sector_name = str(row.get("sector_name") or "").strip()
        for sector_id in _mm_target_group_sector_ids(sector_name, sector_by_key):
            policy_key = (policy_code, sector_id)
            if policy_key in policy_rows:
                continue
            policy_rows[policy_key] = {
                "policy_code": policy_code,
                "policy_title": str(row.get("policy_title") or "").strip(),
                "country_id": _mm_policy_country_id(policy_code, country_by_map_code),
                "sector_id": sector_id,
                "policy_type": str(row.get("policy_type") or "").strip() or None,
                "short_description": str(row.get("short_description") or "").strip() or None,
                "source": "xlsx",
                "excel_row_number": row.get("excel_row_number"),
            }
    for policy_row in policy_rows.values():
        result = connection.execute(
            text(
                """
                INSERT INTO mitigation_measure_policies (
                    policy_code,
                    policy_title,
                    country_id,
                    sector_id,
                    policy_type,
                    short_description,
                    source,
                    excel_row_number
                )
                VALUES (
                    :policy_code,
                    :policy_title,
                    :country_id,
                    :sector_id,
                    :policy_type,
                    :short_description,
                    :source,
                    :excel_row_number
                )
                """
            ),
            policy_row,
        )
        policy_ids[
            (str(policy_row["policy_code"]), policy_row.get("sector_id"))
        ] = int(result.lastrowid)

    inserted = 0
    skipped = 0
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        match_value = str(row.get("match_value") or "").strip()
        if match_value.casefold() == "no":
            skipped += 1
            continue
        question_option_id = option_by_group.get(
            (
                _normalize_mitigation_example_key(
                    str(row.get("target_group_category") or "")
                ),
                _normalize_mitigation_example_key(str(row.get("target_group") or "")),
            )
        )
        if question_option_id is None:
            skipped += 1
            continue
        sector_name = str(row.get("sector_name") or "").strip()
        for sector_id in _mm_target_group_sector_ids(sector_name, sector_by_key):
            policy_id = policy_ids.get((policy_code, sector_id))
            if policy_id is None:
                skipped += 1
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO mitigation_measure_target_groups (
                        mitigation_measure_policy_id,
                        question_option_id,
                        match_value,
                        source,
                        excel_column_number
                    )
                    VALUES (
                        :policy_id,
                        :question_option_id,
                        :match_value,
                        'xlsx',
                        :excel_column_number
                    )
                    """
                ),
                {
                    "policy_id": policy_id,
                    "question_option_id": question_option_id,
                    "match_value": match_value or None,
                    "excel_column_number": row.get("excel_column_number"),
                },
            )
            inserted += 1

    logger.info(
        "Loaded %s mitigation policies and %s target-group mappings from "
        "MM Target group.xlsx; skipped %s mappings",
        len(policy_ids),
        inserted,
        skipped,
    )
    _seed_sectoral_challenge_policy_additional_hazards(connection)


def _seed_sectoral_challenge_policy_additional_hazards(connection) -> None:
    rows = _read_sectoral_challenges_xlsx_rows()
    if not rows:
        return

    policy_ids_by_code_country: dict[tuple[str, int], list[int]] = {}
    for row in connection.execute(
        text(
            """
            SELECT id, policy_code, country_id
            FROM mitigation_measure_policies
            WHERE source = 'xlsx'
              AND country_id IS NOT NULL
            """
        )
    ).mappings():
        policy_ids_by_code_country.setdefault(
            (str(row["policy_code"]), int(row["country_id"])),
            [],
        ).append(int(row["id"]))
    hazard_by_country_name = {
        (
            int(row["country_id"]),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT id, country_id, name
                FROM additional_hazards
                """
            )
        ).mappings()
    }
    connection.execute(
        text("DELETE FROM mitigation_measure_policy_additional_hazards WHERE source = 'xlsx'")
    )

    inserted = 0
    skipped = 0
    for row in rows:
        policy_code = str(row.get("policy_code") or "").strip()
        match_value = str(row.get("match_value") or "").strip()
        if match_value.casefold() == "not addressed":
            skipped += 1
            continue
        hazard_key = _normalize_mitigation_example_key(
            str(row.get("additional_hazard") or "")
        )
        inserted_for_cell = False
        for country_id in {
            country_id
            for stored_policy_code, country_id in policy_ids_by_code_country
            if stored_policy_code == policy_code
        }:
            additional_hazard_id = hazard_by_country_name.get((country_id, hazard_key))
            if additional_hazard_id is None:
                continue
            for policy_id in policy_ids_by_code_country.get((policy_code, country_id), []):
                connection.execute(
                    text(
                        """
                        INSERT INTO mitigation_measure_policy_additional_hazards (
                            mitigation_measure_policy_id,
                            additional_hazard_id,
                            match_value,
                            source,
                            excel_row_number,
                            excel_column_number
                        )
                        VALUES (
                            :policy_id,
                            :additional_hazard_id,
                            :match_value,
                            'xlsx',
                            :excel_row_number,
                            :excel_column_number
                        )
                        """
                    ),
                    {
                        "policy_id": policy_id,
                        "additional_hazard_id": additional_hazard_id,
                        "match_value": match_value or None,
                        "excel_row_number": row.get("excel_row_number"),
                        "excel_column_number": row.get("excel_column_number"),
                    },
                )
                inserted += 1
                inserted_for_cell = True
        if not inserted_for_cell:
            skipped += 1

    logger.info(
        "Loaded %s mitigation-policy additional-hazard mappings from "
        "sectoral_challenges.xlsx; skipped %s challenge cells",
        inserted,
        skipped,
    )


def _seed_hazards_xlsx_policy_system_hazards(connection) -> None:
    rows = _read_hazards_xlsx_rows()
    if not rows:
        return

    policy_ids_by_code_country: dict[tuple[str, int], list[int]] = {}
    for row in connection.execute(
        text(
            """
            SELECT id, policy_code, country_id
            FROM mitigation_measure_policies
            WHERE source = 'xlsx'
              AND country_id IS NOT NULL
            """
        )
    ).mappings():
        policy_ids_by_code_country.setdefault(
            (str(row["policy_code"]), int(row["country_id"])),
            [],
        ).append(int(row["id"]))

    hazard_by_sector_name = {
        (
            _normalize_mitigation_example_key(str(row["sector_name"] or "")),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): int(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT system_hazards.id, sectors.name AS sector_name, system_hazards.name
                FROM system_hazards
                JOIN sectors ON sectors.id = system_hazards.sector_id
                """
            )
        ).mappings()
    }

    connection.execute(
        text("DELETE FROM mitigation_measure_policy_system_hazards WHERE source = 'xlsx'")
    )

    inserted = 0
    skipped = 0
    for row in rows:
        mitigation_effect = str(row.get("mitigation_effect") or "").strip()
        if not mitigation_effect or mitigation_effect.casefold() == "not applicable":
            skipped += 1
            continue

        hazard_lookup_key = _hazards_xlsx_system_hazard_lookup_key(
            str(row.get("hazard_sector") or ""),
            str(row.get("hazard_label") or ""),
        )
        if hazard_lookup_key is None:
            skipped += 1
            continue

        system_hazard_id = hazard_by_sector_name.get(hazard_lookup_key)
        if system_hazard_id is None:
            skipped += 1
            continue

        policy_code = str(row.get("policy_code") or "").strip()
        inserted_for_cell = False
        for country_id in {
            country_id
            for stored_policy_code, country_id in policy_ids_by_code_country
            if stored_policy_code == policy_code
        }:
            for policy_id in policy_ids_by_code_country.get((policy_code, country_id), []):
                connection.execute(
                    text(
                        """
                        INSERT INTO mitigation_measure_policy_system_hazards (
                            mitigation_measure_policy_id,
                            system_hazard_id,
                            mitigation_effect,
                            source,
                            excel_row_number,
                            excel_column_number
                        )
                        VALUES (
                            :policy_id,
                            :system_hazard_id,
                            :mitigation_effect,
                            'xlsx',
                            :excel_row_number,
                            :excel_column_number
                        )
                        ON DUPLICATE KEY UPDATE
                            mitigation_effect = VALUES(mitigation_effect),
                            excel_row_number = VALUES(excel_row_number),
                            excel_column_number = VALUES(excel_column_number)
                        """
                    ),
                    {
                        "policy_id": policy_id,
                        "system_hazard_id": system_hazard_id,
                        "mitigation_effect": mitigation_effect,
                        "excel_row_number": row.get("excel_row_number"),
                        "excel_column_number": row.get("excel_column_number"),
                    },
                )
                inserted += 1
                inserted_for_cell = True
        if not inserted_for_cell:
            skipped += 1

    logger.info(
        "Loaded %s mitigation-policy system-hazard effect mappings from "
        "hazards.xlsx; skipped %s hazard cells",
        inserted,
        skipped,
    )


def _ensure_hazards_xlsx_policy_system_hazards(connection) -> None:
    table_exists = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = 'mitigation_measure_policy_system_hazards'
            """
        )
    ).scalar()
    if not table_exists:
        return
    existing = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM mitigation_measure_policy_system_hazards
            WHERE source = 'xlsx'
            """
        )
    ).scalar()
    if int(existing or 0) > 0:
        return
    _seed_hazards_xlsx_policy_system_hazards(connection)


def _mm_target_group_sector_ids(
    sector_name: str,
    sector_by_key: dict[str, int],
) -> list[int | None]:
    exact_sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
    if exact_sector_id is not None:
        return [int(exact_sector_id)]

    normalized = _normalize_mitigation_example_key(sector_name)
    sector_ids: list[int] = []
    for sector_label, sector_id in sector_by_key.items():
        if sector_label and sector_label in normalized:
            sector_ids.append(int(sector_id))
    if sector_ids:
        return sorted(set(sector_ids))
    return [None]


def _mm_policy_country_id(
    policy_code: str,
    country_by_map_code: dict[str, int],
) -> int | None:
    prefix = str(policy_code or "").split("_", 1)[0].strip().casefold()
    if prefix == "h":
        prefix = "hu"
    return country_by_map_code.get(prefix)


def _ensure_mm_target_group_question_options(connection) -> None:
    age_question_id = connection.execute(
        text(
            """
            SELECT id
            FROM evaluation_questions
            WHERE category = 'target_population'
              AND question = 'Age range'
            LIMIT 1
            """
        )
    ).scalar()
    if age_question_id is None:
        return
    existing = connection.execute(
        text(
            """
            SELECT id
            FROM question_options
            WHERE questionId = :question_id
              AND `option` = '18-25'
            LIMIT 1
            """
        ),
        {"question_id": int(age_question_id)},
    ).scalar()
    if existing is None:
        connection.execute(
            text(
                """
                INSERT INTO question_options (questionId, `option`)
                VALUES (:question_id, '18-25')
                """
            ),
            {"question_id": int(age_question_id)},
        )


def _mm_target_group_option_map(connection) -> dict[tuple[str, str], int]:
    rows = connection.execute(
        text(
            """
            SELECT evaluation_questions.question, question_options.`option`, question_options.id
            FROM question_options
            JOIN evaluation_questions
              ON evaluation_questions.id = question_options.questionId
            WHERE evaluation_questions.category = 'target_population'
              AND evaluation_questions.active = TRUE
            """
        )
    ).mappings()
    option_by_key = {
        (
            _normalize_mitigation_example_key(str(row["question"] or "")),
            _normalize_mitigation_example_key(str(row["option"] or "")),
        ): int(row["id"])
        for row in rows
    }
    aliases: dict[tuple[str, str], tuple[str, str]] = {
        ("livinginlowenergyefficiencyhome", "livesinlowefficiencyhome"): (
            "livinginahousewithlowenergyefficiency",
            "yes",
        ),
        ("livinginlowenergyefficiencyhome", "livesinefficienthome"): (
            "livinginahousewithlowenergyefficiency",
            "no",
        ),
        ("needsacarfordailyactivities", "cardependent"): (
            "needofacartoperformdailyactivities",
            "yes",
        ),
        ("needsacarfordailyactivities", "notcardependent"): (
            "needofacartoperformdailyactivities",
            "no",
        ),
        ("eucitizenship", "eucitizen"): ("eucitizenship", "yes"),
        ("eucitizenship", "noneucitizen"): ("eucitizenship", "no"),
        ("disabilityorlongtermcondition", "hasdisabilitycondition"): (
            "disabilityoflongtermcondition",
            "yes",
        ),
        ("disabilityorlongtermcondition", "nodisabilitycondition"): (
            "disabilityoflongtermcondition",
            "no",
        ),
        ("levelofincome", "low"): ("levelofincome", "lowincome"),
        ("levelofincome", "medium"): ("levelofincome", "mediumincome"),
        ("levelofincome", "high"): ("levelofincome", "highincome"),
        ("levelofeducation", "furtherformaleducation"): (
            "levelofeducation",
            "furthernormaleducation",
        ),
        ("careresponsibilitymainactivity", "yesnonremunerated"): (
            "careresponsibilityasthemainactivity",
            "yesnonremunerated",
        ),
        ("careresponsibilitymainactivity", "yesremunerated"): (
            "careresponsibilityasthemainactivity",
            "yesremunerated",
        ),
        ("careresponsibilitymainactivity", "no"): (
            "careresponsibilityasthemainactivity",
            "no",
        ),
    }
    mapped = dict(option_by_key)
    for source_key, target_key in aliases.items():
        if target_key in option_by_key:
            mapped[source_key] = option_by_key[target_key]
    return mapped


def ensure_mitigation_measure_examples() -> None:
    with engine.begin() as connection:
        _seed_mm_csv_mitigation_measure_examples(connection)
        _seed_mm_target_group_xlsx(connection)
        _seed_hazards_xlsx_policy_system_hazards(connection)


