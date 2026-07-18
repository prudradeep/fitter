import unittest

from app.llm import should_disable_thinking


class LlmModelOptionsTests(unittest.TestCase):
    def test_qwen35_models_disable_thinking(self) -> None:
        self.assertTrue(should_disable_thinking("qwen3.5:2b"))
        self.assertTrue(should_disable_thinking(" QWEN3.5:9B "))

    def test_non_qwen35_models_keep_default_thinking_behavior(self) -> None:
        self.assertFalse(should_disable_thinking("ministral-3:8b"))
        self.assertFalse(should_disable_thinking("mistral-small3.2:24b"))
        self.assertFalse(should_disable_thinking(None))


if __name__ == "__main__":
    unittest.main()
