import csv
import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine
from app.seed.xlsx_readers import (
    _read_xlsx_all_sheet_rows,
    _read_xlsx_first_sheet_rows,
    _xlsx_cell,
)
from app.services.knowledge_base import extract_file_chunks
from app.services.prompt_loader import PROMPT_FILES, load_sector_prompt
from app.services.sector_prompt_rag import section_five_primary_data, strip_rule_lines

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MM_CSV_PATH = PROJECT_ROOT / "mm.csv"
MM_TARGET_GROUP_XLSX_PATH = PROJECT_ROOT / "MM Target group.xlsx"
SECTORAL_CHALLENGES_XLSX_PATH = PROJECT_ROOT / "sectoral_challenges.xlsx"
HAZARDS_XLSX_PATH = PROJECT_ROOT / "hazards.xlsx"
ADDITIONAL_HAZARDS_CSV_PATH = PROJECT_ROOT / "additionalHazards.csv"
ADDITIONAL_HAZARD_PROFILES_CSV_PATH = PROJECT_ROOT / "additionalHazardProfiles.csv"
POLICIES_XLSX_PATH = PROJECT_ROOT / "kb" / "additional" / "Policies.xlsx"
HAZARD_WITH_MITIGATION_DIRECTORY = (
    PROJECT_ROOT / "kb" / "additional" / "hazards with mitigation"
)
HAZARD_WITH_MITIGATION_SOURCE_TYPE = "hazard_with_mitigation"
HAZARD_WITH_MITIGATION_SCOPE = "main"


def seed_reference_data(*, apply_schema: bool = True) -> None:
    """Apply migrations and reload reference data from local CSV/XLSX files."""
    from app.db.migrations_runtime import run_runtime_migrations

    run_runtime_migrations(apply_base_schema=apply_schema, seed_reference_data=True)
    logger.info("Reference data seeded")


def _normalize_mitigation_example_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _hazard_names_from_sector_prompt(sector_prompt: str) -> list[str]:
    prompt = strip_rule_lines(section_five_primary_data(sector_prompt) or sector_prompt)
    hazards: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?im)^HAZARD\s+\d+\s*[\.:–-]\s+(.+?)\s*$", prompt):
        hazard = _clean_sector_prompt_hazard_name(match.group(1))
        key = _normalize_mitigation_example_key(hazard)
        if hazard and key not in seen:
            seen.add(key)
            hazards.append(hazard)
    return hazards


def _clean_sector_prompt_hazard_name(value: str) -> str:
    hazard = re.sub(r"\s+", " ", str(value or "")).strip()
    hazard = re.sub(r"(?i)^HAZARD\s+\d+\s*[\.:–-]\s*", "", hazard).strip()
    hazard = strip_rule_lines(hazard).strip()
    if re.fullmatch(r"[─═\-_=]{6,}", hazard):
        return ""
    return hazard


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

    parsed_rows: list[dict[str, object]] = []
    for rows in _read_xlsx_all_sheet_rows(SECTORAL_CHALLENGES_XLSX_PATH).values():
        if len(rows) < 2:
            continue
        header_row = rows[0]
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


def _read_policies_xlsx_rows() -> list[dict[str, object]]:
    if not POLICIES_XLSX_PATH.exists():
        return []

    rows = _read_xlsx_first_sheet_rows(POLICIES_XLSX_PATH)
    if len(rows) < 2:
        return []

    headers = {
        _normalize_mitigation_example_key(_xlsx_cell(rows[0], column_index)): column_index
        for column_index in range(len(rows[0]))
    }
    required_headers = {"country", "sector", "policy", "policyurl", "language", "policytype"}
    if not required_headers.issubset(headers):
        logger.warning("Policies.xlsx is missing one or more required headers")
        return []

    return [
        {
            "country": _xlsx_cell(row, headers["country"]),
            "sector": _xlsx_cell(row, headers["sector"]),
            "policy": _xlsx_cell(row, headers["policy"]),
            "policy_url": _xlsx_cell(row, headers["policyurl"]),
            "language": _xlsx_cell(row, headers["language"]),
            "policy_type": _xlsx_cell(row, headers["policytype"]),
            "excel_row_number": excel_row_number,
        }
        for excel_row_number, row in enumerate(rows[1:], start=2)
        if _xlsx_cell(row, headers["country"])
        and _xlsx_cell(row, headers["sector"])
        and _xlsx_cell(row, headers["policy"])
    ]


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


