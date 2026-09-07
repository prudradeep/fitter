import asyncio
import unittest

from app.routes.api import hazard_effect_size, hazard_salience
from app.services.hazard_effect_size import hazard_predictor_effect_rows
from app.services.hazard_salience import hazard_concern_rows, hazard_concern_values


class HazardMetricDataTests(unittest.TestCase):
    def test_salience_returns_only_the_related_concern_values_for_the_table(self):
        expected = hazard_concern_values(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
            region="Trentino-Alto Adige",
        )
        expected_rows = hazard_concern_rows(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
            region="Trentino-Alto Adige",
        )

        payload = asyncio.run(
            hazard_salience(
                country="Italy",
                sector="Transport",
                region="Trentino-Alto Adige",
                hazard="more_pollution_exposure",
                current_user=object(),
            )
        )

        self.assertTrue(expected)
        self.assertEqual(
            payload["calculation_data"],
            {
                "region_column": "Region",
                "regions": [row["region"] for row in expected_rows],
                "column": "Concern score",
                "values": expected,
            },
        )

    def test_salience_region_limits_the_source_survey_rows(self):
        national = hazard_concern_values(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
        )
        regional = hazard_concern_values(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
            region="Trentino-Alto Adige",
        )

        self.assertTrue(regional)
        self.assertLess(len(regional), len(national))

    def test_salience_maps_selected_region_to_the_survey_region_label(self):
        selected_region = hazard_concern_values(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
            region="Trentino-Alto Adige",
        )
        survey_region = hazard_concern_values(
            country="Italy",
            sector="Transport",
            hazard_column="hazard_pollution_exposure",
            region="Trentino-South Tyrol",
        )

        self.assertTrue(selected_region)
        self.assertEqual(selected_region, survey_region)

    def test_effect_size_returns_only_the_related_odds_ratios_for_the_table(self):
        expected = hazard_predictor_effect_rows(
            sector="Transport",
            hazard="more_pollution_exposure",
            min_or=1.0,
        )
        payload = asyncio.run(
            hazard_effect_size(
                sector="Transport",
                hazard="more_pollution_exposure",
                min_or=1.0,
                current_user=object(),
            )
        )

        calculation_data = payload["calculation_data"]
        self.assertEqual(
            set(calculation_data),
            {"predictor_column", "predictors", "column", "values"},
        )
        self.assertEqual(calculation_data["predictor_column"], "Predictor")
        self.assertEqual(calculation_data["column"], "Odds ratio (OR)")
        self.assertTrue(calculation_data["values"])
        self.assertTrue(all(value >= 1.0 for value in calculation_data["values"]))
        self.assertEqual(
            list(zip(calculation_data["predictors"], calculation_data["values"])),
            [(row["predictor"], row["odds_ratio"]) for row in expected],
        )


if __name__ == "__main__":
    unittest.main()
