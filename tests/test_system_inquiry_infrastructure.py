import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base
from app.llm import ask_llm_chat
from app.models import SystemInquiryTelemetryEvent
from app.services.chat_session import ChatSession
from app.services.system_inquiry_evaluation import (
    evaluate_system_inquiry_predictions,
    load_expert_system_inquiry_gold_set,
    load_system_inquiry_gold_set,
    validate_expert_system_inquiry_gold_set,
)
from app.services.system_inquiry_corpus import explain_system_inquiry_probe, system_inquiry_corpus_index
from app.services.system_inquiry_probe_library import system_inquiry_probe_library
from app.services.system_inquiry_telemetry import (
    enqueue_system_inquiry_telemetry,
    sanitize_system_inquiry_telemetry,
)


class SystemInquiryInfrastructureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    async def test_ask_llm_chat_sends_ollama_format_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": '{"answer":"ok"}'}}

        class FakeClient:
            payload = None

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, _path, json):
                FakeClient.payload = json
                return FakeResponse()

        with (
            patch("app.llm.get_settings", return_value=Settings(llm_log_enabled=False)),
            patch("app.llm.httpx.AsyncClient", FakeClient),
        ):
            result = await ask_llm_chat(
                "Return JSON.",
                [{"role": "user", "content": "Hello"}],
                response_format=schema,
            )

        self.assertEqual(result, '{"answer":"ok"}')
        self.assertEqual(FakeClient.payload["format"], schema)

    def test_telemetry_sanitizer_removes_local_text_and_enqueues_once(self) -> None:
        payload = {
            "session_id_anon": "anon-1",
            "annotations": [{"user_response": "private", "probe_id": "A4-P1"}],
            "probes": [{"probe_id": "A4-P1", "resolution_state": "open"}],
            "summary": "local prose",
        }

        cleaned = sanitize_system_inquiry_telemetry(payload)

        self.assertNotIn("annotations", cleaned)
        self.assertNotIn("summary", cleaned)
        self.assertEqual(cleaned["probes"][0]["probe_id"], "A4-P1")

        first = enqueue_system_inquiry_telemetry(self.db, payload)
        second = enqueue_system_inquiry_telemetry(self.db, payload)
        self.db.commit()

        self.assertEqual(first.id, second.id)
        rows = list(self.db.scalars(select(SystemInquiryTelemetryEvent)))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("private", rows[0].payload_json)

    def test_gold_set_evaluator_scores_predictions(self) -> None:
        gold = load_system_inquiry_gold_set()
        predictions = {
            "energy-retrofit-delay-cost": [
                {"probe_id": "A4-P1", "candidate_status": "selected", "verify_votes": 3, "anchor_valid": True},
                {"probe_id": "C1-P1", "candidate_status": "selected", "verify_votes": 2, "anchor_valid": True},
                {"probe_id": "Z9-P1", "candidate_status": "selected", "verify_votes": 1, "anchor_valid": False},
                {"probe_id": "C2-P1", "candidate_status": "held_cap", "verify_votes": 3, "anchor_valid": True},
            ],
            "transport-automatic-scrappage": [
                {"probe_id": "A5-P1", "candidate_status": "selected", "verify_votes": 3, "anchor_valid": True},
            ],
            "housing-advice-intermediary": [
                {"probe_id": "B4-P1", "candidate_status": "selected", "verify_votes": 3, "anchor_valid": True},
                {"probe_id": "C3-P1", "candidate_status": "selected", "verify_votes": 3, "anchor_valid": True},
                {"probe_id": "A7-P1", "candidate_status": "selected", "verify_votes": 3, "anchor_valid": True},
            ],
        }

        metrics = evaluate_system_inquiry_predictions(gold, predictions)

        self.assertEqual(metrics["case_count"], 3)
        self.assertEqual(metrics["true_positive"], 6)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 5)
        self.assertEqual(metrics["probe_precision"], 0.8571)
        self.assertEqual(metrics["surfaced"], 7)
        self.assertEqual(metrics["held_by_cap"], 1)

    def test_expert_gold_set_validation_requires_real_expert_metadata(self) -> None:
        valid_cases = [
            {
                "case_id": f"case-{index}",
                "expert_probe_ids": ["A1-P1"],
                "label_source": "consortium_expert",
                "expert_reviewers": ["reviewer-a"],
            }
            for index in range(25)
        ]

        validate_expert_system_inquiry_gold_set({"cases": valid_cases})

        with self.assertRaises(ValueError):
            validate_expert_system_inquiry_gold_set(
                {"cases": [dict(valid_cases[0], label_source="seed") for _ in range(25)]}
            )

    def test_default_expert_gold_set_loads(self) -> None:
        gold = load_expert_system_inquiry_gold_set()

        self.assertEqual(gold["schema_version"], 1)
        self.assertEqual(gold["library_version"], "1.0")
        self.assertGreaterEqual(len(gold["cases"]), 25)
        self.assertLessEqual(len(gold["cases"]), 30)
        self.assertTrue(
            all(case["label_source"] == "consortium_expert" for case in gold["cases"])
        )

    def test_systems_corpus_index_uses_d23_pages_26_to_91(self) -> None:
        index = system_inquiry_corpus_index()
        explanation = explain_system_inquiry_probe("A4-P1")

        self.assertEqual(index["page_start"], 26)
        self.assertEqual(index["page_end"], 91)
        self.assertTrue(index["chunks"])
        self.assertEqual(explanation["source"]["document"], "FITTER_D2.3_FINAL.pdf")
        self.assertTrue(explanation["chunks"])

    def test_all_static_probe_records_are_executable_library_candidates(self) -> None:
        from app.services.chat_mitigation_creation import ChatMitigationCreationMixin

        class Engine(ChatMitigationCreationMixin):
            pass

        engine = Engine()
        library = system_inquiry_probe_library()
        for probe_id in library["records"]:
            observation = engine._system_inquiry_library_observation(
                probe_id,
                measure="A test measure",
                hazard="A test hazard",
                groups=["A test group"],
                group_label="A test group",
                prior_measures=[],
                attributes={"delivery_channel": "application"},
            )
            candidate = engine._system_inquiry_enriched_candidate(observation, ChatSession())
            self.assertEqual(candidate["probe_id"], probe_id)
            self.assertTrue(candidate["candidate_id"].startswith(probe_id))
            self.assertIn("required_anchors", candidate)


if __name__ == "__main__":
    unittest.main()
