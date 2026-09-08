import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import (
    CustomHazardPolicyReference,
    KnowledgeChunk,
    KnowledgeDocument,
)
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
        self.service._associate_policy_references_with_custom_hazard(
            session,
            "custom-hazard-1",
        )

        associations = self.db.query(CustomHazardPolicyReference).all()
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0].custom_hazard_id, "custom-hazard-1")
        self.assertEqual(associations[0].knowledge_document_id, owned.id)
        self.assertIsNone(owned.custom_hazard_id)
        self.assertIsNone(other_user.custom_hazard_id)

    def test_association_survives_clearing_transient_custom_hazard_state(self) -> None:
        document = KnowledgeDocument(
            user_id="owner-1",
            title="Durable policy reference",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
            session_key="session-1",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                user_id="owner-1",
                chunk_index=0,
                content="A policy provision affecting household energy costs.",
                source_type="txt",
            )
        )
        self.db.commit()
        session = ChatSession(
            session_key="session-1",
            custom_hazard={"policy_reference_document_ids": [document.id]},
        )

        self.service._associate_policy_references_with_custom_hazard(
            session,
            "custom-hazard-1",
        )
        self.service._clear_selected_hazard_context(session)
        session.selected_hazard = "Higher household energy costs"
        session.accepted_custom_hazard = "Higher household energy costs"
        session.accepted_custom_hazard_id = "custom-hazard-1"

        results = self.service._mitigation_policy_reference_results(session)

        self.assertIsNone(session.custom_hazard)
        self.assertEqual([row["title"] for row in results], ["Durable policy reference"])

    def test_hazard_finalization_associates_policy_after_hazard_id_is_available(self) -> None:
        service = ChatService.__new__(ChatService)
        service._stored_hazard_profiles = MagicMock(return_value=[])
        service._set_custom_hazard_profiles_from_target_population = MagicMock()
        service._attach_target_population_matches_to_profiles = MagicMock(return_value=[])
        service._ensure_custom_hazard = MagicMock(
            return_value=SimpleNamespace(id="custom-hazard-1")
        )
        service._promote_temporary_policy_references = MagicMock()
        service._record_activity = MagicMock()
        session = ChatSession(
            session_key="session-1",
            phase="custom_hazard_summary_review",
            accepted_custom_hazard="Higher household energy costs",
            custom_hazard={"policy_reference_document_ids": ["document-1"]},
        )

        service._prepare_custom_hazard_added_profiles("session-1", session)

        self.assertEqual(session.accepted_custom_hazard_id, "custom-hazard-1")
        service._promote_temporary_policy_references.assert_called_once_with(
            session,
            "custom-hazard-1",
        )

    def test_policy_promotion_moves_only_selected_temporary_documents(self) -> None:
        policy_document = KnowledgeDocument(
            user_id="owner-1",
            title="Staged policy",
            source_type="txt",
            scope="temporary",
            session_key="session-1",
        )
        evidence_document = KnowledgeDocument(
            user_id="owner-1",
            title="Staged evidence",
            source_type="txt",
            scope="temporary",
            session_key="session-1",
        )
        self.db.add_all([policy_document, evidence_document])
        self.db.flush()
        self.db.add_all(
            [
                KnowledgeChunk(
                    document_id=policy_document.id,
                    user_id="owner-1",
                    chunk_index=0,
                    content="A staged policy provision.",
                    source_type="txt",
                ),
                KnowledgeChunk(
                    document_id=evidence_document.id,
                    user_id="owner-1",
                    chunk_index=0,
                    content="A staged evidence finding.",
                    source_type="txt",
                ),
            ]
        )
        self.db.commit()
        session = ChatSession(
            session_key="session-1",
            custom_hazard={
                "policy_reference_document_ids": [policy_document.id],
            },
        )

        staged_context = asyncio.run(
            self.service._policy_reference_context(
                session,
                [policy_document.id],
            )
        )

        self.service._promote_temporary_policy_references(
            session,
            "custom-hazard-1",
        )

        self.db.refresh(policy_document)
        self.db.refresh(evidence_document)
        association = self.db.get(
            CustomHazardPolicyReference,
            ("custom-hazard-1", policy_document.id),
        )
        self.assertEqual(policy_document.scope, POLICY_REFERENCE_SCOPE)
        self.assertIsNone(policy_document.session_key)
        self.assertEqual(evidence_document.scope, "temporary")
        self.assertIsNotNone(association)
        self.assertIn("A staged policy provision.", staged_context)
        self.assertNotIn("A staged evidence finding.", staged_context)

    def test_discard_removes_only_staged_policy_documents(self) -> None:
        policy_document = KnowledgeDocument(
            user_id="owner-1",
            title="Staged policy",
            source_type="txt",
            scope="temporary",
            session_key="session-1",
        )
        evidence_document = KnowledgeDocument(
            user_id="owner-1",
            title="Staged evidence",
            source_type="txt",
            scope="temporary",
            session_key="session-1",
        )
        self.db.add_all([policy_document, evidence_document])
        self.db.commit()
        session = ChatSession(
            session_key="session-1",
            custom_hazard={
                "policy_reference_document_ids": [policy_document.id],
            },
        )

        self.service._discard_temporary_policy_references(session)

        self.assertIsNone(self.db.get(KnowledgeDocument, policy_document.id))
        self.assertIsNotNone(self.db.get(KnowledgeDocument, evidence_document.id))

    def test_mitigation_context_loads_only_owned_references_for_selected_hazard(self) -> None:
        matching = KnowledgeDocument(
            user_id="owner-1",
            title="Matching policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
        )
        other_hazard = KnowledgeDocument(
            user_id="owner-1",
            title="Other hazard policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
        )
        other_user = KnowledgeDocument(
            user_id="owner-2",
            title="Other user policy",
            source_type="txt",
            scope=POLICY_REFERENCE_SCOPE,
        )
        self.db.add_all([matching, other_hazard, other_user])
        self.db.flush()
        self.db.add_all(
            [
                CustomHazardPolicyReference(
                    custom_hazard_id="custom-hazard-1",
                    knowledge_document_id=matching.id,
                ),
                CustomHazardPolicyReference(
                    custom_hazard_id="custom-hazard-2",
                    knowledge_document_id=other_hazard.id,
                ),
                CustomHazardPolicyReference(
                    custom_hazard_id="custom-hazard-1",
                    knowledge_document_id=other_user.id,
                ),
            ]
        )
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
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            CustomHazardPolicyReference(
                custom_hazard_id="custom-hazard-1",
                knowledge_document_id=document.id,
            )
        )
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
