import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.config import Settings
from app.services.llm_logging import log_llm_exchange


class LlmLoggingTests(unittest.TestCase):
    def test_log_llm_exchange_writes_jsonl_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "llm.jsonl"
            settings = Settings(llm_log_path=str(log_path), llm_log_to_db=False)

            log_llm_exchange(
                settings,
                request_id="request-1",
                provider="ollama",
                endpoint="/api/chat",
                model="mistral-nemo",
                request={"messages": [{"role": "user", "content": "Hello"}]},
                response={"message": {"content": "Hi"}},
                status_code=200,
                duration_ms=12.345,
            )

            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["request_id"], "request-1")
        self.assertEqual(records[0]["endpoint"], "/api/chat")
        self.assertEqual(records[0]["request"]["messages"][0]["content"], "Hello")
        self.assertEqual(records[0]["response"]["message"]["content"], "Hi")
        self.assertEqual(records[0]["duration_ms"], 12.35)

    def test_log_llm_exchange_respects_disabled_setting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "llm.jsonl"
            settings = Settings(
                llm_log_enabled=False,
                llm_log_path=str(log_path),
                llm_log_to_db=False,
            )

            log_llm_exchange(
                settings,
                request_id="request-1",
                provider="ollama",
                endpoint="/api/chat",
                model="mistral-nemo",
                request={},
                response={},
            )

            self.assertFalse(log_path.exists())

    def test_log_llm_exchange_can_write_database_record(self):
        class FakeSession:
            def __init__(self):
                self.parameters = None
                self.committed = False
                self.closed = False

            def execute(self, _statement, parameters):
                self.parameters = parameters

            def commit(self):
                self.committed = True

            def close(self):
                self.closed = True

        session = FakeSession()
        settings = Settings(llm_log_to_file=False, llm_log_to_db=True)

        with patch("app.database.SessionLocal", return_value=session):
            log_llm_exchange(
                settings,
                request_id="request-2",
                provider="ollama",
                endpoint="/api/chat",
                model="mistral-nemo",
                request={"prompt": "Hello"},
                response={"answer": "Hi"},
                status_code=200,
                duration_ms=1.2,
            )

        self.assertTrue(session.committed)
        self.assertTrue(session.closed)
        self.assertEqual(session.parameters["request_id"], "request-2")
        self.assertEqual(session.parameters["request_payload"], '{"prompt": "Hello"}')
        self.assertEqual(session.parameters["response_payload"], '{"answer": "Hi"}')


if __name__ == "__main__":
    unittest.main()
