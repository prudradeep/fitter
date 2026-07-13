import unittest

from pydantic import ValidationError

from app.config import Settings


class SettingsSafetyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
