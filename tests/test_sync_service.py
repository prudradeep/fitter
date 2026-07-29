import unittest
from datetime import datetime, timedelta
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.db.session import Base
from app.models import AppUser, Country, KnowledgeChunk, KnowledgeDocument, Prompt, UserActivity, UserChatMessage, UserSession
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

    def test_app_user_rows_are_encrypted_in_sync_payload(self) -> None:
        original_token = sync_routes.settings.sync_api_token
        sync_routes.settings.sync_api_token = "shared-sync-token"
        try:
            user = self._add_user("secure@example.com")
            password_hash = user.password_hash
            bundle = SyncService(self.db, device_id="device-a").export_bundle()
        finally:
            sync_routes.settings.sync_api_token = original_token

        users = self._rows(bundle, "app_users")
        self.assertEqual(len(users), 1)
        self.assertIn("__encrypted_row", users[0])
        self.assertNotIn("password_hash", users[0])
        self.assertNotIn(password_hash, str(users[0]))

    def test_user_data_export_respects_sync_my_data_cutoff(self) -> None:
        service = SyncService(self.db, device_id="device-a")
        status = service.set_user_data_sync_enabled(True)
        enabled_at = datetime.fromisoformat(str(status["enabled_at"]).replace("Z", ""))
        user = self._add_user("cutoff@example.com")
        old_session = UserSession(
            session_key="old-session",
            title="Old Session",
            user_id=user.id,
            session_data="{}",
            created_at=enabled_at - timedelta(seconds=1),
        )
        new_session = UserSession(
            session_key="new-session",
            title="New Session",
            user_id=user.id,
            session_data="{}",
            created_at=enabled_at + timedelta(seconds=1),
        )
        self.db.add_all([old_session, new_session])
        self.db.commit()

        bundle = service.export_bundle(
            include_user_data=True,
            user_data_enabled_at=enabled_at,
        )

        session_keys = {row["session_key"] for row in self._rows(bundle, "user_sessions")}
        self.assertEqual(session_keys, {"new-session"})

    def test_user_data_sync_defaults_enabled(self) -> None:
        status = SyncService(self.db, device_id="device-a").user_data_sync_status()

        self.assertTrue(status["enabled"])
        self.assertIsNotNone(status["enabled_at"])

    def test_user_data_export_can_be_disabled(self) -> None:
        user = self._add_user("disabled-export@example.com")
        self.db.add(
            UserSession(
                session_key="local-session",
                title="Local Session",
                user_id=user.id,
                session_data="{}",
            )
        )
        self.db.commit()

        bundle = SyncService(self.db, device_id="device-a").export_bundle(
            include_app_users=False,
            include_user_data=False,
        )

        self.assertEqual(self._rows(bundle, "app_users"), [])
        self.assertEqual(self._rows(bundle, "user_sessions"), [])

    def test_server_exports_prompts_to_clients(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        try:
            self.db.add(
                Prompt(
                    prompt_key="llm/custom_prompt.txt",
                    category="llm",
                    display_name="llm / custom_prompt.txt",
                    content="Server prompt",
                )
            )
            self.db.commit()

            bundle = SyncService(self.db, device_id="server-device").export_bundle()
        finally:
            sync_routes.settings.sync_mode = original_mode

        prompts = self._rows(bundle, "prompts")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["prompt_key"], "llm/custom_prompt.txt")

    def test_client_does_not_export_prompts(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "client"
        try:
            self.db.add(
                Prompt(
                    prompt_key="llm/client_prompt.txt",
                    category="llm",
                    display_name="llm / client_prompt.txt",
                    content="Client prompt",
                )
            )
            self.db.commit()

            bundle = SyncService(self.db, device_id="client-device").export_bundle()
        finally:
            sync_routes.settings.sync_mode = original_mode

        self.assertEqual(self._rows(bundle, "prompts"), [])

    def test_server_rejects_inbound_prompt_rows(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "prompts",
                    "rows": [
                        {
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "prompt_key": "llm/client_prompt.txt",
                            "category": "llm",
                            "display_name": "Client Prompt",
                            "content": "Client prompt",
                            "source_path": None,
                        }
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={"active": True, "can_sync_validated_kb": True, "can_sync_user_data": True},
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(self.db.query(Prompt).count(), 0)

    def test_client_applies_server_prompt_rows(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "client"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "server-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "prompts",
                    "rows": [
                        {
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "server-device",
                            "sync_revision": 1,
                            "prompt_key": "llm/server_prompt.txt",
                            "category": "llm",
                            "display_name": "Server Prompt",
                            "content": "Server prompt",
                            "source_path": None,
                        }
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="client-device").apply_bundle(bundle)
        finally:
            sync_routes.settings.sync_mode = original_mode

        prompt = self.db.scalar(select(Prompt).where(Prompt.prompt_key == "llm/server_prompt.txt"))
        self.assertEqual(result.inserted, 1)
        self.assertTrue(result.prompts_dirty)
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.content, "Server prompt")

    def test_client_can_export_app_users_when_user_data_sync_is_disabled(self) -> None:
        user = self._add_user("client-user@example.com")
        self.db.add(
            UserSession(
                session_key="local-session",
                title="Local Session",
                user_id=user.id,
                session_data="{}",
            )
        )
        self.db.commit()

        bundle = SyncService(self.db, device_id="client-device").export_bundle(
            include_app_users=True,
            include_user_data=False,
        )

        self.assertEqual(len(self._rows(bundle, "app_users")), 1)
        self.assertIn("__encrypted_row", self._rows(bundle, "app_users")[0])
        self.assertEqual(self._rows(bundle, "user_sessions"), [])

    def test_encrypted_app_user_rows_apply_with_original_hash(self) -> None:
        original_token = sync_routes.settings.sync_api_token
        sync_routes.settings.sync_api_token = "shared-sync-token"
        try:
            user = self._add_user("secure@example.com")
            password_hash = user.password_hash
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
                result = SyncService(target_db, device_id="target-device").apply_bundle(
                    bundle,
                    current_user_email="secure@example.com",
                )
                imported_user = target_db.scalar(select(AppUser).where(AppUser.email == "secure@example.com"))

                self.assertGreater(result.inserted, 0)
                self.assertIsNotNone(imported_user)
                self.assertEqual(imported_user.password_hash, password_hash)
            finally:
                target_db.close()
                Base.metadata.drop_all(bind=target_engine)
                target_engine.dispose()
        finally:
            sync_routes.settings.sync_api_token = original_token

    def test_client_stores_non_current_synced_app_users_encrypted_at_rest(self) -> None:
        original_token = sync_routes.settings.sync_api_token
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_api_token = "shared-sync-token"
        sync_routes.settings.sync_mode = "client"
        try:
            current_user = self._add_user("current@example.com")
            other_user = self._add_user("other@example.com")
            bundle = SyncService(self.db, device_id="server-device").export_bundle()

            target_engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=target_engine)
            TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
            target_db = TargetSession()
            try:
                result = SyncService(target_db, device_id="client-device").apply_bundle(
                    bundle,
                    current_user_email=current_user.email,
                )
                clear_user = target_db.scalar(select(AppUser).where(AppUser.email == current_user.email))
                encrypted_users = target_db.scalars(
                    select(AppUser).where(AppUser.sync_encrypted_payload.is_not(None))
                ).all()

                self.assertGreater(result.inserted, 0)
                self.assertIsNotNone(clear_user)
                self.assertEqual(clear_user.sync_encrypted_payload, None)
                self.assertEqual(len(encrypted_users), 1)
                self.assertNotEqual(encrypted_users[0].email, other_user.email)
                self.assertNotIn(other_user.email, str(encrypted_users[0].__dict__))
                self.assertIn("__encryption", encrypted_users[0].sync_encrypted_payload or "")
            finally:
                target_db.close()
                Base.metadata.drop_all(bind=target_engine)
                target_engine.dispose()
        finally:
            sync_routes.settings.sync_api_token = original_token
            sync_routes.settings.sync_mode = original_mode

    def test_client_keeps_same_origin_device_app_users_available_for_login(self) -> None:
        original_token = sync_routes.settings.sync_api_token
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_api_token = "shared-sync-token"
        sync_routes.settings.sync_mode = "client"
        try:
            local_user = self._add_user("local-device-user@example.com")
            local_second_user = self._add_user("local-device-second@example.com")
            remote_user = self._add_user("remote-device-user@example.com")
            service = SyncService(self.db, device_id="local-device")
            service.ensure_schema()
            user_table = AppUser.__table__
            self.db.execute(
                user_table.update()
                .where(user_table.c.email.in_([local_user.email, local_second_user.email]))
                .values(origin_device_id="local-device")
            )
            self.db.execute(
                user_table.update()
                .where(user_table.c.email == remote_user.email)
                .values(origin_device_id="remote-device")
            )
            self.db.commit()
            bundle = service.export_bundle()

            target_engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=target_engine)
            TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
            target_db = TargetSession()
            try:
                SyncService(target_db, device_id="local-device").apply_bundle(bundle)
                local_imported = target_db.scalar(select(AppUser).where(AppUser.email == local_user.email))
                local_second_imported = target_db.scalar(select(AppUser).where(AppUser.email == local_second_user.email))
                encrypted_users = target_db.scalars(
                    select(AppUser).where(AppUser.sync_encrypted_payload.is_not(None))
                ).all()

                self.assertIsNotNone(local_imported)
                self.assertIsNotNone(local_second_imported)
                self.assertEqual(local_imported.sync_encrypted_payload, None)
                self.assertEqual(local_second_imported.sync_encrypted_payload, None)
                self.assertEqual(len(encrypted_users), 1)
                self.assertNotEqual(encrypted_users[0].email, remote_user.email)
            finally:
                target_db.close()
                Base.metadata.drop_all(bind=target_engine)
                target_engine.dispose()
        finally:
            sync_routes.settings.sync_api_token = original_token
            sync_routes.settings.sync_mode = original_mode

    def test_apply_bundle_uses_column_unique_key_before_insert(self) -> None:
        self.db.add(Country(name="Germany", map_code="DE", map_path="old.geo.json"))
        self.db.commit()
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "countries",
                    "rows": [
                        {
                            "sync_id": "33a14422-dbb3-42f4-a3d6-67532e1ba570",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "sync_updated_at": "2026-07-20T00:00:00Z",
                            "sync_deleted_at": None,
                            "name": "Germany",
                            "map_code": "DE",
                            "map_path": "countries/de/de-all.geo.json",
                        }
                    ],
                }
            ],
        }

        result = SyncService(self.db, device_id="server-device").apply_bundle(bundle)
        countries = self.db.query(Country).all()
        sync_id = self.db.execute(select(Country.__table__.c.sync_id)).scalar_one()

        self.assertEqual(result.updated, 1)
        self.assertEqual(len(countries), 1)
        self.assertEqual(countries[0].map_path, "countries/de/de-all.geo.json")
        self.assertEqual(sync_id, "33a14422-dbb3-42f4-a3d6-67532e1ba570")

    def test_apply_bundle_skips_older_existing_row(self) -> None:
        session = UserSession(
            session_key="session-a",
            title="New local title",
            session_data="{}",
            updated_at=datetime(2026, 7, 21, 12, 0, 0),
        )
        self.db.add(session)
        self.db.flush()
        self.db.execute(
            UserSession.__table__.update()
            .where(UserSession.__table__.c.id == session.id)
            .values(
                sync_id="11111111-1111-4111-8111-111111111111",
                origin_device_id="local-device",
                sync_revision=3,
                sync_updated_at=datetime(2026, 7, 21, 12, 0, 0),
                updated_at=datetime(2026, 7, 21, 12, 0, 0),
            )
        )
        self.db.commit()
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "remote-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "user_sessions",
                    "rows": [
                        {
                            "id": session.id,
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "remote-device",
                            "sync_revision": 2,
                            "sync_updated_at": "2026-07-20T12:00:00Z",
                            "sync_deleted_at": None,
                            "session_key": "session-a",
                            "title": "Older remote title",
                            "title_is_manual": False,
                            "session_data": "{}",
                            "user_id": None,
                        }
                    ],
                }
            ],
        }

        result = SyncService(self.db, device_id="local-device").apply_bundle(
            bundle,
            sync_client={"can_sync_user_data": True},
        )
        title, sync_revision = self.db.execute(
            select(UserSession.__table__.c.title, UserSession.__table__.c.sync_revision)
            .where(UserSession.__table__.c.id == session.id)
        ).one()

        self.assertEqual(result.skipped, 1)
        self.assertEqual(title, "New local title")
        self.assertEqual(sync_revision, 3)

    def test_apply_bundle_updates_when_inbound_row_is_newer(self) -> None:
        session = UserSession(
            session_key="session-a",
            title="Old local title",
            session_data="{}",
            updated_at=datetime(2026, 7, 20, 12, 0, 0),
        )
        self.db.add(session)
        self.db.flush()
        self.db.execute(
            UserSession.__table__.update()
            .where(UserSession.__table__.c.id == session.id)
            .values(
                sync_id="11111111-1111-4111-8111-111111111111",
                origin_device_id="local-device",
                sync_revision=2,
                sync_updated_at=datetime(2026, 7, 20, 12, 0, 0),
                updated_at=datetime(2026, 7, 20, 12, 0, 0),
            )
        )
        self.db.commit()
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "remote-device",
            "exported_at": "2026-07-21T00:00:00Z",
            "tables": [
                {
                    "name": "user_sessions",
                    "rows": [
                        {
                            "id": session.id,
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "remote-device",
                            "sync_revision": 3,
                            "sync_updated_at": "2026-07-21T12:00:00Z",
                            "sync_deleted_at": None,
                            "session_key": "session-a",
                            "title": "New remote title",
                            "title_is_manual": True,
                            "session_data": "{}",
                            "user_id": None,
                        }
                    ],
                }
            ],
        }

        result = SyncService(self.db, device_id="local-device").apply_bundle(
            bundle,
            sync_client={"can_sync_user_data": True},
        )
        title, sync_revision = self.db.execute(
            select(UserSession.__table__.c.title, UserSession.__table__.c.sync_revision)
            .where(UserSession.__table__.c.id == session.id)
        ).one()

        self.assertEqual(result.updated, 1)
        self.assertEqual(title, "New remote title")
        self.assertEqual(sync_revision, 3)

    def test_export_bumps_sync_metadata_when_local_row_updated(self) -> None:
        session = UserSession(
            session_key="session-a",
            title="Updated local title",
            session_data="{}",
            updated_at=datetime(2026, 7, 22, 12, 0, 0),
        )
        self.db.add(session)
        self.db.flush()
        self.db.execute(
            UserSession.__table__.update()
            .where(UserSession.__table__.c.id == session.id)
            .values(
                sync_id="11111111-1111-4111-8111-111111111111",
                origin_device_id="local-device",
                sync_revision=2,
                sync_updated_at=datetime(2026, 7, 21, 12, 0, 0),
                updated_at=datetime(2026, 7, 22, 12, 0, 0),
            )
        )
        self.db.commit()

        bundle = SyncService(self.db, device_id="local-device").export_bundle()
        exported = next(
            row
            for row in self._rows(bundle, "user_sessions")
            if row["sync_id"] == "11111111-1111-4111-8111-111111111111"
        )

        self.assertEqual(exported["sync_revision"], 3)
        self.assertEqual(exported["sync_updated_at"], "2026-07-22T12:00:00")

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

    def test_apply_bundle_preserves_user_session_user_id_when_app_users_not_in_bundle(self) -> None:
        user = self._add_user("existing-server-user@example.com")
        service = SyncService(self.db, device_id="server-device")
        service.ensure_schema()
        user_table = AppUser.__table__
        self.db.execute(
            user_table.update()
            .where(user_table.c.id == user.id)
            .values(sync_id=None)
        )
        self.db.commit()
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "user_sessions",
                    "rows": [
                        {
                            "id": "11111111-1111-4111-8111-111111111111",
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "sync_updated_at": "2026-07-20T00:00:00Z",
                            "sync_deleted_at": None,
                            "session_key": "client-session-a",
                            "title": "Client Session A",
                            "title_is_manual": False,
                            "session_data": "{}",
                            "user_id": user.id,
                            "__fk_sync_ids": {
                                "user_id": "client-user-sync-id-not-present-in-server"
                            },
                        }
                    ],
                }
            ],
        }

        result = service.apply_bundle(bundle)
        imported_session = self.db.scalar(
            select(UserSession).where(UserSession.session_key == "client-session-a")
        )

        self.assertEqual(result.inserted, 1)
        self.assertIsNotNone(imported_session)
        self.assertEqual(imported_session.user_id, user.id)

    def test_apply_bundle_skips_child_row_when_raw_fk_parent_is_missing(self) -> None:
        service = SyncService(self.db, device_id="server-device")
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "user_activities",
                    "rows": [
                        {
                            "id": "02d6e364-e884-47d5-9660-c83c433a283e",
                            "sync_id": "02d6e364-e884-47d5-9660-c83c433a283e",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "sync_updated_at": "2026-07-20T00:00:00Z",
                            "sync_deleted_at": None,
                            "user_session_id": "08aaf73e-56eb-48f2-9f5e-00604daa9986",
                            "activity_type": "country_selected",
                            "step": "country",
                            "details": "Germany",
                            "created_at": "2026-07-20T00:00:00Z",
                        }
                    ],
                }
            ],
        }

        result = service.apply_bundle(bundle)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(self.db.query(UserActivity).count(), 0)

    def test_knowledge_sync_marks_scope_indexes_dirty(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
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
        try:
            bundle = SyncService(self.db, device_id="source-device").export_bundle()
        finally:
            sync_routes.settings.sync_mode = original_mode

        target_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target_db = TargetSession()
        try:
            sync_routes.settings.sync_mode = "client"
            service = SyncService(target_db, device_id="target-device")
            result = service.apply_bundle(bundle)

            self.assertIn("main", result.knowledge_scopes_dirty)
            self.assertIn("main", service.knowledge_index_dirty_scopes())
        finally:
            sync_routes.settings.sync_mode = original_mode
            target_db.close()
            Base.metadata.drop_all(bind=target_engine)
            target_engine.dispose()

    def test_client_exports_only_validated_knowledge_scope(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "client"
        try:
            for scope in ("main", "validated_evidence", "sector_prompt", "temporary"):
                document = KnowledgeDocument(
                    user_id=None,
                    title=f"{scope} KB",
                    source_type="txt",
                    source_uri=f"{scope}.txt",
                    scope=scope,
                    session_key="session-a" if scope == "temporary" else None,
                )
                self.db.add(document)
                self.db.flush()
                self.db.add(
                    KnowledgeChunk(
                        document_id=document.id,
                        user_id=None,
                        chunk_index=0,
                        content=f"{scope} content",
                        source_type="txt",
                        source_uri=f"{scope}.txt",
                    )
                )
            self.db.commit()

            bundle = SyncService(self.db, device_id="client-device").export_bundle()
        finally:
            sync_routes.settings.sync_mode = original_mode

        document_scopes = {row["scope"] for row in self._rows(bundle, "knowledge_documents")}
        chunk_sources = {row["source_uri"] for row in self._rows(bundle, "knowledge_chunks")}
        self.assertEqual(document_scopes, {"validated_evidence"})
        self.assertEqual(chunk_sources, {"validated_evidence.txt"})

    def test_admin_client_exports_main_validated_and_sector_knowledge_scope(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "client"
        try:
            for scope in ("main", "validated_evidence", "sector_prompt", "temporary"):
                document = KnowledgeDocument(
                    user_id=None,
                    title=f"{scope} KB",
                    source_type="txt",
                    source_uri=f"{scope}.txt",
                    scope=scope,
                    session_key="session-a" if scope == "temporary" else None,
                )
                self.db.add(document)
                self.db.flush()
                self.db.add(
                    KnowledgeChunk(
                        document_id=document.id,
                        user_id=None,
                        chunk_index=0,
                        content=f"{scope} content",
                        source_type="txt",
                        source_uri=f"{scope}.txt",
                    )
                )
            self.db.commit()

            bundle = SyncService(self.db, device_id="client-device").export_bundle(
                include_admin_knowledge=True,
                admin_user_email="admin@example.com",
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        document_scopes = {row["scope"] for row in self._rows(bundle, "knowledge_documents")}
        chunk_sources = {row["source_uri"] for row in self._rows(bundle, "knowledge_chunks")}
        self.assertTrue(bundle["admin_knowledge_sync"])
        self.assertEqual(bundle["admin_user_email"], "admin@example.com")
        self.assertEqual(document_scopes, {"main", "validated_evidence", "sector_prompt"})
        self.assertEqual(chunk_sources, {"main.txt", "validated_evidence.txt", "sector_prompt.txt"})

    def test_server_rejects_inbound_main_and_sector_knowledge(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "knowledge_documents",
                    "rows": [
                        {
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "main",
                            "title": "Client Main",
                            "source_type": "txt",
                            "source_uri": "client-main.txt",
                            "scope_level": "global",
                        },
                        {
                            "sync_id": "22222222-2222-4222-8222-222222222222",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "sector_prompt",
                            "title": "Client Sector",
                            "source_type": "txt",
                            "source_uri": "client-sector.txt",
                            "scope_level": "global",
                        },
                        {
                            "sync_id": "33333333-3333-4333-8333-333333333333",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "validated_evidence",
                            "title": "Client Validated",
                            "source_type": "txt",
                            "source_uri": "client-validated.txt",
                            "scope_level": "global",
                        },
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={"active": True, "can_sync_validated_kb": True, "can_sync_user_data": True},
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        scopes = {row.scope for row in self.db.query(KnowledgeDocument).all()}
        self.assertEqual(result.inserted, 1)
        self.assertEqual(scopes, {"validated_evidence"})

    def test_server_rejects_validated_knowledge_when_token_disallows_validated_kb(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "knowledge_documents",
                    "rows": [
                        {
                            "sync_id": "33333333-3333-4333-8333-333333333333",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "validated_evidence",
                            "title": "Client Validated",
                            "source_type": "txt",
                            "source_uri": "client-validated.txt",
                            "scope_level": "global",
                        },
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={"active": True, "can_sync_validated_kb": False, "can_sync_user_data": True},
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        self.assertEqual(result.inserted, 0)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 0)

    def test_server_rejects_user_data_when_token_disallows_user_data(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "tables": [
                {
                    "name": "user_sessions",
                    "rows": [
                        {
                            "sync_id": "44444444-4444-4444-8444-444444444444",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "session_key": "client-session",
                            "title": "Client Session",
                            "session_data": "{}",
                        },
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={"active": True, "can_sync_validated_kb": True, "can_sync_user_data": False},
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        self.assertEqual(result.inserted, 0)
        self.assertEqual(self.db.query(UserSession).count(), 0)

    def test_server_accepts_admin_main_sector_and_validated_knowledge(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "admin_knowledge_sync": True,
            "admin_user_email": "admin@example.com",
            "tables": [
                {
                    "name": "knowledge_documents",
                    "rows": [
                        {
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "main",
                            "title": "Admin Main",
                            "source_type": "txt",
                            "source_uri": "admin-main.txt",
                            "scope_level": "global",
                        },
                        {
                            "sync_id": "22222222-2222-4222-8222-222222222222",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "sector_prompt",
                            "title": "Admin Sector",
                            "source_type": "txt",
                            "source_uri": "admin-sector.txt",
                            "scope_level": "global",
                        },
                        {
                            "sync_id": "33333333-3333-4333-8333-333333333333",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "validated_evidence",
                            "title": "Admin Validated",
                            "source_type": "txt",
                            "source_uri": "admin-validated.txt",
                            "scope_level": "global",
                        },
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={
                    "active": True,
                    "can_sync_main_kb": True,
                    "can_sync_sector_prompts": True,
                    "can_sync_validated_kb": True,
                },
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        scopes = {row.scope for row in self.db.query(KnowledgeDocument).all()}
        self.assertEqual(result.inserted, 3)
        self.assertEqual(scopes, {"main", "sector_prompt", "validated_evidence"})

    def test_server_rejects_admin_main_when_only_synced_user_role_claims_admin(self) -> None:
        original_mode = sync_routes.settings.sync_mode
        sync_routes.settings.sync_mode = "server"
        self._add_user("user@example.com", role="admin")
        bundle = {
            "format": "dr-transition-sync-v1",
            "device_id": "client-device",
            "exported_at": "2026-07-20T00:00:00Z",
            "admin_knowledge_sync": True,
            "admin_user_email": "user@example.com",
            "tables": [
                {
                    "name": "knowledge_documents",
                    "rows": [
                        {
                            "sync_id": "11111111-1111-4111-8111-111111111111",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "main",
                            "title": "Main",
                            "source_type": "txt",
                            "source_uri": "main.txt",
                            "scope_level": "global",
                        },
                        {
                            "sync_id": "33333333-3333-4333-8333-333333333333",
                            "origin_device_id": "client-device",
                            "sync_revision": 1,
                            "scope": "validated_evidence",
                            "title": "Validated",
                            "source_type": "txt",
                            "source_uri": "validated.txt",
                            "scope_level": "global",
                        },
                    ],
                }
            ],
        }
        try:
            result = SyncService(self.db, device_id="server-device").apply_bundle(
                bundle,
                sync_client={"active": True, "can_sync_validated_kb": True, "can_sync_user_data": True},
            )
        finally:
            sync_routes.settings.sync_mode = original_mode

        scopes = {row.scope for row in self.db.query(KnowledgeDocument).all()}
        self.assertEqual(result.inserted, 1)
        self.assertEqual(scopes, {"validated_evidence"})

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
            SyncService(self.db).upsert_sync_client(token="saved-secret", client_name="Saved client")
            client = TestClient(app)
            denied = client.get("/api/sync/status", headers={"X-Sync-Token": "wrong"})
            legacy_denied = client.get("/api/sync/status", headers={"X-Sync-Token": "secret"})
            allowed = client.get("/api/sync/status", headers={"X-Sync-Token": "saved-secret"})

            self.assertEqual(denied.status_code, 401)
            self.assertEqual(legacy_denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("user_sessions", allowed.json()["tables"])
            self.assertEqual(allowed.json()["server_to_client_knowledge_scopes"], ["main", "validated_evidence", "sector_prompt"])
            self.assertEqual(allowed.json()["client_to_server_knowledge_scopes"], ["validated_evidence"])
            self.assertEqual(allowed.json()["admin_client_to_server_knowledge_scopes"], ["main", "validated_evidence", "sector_prompt"])
            self.assertEqual(allowed.json()["excluded_knowledge_scopes"], ["temporary"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_api_token = original_token

    def test_client_sync_status_uses_authenticated_session_not_sync_token(self) -> None:
        user = self._add_user("sync-user@example.com")
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        app.dependency_overrides[sync_routes.require_current_user] = lambda: user
        original_enabled = sync_routes.settings.sync_enabled
        original_mode = sync_routes.settings.sync_mode
        original_url = sync_routes.settings.sync_server_url
        original_token = sync_routes.settings.sync_api_token
        try:
            sync_routes.settings.sync_enabled = True
            sync_routes.settings.sync_mode = "client"
            sync_routes.settings.sync_server_url = "https://sync.example"
            sync_routes.settings.sync_api_token = "secret"
            client = TestClient(app)

            response = client.get("/api/sync/client/status")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["configured"])
            self.assertEqual(response.json()["server_url"], "https://sync.example")
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_mode = original_mode
            sync_routes.settings.sync_server_url = original_url
            sync_routes.settings.sync_api_token = original_token

    def test_client_sync_status_reports_disabled_sync(self) -> None:
        user = self._add_user("disabled-sync-user@example.com")
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        app.dependency_overrides[sync_routes.require_current_user] = lambda: user
        original_enabled = sync_routes.settings.sync_enabled
        try:
            sync_routes.settings.sync_enabled = False
            client = TestClient(app)

            response = client.get("/api/sync/client/status")

            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["enabled"])
            self.assertFalse(response.json()["configured"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled

    def test_client_user_data_sync_preference_works_when_sync_disabled(self) -> None:
        user = self._add_user("disabled-local-preference-user@example.com")
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        app.dependency_overrides[sync_routes.require_current_user] = lambda: user
        original_enabled = sync_routes.settings.sync_enabled
        try:
            sync_routes.settings.sync_enabled = False
            client = TestClient(app)

            updated = client.post("/api/sync/client/user-data", json={"enabled": True})
            status = client.get("/api/sync/client/status")

            self.assertEqual(updated.status_code, 200)
            self.assertFalse(updated.json()["error"])
            self.assertTrue(updated.json()["user_data_sync"]["enabled"])
            self.assertFalse(status.json()["enabled"])
            self.assertTrue(status.json()["user_data_sync"]["enabled"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled

    def test_client_user_data_sync_preference_can_be_enabled(self) -> None:
        user = self._add_user("preference-user@example.com")
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        app.dependency_overrides[sync_routes.require_current_user] = lambda: user
        original_enabled = sync_routes.settings.sync_enabled
        original_mode = sync_routes.settings.sync_mode
        original_url = sync_routes.settings.sync_server_url
        original_token = sync_routes.settings.sync_api_token
        try:
            sync_routes.settings.sync_enabled = True
            sync_routes.settings.sync_mode = "client"
            sync_routes.settings.sync_server_url = "https://sync.example"
            sync_routes.settings.sync_api_token = "secret"
            client = TestClient(app)

            updated = client.post("/api/sync/client/user-data", json={"enabled": True})
            status = client.get("/api/sync/client/status")

            self.assertEqual(updated.status_code, 200)
            self.assertFalse(updated.json()["error"])
            self.assertTrue(updated.json()["user_data_sync"]["enabled"])
            self.assertTrue(status.json()["user_data_sync"]["enabled"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_mode = original_mode
            sync_routes.settings.sync_server_url = original_url
            sync_routes.settings.sync_api_token = original_token

    def test_client_user_data_sync_preference_works_before_server_config(self) -> None:
        user = self._add_user("local-preference-user@example.com")
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        app.dependency_overrides[sync_routes.require_current_user] = lambda: user
        original_enabled = sync_routes.settings.sync_enabled
        original_mode = sync_routes.settings.sync_mode
        original_url = sync_routes.settings.sync_server_url
        original_token = sync_routes.settings.sync_api_token
        try:
            sync_routes.settings.sync_enabled = True
            sync_routes.settings.sync_mode = "client"
            sync_routes.settings.sync_server_url = ""
            sync_routes.settings.sync_api_token = ""
            client = TestClient(app)

            updated = client.post("/api/sync/client/user-data", json={"enabled": True})
            status = client.get("/api/sync/client/status")

            self.assertEqual(updated.status_code, 200)
            self.assertFalse(updated.json()["error"])
            self.assertTrue(updated.json()["user_data_sync"]["enabled"])
            self.assertFalse(status.json()["configured"])
            self.assertTrue(status.json()["user_data_sync"]["enabled"])
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_mode = original_mode
            sync_routes.settings.sync_server_url = original_url
            sync_routes.settings.sync_api_token = original_token

    def test_exchange_includes_encrypted_app_users_in_normal_server_response(self) -> None:
        self._add_user("server-admin@example.com", role="admin")
        SyncService(self.db).upsert_sync_client(token="secret", client_name="Normal sync client")
        response = self._exchange_response(
            {
                "format": "dr-transition-sync-v1",
                "device_id": "client-device",
                "exported_at": "2026-07-20T00:00:00Z",
                "tables": [],
            }
        )

        self.assertEqual(response.status_code, 200)
        users = self._rows(response.json()["bundle"], "app_users")
        self.assertEqual(len(users), 1)
        self.assertIn("__encrypted_row", users[0])

    def test_exchange_excludes_user_data_when_sync_client_disallows_user_data(self) -> None:
        self._add_user("server-admin@example.com", role="admin")
        SyncService(self.db).upsert_sync_client(
            token="no-user-data-token",
            client_name="No user data client",
            can_sync_user_data=False,
        )
        response = self._exchange_response(
            {
                "format": "dr-transition-sync-v1",
                "device_id": "client-device",
                "exported_at": "2026-07-20T00:00:00Z",
                "tables": [],
            },
            token="no-user-data-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response.json()["bundle"], "app_users"), [])

    def test_exchange_excludes_user_data_when_client_sync_my_data_is_off(self) -> None:
        user = self._add_user("server-admin@example.com", role="admin")
        self.db.add(
            UserSession(
                session_key="other-user-session",
                title="Other user session",
                user_id=user.id,
                session_data="{}",
            )
        )
        self.db.add(
            Prompt(
                prompt_key="llm/server_owned.txt",
                category="llm",
                display_name="Server owned",
                content="Server/admin prompt",
            )
        )
        self.db.commit()
        SyncService(self.db).upsert_sync_client(token="secret", client_name="Normal sync client")

        response = self._exchange_response(
            {
                "format": "dr-transition-sync-v1",
                "device_id": "client-device",
                "exported_at": "2026-07-20T00:00:00Z",
                "request_user_data_sync": False,
                "tables": [],
            }
        )

        self.assertEqual(response.status_code, 200)
        bundle = response.json()["bundle"]
        self.assertEqual(self._rows(bundle, "app_users"), [])
        self.assertEqual(self._rows(bundle, "user_sessions"), [])
        self.assertEqual(self._rows(bundle, "prompts")[0]["prompt_key"], "llm/server_owned.txt")

    def test_exchange_includes_app_users_for_server_approved_admin_sync(self) -> None:
        self._add_user("server-admin@example.com", role="admin")
        SyncService(self.db).upsert_sync_client(
            token="secret",
            client_name="Admin sync client",
            can_sync_validated_kb=True,
        )
        response = self._exchange_response(
            {
                "format": "dr-transition-sync-v1",
                "device_id": "client-device",
                "exported_at": "2026-07-20T00:00:00Z",
                "admin_knowledge_sync": True,
                "admin_user_email": "server-admin@example.com",
                "tables": [],
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._rows(response.json()["bundle"], "app_users")), 1)

    def test_exchange_accepts_admin_knowledge_only_with_server_sync_client_permission(self) -> None:
        SyncService(self.db).upsert_sync_client(
            token="admin-sync-client-token",
            client_name="Admin sync client",
            user_email="admin@example.com",
            can_sync_main_kb=True,
            can_sync_sector_prompts=True,
        )
        response = self._exchange_response(
            {
                "format": "dr-transition-sync-v1",
                "device_id": "client-device",
                "exported_at": "2026-07-20T00:00:00Z",
                "admin_knowledge_sync": True,
                "admin_user_email": "locally-edited-admin@example.com",
                "tables": [
                    {
                        "name": "knowledge_documents",
                        "rows": [
                            {
                                "sync_id": "11111111-1111-4111-8111-111111111111",
                                "origin_device_id": "client-device",
                                "sync_revision": 1,
                                "scope": "main",
                                "title": "Credential Main",
                                "source_type": "txt",
                                "source_uri": "credential-main.txt",
                                "scope_level": "global",
                            },
                            {
                                "sync_id": "22222222-2222-4222-8222-222222222222",
                                "origin_device_id": "client-device",
                                "sync_revision": 1,
                                "scope": "sector_prompt",
                                "title": "Credential Sector",
                                "source_type": "txt",
                                "source_uri": "credential-sector.txt",
                                "scope_level": "global",
                            },
                        ],
                    }
                ],
            },
            token="admin-sync-client-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["applied"]["inserted"], 2)
        self.assertEqual(
            {row.scope for row in self.db.query(KnowledgeDocument).all()},
            {"main", "sector_prompt"},
        )

    def _add_user(self, email: str, *, role: str = "admin") -> AppUser:
        user = AppUser(
            email=email,
            name="Admin",
            password_hash=hash_password("Password!1"),
            designation="Lead",
            organisation_type="Local",
            organisation_name="Dr Transition",
            role=role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _override_db(self) -> Iterator[Session]:
        yield self.db

    def _exchange_response(self, payload: dict[str, object], *, token: str = "secret"):
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[sync_routes.get_db] = self._override_db
        original_enabled = sync_routes.settings.sync_enabled
        original_mode = sync_routes.settings.sync_mode
        original_token = sync_routes.settings.sync_api_token
        try:
            sync_routes.settings.sync_enabled = True
            sync_routes.settings.sync_mode = "server"
            sync_routes.settings.sync_api_token = "secret"
            client = TestClient(app)
            return client.post(
                "/api/sync/exchange",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            sync_routes.settings.sync_enabled = original_enabled
            sync_routes.settings.sync_mode = original_mode
            sync_routes.settings.sync_api_token = original_token

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
