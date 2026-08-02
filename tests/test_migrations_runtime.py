import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import ANY, MagicMock, patch

from app.db.legacy_schema_repair import run_legacy_schema_repair
from app.db.migrations_runtime import (
    run_runtime_migrations,
    run_schema_sql,
    should_apply_schema_basic_data,
)


class RuntimeMigrationTests(unittest.TestCase):
    def test_runtime_migrations_do_not_run_legacy_repair_by_default(self) -> None:
        with (
            patch("app.db.migrations_runtime.run_schema_sql") as run_schema,
            patch("app.db.migrations_runtime.apply_versioned_migrations") as apply_versioned,
            patch("app.db.migrations_runtime.Base.metadata.create_all") as create_all,
            patch("app.db.migrations_runtime.ensure_runtime_schema") as ensure_runtime,
            patch("app.db.migrations_runtime.repair_partial_installer_schema") as repair_partial,
        ):
            run_runtime_migrations()

        run_schema.assert_not_called()
        apply_versioned.assert_called_once_with()
        create_all.assert_not_called()
        ensure_runtime.assert_not_called()
        repair_partial.assert_called_once_with()

    def test_legacy_repair_is_operator_only_path(self) -> None:
        with (
            patch("app.db.legacy_schema_repair.Base.metadata.create_all") as create_all,
            patch("app.db.legacy_schema_repair.ensure_runtime_schema") as ensure_runtime,
        ):
            run_legacy_schema_repair(seed_reference_data=True)

        create_all.assert_called_once_with(bind=ANY)
        ensure_runtime.assert_called_once_with(seed_reference_data=True)

    def test_seed_reference_data_without_legacy_repair_does_not_mutate_schema(self) -> None:
        fake_connection = object()
        begin = MagicMock()
        begin.__enter__.return_value = fake_connection
        with (
            patch("app.db.migrations_runtime.apply_versioned_migrations"),
            patch("app.db.migrations_runtime.ensure_reference_data_schema") as reference_schema,
            patch("app.db.migrations_runtime.ensure_additional_hazards") as additional_hazards,
            patch("app.db.migrations_runtime.ensure_system_hazards_from_sector_prompts") as system_hazards,
            patch("app.db.migrations_runtime.ensure_mitigation_measure_examples") as mitigation,
            patch("app.db.migrations_runtime._ensure_hazards_xlsx_policy_system_hazards") as hazards,
            patch("app.db.migrations_runtime.engine.begin", return_value=begin),
            patch("app.db.migrations_runtime.repair_partial_installer_schema"),
        ):
            run_runtime_migrations(seed_reference_data=True)

        reference_schema.assert_not_called()
        additional_hazards.assert_called_once_with()
        system_hazards.assert_called_once_with()
        mitigation.assert_called_once_with()
        hazards.assert_called_once_with(fake_connection)

    def test_runtime_migrations_can_include_base_schema_data_explicitly(self) -> None:
        with (
            patch("app.db.migrations_runtime.run_schema_sql") as run_schema,
            patch("app.db.migrations_runtime.apply_versioned_migrations"),
            patch("app.db.migrations_runtime.repair_partial_installer_schema"),
        ):
            run_runtime_migrations(apply_base_schema=True, include_basic_data=True)

        run_schema.assert_called_once_with(include_basic_data=True)

    def test_base_schema_skips_basic_data_when_disabled(self) -> None:
        executed = self._run_schema_sql(
            """
            CREATE TABLE countries (id INT);
            INSERT INTO countries (id) VALUES (1);
            REPLACE INTO countries (id) VALUES (2);
            """,
            include_basic_data=False,
        )

        self.assertEqual(executed, ["CREATE TABLE countries (id INT)"])

    def test_base_schema_includes_basic_data_when_enabled(self) -> None:
        executed = self._run_schema_sql(
            """
            CREATE TABLE countries (id INT);
            INSERT INTO countries (id) VALUES (1);
            REPLACE INTO countries (id) VALUES (2);
            """,
            include_basic_data=True,
        )

        self.assertEqual(
            executed,
            [
                "CREATE TABLE countries (id INT)",
                "INSERT INTO countries (id) VALUES (1)",
                "REPLACE INTO countries (id) VALUES (2)",
            ],
        )

    def test_schema_basic_data_is_server_only_by_default(self) -> None:
        with patch(
            "app.db.migrations_runtime.get_settings",
            return_value=SimpleNamespace(sync_mode="client"),
        ):
            self.assertFalse(should_apply_schema_basic_data())

        with patch(
            "app.db.migrations_runtime.get_settings",
            return_value=SimpleNamespace(sync_mode="server"),
        ):
            self.assertTrue(should_apply_schema_basic_data())

    def _run_schema_sql(self, sql: str, *, include_basic_data: bool) -> list[str]:
        with TemporaryDirectory() as temp_dir:
            schema_path = Path(temp_dir) / "schema.sql"
            schema_path.write_text(sql, encoding="utf-8")
            begin = MagicMock()
            connection = begin.__enter__.return_value
            with (
                patch("app.db.migrations_runtime.SCHEMA_PATH", schema_path),
                patch("app.db.migrations_runtime.engine.begin", return_value=begin),
            ):
                run_schema_sql(include_basic_data=include_basic_data)
            return [str(call.args[0]) for call in connection.execute.call_args_list]


if __name__ == "__main__":
    unittest.main()
