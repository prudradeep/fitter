import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import prompt_loader


class ModelPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        prompt_loader._load_nested_prompt_file.cache_clear()

    def test_resolve_nested_prompt_path_uses_selected_model_directory(self) -> None:
        path = prompt_loader.resolve_nested_prompt_path("llm/dr_transition_coach.txt", " qwen3.5:9B ")

        self.assertEqual(path.name, "dr_transition_coach.txt")
        self.assertEqual(path.parent.name, "qwen3.5_9b")

    def test_resolve_nested_prompt_path_falls_back_for_unknown_model(self) -> None:
        path = prompt_loader.resolve_nested_prompt_path("llm/dr_transition_coach.txt", "unknown-local-model")

        self.assertEqual(path.name, "dr_transition_coach.txt")
        self.assertEqual(path.parent.name, "llm")

    def test_load_nested_prompt_file_uses_configured_model(self) -> None:
        settings = SimpleNamespace(ollama_model="ministral-3:14b")

        with patch("app.services.prompt_loader.get_settings", return_value=settings):
            prompt = prompt_loader.load_nested_prompt_file("llm/dr_transition_coach.txt")

        self.assertEqual(prompt, "You are Dr Transition, a digital coach for Twin-Transition policy analysis.")


if __name__ == "__main__":
    unittest.main()
