import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.llm import proxy_bedrock_chat
from app.services.bedrock_provider import BedrockProviderError, _chat_sync


class BedrockChatTests(unittest.TestCase):
    def test_converse_request_and_json_instruction(self) -> None:
        settings = Settings(app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id", _env_file=None)

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
        settings = Settings(app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id", _env_file=None)

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
