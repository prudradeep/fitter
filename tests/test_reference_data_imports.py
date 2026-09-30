import unittest

from app.seed.reference_data import _read_sectoral_challenges_xlsx_rows


class ReferenceDataImportTests(unittest.TestCase):
    def test_sectoral_challenges_imports_rows_from_all_country_worksheets(self):
        rows = [
            row
            for row in _read_sectoral_challenges_xlsx_rows()
            if row["policy_code"] == "IT_EN_AD_4"
        ]

        self.assertEqual(len(rows), 12)
        self.assertEqual(
            rows[0]["additional_hazard"], "Policy gaps & governance fragmentation"
        )
        self.assertEqual(rows[0]["match_value"], "Secondary")


if __name__ == "__main__":
    unittest.main()
