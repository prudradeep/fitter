import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.document_language import detect_document_language, translate_chunks_to_english
from app.services.knowledge_base import ChunkDraft


class DocumentLanguageTests(unittest.TestCase):
    def test_detects_iso_language_code(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="de")):
            language = asyncio.run(detect_document_language([ChunkDraft("Deutscher Gesetzestext")]))
        self.assertEqual(language, "de")

    def test_translates_non_english_chunks_preserving_pages(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="English translation")):
            chunks = asyncio.run(translate_chunks_to_english([ChunkDraft("Deutsch", 4)], "de"))
        self.assertEqual(chunks, [ChunkDraft("English translation", 4)])

    def test_rejects_an_unusable_detection_response(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="I cannot reach Ollama")):
            with self.assertRaises(ValueError):
                asyncio.run(detect_document_language([ChunkDraft("Some text")]))


if __name__ == "__main__":
    unittest.main()
