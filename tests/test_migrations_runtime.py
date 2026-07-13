import unittest
from unittest.mock import ANY, MagicMock, patch

from app.db.legacy_schema_repair import run_legacy_schema_repair
from app.db.migrations_runtime import run_runtime_migrations


class RuntimeMigrationTests(unittest.TestCase):
    def test_runtime_migrations_do_not_run_legacy_repair_by_default(self) -> None:
        with (
            patch("app.db.migrations_runtime.run_schema_sql") as run_schema,
            patch("app.db.migrations_runtime.apply_versioned_migrations") as apply_versioned,
            patch("app.db.migrations_runtime.Base.metadata.create_all") as create_all,
            patch("app.db.migrations_runtime.ensure_runtime_schema") as ensure_runtime,
        ):
            run_runtime_migrations()

        run_schema.assert_not_called()
        apply_versioned.assert_called_once_with()
        create_all.assert_not_called()
        ensure_runtime.assert_not_called()

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
            patch("app.db.migrations_runtime.ensure_mitigation_measure_examples") as mitigation,
            patch("app.db.migrations_runtime._ensure_hazards_xlsx_policy_system_hazards") as hazards,
            patch("app.db.migrations_runtime.engine.begin", return_value=begin),
        ):
            run_runtime_migrations(seed_reference_data=True)

        reference_schema.assert_not_called()
        additional_hazards.assert_called_once_with()
        mitigation.assert_called_once_with()
        hazards.assert_called_once_with(fake_connection)


if __name__ == "__main__":
    unittest.main()
