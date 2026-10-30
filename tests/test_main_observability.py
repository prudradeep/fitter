import unittest

from fastapi.testclient import TestClient

import app.main as main
from app.config import DEFAULT_PRODUCTION_CSP
from app.main import app, settings


class MainObservabilityTests(unittest.TestCase):
    def test_swagger_ui_uses_local_assets_and_documented_schema(self) -> None:
        original_policy = settings.content_security_policy
        settings.content_security_policy = DEFAULT_PRODUCTION_CSP
        client = TestClient(app)
        try:
            page = client.get("/docs")
            script = client.get("/static/js/swagger-ui-init.js")
            bundle = client.get("/static/vendor/swagger-ui/swagger-ui-bundle.js")
            stylesheet = client.get("/static/vendor/swagger-ui/swagger-ui.css")
            schema = client.get("/openapi.json")
            redoc = client.get("/redoc", follow_redirects=False)
        finally:
            client.close()
            settings.content_security_policy = original_policy

        self.assertEqual(page.status_code, 200)
        self.assertIn('/static/vendor/swagger-ui/swagger-ui-bundle.js', page.text)
        self.assertNotIn('cdn.jsdelivr.net', page.text)
        self.assertNotIn('<script>', page.text)
        self.assertNotIn('upgrade-insecure-requests', page.headers['Content-Security-Policy'])
        self.assertEqual(script.status_code, 200)
        self.assertEqual(bundle.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn('SwaggerUIBundle', bundle.text)
        self.assertEqual(schema.status_code, 200)
        self.assertIn('multipart/form-data', schema.json()['paths']['/api/chat']['post']['requestBody']['content'])
        self.assertEqual(redoc.status_code, 307)
        self.assertEqual(redoc.headers['location'], 'docs')

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

    def test_client_startup_policy_seed_flag(self) -> None:
        original_mode = settings.app_mode
        original_flag = settings.client_startup_seed_kb_policy_documents
        try:
            settings.app_mode = "client"
            settings.client_startup_seed_kb_policy_documents = False
            self.assertFalse(main._should_seed_policy_documents())

            settings.client_startup_seed_kb_policy_documents = True
            self.assertTrue(main._should_seed_policy_documents())

            settings.app_mode = "server"
            settings.client_startup_seed_kb_policy_documents = False
            self.assertTrue(main._should_seed_policy_documents())
        finally:
            settings.app_mode = original_mode
            settings.client_startup_seed_kb_policy_documents = original_flag


if __name__ == "__main__":
    unittest.main()
