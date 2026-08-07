from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base
from app.models import (
    AppRateLimit,
    KnowledgeDocument,
    LlmExchangeLog,
    SystemInquiryTelemetryEvent,
)
from app.services.maintenance import cleanup_retained_data
from app.services.knowledge_base import TEMPORARY_KB_SCOPE


class MaintenanceCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_cleanup_retained_data_removes_expired_rows(self) -> None:
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
        self.db.add(
            AppRateLimit(
                key="login:test:old",
                attempts=1,
                window_started_at=(datetime.now(timezone.utc) - timedelta(days=10)).timestamp(),
                locked_until=0.0,
            )
        )
        self.db.add(
            KnowledgeDocument(
                title="Temporary",
                source_type="txt",
                scope=TEMPORARY_KB_SCOPE,
                created_at=old,
            )
        )
        self.db.add(
            LlmExchangeLog(
                request_id="req",
                provider="test",
                endpoint="/api/chat",
                model="model",
                request_payload="{}",
                created_at=old,
            )
        )
        self.db.add(
            SystemInquiryTelemetryEvent(
                event_key="old-system-inquiry-profile",
                payload_json="{}",
                status="synced",
                created_at=old,
            )
        )
        self.db.commit()

        result = cleanup_retained_data(
            self.db,
            Settings(
                rate_limit_retention_days=1,
                temporary_knowledge_retention_hours=1,
                llm_log_retention_days=1,
                system_inquiry_profile_retention_days=1,
            ),
        )

        self.assertEqual(result["rate_limits"], 1)
        self.assertEqual(result["temporary_knowledge_documents"], 1)
        self.assertEqual(result["llm_exchange_logs"], 1)
        self.assertEqual(result["system_inquiry_telemetry_events"], 1)
        self.assertIsNone(self.db.get(AppRateLimit, "login:test:old"))


if __name__ == "__main__":
    unittest.main()
