import asyncio
import unittest

from app.services.eurostat_service import EurostatService
from app.services.reach_service import ReachService


class ReachServiceProfileTests(unittest.TestCase):
    def test_protective_predictors_are_excluded_from_reach_profiles(self):
        result = asyncio.run(
            ReachService(EurostatService()).hazard_reach(
                sector="Energy",
                hazard="heating_and_cooling_costs_increase",
                hazard_name="Heating and cooling costs increase",
                country="Germany",
                region="Baden-Württemberg",
            )
        )

        self.assertFalse(
            any(
                profile["predictor"] == "religious_minority__Yes"
                for profile in result["profiles"]
            )
        )
        self.assertEqual(result["used_predictors"], 3)


if __name__ == "__main__":
    unittest.main()
