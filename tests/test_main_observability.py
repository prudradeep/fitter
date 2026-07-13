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


if __name__ == "__main__":
    unittest.main()
