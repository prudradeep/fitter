import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routes import api


class SyncAuthTests(unittest.TestCase):
    def setUp(self):
        api._RATE_LIMIT_BUCKETS.clear()
        self.request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    def test_bearer_token_parses_only_bearer_scheme(self):
        self.assertEqual(api._bearer_token("Bearer abc123"), "abc123")
        self.assertEqual(api._bearer_token("bearer abc123"), "abc123")
        self.assertEqual(api._bearer_token("Basic abc123"), "")
        self.assertEqual(api._bearer_token(None), "")

    def test_sync_token_required_when_configured(self):
        settings = SimpleNamespace(central_sync_token="sync-token", central_api_token="")
        with patch("app.routes.api.get_settings", return_value=settings):
            api.require_sync_access(self.request, "Bearer sync-token", current_user=None)
            with self.assertRaises(HTTPException) as context:
                api.require_sync_access(self.request, "Bearer wrong", current_user=SimpleNamespace(role="admin"))

        self.assertEqual(context.exception.status_code, 403)

    def test_evidence_submit_requires_evidence_token(self):
        settings = SimpleNamespace(central_evidence_token="evidence-token", central_api_token="")
        with patch("app.routes.api.get_settings", return_value=settings):
            api.require_evidence_submit_access(self.request, "Bearer evidence-token")
            with self.assertRaises(HTTPException) as context:
                api.require_evidence_submit_access(self.request, "Bearer sync-token")

        self.assertEqual(context.exception.status_code, 403)

    def test_rate_limit_blocks_after_limit(self):
        api._check_rate_limit("sync", "token:test", 2, window_seconds=60)
        api._check_rate_limit("sync", "token:test", 2, window_seconds=60)
        with self.assertRaises(HTTPException) as context:
            api._check_rate_limit("sync", "token:test", 2, window_seconds=60)

        self.assertEqual(context.exception.status_code, 429)

    def test_submission_title_includes_client_id_without_trusting_user_id(self):
        title = api._submission_title(
            {"client_id": "desktop-01", "user_id": 123},
            "Evidence report",
        )

        self.assertEqual(title, "[client=desktop-01] Evidence report")
        self.assertNotIn("123", title)


if __name__ == "__main__":
    unittest.main()
