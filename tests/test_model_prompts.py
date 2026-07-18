import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import prompt_loader

MODEL_PROMPT_DIRS = {
    "qwen3.5:2b": "qwen3.5_2b",
    "qwen3.5:4b": "qwen3.5_4b",
    "ministral-3:8b": "ministral-3_8b",
    "qwen3.5:9b": "qwen3.5_9b",
    "ministral-3:14b": "ministral-3_14b",
    "mistral-small3.2:24b": "mistral-small3.2_24b",
    "qwen3.5:27b": "qwen3.5_27b",
}


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

        self.assertTrue(prompt.startswith("MODEL OPTIMIZATION - ministral-3:14b"))
        self.assertIn("You are Dr Transition, a digital coach", prompt)

    def test_all_model_prompt_directories_are_supported_and_tuned(self) -> None:
        self.assertEqual(prompt_loader.MODEL_PROMPT_DIRS, MODEL_PROMPT_DIRS)

        for model, directory in MODEL_PROMPT_DIRS.items():
            with self.subTest(model=model):
                prompt_path = prompt_loader.resolve_nested_prompt_path(
                    "llm/mitigation_measure_validation.txt",
                    model,
                )
                prompt = prompt_path.read_text(encoding="utf-8")

                self.assertEqual(prompt_path.parent.name, directory)
                self.assertTrue(prompt.startswith(f"MODEL OPTIMIZATION - {model}"))
                self.assertIn("TASK RESULT GUARDRAILS - validation and classification", prompt)

    def test_user_templates_are_not_prefixed_with_model_tuning(self) -> None:
        for model in MODEL_PROMPT_DIRS:
            with self.subTest(model=model):
                prompt_path = prompt_loader.resolve_nested_prompt_path(
                    "llm/mitigation_measure_validation_user.txt",
                    model,
                )
                first_line = prompt_path.read_text(encoding="utf-8").splitlines()[0]

                self.assertFalse(first_line.startswith("MODEL OPTIMIZATION -"))


if __name__ == "__main__":
    unittest.main()
