import logging

from sqlalchemy import inspect, text

from app.db.schema_type_helpers import mysql_question_option_id_type
from app.db.session import engine


logger = logging.getLogger(__name__)


def ensure_reference_data_schema() -> None:
    ensure_additional_hazard_schema()
    ensure_mitigation_measure_schema()


def ensure_additional_hazard_schema() -> None:
    with engine.begin() as connection:
        question_option_id_type = mysql_question_option_id_type(connection)
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS additional_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  country_id INT NOT NULL,
                  sector_id INT NOT NULL,
                  name VARCHAR(255) NOT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'csv',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazards_country
                    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
                  CONSTRAINT fk_additional_hazards_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_scope_name
                    UNIQUE (country_id, sector_id, name),
                  INDEX ix_additional_hazards_country_id (country_id),
                  INDEX ix_additional_hazards_sector_id (sector_id),
                  INDEX ix_additional_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS additional_hazard_profiles (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  additional_hazard_id INT NOT NULL,
                  profile VARCHAR(255) NOT NULL,
                  evidence TEXT NULL,
                  reference TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'd4_2_pdf',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazard_profiles_hazard
                    FOREIGN KEY (additional_hazard_id)
                    REFERENCES additional_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_profile
                    UNIQUE (additional_hazard_id, profile),
                  INDEX ix_additional_hazard_profiles_hazard_id (additional_hazard_id),
                  INDEX ix_additional_hazard_profiles_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS additional_hazard_profile_target_populations (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  additional_hazard_profile_id INT NOT NULL,
                  question_option_id {question_option_id_type} NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_additional_hazard_profile_target_profile
                    FOREIGN KEY (additional_hazard_profile_id)
                    REFERENCES additional_hazard_profiles(id) ON DELETE CASCADE,
                  CONSTRAINT fk_additional_hazard_profile_target_option
                    FOREIGN KEY (question_option_id)
                    REFERENCES question_options(id) ON DELETE CASCADE,
                  CONSTRAINT uq_additional_hazard_profile_target_option
                    UNIQUE (additional_hazard_profile_id, question_option_id),
                  INDEX ix_additional_hazard_profile_target_profile (additional_hazard_profile_id),
                  INDEX ix_additional_hazard_profile_target_option (question_option_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )


def ensure_mitigation_measure_schema() -> None:
    inspector = inspect(engine)
    if "mitigation_measure_target_groups" in inspector.get_table_names():
        target_group_columns = {
            column["name"]
            for column in inspector.get_columns("mitigation_measure_target_groups")
        }
        if "mitigation_measure_policy_id" not in target_group_columns:
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE mitigation_measure_target_groups"))

    with engine.begin() as connection:
        question_option_id_type = mysql_question_option_id_type(connection)
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_examples (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  sector_id INT NOT NULL,
                  system_hazard_id INT NULL,
                  system_hazard_socio_demographic_id INT NULL,
                  profile_label VARCHAR(255) NULL,
                  measure TEXT NOT NULL,
                  policy_case_study TEXT NULL,
                  country_city VARCHAR(255) NULL,
                  implementation_summary TEXT NULL,
                  evidence TEXT NULL,
                  reference_links TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'seed',
                  csv_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_examples_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_examples_hazard
                    FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL,
                  CONSTRAINT fk_mitigation_examples_profile
                    FOREIGN KEY (system_hazard_socio_demographic_id)
                    REFERENCES system_hazard_socio_demographics(id) ON DELETE SET NULL,
                  INDEX ix_mitigation_measure_examples_sector_id (sector_id),
                  INDEX ix_mitigation_measure_examples_hazard_id (system_hazard_id),
                  INDEX ix_mitigation_measure_examples_profile_id (system_hazard_socio_demographic_id),
                  INDEX ix_mitigation_measure_examples_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policies (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  policy_code VARCHAR(80) NOT NULL,
                  policy_title TEXT NOT NULL,
                  country_id INT NULL,
                  sector_id INT NULL,
                  policy_type VARCHAR(120) NULL,
                  short_description TEXT NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policies_sector
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
                  CONSTRAINT fk_mitigation_policies_country
                    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
                  CONSTRAINT uq_mitigation_policy_code_sector_source UNIQUE (policy_code, sector_id, source),
                  INDEX ix_mitigation_policies_policy_code (policy_code),
                  INDEX ix_mitigation_policies_country_id (country_id),
                  INDEX ix_mitigation_policies_sector_id (sector_id),
                  INDEX ix_mitigation_policies_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS mitigation_measure_target_groups (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  question_option_id {question_option_id_type} NOT NULL,
                  match_value VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_target_groups_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_target_groups_option
                    FOREIGN KEY (question_option_id)
                    REFERENCES question_options(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_target_group_xlsx_cell
                    UNIQUE (mitigation_measure_policy_id, question_option_id),
                  INDEX ix_mitigation_target_groups_policy_id (mitigation_measure_policy_id),
                  INDEX ix_mitigation_target_groups_option_id (question_option_id),
                  INDEX ix_mitigation_target_groups_match_value (match_value),
                  INDEX ix_mitigation_target_groups_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policy_additional_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  additional_hazard_id INT NOT NULL,
                  match_value VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policy_hazards_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_policy_hazards_additional_hazard
                    FOREIGN KEY (additional_hazard_id)
                    REFERENCES additional_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_policy_additional_hazard
                    UNIQUE (mitigation_measure_policy_id, additional_hazard_id),
                  INDEX ix_mitigation_policy_hazards_policy_id (mitigation_measure_policy_id),
                  INDEX ix_mitigation_policy_hazards_additional_hazard_id (additional_hazard_id),
                  INDEX ix_mitigation_policy_hazards_match_value (match_value),
                  INDEX ix_mitigation_policy_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mitigation_measure_policy_system_hazards (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  mitigation_measure_policy_id INT NOT NULL,
                  system_hazard_id INT NOT NULL,
                  mitigation_effect VARCHAR(40) NULL,
                  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
                  excel_row_number INT NULL,
                  excel_column_number INT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_mitigation_policy_system_hazards_policy
                    FOREIGN KEY (mitigation_measure_policy_id)
                    REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_mitigation_policy_system_hazards_hazard
                    FOREIGN KEY (system_hazard_id)
                    REFERENCES system_hazards(id) ON DELETE CASCADE,
                  CONSTRAINT uq_mitigation_policy_system_hazard
                    UNIQUE (mitigation_measure_policy_id, system_hazard_id),
                  INDEX ix_mitigation_policy_system_hazards_policy_id
                    (mitigation_measure_policy_id),
                  INDEX ix_mitigation_policy_system_hazards_hazard_id
                    (system_hazard_id),
                  INDEX ix_mitigation_policy_system_hazards_effect (mitigation_effect),
                  INDEX ix_mitigation_policy_system_hazards_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

    _repair_mitigation_measure_schema()


def _repair_mitigation_measure_schema() -> None:
    inspector = inspect(engine)
    policy_columns = {
        column["name"]
        for column in inspector.get_columns("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else set()
    policy_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else {}
    policy_foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("mitigation_measure_policies")
    } if "mitigation_measure_policies" in inspector.get_table_names() else set()
    with engine.begin() as connection:
        if "country_id" not in policy_columns:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD COLUMN country_id INT NULL AFTER policy_title"
                )
            )
            policy_columns.add("country_id")
        connection.execute(
            text(
                """
                UPDATE mitigation_measure_policies policies
                JOIN countries
                  ON LOWER(countries.map_code) = LOWER(SUBSTRING_INDEX(policies.policy_code, '_', 1))
                SET policies.country_id = countries.id
                WHERE policies.country_id IS NULL
                """
            )
        )
        if "ix_mitigation_policies_country_id" not in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD INDEX ix_mitigation_policies_country_id (country_id)"
                )
            )
            policy_indexes["ix_mitigation_policies_country_id"] = {}
        if "fk_mitigation_policies_country" not in policy_foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_policies "
                        "ADD CONSTRAINT fk_mitigation_policies_country "
                        "FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation policy country foreign key; continuing",
                    exc_info=True,
                )
        if "uq_mitigation_policy_code_source" in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "DROP INDEX uq_mitigation_policy_code_source"
                )
            )
            policy_indexes.pop("uq_mitigation_policy_code_source", None)
        if "uq_mitigation_policy_code_sector_source" not in policy_indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_policies "
                    "ADD CONSTRAINT uq_mitigation_policy_code_sector_source "
                    "UNIQUE (policy_code, sector_id, source)"
                )
            )

    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("mitigation_measure_examples")
    }
    indexes = {
        index["name"]
        for index in inspector.get_indexes("mitigation_measure_examples")
    }
    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("mitigation_measure_examples")
    }

    with engine.begin() as connection:
        if "sector_id" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD COLUMN sector_id INT NULL AFTER id"
                )
            )
            columns.add("sector_id")
        new_columns = {
            "system_hazard_id": "INT NULL AFTER sector_id",
            "system_hazard_socio_demographic_id": "INT NULL AFTER system_hazard_id",
            "profile_label": "VARCHAR(255) NULL AFTER system_hazard_socio_demographic_id",
            "policy_case_study": "TEXT NULL AFTER measure",
            "country_city": "VARCHAR(255) NULL AFTER policy_case_study",
            "implementation_summary": "TEXT NULL AFTER country_city",
            "evidence": "TEXT NULL AFTER implementation_summary",
            "reference_links": "TEXT NULL AFTER evidence",
            "source": "VARCHAR(40) NOT NULL DEFAULT 'seed' AFTER reference_links",
            "csv_row_number": "INT NULL AFTER source",
        }
        for column_name, column_definition in new_columns.items():
            if column_name not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
                )
                columns.add(column_name)
        if "sector_name" in columns:
            connection.execute(
                text(
                    """
                    UPDATE mitigation_measure_examples examples
                    JOIN sectors ON LOWER(sectors.name) = LOWER(examples.sector_name)
                    SET examples.sector_id = sectors.id
                    WHERE examples.sector_id IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    DELETE newer
                    FROM mitigation_measure_examples newer
                    JOIN mitigation_measure_examples older
                      ON newer.id > older.id
                     AND newer.sector_id = older.sector_id
                     AND newer.measure = older.measure
                    WHERE newer.sector_id IS NOT NULL
                    """
                )
            )
            if "uq_mitigation_example_sector_measure" in indexes:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP INDEX uq_mitigation_example_sector_measure"
                    )
                )
                indexes.remove("uq_mitigation_example_sector_measure")
            if "ix_mitigation_measure_examples_sector_name" in indexes:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP INDEX ix_mitigation_measure_examples_sector_name"
                    )
                )
                indexes.remove("ix_mitigation_measure_examples_sector_name")
            connection.execute(
                text(
                    "DELETE FROM mitigation_measure_examples "
                    "WHERE sector_id IS NULL"
                )
            )
            if "fk_mitigation_examples_sector" in foreign_keys:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "DROP FOREIGN KEY fk_mitigation_examples_sector"
                    )
                )
                foreign_keys.remove("fk_mitigation_examples_sector")
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "MODIFY COLUMN sector_id INT NOT NULL"
                )
            )
            connection.execute(
                text("ALTER TABLE mitigation_measure_examples DROP COLUMN sector_name")
            )
            columns.remove("sector_name")
        if "ix_mitigation_measure_examples_sector_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_sector_id (sector_id)"
                )
            )
        if "uq_mitigation_example_sector_measure" in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "DROP INDEX uq_mitigation_example_sector_measure"
                )
            )
            indexes.remove("uq_mitigation_example_sector_measure")
        if "ix_mitigation_measure_examples_hazard_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_hazard_id (system_hazard_id)"
                )
            )
        if "ix_mitigation_measure_examples_profile_id" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_profile_id "
                    "(system_hazard_socio_demographic_id)"
                )
            )
        if "ix_mitigation_measure_examples_source" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE mitigation_measure_examples "
                    "ADD INDEX ix_mitigation_measure_examples_source (source)"
                )
            )
        if "fk_mitigation_examples_sector" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_sector "
                        "FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example sector foreign key; continuing",
                    exc_info=True,
                )
        if "fk_mitigation_examples_hazard" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_hazard "
                        "FOREIGN KEY (system_hazard_id) "
                        "REFERENCES system_hazards(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example hazard foreign key; continuing",
                    exc_info=True,
                )
        if "fk_mitigation_examples_profile" not in foreign_keys:
            try:
                connection.execute(
                    text(
                        "ALTER TABLE mitigation_measure_examples "
                        "ADD CONSTRAINT fk_mitigation_examples_profile "
                        "FOREIGN KEY (system_hazard_socio_demographic_id) "
                        "REFERENCES system_hazard_socio_demographics(id) ON DELETE SET NULL"
                    )
                )
            except Exception:
                logger.warning(
                    "Could not add mitigation example profile foreign key; continuing",
                    exc_info=True,
                )
