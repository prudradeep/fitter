import unittest

from app.services.chat_grounded_question_steps import ChatGroundedQuestionStepsMixin


class GroundedSourceCitationTests(unittest.TestCase):
    def test_grounded_answer_wraps_kb_and_sector_source_ids(self):
        html = ChatGroundedQuestionStepsMixin._grounded_answer_html(
            "The finding is supported by [S1] and [SP1].",
            {
                "S1": {
                    "title": "Uploaded report",
                    "source_type": "Knowledge Base",
                    "source_uri": "https://example.org/report",
                    "page": "4",
                    "excerpt": "Evidence from the uploaded report.",
                },
                "SP1": {
                    "title": "Sector prompt: housing",
                    "source_type": "Sector stats",
                    "source_uri": "sector-prompt://v4/housing",
                    "page": "",
                    "excerpt": "Sector-prompt statistical context.",
                },
            },
        )

        self.assertIn('class="source-citation"', html)
        self.assertIn('href="https://example.org/report"', html)
        self.assertIn("Uploaded report", html)
        self.assertIn("Sector prompt: housing", html)
        self.assertIn(">S1<", html)
        self.assertIn(">SP1<", html)

    def test_grounded_answer_does_not_wrap_unknown_or_partial_ids(self):
        html = ChatGroundedQuestionStepsMixin._grounded_answer_html(
            "Known [SP1], unknown [SP2], plain S1, and bracketed [S1].",
            {
                "SP1": {
                    "title": "Sector prompt",
                    "source_type": "Sector stats",
                    "source_uri": "sector-prompt://v4/energy",
                    "page": "",
                    "excerpt": "Sector excerpt.",
                },
                "S1": {
                    "title": "KB report",
                    "source_type": "Knowledge Base",
                    "source_uri": "",
                    "page": "",
                    "excerpt": "KB excerpt.",
                },
            },
        )

        self.assertEqual(html.count('class="source-citation"'), 2)
        self.assertIn("[SP2]", html)
        self.assertIn("plain S1", html)


if __name__ == "__main__":
    unittest.main()
