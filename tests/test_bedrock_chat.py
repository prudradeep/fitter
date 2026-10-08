import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import get_db
from app.llm import proxy_bedrock_chat
from app.routes import sync as sync_routes
from app.services.bedrock_provider import BedrockProviderError, _chat_sync


class BedrockChatTests(unittest.TestCase):
    def test_converse_request_and_json_instruction(self) -> None:
        settings = Settings(
            app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id",
            bedrock_chat_cache_enabled=False, _env_file=None,
        )

        class Client:
            def converse(self, **request):
                self.request = request
                return {"output": {"message": {"content": [{"text": '{"ok": true}'}]}}}

        client = Client()
        with (
            patch("app.services.bedrock_provider._client", return_value=client),
            patch("app.services.bedrock_provider.log_llm_exchange"),
        ):
            answer = _chat_sync(
                settings, "Answer accurately", [{"role": "user", "content": "test"}],
                temperature=0.2, max_tokens=300, response_format="json",
            )
        self.assertEqual(answer, '{"ok": true}')
        self.assertEqual(client.request["modelId"], "model-id")
        self.assertEqual(client.request["messages"], [{"role": "user", "content": [{"text": "test"}]}])
        self.assertIn("Return valid JSON only", client.request["system"][0]["text"])

    def test_empty_converse_response_reports_error(self) -> None:
        settings = Settings(
            app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id",
            bedrock_chat_cache_enabled=False, _env_file=None,
        )

        class Client:
            def converse(self, **request):
                return {"output": {"message": {"content": []}}}

        with (
            patch("app.services.bedrock_provider._client", return_value=Client()),
            patch("app.services.bedrock_provider.log_llm_exchange"),
            self.assertRaises(BedrockProviderError),
        ):
            _chat_sync(
                settings, "", [{"role": "user", "content": "test"}],
                temperature=0.2, max_tokens=300, response_format=None,
            )

    def test_cache_reuses_matching_request_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "chat.sqlite"
            settings = Settings(
                app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id",
                bedrock_chat_cache_path=str(cache_path), bedrock_chat_cache_ttl_seconds=60,
                _env_file=None,
            )
            client = MagicMock()
            client.converse.return_value = {"output": {"message": {"content": [{"text": "answer"}]}}}
            messages = [{"role": "user", "content": "private question"}]
            with (
                patch("app.services.bedrock_provider._client", return_value=client),
                patch("app.services.bedrock_provider.log_llm_exchange"),
            ):
                first = _chat_sync(settings, "context", messages, temperature=0.2, max_tokens=300, response_format=None)
                second = _chat_sync(settings, "context", messages, temperature=0.2, max_tokens=300, response_format=None)
                changed = _chat_sync(settings, "context", messages, temperature=0.3, max_tokens=300, response_format=None)
                with closing(sqlite3.connect(cache_path)) as connection:
                    with connection:
                        connection.execute("UPDATE bedrock_chat SET created_at = 0")
                expired = _chat_sync(settings, "context", messages, temperature=0.2, max_tokens=300, response_format=None)
                with closing(sqlite3.connect(cache_path)) as connection:
                    stored_rows = connection.execute("SELECT COUNT(*) FROM bedrock_chat").fetchone()[0]
            self.assertEqual((first, second, changed, expired), ("answer",) * 4)
            self.assertEqual(client.converse.call_count, 3)
            self.assertEqual(stored_rows, 1)
            self.assertNotIn(b"private question", cache_path.read_bytes())

    def test_cache_write_failure_returns_bedrock_answer(self) -> None:
        settings = Settings(app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id", _env_file=None)
        client = MagicMock()
        client.converse.return_value = {"output": {"message": {"content": [{"text": "answer"}]}}}
        with (
            patch("app.services.bedrock_provider._client", return_value=client),
            patch("app.services.bedrock_provider.cached_chat", return_value=None),
            patch("app.services.bedrock_provider.store_chat", side_effect=OSError("disk unavailable")),
            patch("app.services.bedrock_provider.log_llm_exchange"),
        ):
            answer = _chat_sync(
                settings, "context", [{"role": "user", "content": "question"}],
                temperature=0.2, max_tokens=300, response_format=None,
            )
        self.assertEqual(answer, "answer")
        client.converse.assert_called_once()

    def test_server_proxy_reuses_cached_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id",
                sync_enabled=True, bedrock_chat_cache_path=str(Path(temp_dir) / "chat.sqlite"),
                _env_file=None,
            )
            app = FastAPI()
            app.include_router(sync_routes.router)
            app.dependency_overrides[get_db] = lambda: object()
            bedrock_client = MagicMock()
            bedrock_client.converse.return_value = {"output": {"message": {"content": [{"text": "answer"}]}}}
            with (
                patch.object(sync_routes, "settings", settings),
                patch.object(sync_routes.SyncService, "sync_client_for_token", return_value={"id": "client"}),
                patch("app.services.bedrock_provider._client", return_value=bedrock_client),
                patch("app.services.bedrock_provider.log_llm_exchange"),
                TestClient(app) as client,
            ):
                payload = {
                    "context": "context", "messages": [{"role": "user", "content": "question"}],
                    "temperature": 0.2, "max_tokens": 300,
                }
                first = client.post("/api/sync/llm/chat", headers={"X-Sync-Token": "valid"}, json=payload)
                second = client.post("/api/sync/llm/chat", headers={"X-Sync-Token": "valid"}, json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json(), second.json())
            bedrock_client.converse.assert_called_once()


class BedrockProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_sends_sync_token_and_chat_payload(self) -> None:
        settings = Settings(
            app_mode="client", llm_provider="bedrock", sync_server_url="https://example.test/",
            sync_api_token="token", _env_file=None,
        )
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["request"] = request
            return httpx.Response(200, json={"error": False, "answer": " Bedrock answer "})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.llm.httpx.AsyncClient", return_value=client):
            answer = await proxy_bedrock_chat(
                settings, "context", [{"role": "user", "content": "hello"}],
                temperature=0.2, max_tokens=300, response_format=None,
            )
        self.assertEqual(answer, "Bedrock answer")
        self.assertEqual(seen["request"].url.path, "/api/sync/llm/chat")
        self.assertEqual(seen["request"].headers["X-Sync-Token"], "token")
        self.assertIn(b'"context":"context"', seen["request"].content)
