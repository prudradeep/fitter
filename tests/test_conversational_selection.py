import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.conversational_selection import resolve_selection


def _run(coro):
    return asyncio.run(coro)


class ConversationalSelectionResolverTests(unittest.TestCase):
    def _resolve(self, llm_json: str, text: str = "I want Germany"):
        with patch(
            "app.services.conversational_selection.ask_llm_chat",
            new=AsyncMock(return_value=llm_json),
        ):
            return _run(
                resolve_selection(
                    user_text=text,
                    available_countries=["Germany", "Spain"],
                    available_regions=["Baden-Wurttemberg", "Berlin", "Catalonia"],
                    available_sectors=["Energy", "Housing", "Transport"],
                    current_phase="country",
                )
            )

    def test_exact_country_match(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": null, '
            '"sector": null, "confidence": "high", "reason": "Exact country."}',
            "Germany",
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["country"], "Germany")

    def test_synonym_match_uses_backend_name(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": null, '
            '"sector": null, "confidence": "high", "reason": "Deutschland maps to Germany."}',
            "Deutschland",
        )

        self.assertEqual(result["country"], "Germany")

    def test_mixed_natural_language_multiple_selection(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": null, '
            '"sector": "Housing", "confidence": "high", "reason": "Country and sector."}',
            "I'd like Germany in the Housing sector",
        )

        self.assertEqual(result["country"], "Germany")
        self.assertEqual(result["sector"], "Housing")

    def test_complete_selection(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": "Baden-Wurttemberg", '
            '"sector": "Housing", "confidence": "high", "reason": "All supplied."}',
            "Germany Baden-Wurttemberg Housing",
        )

        self.assertEqual(result["country"], "Germany")
        self.assertEqual(result["region"], "Baden-Wurttemberg")
        self.assertEqual(result["sector"], "Housing")

    def test_invalid_country_is_unmatched(self):
        result = self._resolve(
            '{"matched": true, "country": "Canada", "region": null, '
            '"sector": null, "confidence": "high", "reason": "Country."}',
            "Canada",
        )

        self.assertFalse(result["matched"])

    def test_invalid_region_is_unmatched(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": "Lisbon", '
            '"sector": null, "confidence": "high", "reason": "Region."}',
            "Germany Lisbon",
        )

        self.assertFalse(result["matched"])

    def test_invalid_sector_is_unmatched(self):
        result = self._resolve(
            '{"matched": true, "country": "Germany", "region": null, '
            '"sector": "Agriculture", "confidence": "high", "reason": "Sector."}',
            "Germany Agriculture",
        )

        self.assertFalse(result["matched"])

    def test_random_text_is_unmatched(self):
        result = self._resolve(
            '{"matched": false, "country": null, "region": null, '
            '"sector": null, "confidence": "low", "reason": "No selection."}',
            "I don't know",
        )

        self.assertFalse(result["matched"])
