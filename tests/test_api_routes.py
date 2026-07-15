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
from app.models import (
    AppRateLimit,
    AppUser,
    AuditLog,
    Country,
    KnowledgeChunk,
    KnowledgeDocument,
    Region,
    Sector,
    UserChatMessage,
    UserSession,
)
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

    async def ingest_file(
        self,
        filename: str,
        content: bytes,
        *,
        allow_lexical_only: bool = False,
    ) -> dict[str, object]:
        if not allow_lexical_only:
            raise AssertionError("Hosted KB uploads must not require server embeddings.")
        return {
            "error": False,
            "id": 11,
            "title": filename,
            "chunks": 2,
        }

    async def ingest_url(
        self,
        url: str,
        title: str | None = None,
        *,
        allow_lexical_only: bool = False,
    ) -> dict[str, object]:
        if not allow_lexical_only:
            raise AssertionError("Hosted KB URL imports must not require server embeddings.")
        return {
            "error": False,
            "id": 12,
            "title": title or url,
            "chunks": 1,
        }

    async def search(self, query: str, limit: int, **kwargs) -> list[dict[str, object]]:
        if kwargs.get("use_server_models"):
            raise AssertionError("Hosted KB search must not require server models.")
        return [{"title": "Policy note", "content": query, "score": 1.0}]

    async def delete_document(self, document_id: int) -> bool:
        self.deleted_ids.append(document_id)
        return True


class FailingKnowledgeBaseService:
    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("Temporary evidence must not be handled by the backend KB service.")


