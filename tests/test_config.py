import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings, _env_files


class SettingsSafetyTests(unittest.TestCase):
    def test_client_mode_uses_sqlite_when_database_url_is_left_at_default(self) -> None:
        settings = Settings(app_mode="client")

        self.assertEqual(settings.app_mode, "client")
        self.assertEqual(settings.database_url, "sqlite:///data/dr_transition.db")

    def test_sync_client_mode_derives_client_app_mode(self) -> None:
        settings = Settings(sync_enabled=True, sync_mode="client")

        self.assertEqual(settings.app_mode, "client")
        self.assertEqual(settings.database_url, "sqlite:///data/dr_transition.db")

    def test_client_mode_rejects_explicit_mysql_database_url(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                app_mode="client",
                database_url="mysql+pymysql://user:strong-password@db.example/app",
            )

    def test_server_mode_rejects_sqlite_database_url(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(app_mode="server", database_url="sqlite:///data/dr_transition.db")

    def test_auto_migrate_is_rejected_outside_development(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                app_env="production",
                app_debug=False,
                secret_key="strong-production-secret",
                database_url="mysql+pymysql://user:strong-password@db.example/app",
                database_auto_migrate=True,
            )

    def test_production_defaults_enable_csrf_and_disable_llm_payloads(self) -> None:
        settings = Settings(
            app_env="production",
            app_debug=False,
            secret_key="strong-production-secret",
            database_url="mysql+pymysql://user:strong-password@db.example/app",
            llm_log_enabled=None,
            llm_log_to_file=None,
            llm_log_to_db=None,
            llm_log_include_payloads=None,
        )

        self.assertTrue(settings.use_csrf_protection)
        self.assertFalse(settings.include_llm_log_payloads)
        self.assertFalse(settings.use_llm_logging)
        self.assertFalse(settings.write_llm_log_to_file)
        self.assertFalse(settings.write_llm_log_to_db)
        self.assertNotIn("'unsafe-inline'", settings.content_security_policy.split("script-src", 1)[1].split(";", 1)[0])

    def test_production_rejects_llm_payload_logging_without_override(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                app_env="production",
                app_debug=False,
                secret_key="strong-production-secret",
                database_url="mysql+pymysql://user:strong-password@db.example/app",
                llm_log_include_payloads=True,
            )

    def test_database_pool_settings_are_configurable(self) -> None:
        settings = Settings(
            database_pool_size=8,
            database_max_overflow=12,
            database_pool_timeout_seconds=5,
            database_connect_timeout_seconds=3,
        )

        self.assertEqual(settings.database_pool_size, 8)
        self.assertEqual(settings.database_max_overflow, 12)
        self.assertEqual(settings.database_pool_timeout_seconds, 5)
        self.assertEqual(settings.database_connect_timeout_seconds, 3)

    def test_prompt_source_accepts_auto_db_and_file(self) -> None:
        self.assertEqual(Settings(prompt_source="auto").prompt_source, "auto")
        self.assertEqual(Settings(prompt_source="DB").prompt_source, "db")
        self.assertEqual(Settings(prompt_source=" file ").prompt_source, "file")

    def test_prompt_source_rejects_unknown_value(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(prompt_source="remote")

    def test_env_file_override_is_supported_for_parallel_dev_runs(self) -> None:
        with patch.dict("os.environ", {"ENV_FILE": ".env.server.dev"}):
            self.assertEqual(_env_files(), (Path(".env.server.dev"),))


if __name__ == "__main__":
    unittest.main()
