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


if __name__ == "__main__":
    unittest.main()
