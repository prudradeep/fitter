import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.document_language import (
    detect_document_language,
    display_language,
    document_language_guidance,
    translate_chunks_to_english,
)
from app.services.knowledge_base import ChunkDraft, KnowledgeBaseService


class DocumentLanguageTests(unittest.TestCase):
    def test_displays_detected_language_name(self) -> None:
        self.assertEqual(display_language("de"), "German")

    def test_document_prompt_matches_translation_setting(self) -> None:
        with patch(
            "app.services.document_language.get_settings",
            return_value=SimpleNamespace(enable_english_translation=False),
        ):
            self.assertIn("Please provide an English document", document_language_guidance())
        with patch(
            "app.services.document_language.get_settings",
            return_value=SimpleNamespace(enable_english_translation=True),
        ):
            self.assertIn("translated into English", document_language_guidance())

    def test_detects_iso_language_code(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="de")):
            language = asyncio.run(detect_document_language([ChunkDraft("Deutscher Gesetzestext")]))
        self.assertEqual(language, "de")

    def test_translates_non_english_chunks_preserving_pages(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="English translation")):
            chunks = asyncio.run(translate_chunks_to_english([ChunkDraft("Deutsch", 4)], "de"))
        self.assertEqual(chunks, [ChunkDraft("English translation", 4)])

    def test_keeps_english_chunks_without_translation(self) -> None:
        source = [ChunkDraft("English text", 4)]
        with patch("app.services.document_language.ask_llm_chat", AsyncMock()) as llm:
            chunks = asyncio.run(translate_chunks_to_english(source, "en"))
        self.assertEqual(chunks, source)
        llm.assert_not_awaited()

    def test_rejects_an_unusable_detection_response(self) -> None:
        with patch("app.services.document_language.ask_llm_chat", AsyncMock(return_value="I cannot reach Ollama")):
            with self.assertRaises(ValueError):
                asyncio.run(detect_document_language([ChunkDraft("Some text")]))

    def test_url_is_translated_before_ingestion_by_default(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_url_ingest_bytes": 1024,
            "enable_english_translation": True,
        })()
        service.scope = "temporary"
        source = [ChunkDraft("Deutscher Text", 2)]
        english = [ChunkDraft("German text", 2)]
        ingest = AsyncMock(return_value={"error": False})
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_url_chunks", AsyncMock(return_value=source)),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")) as detect,
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock(return_value=english)) as translate,
        ):
            asyncio.run(service.ingest_url("https://example.com/report.pdf"))
        detect.assert_awaited_once_with(source)
        translate.assert_awaited_once_with(source, "de")
        self.assertEqual(ingest.await_args.args[0], english)

    def test_pdf_is_translated_before_ingestion_by_default(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_upload_bytes": 1024,
            "enable_english_translation": True,
        })()
        source = [ChunkDraft("Deutscher Text", 3)]
        english = [ChunkDraft("German text", 3)]
        ingest = AsyncMock(return_value={"error": False})
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_file_chunks", return_value=source),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")) as detect,
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock(return_value=english)) as translate,
        ):
            asyncio.run(service.ingest_file("report.pdf", b"PDF"))
        detect.assert_awaited_once_with(source)
        translate.assert_awaited_once_with(source, "de")
        self.assertEqual(ingest.await_args.args[0], english)

    def test_pdf_reports_backend_stages_in_order(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_upload_bytes": 1024,
            "enable_english_translation": True,
        })()
        service.ingest_chunks = AsyncMock(return_value={"error": False})
        stages = []
        with (
            patch("app.services.knowledge_base.extract_file_chunks", return_value=[ChunkDraft("Deutsch")]),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")),
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock(return_value=[ChunkDraft("English")])),
        ):
            asyncio.run(service.ingest_file("report.pdf", b"PDF", progress=stages.append))
        self.assertEqual(stages, [
            "extracting", "detecting_language", "detected_language:German",
            "translating", "ingesting", "complete",
        ])

    def test_url_reports_download_and_extraction_stages(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_url_ingest_bytes": 1024,
            "enable_english_translation": True,
        })()
        service.scope = "temporary"
        service.ingest_chunks = AsyncMock(return_value={"error": False})
        stages = []
        with (
            patch("app.services.knowledge_base._fetch_public_url", AsyncMock(return_value=(
                "https://example.com/report", "text/plain", "utf-8", b"English report text",
            ))),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="en")),
        ):
            asyncio.run(service.ingest_url("https://example.com/report", progress=stages.append))
        self.assertEqual(stages, [
            "downloading", "extracting", "detecting_language",
            "detected_language:English", "ingesting", "complete",
        ])

    def test_failed_translation_does_not_ingest_pdf(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_upload_bytes": 1024,
            "enable_english_translation": True,
        })()
        ingest = AsyncMock()
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_file_chunks", return_value=[ChunkDraft("Deutsch")]),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")),
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock(side_effect=ValueError("Translation failed"))),
        ):
            with self.assertRaises(ValueError):
                asyncio.run(service.ingest_file("report.pdf", b"PDF"))
        ingest.assert_not_awaited()

    def test_disabled_translation_rejects_non_english_pdf_before_ingestion(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_upload_bytes": 1024,
            "enable_english_translation": False,
        })()
        ingest = AsyncMock()
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_file_chunks", return_value=[ChunkDraft("Deutsch")]),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")),
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock()) as translate,
        ):
            with self.assertRaisesRegex(ValueError, "detected as German.*Please provide an English document"):
                asyncio.run(service.ingest_file("report.pdf", b"PDF"))
        translate.assert_not_awaited()
        ingest.assert_not_awaited()

    def test_disabled_translation_accepts_english_url_without_translation(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_url_ingest_bytes": 1024,
            "enable_english_translation": False,
        })()
        service.scope = "temporary"
        source = [ChunkDraft("English report", 2)]
        ingest = AsyncMock(return_value={"error": False})
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_url_chunks", AsyncMock(return_value=source)),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="en")) as detect,
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock()) as translate,
        ):
            asyncio.run(service.ingest_url("https://example.com/report.pdf"))
        detect.assert_awaited_once_with(source)
        translate.assert_not_awaited()
        self.assertEqual(ingest.await_args.args[0], source)

    def test_disabled_translation_rejects_non_english_url_before_ingestion(self) -> None:
        service = KnowledgeBaseService.__new__(KnowledgeBaseService)
        service.settings = type("Settings", (), {
            "max_url_ingest_bytes": 1024,
            "enable_english_translation": False,
        })()
        service.scope = "temporary"
        ingest = AsyncMock()
        service.ingest_chunks = ingest
        with (
            patch("app.services.knowledge_base.extract_url_chunks", AsyncMock(return_value=[ChunkDraft("Deutsch")])),
            patch("app.services.knowledge_base.detect_document_language", AsyncMock(return_value="de")),
            patch("app.services.knowledge_base.translate_chunks_to_english", AsyncMock()) as translate,
        ):
            with self.assertRaisesRegex(ValueError, "Please provide an English document"):
                asyncio.run(service.ingest_url("https://example.com/report"))
        translate.assert_not_awaited()
        ingest.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
