import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.question_intent import detect_message_intent, detect_user_question_intent


def _run(coro):
    return asyncio.run(coro)


class QuestionIntentTests(unittest.TestCase):
    def test_detects_question(self):
        with patch(
            "app.services.question_intent.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"is_question": true, "confidence": "high", '
                    '"reason": "User asks for an explanation."}'
                )
            ),
        ):
            result = _run(detect_user_question_intent("Why Housing?"))

        self.assertTrue(result["is_question"])
        self.assertEqual(result["confidence"], "high")

    def test_detects_selection(self):
        with patch(
            "app.services.question_intent.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"is_question": false, "confidence": "high", '
                    '"reason": "User is selecting an option."}'
                )
            ),
        ):
            result = _run(detect_user_question_intent("Germany please"))

        self.assertFalse(result["is_question"])
        self.assertEqual(result["confidence"], "high")

    def test_bad_json_uses_fallback(self):
        with patch(
            "app.services.question_intent.ask_llm_chat",
            new=AsyncMock(return_value="not json"),
        ):
            result = _run(
                detect_user_question_intent(
                    "Tell me more about Transport",
                    fallback=lambda message: message.casefold().startswith("tell me "),
                )
        )

        self.assertTrue(result["is_question"])
        self.assertEqual(result["confidence"], "medium")

    def test_detect_message_intent_change_country(self):
        with patch(
            "app.services.question_intent.ask_llm_chat",
            new=AsyncMock(
                return_value=(
                    '{"intent": "change_country", "confidence": "high", '
                    '"reason": "User wants to change country."}'
                )
            ),
        ):
            result = _run(detect_message_intent("Change Country"))

        self.assertEqual(result["intent"], "change_country")
        self.assertEqual(result["confidence"], "high")

    def test_message_intent_bad_json_fallback_restart(self):
        with patch(
            "app.services.question_intent.ask_llm_chat",
            new=AsyncMock(return_value="not json"),
        ):
            result = _run(detect_message_intent("Start over"))

        self.assertEqual(result["intent"], "restart_selection")
        self.assertEqual(result["confidence"], "medium")
