import asyncio
import unittest
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.knowledge_base import POLICY_REFERENCE_SCOPE


class PolicyReferenceMitigationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.service = ChatService.__new__(ChatService)
        self.service.db = self.db
        self.service.user_id = "owner-1"

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_associates_only_owned_session_policy_references(self) -> None:
        owned = KnowledgeDocument(
            user_id="owner-1",
            title="Owned policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            session_key="session-1",
        )
        other_user = KnowledgeDocument(
            user_id="owner-2",
            title="Other policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            session_key="session-1",
        )
        self.db.add_all([owned, other_user])
        self.db.commit()

        session = ChatSession(
            session_key="session-1",
            custom_hazard={
                "policy_reference_document_ids": [owned.id, other_user.id],
            },
        )
        self.service._associate_policy_references_with_custom_hazard(
            session,
            "custom-hazard-1",
        )

        self.assertEqual(owned.custom_hazard_id, "custom-hazard-1")
        self.assertIsNone(other_user.custom_hazard_id)

    def test_mitigation_context_loads_only_owned_references_for_selected_hazard(self) -> None:
        matching = KnowledgeDocument(
            user_id="owner-1",
            title="Matching policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            custom_hazard_id="custom-hazard-1",
        )
        other_hazard = KnowledgeDocument(
            user_id="owner-1",
            title="Other hazard policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            custom_hazard_id="custom-hazard-2",
        )
        other_user = KnowledgeDocument(
            user_id="owner-2",
            title="Other user policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            custom_hazard_id="custom-hazard-1",
        )
        self.db.add_all([matching, other_hazard, other_user])
        self.db.flush()
        self.db.add_all(
            [
                KnowledgeChunk(
                    document_id=document.id,
                    user_id=document.user_id,
                    chunk_index=0,
                    content=document.title,
                    source_type="txt",
                )
                for document in (matching, other_hazard, other_user)
            ]
        )
        self.db.commit()

        results = self.service._mitigation_policy_reference_results(
            ChatSession(accepted_custom_hazard_id="custom-hazard-1")
        )

        self.assertEqual([row["title"] for row in results], ["Matching policy"])

    def test_mitigation_knowledge_context_includes_associated_policy_reference(self) -> None:
        document = KnowledgeDocument(
            user_id="owner-1",
            title="Associated policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            custom_hazard_id="custom-hazard-1",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id="owner-1",
                chunk_index=0,
                content="The policy funds household heat-pump grants.",
                source_type="txt",
            )
        )
        self.db.commit()
        self.service._shared_knowledge_results = AsyncMock(return_value=[])
        self.service._mitigation_target_population_text = lambda _session: ""
        self.service.grounding_models = type(
            "GroundingStub",
            (),
            {"ground_results": AsyncMock(side_effect=lambda _query, rows: rows)},
        )()

        context = asyncio.run(
            self.service._mitigation_knowledge_context(
                ChatSession(
                    accepted_custom_hazard_id="custom-hazard-1",
                    accepted_custom_hazard="Higher heating costs",
                    selected_hazard="Higher heating costs",
                ),
                "Offer targeted heat-pump grants",
                "Reduce household energy costs",
            )
        )

        self.assertIn("Associated policy", context)
        self.assertIn("heat-pump grants", context)


if __name__ == "__main__":
    unittest.main()
