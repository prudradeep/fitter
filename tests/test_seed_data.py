import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.db.session import Base
from app.models import AppUser
from app import seed_data


class SeedDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_session_local = seed_data.SessionLocal
        seed_data.SessionLocal = self.SessionLocal

    def tearDown(self) -> None:
        seed_data.SessionLocal = self.original_session_local
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_default_app_user_role_for_sync_mode(self) -> None:
        self.assertEqual(seed_data.default_app_user_role_for_sync_mode("client"), "user")
        self.assertEqual(seed_data.default_app_user_role_for_sync_mode("server"), "admin")
        self.assertEqual(seed_data.default_app_user_role_for_sync_mode(""), "user")
        self.assertFalse(seed_data.should_seed_default_app_user("auto", "client"))
        self.assertTrue(seed_data.should_seed_default_app_user("auto", "server"))
        self.assertTrue(seed_data.should_seed_default_app_user("user", "client"))

    def test_explicit_user_seed_creates_normal_user(self) -> None:
        created = self._ensure_default_user(role="user")

        with self.SessionLocal() as db:
            user = db.scalar(select(AppUser).where(AppUser.email == "admin@drtransition.local"))

        self.assertTrue(created)
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "user")

    def test_server_seed_updates_default_user_to_admin(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                AppUser(
                    email="admin@drtransition.local",
                    name="Default User",
                    password_hash=hash_password("OldPassword!1"),
                    designation="User",
                    organisation_type="Local",
                    organisation_name="Dr Transition",
                    role="user",
                )
            )
            db.commit()

        created = self._ensure_default_user(role="admin")

        with self.SessionLocal() as db:
            user = db.scalar(select(AppUser).where(AppUser.email == "admin@drtransition.local"))

        self.assertFalse(created)
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "admin")

    def _ensure_default_user(self, *, role: str) -> bool:
        return seed_data.ensure_default_app_user(
            email="admin@drtransition.local",
            password="StrongPassword!1",
            name="Default User",
            designation="User",
            organisation_type="Local",
            organisation_name="Dr Transition",
            role=role,
        )


if __name__ == "__main__":
    unittest.main()
