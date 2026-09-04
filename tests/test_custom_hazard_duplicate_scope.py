import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


class _ScalarRows:
    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def all(self) -> list[str]:
        return self._rows


class _RecordingDb:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def scalars(self, statement: object) -> _ScalarRows:
        self.statements.append(statement)
        return _ScalarRows([])


class CustomHazardDuplicateScopeTests(unittest.TestCase):
    def test_system_duplicate_lookup_filters_by_sector_only(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = _RecordingDb()
        session = ChatSession(country_id=1, region_id=2, sector_id=3)

        names = service._same_sector_hazard_names_for_duplicate_check(session)

        self.assertEqual(names, [])
        self.assertEqual(len(service.db.statements), 1)
        query = str(service.db.statements[0])
        self.assertIn("system_hazards.sector_id", query)
        self.assertNotIn("country_id", query)
        self.assertNotIn("region_id", query)

    def test_custom_duplicate_lookup_requires_complete_scope(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = _RecordingDb()
        session = ChatSession(country_id=None, region_id=2, sector_id=3)

        names = service._same_scope_custom_hazard_names_for_duplicate_check(session)

        self.assertEqual(names, [])
        self.assertEqual(service.db.statements, [])

    def test_custom_duplicate_lookup_filters_by_country_region_and_sector(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = _RecordingDb()
        session = ChatSession(country_id=1, region_id=2, sector_id=3)

        names = service._same_scope_custom_hazard_names_for_duplicate_check(session)

        self.assertEqual(names, [])
        self.assertEqual(len(service.db.statements), 2)
        custom_query = str(service.db.statements[0])
        user_query = str(service.db.statements[1])
        self.assertIn("custom_hazards.country_id", custom_query)
        self.assertIn("custom_hazards.sector_id", custom_query)
        self.assertIn("custom_hazards.region_scope_key", custom_query)
        self.assertIn("user_hazards.source", user_query)
        self.assertIn("user_hazards.sector_id", user_query)
        self.assertIn("user_hazards.region_id", user_query)
        self.assertIn("user_sessions.country_id", user_query)
        self.assertIn("user_sessions.sector_id", user_query)
        self.assertIn("user_sessions.region_id", user_query)

    def test_custom_duplicate_lookup_dedupes_custom_names(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    _ScalarRows(["Heating cost shock", "Heating cost shock"]),
                    _ScalarRows(["heating  cost shock", "Regional job loss"]),
                ]
            )
        )
        session = ChatSession(country_id=1, region_id=2, sector_id=3)

        names = service._same_scope_custom_hazard_names_for_duplicate_check(session)

        self.assertEqual(names, ["Heating cost shock", "Regional job loss"])

    def test_additional_duplicate_lookup_filters_by_country_and_sector(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = _RecordingDb()
        session = ChatSession(country_id=1, region_id=2, sector_id=3)

        names = service._same_country_sector_additional_hazard_names_for_duplicate_check(
            session
        )

        self.assertEqual(names, [])
        self.assertEqual(len(service.db.statements), 1)
        query = str(service.db.statements[0])
        self.assertIn("additional_hazards.country_id", query)
        self.assertIn("additional_hazards.sector_id", query)
        self.assertNotIn("region_id", query)

    def test_combined_duplicate_scope_uses_each_hazard_source(self) -> None:
        service = ChatService.__new__(ChatService)
        service._same_sector_hazard_names_for_duplicate_check = MagicMock(
            return_value=["System hazard"]
        )
        service._same_scope_custom_hazard_names_for_duplicate_check = MagicMock(
            return_value=["Custom hazard"]
        )
        service._same_country_sector_additional_hazard_names_for_duplicate_check = MagicMock(
            return_value=["Additional hazard"]
        )
        session = ChatSession(country_id=1, region_id=2, sector_id=3)

        names = service._duplicate_hazard_names_for_check(session)

        self.assertEqual(names, ["System hazard", "Custom hazard", "Additional hazard"])

    def test_possible_duplicate_message_includes_saved_summary(self) -> None:
        service = ChatService.__new__(ChatService)
        service.user_id = "user-1"
        service.db = SimpleNamespace(
            scalar=MagicMock(return_value="A concise summary of the existing hazard.")
        )
        session = ChatSession(
            country_id="country-1",
            region_id="region-1",
            sector_id="sector-1",
            custom_hazard={},
        )

        response = service._hazard_duplicate_suggestion_step(
            "session-1",
            session,
            "Proposed hazard",
            "Existing custom hazard",
            "These hazards appear similar.",
        )

        self.assertIn("Existing custom hazard", response.bot_message)
        self.assertIn("<strong>Summary:</strong>", response.bot_message)
        self.assertIn("A concise summary of the existing hazard", response.bot_message)

    def test_duplicate_summary_fallback_supports_outdated_db_prompt(self) -> None:
        service = ChatService.__new__(ChatService)
        old_message = (
            "<p>Suggested existing hazard:</p>"
            "<ul><li><strong>Existing hazard</strong></li></ul>"
            "<p>These hazards appear similar.</p>"
        )

        message = service._ensure_duplicate_hazard_summary_visible(
            old_message,
            "A summary with <internal> details.",
        )

        self.assertIn("<strong>Summary:</strong>", message)
        self.assertIn("A summary with &lt;internal&gt; details.", message)
        self.assertLess(message.index("Summary:"), message.index("These hazards"))


if __name__ == "__main__":
    unittest.main()
