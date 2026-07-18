import json
import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.llm_logging import log_llm_exchange


class LlmLoggingTests(unittest.TestCase):
    def test_log_llm_exchange_writes_jsonl_record(self):
        writes: list[str] = []
        handle = MagicMock()
        handle.__enter__.return_value.write.side_effect = writes.append
        settings = Settings(llm_log_path="unused/llm.jsonl", llm_log_to_db=False)

        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.open", return_value=handle),
        ):
            log_llm_exchange(
                settings,
                request_id="request-1",
                provider="ollama",
                endpoint="/api/chat",
                model="qwen3.5:4b",
                request={"messages": [{"role": "user", "content": "Hello"}]},
                response={"message": {"content": "Hi"}},
                status_code=200,
                duration_ms=12.345,
            )

        records = [json.loads(line) for line in "".join(writes).splitlines()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["request_id"], "request-1")
        self.assertEqual(records[0]["endpoint"], "/api/chat")
        self.assertEqual(records[0]["request"]["messages"][0]["content"], "Hello")
        self.assertEqual(records[0]["response"]["message"]["content"], "Hi")
        self.assertEqual(records[0]["duration_ms"], 12.35)

    def test_log_llm_exchange_respects_disabled_setting(self):
        settings = Settings(
            llm_log_enabled=False,
            llm_log_path="unused/llm.jsonl",
            llm_log_to_db=False,
        )

        with patch("pathlib.Path.open") as open_mock:
            log_llm_exchange(
                settings,
                request_id="request-1",
                provider="ollama",
                endpoint="/api/chat",
                model="qwen3.5:4b",
                request={},
                response={},
            )

        open_mock.assert_not_called()

    def test_production_log_redacts_payloads_by_default(self):
        writes: list[str] = []
        handle = MagicMock()
        handle.__enter__.return_value.write.side_effect = writes.append
        settings = Settings(
            app_env="production",
            app_debug=False,
            secret_key="strong-production-secret",
            database_url="mysql+pymysql://user:strong-password@db.example/app",
            llm_log_enabled=True,
            llm_log_to_file=True,
            llm_log_path="unused/llm.jsonl",
            llm_log_to_db=False,
        )

        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.open", return_value=handle),
        ):
            log_llm_exchange(
                settings,
                request_id="request-3",
                provider="ollama",
                endpoint="/api/chat",
                model="qwen3.5:4b",
                request={"messages": [{"role": "user", "content": "Sensitive prompt"}]},
                response={"message": {"content": "Sensitive answer"}},
            )

        record = json.loads("".join(writes).splitlines()[0])

        self.assertTrue(record["request"]["redacted"])
        self.assertTrue(record["response"]["redacted"])
        self.assertNotIn("Sensitive prompt", json.dumps(record))

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

        with patch("app.db.session.SessionLocal", return_value=session):
            log_llm_exchange(
                settings,
                request_id="request-2",
                provider="ollama",
                endpoint="/api/chat",
                model="qwen3.5:4b",
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
