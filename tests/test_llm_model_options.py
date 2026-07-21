import unittest

from app.llm import should_disable_thinking, sync_server_llm_disabled


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


if __name__ == "__main__":
    unittest.main()
