import unittest
from unittest.mock import AsyncMock, patch

from app.llm import ask_llm_chat, should_disable_thinking, sync_server_llm_disabled


class LlmModelOptionsTests(unittest.TestCase):
    def test_qwen35_models_disable_thinking(self) -> None:
        self.assertTrue(should_disable_thinking("qwen3.5:2b"))
        self.assertTrue(should_disable_thinking(" QWEN3.5:9B "))

    def test_non_qwen35_models_keep_default_thinking_behavior(self) -> None:
        self.assertFalse(should_disable_thinking("ministral-3:8b"))
        self.assertFalse(should_disable_thinking("mistral-small3.2:24b"))
        self.assertFalse(should_disable_thinking(None))

    def test_sync_server_mode_disables_llm_requests(self) -> None:
        from app.config import get_settings

        settings = get_settings()
        original_enabled = settings.sync_enabled
        original_mode = settings.sync_mode
        original_expose = settings.sync_server_expose_app_apis
        try:
            settings.sync_enabled = True
            settings.sync_mode = "server"
            settings.sync_server_expose_app_apis = False
            self.assertTrue(sync_server_llm_disabled())

            settings.sync_server_expose_app_apis = True
            self.assertFalse(sync_server_llm_disabled())
        finally:
            settings.sync_enabled = original_enabled
            settings.sync_mode = original_mode
            settings.sync_server_expose_app_apis = original_expose


class BedrockRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_chat_uses_proxy_even_without_data_sync(self) -> None:
        from app.config import Settings

        settings = Settings(
            app_mode="client", llm_provider="bedrock", sync_enabled=False,
            sync_server_url="https://example.test", sync_api_token="token", _env_file=None,
        )
        with (
            patch("app.llm.get_settings", return_value=settings),
            patch("app.llm.proxy_bedrock_chat", new_callable=AsyncMock) as proxy,
            patch("app.llm.bedrock_chat", new_callable=AsyncMock) as direct,
        ):
            proxy.return_value = "proxied"
            answer = await ask_llm_chat("context", [{"role": "user", "content": "hello"}])
        self.assertEqual(answer, "proxied")
        proxy.assert_awaited_once()
        direct.assert_not_awaited()

    async def test_server_chat_uses_bedrock_directly(self) -> None:
        from app.config import Settings

        settings = Settings(app_mode="server", llm_provider="bedrock", bedrock_model_id="model-id", _env_file=None)
        with (
            patch("app.llm.get_settings", return_value=settings),
            patch("app.llm.proxy_bedrock_chat", new_callable=AsyncMock) as proxy,
            patch("app.llm.bedrock_chat", new_callable=AsyncMock) as direct,
        ):
            direct.return_value = "direct"
            answer = await ask_llm_chat("context", [{"role": "user", "content": "hello"}])
        self.assertEqual(answer, "direct")
        direct.assert_awaited_once()
        proxy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