class FailingChatService:
    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("Hosted chat routes must not instantiate server LLM workflows.")


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

    def test_knowledge_sync_returns_authoritative_documents_and_chunks(self) -> None:
        document = KnowledgeDocument(
            title="Core policy",
            source_type="txt",
            source_uri="core.txt",
            scope="main",
            scope_level="global",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_index=0,
                content="Core policy chunk",
                source_type="txt",
                source_uri="core.txt",
                scope_level="global",
            )
        )
        self.db.commit()

        response = self.client.get("/api/knowledge/sync", params={"scope": "main"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["documents"][0]["title"], "Core policy")
        self.assertEqual(payload["documents"][0]["chunks"][0]["content"], "Core policy chunk")
        self.assertEqual(payload["next_cursor"], document.id)

    def test_knowledge_sync_manifest_returns_authoritative_document_ids(self) -> None:
        document = KnowledgeDocument(
            title="Manifest policy",
            source_type="txt",
            source_uri="manifest.txt",
            scope="main",
            scope_level="global",
        )
        self.db.add(document)
        self.db.commit()

        response = self.client.get("/api/knowledge/sync/manifest", params={"scope": "main"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["scope"], "main")
        self.assertEqual(payload["cursor"], document.id)
        self.assertEqual(payload["documents"][0]["id"], document.id)
        self.assertIn("checksum", payload["documents"][0])

    def test_knowledge_sync_rejects_temporary_scope(self) -> None:
        response = self.client.get("/api/knowledge/sync", params={"scope": "temporary"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("scope must be main", response.json()["detail"])

    def test_validated_evidence_promotion_persists_document_and_chunks(self) -> None:
        response = self.client.post(
            "/api/validated-evidence/promote",
            json={
                "title": "Accepted evidence",
                "source_type": "pdf",
                "source_uri": "evidence.pdf",
                "country_id": 1,
                "sector_id": 2,
                "session_key": "session-1",
                "validation_summary": "accepted",
                "chunks": [
                    {
                        "content": "Validated evidence chunk",
                        "page_number": 3,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        document_id = payload["document"]["id"]
        self.assertEqual(payload["document"]["chunks"][0]["document_id"], document_id)
        self.assertIsNotNone(payload["document"]["chunks"][0]["id"])
        self.assertIsNotNone(payload["version"])
        document = self.db.get(KnowledgeDocument, document_id)
        self.assertIsNotNone(document)
        self.assertEqual(document.scope, "validated_evidence")
        self.assertEqual(document.user_id, self.user.id)
        chunk = self.db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document_id).one()
        self.assertEqual(chunk.content, "Validated evidence chunk")
        audit = self.db.query(AuditLog).filter(AuditLog.action == "validated_evidence.promote").one()
        self.assertEqual(audit.target_id, str(document_id))

        sync_response = self.client.get("/api/knowledge/sync", params={"scope": "validated_evidence"})
        self.assertEqual(sync_response.status_code, 200)
        sync_payload = sync_response.json()
        self.assertEqual(sync_payload["documents"][0]["id"], document_id)
        self.assertEqual(sync_payload["documents"][0]["chunks"][0]["content"], "Validated evidence chunk")

    def test_validated_evidence_sync_filters_to_authorized_records(self) -> None:
        other_user = AppUser(
            email="other@example.com",
            name="Other",
            password_hash=hash_password("Password!1"),
            designation="Analyst",
            organisation_type="Local",
            organisation_name="Other Org",
            role="user",
        )
        self.db.add(other_user)
        self.db.flush()
        own_document = KnowledgeDocument(
            user_id=self.user.id,
            title="Own validated evidence",
            source_type="txt",
            source_uri="own.txt",
            scope="validated_evidence",
            scope_level="global",
        )
        other_document = KnowledgeDocument(
            user_id=other_user.id,
            title="Other user's evidence",
            source_type="txt",
            source_uri="other.txt",
            scope="validated_evidence",
            scope_level="global",
        )
        public_document = KnowledgeDocument(
            user_id=None,
            title="Public validated evidence",
            source_type="txt",
            source_uri="public.txt",
            scope="validated_evidence",
            scope_level="global",
        )
        self.db.add_all([own_document, other_document, public_document])
        self.db.commit()

        response = self.client.get("/api/knowledge/sync/manifest", params={"scope": "validated_evidence"})

        self.assertEqual(response.status_code, 200)
        titles_by_id = {
            row.id: row.title
            for row in (own_document, other_document, public_document)
        }
        synced_titles = {
            titles_by_id[item["id"]]
            for item in response.json()["documents"]
        }
        self.assertEqual(synced_titles, {"Own validated evidence", "Public validated evidence"})

    def test_chat_evidence_upload_does_not_use_backend_temporary_kb(self) -> None:
        with (
            patch.object(api_routes, "KnowledgeBaseService", FailingKnowledgeBaseService),
            patch.object(api_routes, "ChatService", FailingChatService),
        ):
            response = self.client.post(
                "/api/chat",
                data={
                    "message": "Reason: test",
                    "session_id": "session-1",
                    "evidence_url": "https://example.org/evidence",
                },
                files={"evidence_file": ("evidence.txt", b"client-side only", "text/plain")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Temporary evidence must be handled by the client app", response.json()["detail"])
        self.assertEqual(self.db.query(UserChatMessage).count(), 0)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 0)

    def test_session_state_with_local_workflow_result_does_not_persist_temporary_evidence(self) -> None:
        response = self.client.post(
            "/api/sessions/state",
            json={
                "session_id": "client-session",
                "phase": "hazard_validation",
                "session": {
                    "workflow_result": {
                        "workflow": "hazard_validation",
                        "status": "ok",
                        "summary": "accepted locally",
                    }
                },
                "messages": [
                    {"role": "user", "content": "Reason: locally validated"},
                    {"role": "bot", "content": "Accepted with local temporary evidence."},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 0)
        self.assertEqual(self.db.query(KnowledgeChunk).count(), 0)

    def test_session_state_endpoint_persists_client_snapshot_and_messages(self) -> None:
        response = self.client.post(
            "/api/sessions/state",
            json={
                "session_id": "client-session",
                "title": "Client Session",
                "country_id": 1,
                "region_id": 2,
                "sector_id": 3,
                "session": {
                    "country": "Germany",
                    "region": "Bavaria",
                    "sector": "Transport",
                    "phase": "client_validation",
                },
                "messages": [
                    {"role": "user", "content": "Client-side message"},
                    {"role": "assistant", "content": "Client-side answer"},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["session_id"], "client-session")
        user_session = self.db.query(UserSession).filter(UserSession.session_key == "client-session").one()
        self.assertEqual(user_session.user_id, self.user.id)
        self.assertEqual(user_session.country_id, 1)
        messages = self.db.query(UserChatMessage).filter(UserChatMessage.user_session_id == user_session.id).all()
        self.assertEqual([message.content for message in messages], ["Client-side message", "Client-side answer"])

    def test_selection_advance_persists_country_without_chat_service(self) -> None:
        country = Country(name="Germany", map_code="DE")
        region = Region(name="Bavaria", country=country)
        sector = Sector(name="Transport")
        country.sectors.append(sector)
        self.db.add_all([country, region, sector])
        self.db.commit()

        with patch.object(api_routes, "ChatService", FailingChatService):
            response = self.client.post(
                "/api/selections/advance",
                json={
                    "session_id": "selection-session",
                    "step": "country",
                    "message": "Germany",
                    "session": {"phase": "country"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["matched"])
        self.assertEqual(payload["step"], "region")
        self.assertEqual(payload["session"]["country"], "Germany")
        self.assertEqual(payload["options"], [{"id": region.id, "label": "Bavaria"}])
        user_session = self.db.query(UserSession).filter(UserSession.session_key == "selection-session").one()
        self.assertEqual(user_session.country_id, country.id)
        self.assertIsNone(user_session.region_id)
        messages = self.db.query(UserChatMessage).filter(UserChatMessage.user_session_id == user_session.id).all()
        self.assertEqual([message.content for message in messages], ["Germany"])

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