def ensure_system_hazards_from_sector_prompts() -> None:
    with engine.begin() as connection:
        _seed_system_hazards_from_sector_prompts(connection)


def ensure_hazard_with_mitigation_knowledge() -> None:
    """Seed the country-level hazard/mitigation documents into the main KB."""
    with engine.begin() as connection:
        _seed_hazard_with_mitigation_documents(connection)


def _seed_hazard_with_mitigation_documents(connection) -> None:
    if not HAZARD_WITH_MITIGATION_DIRECTORY.is_dir():
        logger.info(
            "Hazard-with-mitigation knowledge directory is missing; skipped seeding: %s",
            HAZARD_WITH_MITIGATION_DIRECTORY,
        )
        return

    for path in sorted(HAZARD_WITH_MITIGATION_DIRECTORY.glob("*.docx")):
        source_uri = path.relative_to(PROJECT_ROOT).as_posix()
        existing_document_id = connection.execute(
            text(
                """
                SELECT id
                FROM knowledge_documents
                WHERE scope = :scope
                  AND source_type = :source_type
                  AND source_uri = :source_uri
                LIMIT 1
                """
            ),
            {
                "scope": HAZARD_WITH_MITIGATION_SCOPE,
                "source_type": HAZARD_WITH_MITIGATION_SOURCE_TYPE,
                "source_uri": source_uri,
            },
        ).scalar()
        if existing_document_id is not None:
            continue

        try:
            chunks = extract_file_chunks(path.name, path.read_bytes())
        except OSError:
            logger.exception("Could not read hazard-with-mitigation document %s", path)
            continue
        if not chunks:
            logger.warning("No readable text found in hazard-with-mitigation document %s", path)
            continue

        document_id = str(uuid.uuid4())
        country = path.stem.replace("_", " ").replace("-", " ").title()
        connection.execute(
            text(
                """
                INSERT INTO knowledge_documents (
                    id, user_id, title, source_type, source_uri, scope, scope_level
                ) VALUES (
                    :id, NULL, :title, :source_type, :source_uri, :scope, 'global'
                )
                """
            ),
            {
                "id": document_id,
                "title": f"Hazards with mitigation — {country}",
                "source_type": HAZARD_WITH_MITIGATION_SOURCE_TYPE,
                "source_uri": source_uri,
                "scope": HAZARD_WITH_MITIGATION_SCOPE,
            },
        )
        for index, chunk in enumerate(chunks):
            connection.execute(
                text(
                    """
                    INSERT INTO knowledge_chunks (
                        id, document_id, user_id, chunk_index, content, source_type,
                        source_uri, page_number, scope_level
                    ) VALUES (
                        :id, :document_id, NULL, :chunk_index, :content, :source_type,
                        :source_uri, :page_number, 'global'
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "document_id": document_id,
                    "chunk_index": index,
                    "content": chunk.content,
                    "source_type": HAZARD_WITH_MITIGATION_SOURCE_TYPE,
                    "source_uri": source_uri,
                    "page_number": chunk.page_number,
                },
            )


def _seed_system_hazards_from_sector_prompts(connection) -> None:
    sector_by_key = {
        _normalize_mitigation_example_key(str(row["name"] or "")): str(row["id"])
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    if not sector_by_key:
        logger.info("No sectors found; skipped sector-prompt system hazard seeding")
        return

    existing_by_sector_hazard = {
        (
            str(row["sector_id"]),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        )
        for row in connection.execute(
            text("SELECT sector_id, name FROM system_hazards")
        ).mappings()
    }

    inserted = 0
    skipped = 0
    for sector_key in PROMPT_FILES:
        sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_key))
        if sector_id is None:
            skipped += 1
            continue
        try:
            prompt = load_sector_prompt(sector_key)
        except OSError:
            logger.exception("Failed to read sector prompt for %s", sector_key)
            skipped += 1
            continue
        for hazard in _hazard_names_from_sector_prompt(prompt):
            hazard_key = _normalize_mitigation_example_key(hazard)
            existing_key = (sector_id, hazard_key)
            if existing_key in existing_by_sector_hazard:
                skipped += 1
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO system_hazards (
                        id,
                        sector_id,
                        name
                    )
                    VALUES (
                        :id,
                        :sector_id,
                        :name
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "sector_id": sector_id,
                    "name": hazard,
                },
            )
            existing_by_sector_hazard.add(existing_key)
            inserted += 1

    logger.info(
        "Loaded %s system hazards from sector prompts; skipped %s existing or unmatched hazards",
        inserted,
        skipped,
    )


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

    inserted = 0
    skipped = 0
    seen = {
        (
            str(row["country_id"]),
            str(row["sector_id"]),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        )
        for row in connection.execute(
            text("SELECT country_id, sector_id, name FROM additional_hazards")
        ).mappings()
    }
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
        scope_key = (str(country_id), str(sector_id), hazard_key)
        if scope_key in seen:
            skipped += 1
            continue
        seen.add(scope_key)
        connection.execute(
            text(
                """
                INSERT INTO additional_hazards (
                    id,
                    country_id,
                    sector_id,
                    name,
                    source,
                    csv_row_number
                )
                VALUES (
                    :id,
                    :country_id,
                    :sector_id,
                    :name,
                    'csv',
                    :csv_row_number
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
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
            str(row["country_id"]),
            str(row["sector_id"]),
            _normalize_mitigation_example_key(row["name"]),
        ): str(row["id"])
        for row in connection.execute(
            text("SELECT id, country_id, sector_id, name FROM additional_hazards")
        ).mappings()
    }

    inserted = 0
    skipped = 0
    seen = {
        (str(row["additional_hazard_id"]), _normalize_mitigation_example_key(str(row["profile"] or "")))
        for row in connection.execute(
            text("SELECT additional_hazard_id, profile FROM additional_hazard_profiles")
        ).mappings()
    }
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
        additional_hazard_id = hazard_by_scope.get((str(country_id), str(sector_id), hazard_key))
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
                    id,
                    additional_hazard_id,
                    profile,
                    evidence,
                    reference,
                    source,
                    csv_row_number
                )
                VALUES (
                    :id,
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
                "id": str(uuid.uuid4()),
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
        ): str(row["id"])
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
    inserted = 0
    existing_links = {
        (str(row["additional_hazard_profile_id"]), str(row["question_option_id"]))
        for row in connection.execute(
            text(
                "SELECT additional_hazard_profile_id, question_option_id "
                "FROM additional_hazard_profile_target_populations"
            )
        ).mappings()
    }
    for row in profile_rows:
        option_ids: set[str] = set()
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
            link_key = (str(row["id"]), option_id)
            if link_key in existing_links:
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO additional_hazard_profile_target_populations (
                        id,
                        additional_hazard_profile_id,
                        question_option_id
                    )
                    VALUES (:id, :profile_id, :option_id)
                    """
                ),
                {"id": str(uuid.uuid4()), "profile_id": str(row["id"]), "option_id": option_id},
            )
            existing_links.add(link_key)
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
    system_hazard_id: str | None,
    profile_rows: list[dict[str, object]],
) -> str | None:
    if system_hazard_id is None:
        return None

    profile_key = _normalize_mitigation_example_key(profile_label)
    if not profile_key:
        return None

    same_hazard_rows = [
        row for row in profile_rows if row.get("system_hazard_id") == system_hazard_id
    ]
    exact_matches: list[str] = []
    fallback_matches: list[str] = []
    for row in same_hazard_rows:
        row_id = row.get("id")
        if row_id is None:
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

    inserted = 0
    skipped = 0
    existing_rows = {
        int(row["csv_row_number"])
        for row in connection.execute(
            text(
                "SELECT csv_row_number FROM mitigation_measure_examples "
                "WHERE source = 'mm_csv' AND csv_row_number IS NOT NULL"
            )
        ).mappings()
    }
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
        if csv_index in existing_rows:
            skipped += 1
            continue

        system_hazard_id = hazard_by_key.get(
            (sector_id, _normalize_mitigation_example_key(hazard_name))
        )
        profile_id = _resolve_mitigation_profile_id(
            profile_label,
            system_hazard_id if system_hazard_id else None,
            profile_rows,
        )
        connection.execute(
            text(
                """
                INSERT INTO mitigation_measure_examples (
                    id,
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
                    :id,
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
                "id": str(uuid.uuid4()),
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
        existing_rows.add(csv_index)
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
        str(row["map_code"] or "").casefold(): str(row["id"])
        for row in connection.execute(
            text("SELECT id, map_code FROM countries WHERE map_code IS NOT NULL")
        ).mappings()
    }
    option_by_group = _mm_target_group_option_map(connection)

    policy_ids: dict[tuple[str, str | None], str] = {}
    policy_rows: dict[tuple[str, str | None], dict[str, object]] = {}
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
    for policy_key, policy_row in policy_rows.items():
        existing_policy_id = connection.execute(
            text(
                "SELECT id FROM mitigation_measure_policies "
                "WHERE policy_code = :policy_code AND source = 'xlsx' "
                "AND ((sector_id = :sector_id) OR (sector_id IS NULL AND :sector_id IS NULL)) "
                "LIMIT 1"
            ),
            {"policy_code": policy_row["policy_code"], "sector_id": policy_row["sector_id"]},
        ).scalar()
        policy_id = str(existing_policy_id or uuid.uuid4())
        if existing_policy_id is None:
            connection.execute(
                text(
                    """
                    INSERT INTO mitigation_measure_policies (
                        id,
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
                        :id,
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
                {"id": policy_id, **policy_row},
            )
        policy_ids[
            policy_key
        ] = policy_id

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
            existing_mapping = connection.execute(
                text(
                    "SELECT 1 FROM mitigation_measure_target_groups "
                    "WHERE mitigation_measure_policy_id = :policy_id "
                    "AND question_option_id = :option_id LIMIT 1"
                ),
                {"policy_id": policy_id, "option_id": question_option_id},
            ).scalar()
            if existing_mapping is not None:
                skipped += 1
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO mitigation_measure_target_groups (
                        id,
                        mitigation_measure_policy_id,
                        question_option_id,
                        match_value,
                        source,
                        excel_column_number
                    )
                    VALUES (
                        :id,
                        :policy_id,
                        :question_option_id,
                        :match_value,
                        'xlsx',
                        :excel_column_number
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
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


def _seed_policies_xlsx(connection) -> None:
    rows = _read_policies_xlsx_rows()
    if not rows:
        return

    country_by_name = {
        _normalize_mitigation_example_key(str(row["name"] or "")): str(row["id"])
        for row in connection.execute(text("SELECT id, name FROM countries")).mappings()
    }
    sector_by_name = {
        _normalize_mitigation_example_key(str(row["name"] or "")): str(row["id"])
        for row in connection.execute(text("SELECT id, name FROM sectors")).mappings()
    }
    inserted = 0
    skipped = 0
    existing_rows = {
        int(row["excel_row_number"])
        for row in connection.execute(
            text(
                "SELECT excel_row_number FROM policies "
                "WHERE source = 'xlsx' AND excel_row_number IS NOT NULL"
            )
        ).mappings()
    }
    for row in rows:
        country_id = country_by_name.get(_normalize_mitigation_example_key(str(row["country"] or "")))
        sector_id = sector_by_name.get(_normalize_mitigation_example_key(str(row["sector"] or "")))
        if country_id is None or sector_id is None:
            skipped += 1
            continue
        row_number = int(row["excel_row_number"])
        if row_number in existing_rows:
            skipped += 1
            continue
        connection.execute(
            text(
                """
                INSERT INTO policies (
                    id, country_id, sector_id, policy, policy_url, language,
                    policy_type, source, excel_row_number
                )
                VALUES (
                    :id, :country_id, :sector_id, :policy, :policy_url, :language,
                    :policy_type, 'xlsx', :excel_row_number
                )
                """
            ),
            {
                "id": str(uuid.uuid4()), "country_id": country_id, "sector_id": sector_id,
                "policy": row["policy"], "policy_url": row["policy_url"] or None,
                "language": row["language"] or None, "policy_type": row["policy_type"] or None,
                "excel_row_number": row["excel_row_number"],
            },
        )
        existing_rows.add(row_number)
        inserted += 1

    logger.info(
        "Loaded %s policies from kb/additional/Policies.xlsx; skipped %s rows",
        inserted, skipped,
    )


def _seed_sectoral_challenge_policy_additional_hazards(connection) -> None:
    rows = _read_sectoral_challenges_xlsx_rows()
    if not rows:
        return

    policy_ids_by_code_country: dict[tuple[str, str], list[str]] = {}
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
            (str(row["policy_code"]), str(row["country_id"])),
            [],
        ).append(str(row["id"]))
    hazard_by_country_name = {
        (
            str(row["country_id"]),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): str(row["id"])
        for row in connection.execute(
            text(
                """
                SELECT id, country_id, name
                FROM additional_hazards
                """
            )
        ).mappings()
    }
    inserted = 0
    skipped = 0
    seen_links = {
        (str(row["mitigation_measure_policy_id"]), str(row["additional_hazard_id"]))
        for row in connection.execute(
            text(
                "SELECT mitigation_measure_policy_id, additional_hazard_id "
                "FROM mitigation_measure_policy_additional_hazards"
            )
        ).mappings()
    }
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
                link_key = (policy_id, additional_hazard_id)
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)
                connection.execute(
                    text(
                        """
                        INSERT INTO mitigation_measure_policy_additional_hazards (
                            id,
                            mitigation_measure_policy_id,
                            additional_hazard_id,
                            match_value,
                            source,
                            excel_row_number,
                            excel_column_number
                        )
                        VALUES (
                            :id,
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
                        "id": str(uuid.uuid4()),
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

    policy_ids_by_code: dict[str, list[str]] = {}
    for row in connection.execute(
        text(
            """
            SELECT id, policy_code
            FROM mitigation_measure_policies
            WHERE source = 'xlsx'
            """
        )
    ).mappings():
        policy_code = str(row["policy_code"] or "").strip()
        if policy_code:
            policy_ids_by_code.setdefault(policy_code, []).append(str(row["id"]))

    hazard_by_sector_name = {
        (
            _normalize_mitigation_example_key(str(row["sector_name"] or "")),
            _normalize_mitigation_example_key(str(row["name"] or "")),
        ): str(row["id"])
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

    inserted = 0
    skipped = 0
    seen_links = {
        (str(row["mitigation_measure_policy_id"]), str(row["system_hazard_id"]))
        for row in connection.execute(
            text(
                "SELECT mitigation_measure_policy_id, system_hazard_id "
                "FROM mitigation_measure_policy_system_hazards"
            )
        ).mappings()
    }
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
        for policy_id in policy_ids_by_code.get(policy_code, []):
            link_key = (policy_id, system_hazard_id)
            if link_key in seen_links:
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO mitigation_measure_policy_system_hazards (
                        id,
                        mitigation_measure_policy_id,
                        system_hazard_id,
                        mitigation_effect,
                        source,
                        excel_row_number,
                        excel_column_number
                    )
                    VALUES (
                        :id,
                        :policy_id,
                        :system_hazard_id,
                        :mitigation_effect,
                        'xlsx',
                        :excel_row_number,
                        :excel_column_number
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "policy_id": policy_id,
                    "system_hazard_id": system_hazard_id,
                    "mitigation_effect": mitigation_effect,
                    "excel_row_number": row.get("excel_row_number"),
                    "excel_column_number": row.get("excel_column_number"),
                },
            )
            seen_links.add(link_key)
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
    """Refresh workbook mappings so newly available hazards are linked on existing installs."""
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
    _seed_hazards_xlsx_policy_system_hazards(connection)


def _mm_target_group_sector_ids(
    sector_name: str,
    sector_by_key: dict[str, str],
) -> list[str | None]:
    exact_sector_id = sector_by_key.get(_normalize_mitigation_example_key(sector_name))
    if exact_sector_id is not None:
        return [str(exact_sector_id)]

    normalized = _normalize_mitigation_example_key(sector_name)
    sector_ids: list[str] = []
    for sector_label, sector_id in sector_by_key.items():
        if sector_label and sector_label in normalized:
            sector_ids.append(str(sector_id))
    if sector_ids:
        return sorted(set(sector_ids))
    return [None]


def _mm_policy_country_id(
    policy_code: str,
    country_by_map_code: dict[str, str],
) -> str | None:
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
        {"question_id": str(age_question_id)},
    ).scalar()
    if existing is None:
        connection.execute(
            text(
                """
                INSERT INTO question_options (id, questionId, `option`)
                VALUES (:id, :question_id, '18-25')
                """
            ),
            {"id": str(uuid.uuid4()), "question_id": str(age_question_id)},
        )


def _mm_target_group_option_map(connection) -> dict[tuple[str, str], str]:
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
        ): str(row["id"])
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
        _seed_system_hazards_from_sector_prompts(connection)
        _seed_mm_csv_mitigation_measure_examples(connection)
        _seed_mm_target_group_xlsx(connection)
        _seed_hazards_xlsx_policy_system_hazards(connection)
        _seed_policies_xlsx(connection)


