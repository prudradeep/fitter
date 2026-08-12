import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from typing import Iterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy import select
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
    CountrySector,
    EvaluationQuestion,
    Prompt,
    Region,
    Sector,
    SystemHazardSocioDemographic,
    UserChatMessage,
    UserMitigationMeasure,
    UserQuestionResponse,
    UserSession,
)
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

    def test_mitigation_report_export_returns_pdf(self) -> None:
        session = UserSession(
            session_key="report-session",
            title="Report",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": "Germany",
                    "region": "Baden-Württemberg",
                    "sector": "Energy",
                    "selected_hazard": "MISSING OUT ON SOLAR SAVINGS",
                    "mitigation_record_id": "measure-report-1",
                    "mitigation_measure": "Provide targeted rooftop solar subsidies.",
                    "affected_profiles": ["Low-income households"],
                }
            ),
        )
        question = EvaluationQuestion(
            id="report-q1",
            category="The transformative impact",
            chart_title="Direct Effect",
            question="Direct Effect",
            sort_order=1,
            active=True,
        )
        other_user = AppUser(
            email="other@example.com",
            name="Other",
            password_hash=hash_password("OldPassword!1"),
            designation="Lead",
            organisation_type="Local",
            organisation_name="Other Org",
            role="user",
        )
        other_session = UserSession(
            session_key="other-report-session",
            title="Other report",
            user_id=None,
            session_data="{}",
        )
        self.db.add_all([session, question, other_user, other_session])
        self.db.commit()
        self.db.refresh(session)
        self.db.refresh(other_user)
        other_session.user_id = other_user.id
        self.db.add_all(
            [
                UserMitigationMeasure(
                    id="measure-report-1",
                    user_session_id=session.id,
                    system_hazard_id="hazard-1",
                    measure="Provide targeted rooftop solar subsidies.",
                    reason="It lowers upfront installation cost for exposed households.",
                    target_population=json.dumps(["Low-income households"]),
                    system_inquiry_json=json.dumps(
                        {
                            "summary": "1 reflection response was recorded.",
                            "annotations": [
                                {
                                    "lens_title": "Distributional incidence",
                                    "resolution_state": "addressed",
                                    "user_response": "Prioritise lower-income households.",
                                }
                            ],
                        }
                    ),
                ),
                UserMitigationMeasure(
                    id="measure-report-2",
                    user_session_id=other_session.id,
                    system_hazard_id="hazard-1",
                    measure="Offer low-interest solar loans.",
                    reason="It spreads installation costs over time.",
                    target_population=json.dumps(["Homeowner households"]),
                ),
                UserQuestionResponse(
                    user_session_id=session.id,
                    mitigation_measure_id="measure-report-1",
                    question_id="report-q1",
                    category="The transformative impact",
                    response_text="8",
                    score=8,
                    reason="Strong direct affordability benefit.",
                    evidence="Evaluation note",
                ),
                SystemHazardSocioDemographic(
                    id="profile-report-1",
                    system_hazard_id="hazard-1",
                    sector_id="energy",
                    variable_name="issue_high_energy_bills",
                    profile="Low-income households with high energy bills",
                    explanation="This group is exposed to high upfront installation costs.",
                    statistical_basis="Survey and sector-prompt evidence.",
                    source="sector_prompt",
                ),
            ]
        )
        self.db.commit()

        response = self.client.get("/api/sessions/report-session/report?scope=all_hazard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", response.content[-32:])
        reader = PdfReader(BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        normalized_text = " ".join(text.split())
        self.assertIn("high energy bills", normalized_text)
        self.assertIn("Sector statistical data", normalized_text)
        self.assertIn("Affected and target populations", normalized_text)
        self.assertIn("Target population", normalized_text)
        self.assertIn("This comparison brings together 2 mitigation measures", normalized_text)
        self.assertNotIn(
            "All mitigation measures created against this hazard from all users",
            normalized_text,
        )

        user_scope_response = self.client.get("/api/sessions/report-session/report?scope=user_hazard")

        self.assertEqual(user_scope_response.status_code, 200)
        user_scope_reader = PdfReader(BytesIO(user_scope_response.content))
        user_scope_text = " ".join(
            (page.extract_text() or "") for page in user_scope_reader.pages
        )
        self.assertNotIn(
            "All mitigation measures created by me against this hazard",
            " ".join(user_scope_text.split()),
        )

    def test_restore_session_uses_persisted_current_step_options(self) -> None:
        session = UserSession(
            session_key="mitigation-session",
            title="Mitigation session",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": "Spain",
                    "region": "Catalonia",
                    "sector": "Energy",
                    "phase": "wizard",
                    "current_step": "mitigation_duplicate_suggestion",
                    "current_input_mode": "text",
                    "current_options": [
                        {"id": "use_existing", "label": "Use existing mitigation"},
                        {"id": "write_again", "label": "Write mitigation again"},
                    ],
                    "current_other_options": ["Choose a different sector"],
                }
            ),
        )
        self.db.add(session)
        self.db.commit()

        response = self.client.get("/api/sessions/mitigation-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["step"], "mitigation_duplicate_suggestion")
        self.assertEqual(
            [option["label"] for option in payload["options"]],
            ["Use existing mitigation", "Write mitigation again"],
        )
        self.assertNotIn("Spain", [option["label"] for option in payload["options"]])

    def test_restore_session_ignores_stale_country_options_for_current_phase(self) -> None:
        session = UserSession(
            session_key="evidence-session",
            title="Evidence session",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": "Spain",
                    "region": "Catalonia",
                    "sector": "Energy",
                    "phase": "mitigation_evidence_decision",
                    "pending_mitigation_measure": "Provide bill credits.",
                    "pending_mitigation_reason": "It reduces bill pressure.",
                    "current_step": "country",
                    "current_input_mode": "text",
                    "current_options": [
                        {"id": "es", "label": "Spain"},
                        {"id": "de", "label": "Germany"},
                    ],
                }
            ),
        )
        self.db.add(session)
        self.db.commit()

        response = self.client.get("/api/sessions/evidence-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["step"], "mitigation_evidence_decision")
        self.assertEqual(
            [option["label"] for option in payload["options"]],
            ["Yes", "No"],
        )
        self.assertNotIn("Spain", [option["label"] for option in payload["options"]])

    def test_restore_session_ignores_matching_stale_options_for_optionless_phase(self) -> None:
        session = UserSession(
            session_key="evaluation-session",
            title="Evaluation session",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": "Germany",
                    "region": "Bavaria",
                    "sector": "Energy",
                    "phase": "evaluation_question",
                    "evaluation_questions": [
                        {
                            "id": "q2",
                            "category": "The transformative impact",
                            "question": "Systemic & Structural Impact",
                        }
                    ],
                    "evaluation_index": 0,
                    "current_step": "evaluation_question",
                    "current_input_mode": "text",
                    "current_options": [
                        {"id": "de", "label": "Germany"},
                        {"id": "hu", "label": "Hungary"},
                        {"id": "ie", "label": "Ireland"},
                    ],
                }
            ),
        )
        self.db.add(session)
        self.db.commit()

        response = self.client.get("/api/sessions/evaluation-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["step"], "evaluation_question")
        self.assertEqual(payload["input_mode"], "evaluation_question")
        self.assertEqual(payload["options"], [])

    def test_restore_session_recovers_current_phase_from_messages_when_session_data_is_blank(self) -> None:
        country = Country(id="de", name="Germany")
        region = Region(id="bw", country_id="de", name="Baden-Württemberg")
        sector = Sector(id="energy", name="Energy")
        self.db.add_all(
            [
                country,
                region,
                sector,
                CountrySector(country_id="de", sector_id="energy"),
                EvaluationQuestion(
                    id="q1",
                    category="The transformative impact",
                    chart_title="Direct impact",
                    question="Direct Impact",
                    sort_order=1,
                    active=True,
                ),
                EvaluationQuestion(
                    id="q2",
                    category="The transformative impact",
                    chart_title="Systemic & Structural Impact",
                    question="Systemic & Structural Impact",
                    sort_order=2,
                    active=True,
                ),
            ]
        )
        session = UserSession(
            session_key="blank-evaluation-session",
            title="Evaluation",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": None,
                    "region": None,
                    "sector": None,
                    "selected_hazard": None,
                    "mitigation_measure": None,
                }
            ),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        self.db.add(
            UserMitigationMeasure(
                id="measure-1",
                user_session_id=session.id,
                measure=(
                    "Provide temporary bill credits combined with free energy-efficiency "
                    "assessments for low-income households until longer-term building "
                    "upgrades are completed."
                ),
                reason="It directly reduces interim affordability pressure.",
                target_population=json.dumps(["Low-income households", "Homeowner households"]),
            )
        )
        started_at = datetime(2026, 1, 1, 12, 0, 0)
        transcript = [
            ("user", "Germany"),
            ("user", "Baden-Württemberg"),
            ("user", "Energy"),
            ("bot", "<p>Selected hazard: <strong>MISSING OUT ON SOLAR SAVINGS</strong></p>"),
            (
                "user",
                "Mitigation measure: Provide temporary bill credits combined with free "
                "energy-efficiency assessments for low-income households until longer-term "
                "building upgrades are completed.",
            ),
            (
                "bot",
                "<h3>Target population identified</h3><p>I identified these target-population "
                "groups from the mitigation information:</p><ul><li><strong>Homeowner "
                "households</strong></li></ul><p>Choose Continue to use these groups.</p>",
            ),
            (
                "bot",
                "<p>The transformative impact</p><p>Question 1 of 2</p>"
                "<p>1. Direct Impact (Weight: ~35%)</p>"
                "<p>Use the score slider below.</p>",
            ),
            ("user", "Score: 9"),
            (
                "bot",
                "<p>The transformative impact</p><p>Question 2 of 2</p>"
                "<p>2. Systemic & Structural Impact (Weight: ~35%)</p>"
                "<p>Use the score slider below.</p>",
            ),
        ]
        for index, (role, content) in enumerate(transcript):
            self.db.add(
                UserChatMessage(
                    user_session_id=session.id,
                    role=role,
                    content=content,
                    is_error=False,
                    created_at=started_at + timedelta(seconds=index),
                )
            )
        self.db.commit()

        response = self.client.get("/api/sessions/blank-evaluation-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["error"])
        self.assertEqual(payload["step"], "evaluation_question")
        self.assertEqual(payload["input_mode"], "evaluation_question")
        self.assertEqual(payload["options"], [])
        self.assertEqual(payload["session"]["country"], "Germany")
        self.assertEqual(payload["session"]["region"], "Baden-Württemberg")
        self.assertEqual(payload["session"]["sector"], "Energy")
        self.assertEqual(payload["session"]["selected_hazard"], "MISSING OUT ON SOLAR SAVINGS")
        self.assertIn("temporary bill credits", payload["session"]["mitigation_measure"])
        self.assertEqual(
            payload["session"]["benefited_profiles"],
            ["Low-income households", "Homeowner households"],
        )
        self.db.refresh(session)
        restored_data = json.loads(session.session_data or "{}")
        self.assertEqual(restored_data["phase"], "evaluation_question")
        self.assertEqual(restored_data["evaluation_index"], 1)
        self.assertEqual(
            restored_data["mitigation_target_population"],
            ["Low-income households", "Homeowner households"],
        )

    def test_restore_session_recovers_system_inquiry_complete_report_options(self) -> None:
        session = UserSession(
            session_key="stale-system-inquiry-session",
            title="System inquiry",
            user_id=self.user.id,
            session_data=json.dumps(
                {
                    "country": "Germany",
                    "region": "Baden-Württemberg",
                    "sector": "Energy",
                    "phase": "complete",
                    "selected_hazard": "MISSING OUT ON SOLAR SAVINGS",
                    "mitigation_measure": "Provide targeted rooftop solar subsidies.",
                    "mitigation_record_id": "measure-system-inquiry-1",
                }
            ),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        self.db.add_all(
            [
                UserMitigationMeasure(
                    id="measure-system-inquiry-1",
                    user_session_id=session.id,
                    measure="Provide targeted rooftop solar subsidies.",
                    reason="It lowers upfront installation cost.",
                    target_population=json.dumps(["Low-income households"]),
                ),
                UserChatMessage(
                    user_session_id=session.id,
                    role="bot",
                    content="<h2>System Inquiry Recorded</h2><p>System inquiry was skipped.</p>",
                    is_error=False,
                    created_at=datetime(2026, 1, 1, 12, 0, 0),
                ),
            ]
        )
        self.db.commit()

        response = self.client.get("/api/sessions/stale-system-inquiry-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["step"], "system_inquiry_complete")
        self.assertIn(
            "Download report - Mitigation measure",
            [option["label"] for option in payload["options"]],
        )
        self.assertIn(
            "Download report - All mitigation measures created against this hazard from all users",
            [option["label"] for option in payload["options"]],
        )

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
            workflow_response = self.client.post(
                "/api/prompts",
                json={
                    "prompt_key": "workflow/hazards.txt",
                    "content": "Workflow hazard help",
                },
            )
        finally:
            api_routes.settings.sync_enabled = original_enabled
            api_routes.settings.sync_mode = original_mode

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(workflow_response.status_code, 200)
        self.assertFalse(response.json()["error"])
        self.assertFalse(workflow_response.json()["error"])
        prompt = self.db.scalar(select(Prompt).where(Prompt.prompt_key == "llm/custom_prompt.txt"))
        workflow_prompt = self.db.scalar(select(Prompt).where(Prompt.prompt_key == "workflow/hazards.txt"))
        self.assertIsNotNone(prompt)
        self.assertIsNotNone(workflow_prompt)
        self.assertEqual(prompt.content, "Custom prompt")
        self.assertEqual(workflow_prompt.category, "workflow")
        self.assertEqual(workflow_prompt.content, "Workflow hazard help")
        audit_targets = {
            row.target_id
            for row in self.db.query(AuditLog).filter(AuditLog.action == "prompts.create").all()
        }
        self.assertEqual(audit_targets, {"llm/custom_prompt.txt", "workflow/hazards.txt"})

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
