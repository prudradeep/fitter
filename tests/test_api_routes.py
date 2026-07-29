import json
import unittest
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.auth import create_auth_token, get_current_user, hash_password, verify_password
from app.db.session import Base
from app.models import AppRateLimit, AppUser, AuditLog, Prompt, UserChatMessage, UserSession
from app.resource_paths import resource_path
from app.services.rate_limit import clear_rate_limits
from app.services.sync_service import SyncService
from app.services import sync_permissions
import app.routes.api as api_routes
import app.routes.auth as auth_routes
import app.routes.sync as sync_routes


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
        self.original_prompt_source = api_routes.settings.prompt_source
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
        self.coverage_patcher = patch.object(auth_routes, "get_coverage_map_rows", return_value=[])
        self.coverage_patcher.start()
        self.app = app
        self.client = TestClient(app)
        clear_rate_limits()

    def tearDown(self) -> None:
        api_routes.settings.prompt_source = self.original_prompt_source
        clear_rate_limits()
        self.coverage_patcher.stop()
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

    def test_sync_token_check_ensures_schema_before_lookup(self) -> None:
        original_enabled = sync_routes.settings.sync_enabled
        try:
            sync_routes.settings.sync_enabled = True

            self.assertNotIn("sync_clients", inspect(self.engine).get_table_names())
            with self.assertRaises(Exception) as raised:
                sync_routes.require_sync_token(
                    authorization="Bearer invalid-token",
                    x_sync_token=None,
                    db=self.db,
                )

            self.assertEqual(getattr(raised.exception, "status_code", None), 401)
            self.assertIn("sync_clients", inspect(self.engine).get_table_names())
        finally:
            sync_routes.settings.sync_enabled = original_enabled

    def test_signup_page_sets_matching_csrf_cookie_and_form_field(self) -> None:
        original_csrf_enabled = auth_routes.settings.csrf_protection_enabled
        original_secret_key = auth_routes.settings.secret_key
        try:
            auth_routes.settings.csrf_protection_enabled = True
            auth_routes.settings.secret_key = "test-secret-for-csrf"

            response = self.client.get("/signup")

            cookie_token = response.cookies.get("dr_transition_csrf")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(cookie_token)
            self.assertIn(
                f'name="csrf_token" value="{cookie_token}"',
                response.text,
            )
        finally:
            auth_routes.settings.csrf_protection_enabled = original_csrf_enabled
            auth_routes.settings.secret_key = original_secret_key

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

    def test_signup_creates_user_when_all_fields_are_submitted(self) -> None:
        response = self.client.post(
            "/signup",
            data={
                "email": "new@example.com",
                "name": "New User",
                "password": "StrongPassword!1",
                "confirm_password": "StrongPassword!1",
                "designation": "Policy Lead",
                "organisation_type": "Public sector",
                "organisation_name": "Transition Office",
            },
            follow_redirects=False,
        )

        created = self.db.query(AppUser).filter(AppUser.email == "new@example.com").one_or_none()
        self.assertEqual(response.status_code, 303)
        self.assertIsNotNone(created)
        self.assertEqual(created.role, "user")
        self.assertIn("dr_transition_auth", response.headers.get("set-cookie", ""))

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

    def test_main_knowledge_mutation_requires_sync_main_permission_when_sync_enabled(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "server"
            with patch.object(api_routes, "KnowledgeBaseService", FakeKnowledgeBaseService):
                denied = self.client.delete("/api/knowledge/7")
            SyncService(self.db).upsert_sync_client(
                token="admin-token",
                client_name="Admin workstation",
                user_email=self.user.email,
                can_sync_main_kb=True,
            )
            with patch.object(api_routes, "KnowledgeBaseService", FakeKnowledgeBaseService):
                allowed = self.client.delete("/api/knowledge/7")
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["deleted"])

    def test_main_knowledge_mutation_uses_server_token_permission_on_sync_client(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        original_url = api_routes.settings.sync_server_url
        original_token = api_routes.settings.sync_api_token

        class FakeStatusResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"sync_client": {"can_sync_main_kb": True}}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, *args, **kwargs) -> FakeStatusResponse:
                return FakeStatusResponse()

        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "client"
            api_routes.settings.sync_server_url = "https://sync.example"
            api_routes.settings.sync_api_token = "raw-client-token"
            with (
                patch.object(sync_permissions.httpx, "AsyncClient", FakeAsyncClient),
                patch.object(api_routes, "KnowledgeBaseService", FakeKnowledgeBaseService),
            ):
                response = self.client.delete("/api/knowledge/7")
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode
            api_routes.settings.sync_server_url = original_url
            api_routes.settings.sync_api_token = original_token

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])

    def test_admin_prompt_routes_list_detail_and_update(self) -> None:
        prompt = Prompt(
            prompt_key="llm/test_prompt.txt",
            category="llm",
            display_name="llm / test_prompt.txt",
            content="Original prompt",
            source_path="llm/test_prompt.txt",
        )
        self.db.add(prompt)
        self.db.commit()
        self.db.refresh(prompt)

        list_response = self.client.get("/api/prompts")
        detail_response = self.client.get(f"/api/prompts/{prompt.id}")
        update_response = self.client.patch(
            f"/api/prompts/{prompt.id}",
            json={"content": "Updated prompt"},
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["prompts"][0]["prompt_key"], "llm/test_prompt.txt")
        self.assertEqual(detail_response.json()["prompt"]["content"], "Original prompt")
        self.assertFalse(update_response.json()["error"])
        self.db.refresh(prompt)
        self.assertEqual(prompt.content, "Updated prompt")
        audit = self.db.query(AuditLog).filter(AuditLog.action == "prompts.update").one()
        self.assertEqual(audit.target_id, "llm/test_prompt.txt")

    def test_admin_can_create_prompt(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "server"
            denied = self.client.post(
                "/api/prompts",
                json={"prompt_key": "llm/denied_prompt.txt", "content": "Denied prompt"},
            )
            SyncService(self.db).upsert_sync_client(
                token="admin-token",
                client_name="Admin workstation",
                user_email=self.user.email,
                can_manage_prompts=True,
            )
            response = self.client.post(
                "/api/prompts",
                json={"prompt_key": "llm/custom_prompt.txt", "content": "Custom prompt"},
            )
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["error"])
        prompt = self.db.scalar(select(Prompt).where(Prompt.prompt_key == "llm/custom_prompt.txt"))
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.content, "Custom prompt")
        audit = self.db.query(AuditLog).filter(AuditLog.action == "prompts.create").one()
        self.assertEqual(audit.target_id, "llm/custom_prompt.txt")

    def test_admin_can_view_and_update_prompt_source_setting(self) -> None:
        api_routes.settings.prompt_source = "auto"

        current_response = self.client.get("/api/settings/prompt-source")
        update_response = self.client.patch(
            "/api/settings/prompt-source",
            json={"prompt_source": "file"},
        )

        self.assertEqual(current_response.status_code, 200)
        self.assertEqual(current_response.json()["prompt_source"], "auto")
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["prompt_source"], "file")
        self.assertEqual(api_routes.settings.prompt_source, "file")
        audit = self.db.query(AuditLog).filter(
            AuditLog.action == "settings.prompt_source.update"
        ).one()
        self.assertEqual(audit.target_id, "prompt_source")

    def test_prompt_source_update_rejects_unknown_value(self) -> None:
        api_routes.settings.prompt_source = "auto"

        response = self.client.patch(
            "/api/settings/prompt-source",
            json={"prompt_source": "remote"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["error"])
        self.assertEqual(api_routes.settings.prompt_source, "auto")

    def test_admin_cannot_create_prompt_on_sync_client(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "client"
            response = self.client.post(
                "/api/prompts",
                json={"prompt_key": "llm/client_prompt.txt", "content": "Client prompt"},
            )
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.query(Prompt).count(), 0)

    def test_sector_prompt_reindex_requires_sync_client_permission_when_sync_enabled(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "server"
            denied = self.client.post("/api/sector-prompts/reindex")
            SyncService(self.db).upsert_sync_client(
                token="admin-token",
                client_name="Admin workstation",
                user_email=self.user.email,
                can_reindex_sector_prompts=True,
            )
            with patch.object(
                api_routes.SectorPromptRagService,
                "rebuild",
                return_value={"error": False, "indexed": 3},
            ):
                allowed = self.client.post("/api/sector-prompts/reindex")
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(allowed.json()["error"])

    def test_sector_prompt_reindex_uses_server_token_permission_on_sync_client(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        original_url = api_routes.settings.sync_server_url
        original_token = api_routes.settings.sync_api_token

        class FakeStatusResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"sync_client": {"can_reindex_sector_prompts": True}}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, *args, **kwargs) -> FakeStatusResponse:
                return FakeStatusResponse()

        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "client"
            api_routes.settings.sync_server_url = "https://sync.example"
            api_routes.settings.sync_api_token = "raw-client-token"
            with (
                patch.object(sync_permissions.httpx, "AsyncClient", FakeAsyncClient),
                patch.object(
                    api_routes.SectorPromptRagService,
                    "rebuild",
                    return_value={"error": False, "indexed": 3},
                ),
            ):
                response = self.client.post("/api/sector-prompts/reindex")
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode
            api_routes.settings.sync_server_url = original_url
            api_routes.settings.sync_api_token = original_token

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["error"])

    def test_prompt_create_on_sync_client_uses_server_prompt_permission(self) -> None:
        original_enabled = api_routes.settings.sync_enabled
        original_mode = api_routes.settings.sync_mode
        original_url = api_routes.settings.sync_server_url
        original_token = api_routes.settings.sync_api_token

        class FakeStatusResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"sync_client": {"can_manage_prompts": True}}

        class FakePromptResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "error": False,
                    "prompt": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "prompt_key": "llm/client_prompt.txt",
                        "category": "llm",
                        "model": None,
                        "display_name": "llm / client_prompt.txt",
                        "source_path": None,
                        "updated_at": None,
                        "content_preview": "Client prompt",
                        "content": "Client prompt",
                    },
                    "detail": "Prompt created.",
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, *args, **kwargs) -> FakeStatusResponse:
                return FakeStatusResponse()

            async def request(self, *args, **kwargs) -> FakePromptResponse:
                return FakePromptResponse()

        try:
            api_routes.settings.sync_enabled = True
            api_routes.settings.sync_mode = "client"
            api_routes.settings.sync_server_url = "https://sync.example"
            api_routes.settings.sync_api_token = "raw-client-token"
            with (
                patch.object(sync_permissions.httpx, "AsyncClient", FakeAsyncClient),
                patch.object(api_routes.httpx, "AsyncClient", FakeAsyncClient),
            ):
                response = self.client.post(
                    "/api/prompts",
                    json={"prompt_key": "llm/client_prompt.txt", "content": "Client prompt"},
                )
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode
            api_routes.settings.sync_server_url = original_url
            api_routes.settings.sync_api_token = original_token

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["error"])
        prompt = self.db.get(Prompt, "11111111-1111-4111-8111-111111111111")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.content, "Client prompt")

    def test_prompt_routes_require_admin_user(self) -> None:
        regular_user = AppUser(
            email="user@example.com",
            name="User",
            password_hash=hash_password("OldPassword!1"),
            designation="Analyst",
            organisation_type="Local",
            organisation_name="Dr Transition",
            role="user",
        )
        self.db.add(regular_user)
        self.db.commit()
        self.db.refresh(regular_user)
        app = FastAPI()
        app.include_router(api_routes.router)
        app.dependency_overrides[api_routes.get_db] = self._override_db
        app.dependency_overrides[api_routes.require_current_user] = lambda: regular_user
        client = TestClient(app)
        try:
            response = client.get("/api/prompts")
        finally:
            client.close()

        self.assertEqual(response.status_code, 403)

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
