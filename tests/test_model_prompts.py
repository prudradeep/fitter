import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import prompt_store
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

USER_TEMPLATE_SUFFIXES = ("_user.txt", "_user_context.txt", "_user_followup.txt")

USER_FACING_PROMPTS = {
    "dr_transition_coach.txt",
    "intro_message.txt",
    "mitigation_review_assistant.txt",
    "new_policy_suggestion.txt",
    "other_actions_navigation.txt",
    "selection_message.txt",
}

EXPECTED_GUARDRAIL_COUNTS = Counter(
    {
        "TASK RESULT GUARDRAILS - extraction and matching": 10,
        "TASK RESULT GUARDRAILS - simulated user message": 1,
        "TASK RESULT GUARDRAILS - grounding and citations": 4,
        "TASK RESULT GUARDRAILS - validation and classification": 12,
        "TASK RESULT GUARDRAILS - structured output": 3,
        "TASK RESULT GUARDRAILS - evidence and statistical context": 6,
        "TASK RESULT GUARDRAILS - user-facing response": 6,
    }
)


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

    def test_prompt_store_file_source_skips_database_reads(self) -> None:
        session_local = MagicMock()

        with (
            patch("app.config.get_settings", return_value=SimpleNamespace(prompt_source="file")),
            patch("app.services.prompt_store.SessionLocal", session_local),
        ):
            prompt = prompt_store.load_prompt_from_db("llm/test.txt")

        self.assertIsNone(prompt)
        session_local.assert_not_called()

    def test_prompt_store_db_source_reads_database_without_auto_enable(self) -> None:
        original_enabled = prompt_store.PROMPT_DB_ENABLED
        prompt_store.PROMPT_DB_ENABLED = False
        self.addCleanup(setattr, prompt_store, "PROMPT_DB_ENABLED", original_enabled)

        with patch("app.config.get_settings", return_value=SimpleNamespace(prompt_source="db")):
            self.assertTrue(prompt_store.should_read_prompts_from_db())

    def test_workflow_prompts_are_categorized_for_database_storage(self) -> None:
        category, model, display_name = prompt_store.prompt_metadata("workflow/hazards.txt")

        self.assertEqual(category, "workflow")
        self.assertIsNone(model)
        self.assertEqual(display_name, "workflow / hazards.txt")

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

    def test_model_prompt_directories_mirror_base_prompt_files(self) -> None:
        base_dir = Path("app/prompts/llm")
        base_files = {path.name for path in base_dir.glob("*.txt")}

        self.assertEqual(len(base_files), 74)
        for model, directory in MODEL_PROMPT_DIRS.items():
            with self.subTest(model=model):
                model_files = {path.name for path in (base_dir / directory).glob("*.txt")}

                self.assertEqual(model_files, base_files)

    def test_system_task_prompts_have_one_header_and_guardrail(self) -> None:
        base_dir = Path("app/prompts/llm")

        for model, directory in MODEL_PROMPT_DIRS.items():
            for prompt_path in sorted((base_dir / directory).glob("*.txt")):
                if prompt_path.name.endswith(USER_TEMPLATE_SUFFIXES):
                    continue

                with self.subTest(model=model, prompt=prompt_path.name):
                    prompt = prompt_path.read_text(encoding="utf-8")

                    self.assertEqual(prompt.count("MODEL OPTIMIZATION -"), 1)
                    self.assertEqual(prompt.count("TASK RESULT GUARDRAILS -"), 1)
                    self.assertTrue(prompt.startswith(f"MODEL OPTIMIZATION - {model}"))

    def test_guardrail_distribution_is_consistent_for_each_model(self) -> None:
        base_dir = Path("app/prompts/llm")

        for model, directory in MODEL_PROMPT_DIRS.items():
            counts = Counter()
            for prompt_path in sorted((base_dir / directory).glob("*.txt")):
                for line in prompt_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("TASK RESULT GUARDRAILS -"):
                        counts[line] += 1

            with self.subTest(model=model):
                self.assertEqual(counts, EXPECTED_GUARDRAIL_COUNTS)

    def test_conversational_prompts_use_user_facing_guardrails(self) -> None:
        base_dir = Path("app/prompts/llm")

        for model, directory in MODEL_PROMPT_DIRS.items():
            for prompt_name in USER_FACING_PROMPTS:
                with self.subTest(model=model, prompt=prompt_name):
                    prompt = (base_dir / directory / prompt_name).read_text(encoding="utf-8")

                    self.assertIn("TASK RESULT GUARDRAILS - user-facing response", prompt)
                    self.assertNotIn("TASK RESULT GUARDRAILS - structured output", prompt)

    def test_selection_prompts_cover_combined_message_patterns(self) -> None:
        for model in MODEL_PROMPT_DIRS:
            with self.subTest(model=model):
                resolver_path = prompt_loader.resolve_nested_prompt_path(
                    "llm/conversational_selection_resolver.txt",
                    model,
                )
                intent_path = prompt_loader.resolve_nested_prompt_path(
                    "llm/message_intent_detector.txt",
                    model,
                )
                resolver = resolver_path.read_text(encoding="utf-8")
                intent = intent_path.read_text(encoding="utf-8")

                for phrase in [
                    "Country Germany region Berlin sector Energy",
                    "Country Portugal sector Transport",
                    "Use Ireland and Waterford",
                    "Use Galway with Transport",
                ]:
                    self.assertIn(phrase, resolver)
                    self.assertIn(phrase, intent)


if __name__ == "__main__":
    unittest.main()
