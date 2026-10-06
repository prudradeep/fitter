import unittest
from uuid import uuid4

from app.services.ingestion_progress import (
    begin_ingestion_progress,
    get_ingestion_progress,
    update_ingestion_progress,
)


class IngestionProgressTests(unittest.TestCase):
    def test_progress_is_scoped_to_user_and_keeps_stage_history(self) -> None:
        progress_id = str(uuid4())
        self.assertEqual(begin_ingestion_progress(progress_id, "owner"), progress_id)
        update_ingestion_progress(progress_id, "report.pdf", "extracting")
        update_ingestion_progress(progress_id, "report.pdf", "detecting_language")
        self.assertIsNone(get_ingestion_progress(progress_id, "other-user"))
        self.assertEqual(get_ingestion_progress(progress_id, "owner"), {
            "source": "report.pdf",
            "stage": "detecting_language",
            "history": ["extracting", "detecting_language"],
        })

    def test_invalid_progress_id_is_ignored(self) -> None:
        self.assertIsNone(begin_ingestion_progress("not-a-uuid", "owner"))
