import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.seed import reference_data


class ReferenceDataSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_hazard_names_from_sector_prompt_extracts_section_five_hazards(self) -> None:
        prompt = """
SECTION 3. HAZARDS
HAZARD 99. Not the authoritative section

SECTION 5. PER-HAZARD CONFIRMED PREDICTORS
HAZARD 1. Higher electricity bills
PREDICTOR 1. Income

HAZARD 2: Heating and cooling costs increase
PREDICTOR 1. Age

SECTION 6. OTHER CONTENT
HAZARD 3. Ignore me
"""

        self.assertEqual(
            reference_data._hazard_names_from_sector_prompt(prompt),
            ["Higher electricity bills", "Heating and cooling costs increase"],
        )

    def test_seed_system_hazards_from_sector_prompts_inserts_missing_hazards(self) -> None:
        prompt = """
SECTION 5. PER-HAZARD CONFIRMED PREDICTORS
HAZARD 1. Higher electricity bills
PREDICTOR 1. Income

HAZARD 2. Higher electricity bills
PREDICTOR 1. Duplicate

HAZARD 3. Heating and cooling costs increase
PREDICTOR 1. Age
"""
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO sectors (id, name) VALUES ('energy-sector', 'Energy')")
            )
            with (
                patch.object(reference_data, "PROMPT_FILES", {"energy": "Energy_truth.txt"}),
                patch.object(reference_data, "load_sector_prompt", return_value=prompt),
            ):
                reference_data._seed_system_hazards_from_sector_prompts(connection)
                reference_data._seed_system_hazards_from_sector_prompts(connection)

            rows = connection.execute(
                text(
                    """
                    SELECT name
                    FROM system_hazards
                    WHERE sector_id = 'energy-sector'
                    ORDER BY name
                    """
                )
            ).scalars().all()

        self.assertEqual(
            rows,
            ["Heating and cooling costs increase", "Higher electricity bills"],
        )


if __name__ == "__main__":
    unittest.main()
