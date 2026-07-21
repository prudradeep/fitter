import unittest

from fastapi.testclient import TestClient

import app.main as main
from app.main import app, settings


class MainObservabilityTests(unittest.TestCase):
    def test_health_live_includes_request_id_header(self) -> None:
        client = TestClient(app)
        try:
            response = client.get("/health/live", headers={settings.request_id_header: "test-request"})
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[settings.request_id_header], "test-request")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.json()["status"], "ok")

    def test_access_log_suppression_uses_configured_paths(self) -> None:
        original_paths = settings.access_log_suppressed_paths
        original_enabled = settings.access_log_enabled
        try:
            settings.access_log_enabled = True
            settings.access_log_suppressed_paths = "/health/live,/metrics"
            self.assertFalse(main._should_log_access("/health/live"))
            self.assertTrue(main._should_log_access("/api/chat"))

            settings.access_log_enabled = False
            self.assertFalse(main._should_log_access("/api/chat"))
        finally:
            settings.access_log_suppressed_paths = original_paths
            settings.access_log_enabled = original_enabled

    def test_sync_server_mode_blocks_app_apis(self) -> None:
        original_enabled = settings.sync_enabled
        original_mode = settings.sync_mode
        original_expose = settings.sync_server_expose_app_apis
        settings.sync_enabled = True
        settings.sync_mode = "server"
        settings.sync_server_expose_app_apis = False
        client = TestClient(app)
        try:
            login_response = client.get("/login", follow_redirects=False)
            chat_response = client.post("/api/chat", json={"message": "hello"})
            health_response = client.get("/health/live")

            self.assertEqual(login_response.status_code, 404)
            self.assertEqual(chat_response.status_code, 404)
            self.assertEqual(health_response.status_code, 200)
        finally:
            client.close()
            settings.sync_enabled = original_enabled
            settings.sync_mode = original_mode
            settings.sync_server_expose_app_apis = original_expose

    def test_sync_server_mode_disables_llm_startup_work(self) -> None:
        original_enabled = settings.sync_enabled
        original_mode = settings.sync_mode
        original_expose = settings.sync_server_expose_app_apis
        try:
            settings.sync_enabled = True
            settings.sync_mode = "server"
            settings.sync_server_expose_app_apis = False

            self.assertTrue(main._sync_server_disables_llm_services())
        finally:
            settings.sync_enabled = original_enabled
            settings.sync_mode = original_mode
            settings.sync_server_expose_app_apis = original_expose

    def test_client_sync_configuration_skips_local_seeded_startup_data(self) -> None:
        original_enabled = settings.sync_enabled
        original_mode = settings.sync_mode
        original_url = settings.sync_server_url
        original_token = settings.sync_api_token
        try:
            settings.sync_enabled = True
            settings.sync_mode = "client"
            settings.sync_server_url = "https://sync.example"
            settings.sync_api_token = "secret"

            self.assertTrue(main._client_sync_configured())
        finally:
            settings.sync_enabled = original_enabled
            settings.sync_mode = original_mode
            settings.sync_server_url = original_url
            settings.sync_api_token = original_token


if __name__ == "__main__":
    unittest.main()
