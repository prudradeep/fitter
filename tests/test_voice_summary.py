import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.voice_summary import (
    VOICE_SUMMARY_CLOSING,
    fallback_voice_summary,
    generate_voice_summary,
)


def _run(coro):
    return asyncio.run(coro)


class VoiceSummaryTests(unittest.TestCase):
    def test_fallback_voice_summary_omits_closing_when_short_message_is_complete(self):
        summary = fallback_voice_summary(
            "<p>The assistant selected Germany and Bavaria.</p>"
            "<p>It is now asking for a sector.</p>"
        )

        self.assertIn("Germany and Bavaria", summary)
        self.assertNotIn(VOICE_SUMMARY_CLOSING, summary)

    def test_generate_voice_summary_uses_llm_without_closing_when_complete(self):
        with patch(
            "app.services.voice_summary.ask_llm_chat",
            AsyncMock(return_value="Germany and Bavaria are selected. Choose the sector next."),
        ) as ask_llm:
            summary = _run(
                generate_voice_summary(
                    "<p>Germany and Bavaria are selected. Choose the sector next.</p>"
                )
            )

        ask_llm.assert_awaited_once()
        self.assertEqual(summary, "Germany and Bavaria are selected. Choose the sector next.")

    def test_generate_voice_summary_adds_closing_when_summary_is_partial(self):
        with patch(
            "app.services.voice_summary.ask_llm_chat",
            AsyncMock(return_value="Germany and Bavaria are selected."),
        ):
            summary = _run(
                generate_voice_summary(
                    "<p>Germany and Bavaria are selected. Choose the sector next.</p>"
                )
            )

        self.assertTrue(summary.endswith(VOICE_SUMMARY_CLOSING))


if __name__ == "__main__":
    unittest.main()
