import json
import unittest
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.auth import create_auth_token, get_current_user, hash_password, verify_password
from app.db.session import Base
from app.models import AppRateLimit, AppUser, AuditLog, UserChatMessage, UserSession
from app.resource_paths import resource_path
from app.services.rate_limit import clear_rate_limits
import app.routes.api as api_routes
import app.routes.auth as auth_routes


class FakeKnowledgeBaseService:
    documents: list[dict[str, object]] = [{"id": 7, "title": "Policy note"}]
    deleted_ids: list[int] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def list_documents(self) -> list[dict[str, object]]:
        return self.documents

    async def ingest_file(self, filename: str, content: bytes) -> dict[str, object]:
        return {
            "error": False,
            "id": 11,
            "title": filename,
            "chunks": 2,
        }

    async def ingest_url(self, url: str, title: str | None = None) -> dict[str, object]:
        return {
            "error": False,
            "id": 12,
            "title": title or url,
            "chunks": 1,
        }

    async def search(self, query: str, limit: int) -> list[dict[str, object]]:
        return [{"title": "Policy note", "content": query, "score": 1.0}]

    async def delete_document(self, document_id: int) -> bool:
        self.deleted_ids.append(document_id)
        return True


class ApiRouteIntegrationTests(unittest.TestCase):
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
        self.user = AppUser(
            email="admin@example.com",
            name="Admin",
            password_hash=hash_password("OldPassword!1"),
            designation="Lead",
            organisation_type="Local",
            organisation_name="Dr Transition",
            role="admin",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        app = FastAPI()
        app.mount("/static", StaticFiles(directory=str(resource_path("app/static"))), name="static")
        app.include_router(auth_routes.router)
        app.include_router(api_routes.router)
        app.dependency_overrides[auth_routes.get_db] = self._override_db
        app.dependency_overrides[api_routes.get_db] = self._override_db
        app.dependency_overrides[api_routes.require_current_user] = lambda: self.user
        app.dependency_overrides[api_routes.require_admin_user] = lambda: self.user
        self.app = app
        self.client = TestClient(app)
        clear_rate_limits()

    def tearDown(self) -> None:
        clear_rate_limits()
        self.client.close()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _override_db(self) -> Iterator[Session]:
        yield self.db

    def test_auth_login_sets_cookie_for_valid_credentials(self) -> None:
        response = self.client.post(
            "/login",
            data={"email": "admin@example.com", "password": "OldPassword!1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("dr_transition_auth", response.headers.get("set-cookie", ""))

    def test_auth_login_locks_out_after_repeated_failures(self) -> None:
        with self._temporary_auth_limits(login_rate_limit_attempts=2, login_rate_limit_lockout_seconds=60):
            first = self.client.post(
                "/login",
                data={"email": "admin@example.com", "password": "wrong"},
            )
            second = self.client.post(
                "/login",
                data={"email": "admin@example.com", "password": "wrong"},
            )

        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Too many login attempts", second.text)
        self.assertIsNotNone(self.db.get(AppRateLimit, "login:testclient:admin@example.com"))

    def test_signup_locks_out_after_repeated_invalid_attempts(self) -> None:
        with self._temporary_auth_limits(
            signup_rate_limit_attempts=2,
            signup_rate_limit_lockout_seconds=60,
        ):
            first = self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "name": "New User",
                    "password": "weak",
                    "confirm_password": "weak",
                },
            )
            second = self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "name": "New User",
                    "password": "weak",
                    "confirm_password": "weak",
                },
            )

        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Too many signup attempts", second.text)
        self.assertIsNotNone(self.db.get(AppRateLimit, "signup:testclient:new@example.com"))

    def test_password_change_updates_hash(self) -> None:
        original_version = int(self.user.session_version or 1)
        response = self.client.patch(
            "/api/profile/password",
            json={
                "current_password": "OldPassword!1",
                "new_password": "NewPassword!2",
                "confirm_password": "NewPassword!2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["error"])
        self.db.refresh(self.user)
        self.assertFalse(verify_password("OldPassword!1", self.user.password_hash))
        self.assertTrue(verify_password("NewPassword!2", self.user.password_hash))
        self.assertEqual(self.user.session_version, original_version + 1)
        self.assertIn("dr_transition_auth", response.headers.get("set-cookie", ""))
        audit = self.db.query(AuditLog).filter(AuditLog.action == "password.change").one()
        self.assertEqual(audit.user_id, self.user.id)
        self.assertEqual(audit.target_type, "user")

    def test_auth_cookie_session_version_must_match_user(self) -> None:
        token = create_auth_token(self.user.id, int(self.user.session_version or 1))

        self.assertEqual(get_current_user(self.db, token), self.user)

        self.user.session_version = int(self.user.session_version or 1) + 1
        self.db.commit()

        self.assertIsNone(get_current_user(self.db, token))

    def test_password_change_locks_out_after_repeated_current_password_failures(self) -> None:
        with self._temporary_route_limits(
            password_rate_limit_attempts=2,
            password_rate_limit_lockout_seconds=60,
        ):
            first = self.client.patch(
                "/api/profile/password",
                json={
                    "current_password": "wrong",
                    "new_password": "NewPassword!2",
                    "confirm_password": "NewPassword!2",
                },
            )
            second = self.client.patch(
                "/api/profile/password",
                json={
                    "current_password": "wrong",
                    "new_password": "NewPassword!2",
                    "confirm_password": "NewPassword!2",
                },
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Too many password change attempts", second.json()["detail"])
        self.assertIsNotNone(self.db.get(AppRateLimit, f"password:testclient:{self.user.id}"))

    def test_session_export_and_import_round_trip(self) -> None:
        session = UserSession(
            session_key="source-session",
            title="Source session",
            user_id=self.user.id,
            session_data=json.dumps({"phase": "country"}),
        )
        self.db.add(session)
        self.db.flush()
        self.db.add(
            UserChatMessage(
                user_session_id=session.id,
                role="user",
                content="Hello",
                is_error=False,
            )
        )
        self.db.commit()

        export_response = self.client.get("/api/sessions/source-session/export")
        self.assertEqual(export_response.status_code, 200)
        payload = export_response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["messages"][0]["raw_content"], "Hello")

        import_response = self.client.post(
            "/api/sessions/import",
            files={"file": ("session.json", json.dumps(payload), "application/json")},
        )
        self.assertEqual(import_response.status_code, 200)
        self.assertFalse(import_response.json()["error"])
        self.assertEqual(import_response.json()["messages"], 1)
        audit = self.db.query(AuditLog).filter(AuditLog.action == "session.import").one()
        self.assertEqual(audit.user_id, self.user.id)
        self.assertEqual(audit.target_type, "session")

    def test_session_import_rejects_oversized_request_before_read(self) -> None:
        with self._temporary_route_limits(max_session_import_bytes=32):
            response = self.client.post(
                "/api/sessions/import",
                files={"file": ("session.json", "{}" * 200, "application/json")},
            )

        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.json()["error"])

    def test_knowledge_routes_upload_search_delete(self) -> None:
        with patch.object(api_routes, "KnowledgeBaseService", FakeKnowledgeBaseService):
            upload_response = self.client.post(
                "/api/knowledge/upload",
                files={"file": ("policy.txt", b"policy evidence", "text/plain")},
            )
            search_response = self.client.post("/api/knowledge/search", json={"query": "policy"})
            delete_response = self.client.delete("/api/knowledge/7")

        self.assertEqual(upload_response.status_code, 200)
        self.assertFalse(upload_response.json()["error"])
        self.assertEqual(search_response.json()["results"][0]["content"], "policy")
        self.assertTrue(delete_response.json()["deleted"])
        actions = {
            row.action
            for row in self.db.query(AuditLog).filter(AuditLog.action.like("knowledge.%")).all()
        }
        self.assertEqual(actions, {"knowledge.upload", "knowledge.delete"})

    def test_json_endpoint_rejects_oversized_body_before_parse(self) -> None:
        with self._temporary_route_limits(max_json_bytes=32):
            response = self.client.post(
                "/api/knowledge/search",
                json={"query": "x" * 200},
            )

        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.json()["error"])

    def test_knowledge_upload_rejects_oversized_request_before_read(self) -> None:
        with self._temporary_route_limits(max_upload_bytes=32):
            response = self.client.post(
                "/api/knowledge/upload",
                files={"file": ("policy.txt", b"x" * 200, "text/plain")},
            )

        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.json()["error"])

    def test_evidence_upload_rejects_oversized_request_before_read(self) -> None:
        with self._temporary_route_limits(max_upload_bytes=32):
            response = self.client.post(
                "/api/chat",
                data={"message": "Reason: test", "session_id": "session-1"},
                files={"evidence_file": ("evidence.txt", b"x" * 200, "text/plain")},
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "Evidence upload is too large.")

    def test_content_length_parses_numeric_header(self) -> None:
        request = self.client.build_request(
            "POST",
            "/api/knowledge/upload",
            headers={"content-length": "123"},
        )

        self.assertEqual(api_routes._content_length(request), 123)

    def test_content_length_ignores_invalid_header(self) -> None:
        request = self.client.build_request(
            "POST",
            "/api/knowledge/upload",
            headers={"content-length": "not-a-number"},
        )

        self.assertIsNone(api_routes._content_length(request))

    @contextmanager
    def _temporary_route_limits(self, **values: int) -> Iterator[None]:
        original = {name: getattr(api_routes.settings, name) for name in values}
        try:
            for name, value in values.items():
                setattr(api_routes.settings, name, value)
            yield
        finally:
            for name, value in original.items():
                setattr(api_routes.settings, name, value)

    @contextmanager
    def _temporary_auth_limits(self, **values: int) -> Iterator[None]:
        original = {name: getattr(auth_routes.settings, name) for name in values}
        try:
            for name, value in values.items():
                setattr(auth_routes.settings, name, value)
            yield
        finally:
            for name, value in original.items():
                setattr(auth_routes.settings, name, value)


if __name__ == "__main__":
    unittest.main()
