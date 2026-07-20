import unittest
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.db.session import Base
from app.models import AppUser, KnowledgeChunk, KnowledgeDocument, UserChatMessage, UserSession
from app.routes import sync as sync_routes
from app.services.sync_service import SyncService


class SyncServiceTests(unittest.TestCase):
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

    def test_export_assigns_sync_ids_and_fk_sync_refs(self) -> None:
        user = self._add_user("admin@example.com")
        session = UserSession(
            session_key="session-a",
            title="Session A",
            user_id=user.id,
            session_data="{}",
        )
        self.db.add(session)
        self.db.flush()
        self.db.add(UserChatMessage(user_session_id=session.id, role="user", content="Hello"))
        self.db.commit()

        bundle = SyncService(self.db, device_id="device-a").export_bundle()

        sessions = self._rows(bundle, "user_sessions")
        messages = self._rows(bundle, "user_chat_messages")
        self.assertTrue(sessions[0]["sync_id"])
        self.assertEqual(messages[0]["__fk_sync_ids"]["user_session_id"], sessions[0]["sync_id"])

    def test_apply_bundle_remaps_foreign_keys_to_local_ids(self) -> None:
        user = self._add_user("admin@example.com")
        session = UserSession(
            session_key="session-a",
            title="Session A",
            user_id=user.id,
            session_data="{}",
        )
        self.db.add(session)
        self.db.flush()
        self.db.add(UserChatMessage(user_session_id=session.id, role="user", content="Hello"))
        self.db.commit()
        bundle = SyncService(self.db, device_id="source-device").export_bundle()

        target_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target_db = TargetSession()
        try:
            result = SyncService(target_db, device_id="target-device").apply_bundle(bundle)
            imported_session = target_db.scalar(select(UserSession).where(UserSession.session_key == "session-a"))
            imported_message = target_db.scalar(select(UserChatMessage).where(UserChatMessage.content == "Hello"))

            self.assertGreater(result.inserted, 0)
            self.assertIsNotNone(imported_session)
            self.assertIsNotNone(imported_message)
            self.assertEqual(imported_message.user_session_id, imported_session.id)
        finally:
            target_db.close()
            Base.metadata.drop_all(bind=target_engine)
            target_engine.dispose()

    def test_knowledge_sync_marks_scope_indexes_dirty(self) -> None:
        document = KnowledgeDocument(
            user_id=None,
            title="Main KB",
            source_type="txt",
            source_uri="main.txt",
            scope="main",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id=None,
                chunk_index=0,
                content="Knowledge content",
                source_type="txt",
                source_uri="main.txt",
            )
        )
        self.db.commit()
        bundle = SyncService(self.db, device_id="source-device").export_bundle()

        target_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target_db = TargetSession()
        try:
            service = SyncService(target_db, device_id="target-device")
            result = service.apply_bundle(bundle)

            self.assertIn("main", result.knowledge_scopes_dirty)
            self.assertIn("main", service.knowledge_index_dirty_scopes())
        finally:
            target_db.close()
            Base.metadata.drop_all(bind=target_engine)
            target_engine.dispose()

    def test_temporary_knowledge_scope_is_not_exported(self) -> None:
        document = KnowledgeDocument(
            user_id=None,
            title="Temporary KB",
            source_type="txt",
            source_uri="temporary.txt",
            scope="temporary",
            session_key="session-a",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id=None,
                chunk_index=0,
                content="Temporary evidence",
                source_type="txt",
                source_uri="temporary.txt",
            )
        )
        self.db.commit()

        bundle = SyncService(self.db, device_id="source-device").export_bundle()

        self.assertEqual(self._rows(bundle, "knowledge_documents"), [])
        self.assertEqual(self._rows(bundle, "knowledge_chunks"), [])

    def test_sync_status_requires_token(self) -> None:
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        original_enabled = sync_routes.settings.sync_enabled
        original_token = sync_routes.settings.sync_api_token
        try:
            sync_routes.settings.sync_enabled = True
            sync_routes.settings.sync_api_token = "secret"
            client = TestClient(app)
            denied = client.get("/api/sync/status", headers={"X-Sync-Token": "wrong"})
            allowed = client.get("/api/sync/status", headers={"X-Sync-Token": "secret"})

            self.assertEqual(denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("user_sessions", allowed.json()["tables"])
            self.assertEqual(
                allowed.json()["knowledge_scopes"],
                ["main", "validated_evidence", "sector_prompt"],
            )
            self.assertEqual(allowed.json()["excluded_knowledge_scopes"], ["temporary"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_api_token = original_token

    def _add_user(self, email: str) -> AppUser:
        user = AppUser(
            email=email,
            name="Admin",
            password_hash=hash_password("Password!1"),
            designation="Lead",
            organisation_type="Local",
            organisation_name="Dr Transition",
            role="admin",
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _override_db(self) -> Iterator[Session]:
        yield self.db

    @staticmethod
    def _rows(bundle: dict[str, object], table_name: str) -> list[dict[str, object]]:
        tables = bundle["tables"]
        assert isinstance(tables, list)
        for item in tables:
            if isinstance(item, dict) and item.get("name") == table_name:
                rows = item.get("rows")
                assert isinstance(rows, list)
                return rows
        return []


if __name__ == "__main__":
    unittest.main()
