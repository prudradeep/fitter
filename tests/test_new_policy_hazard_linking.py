import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.db.sqlite_migrations import _019_policy_hazard_links
from app.models import KnowledgeChunk, KnowledgeDocument, Policy, PolicyHazardLink, SystemHazard
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


class NewPolicyHazardLinkingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.service = ChatService.__new__(ChatService)
        self.service.db = self.db
        self.service.user_id = "owner-1"
        old = Policy(country_id="country-1", sector_id="sector-1", policy="Existing energy policy")
        hazard = SystemHazard(sector_id="sector-1", name="Higher household energy costs")
        self.db.add_all([old, hazard])
        self.db.flush()
        old_doc = KnowledgeDocument(title="Old policy", source_type="txt", scope="policy_document", policy_id=old.id)
        new_doc = KnowledgeDocument(title="New policy", source_type="txt", scope="temporary", user_id="owner-1", session_key="session-1")
        self.db.add_all([old_doc, new_doc])
        self.db.flush()
        self.db.add(KnowledgeChunk(document_id=old_doc.id, chunk_index=0, content="Smart meters expose customers to variable tariffs; intended benefit is efficient use.", source_type="txt"))
        self.db.add(KnowledgeChunk(document_id=new_doc.id, chunk_index=0, content="Dynamic prices improve efficiency but may increase peak-hour bills.", source_type="txt", user_id="owner-1"))
        self.db.commit()
        self.hazard_id = hazard.id
        self.new_doc_id = new_doc.id
        self.session = ChatSession(
            session_key="session-1", country_id="country-1", country="Germany",
            sector_id="sector-1", sector="Energy", phase="policy_reference",
            adding_context_policy=True, selected_context_policy="New smart tariff policy",
            pending_context_policy_document_ids=[new_doc.id],
            hazards=[hazard.name], hazard_profiles={hazard.name: [{"name": "Low-income households"}]},
        )
        self.service._policy_reference_context = AsyncMock(return_value="New smart tariff policy uses dynamic prices to improve efficiency.")
        self.service._summarize_context_policy = AsyncMock(return_value="Mechanisms: dynamic prices. Intended benefits: efficiency.")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_found_hazards_are_linked_without_confirmation(self):
        answer = '{"hazards":[{"id":"system:%s","reason":"Dynamic prices -> peak rates -> higher bills"}]}' % self.hazard_id
        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, return_value=answer):
            result = asyncio.run(self.service._new_policy_hazard_suggestions_step("session-1", self.session))
        self.assertEqual(result.step, "policy_summary")
        self.assertIn("Higher household energy costs", result.bot_message)
        self.assertNotIn("No hazards found.", result.bot_message)
        self.assertEqual(result.options[0].label, "Continue to hazards")
        policy = self.db.scalar(select(Policy).where(Policy.source == "user"))
        self.assertIsNotNone(policy)
        link = self.db.scalar(select(PolicyHazardLink).where(PolicyHazardLink.policy_id == policy.id))
        self.assertEqual(link.system_hazard_id, self.hazard_id)
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).policy_id, policy.id)
        self.assertFalse(self.session.adding_context_policy)
        self.service._limit_hazards_to_selected_policy(self.session)
        self.assertEqual(self.session.hazards, ["Higher household energy costs"])

    def test_existing_policy_title_is_used_when_its_document_is_unavailable(self):
        old_chunk = self.db.scalar(select(KnowledgeChunk).where(
            KnowledgeChunk.document_id != self.new_doc_id
        ))
        self.db.delete(old_chunk)
        self.db.commit()
        answer = '{"hazards":[{"id":"system:%s","reason":"Dynamic prices -> peak rates -> higher bills"}]}' % self.hazard_id

        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, return_value=answer) as ask_llm:
            response = asyncio.run(self.service._new_policy_hazard_suggestions_step("session-1", self.session))

        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Existing policy title: Existing energy policy", prompt)
        self.assertIn("Mechanisms and intended benefits source: Not available", prompt)
        self.assertEqual(response.step, "policy_summary")
        self.assertIn("Higher household energy costs", response.bot_message)

    def test_new_policy_title_is_considered_without_existing_policy_comparisons(self):
        old_policy = self.db.scalar(select(Policy).where(Policy.policy == "Existing energy policy"))
        old_policy.source = "user"
        self.db.commit()
        answer = '{"hazards":[{"id":"system:%s","reason":"Dynamic prices -> peak rates -> higher bills"}]}' % self.hazard_id

        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, return_value=answer) as ask_llm:
            response = asyncio.run(self.service._new_policy_hazard_suggestions_step("session-1", self.session))

        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("New policy: New smart tariff policy", prompt)
        self.assertIn("None available", prompt)
        self.assertEqual(response.step, "policy_summary")
        self.assertIn("Higher household energy costs", response.bot_message)

    def test_no_supported_hazards_still_saves_policy_and_document(self):
        answer = '{"hazards":[{"id":"system:unknown","reason":"Unrelated claim"}]}'
        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, return_value=answer):
            response = asyncio.run(self.service._new_policy_hazard_suggestions_step("session-1", self.session))
        self.assertEqual(response.step, "policy_summary")
        self.assertIn("No hazards found.", response.bot_message)
        self.assertEqual(response.options[0].label, "Continue to hazards")
        self.assertEqual(len(self.db.scalars(select(Policy)).all()), 2)
        policy = self.db.scalar(select(Policy).where(Policy.source == "user"))
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).policy_id, policy.id)
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).scope, "policy_reference")
        self.assertEqual(self.db.scalars(select(PolicyHazardLink)).all(), [])

        self.session.custom_hazard_evidence = {}
        self.session.custom_hazard_summaries = {}
        with (
            patch.object(self.service, "_hydrate_custom_hazard_profiles"),
            patch.object(self.service, "_filter_session_hazards_without_profiles"),
            patch.object(self.service, "_enrich_policy_hazards_with_population_context", new_callable=AsyncMock),
        ):
            hazards = asyncio.run(self.service._hazards_step("session-1", self.session))
        self.assertEqual(hazards.step, "hazards")
        self.assertEqual(self.session.hazards, [])
        self.assertIn("No hazards are currently linked", hazards.bot_message)

    def test_accepted_document_reaches_saved_summary_without_confirmation(self):
        review = {
            "checks": {
                "sector_objective_fit": {"status": "supported", "reason": "Relevant."},
                "twin_transition_fit": {"status": "unclear", "reason": "Not established."},
            },
            "missing": [],
            "clarification_question": "",
        }
        with (
            patch("app.services.chat_hazard_steps.validate_context_policy_document", new_callable=AsyncMock, return_value=review),
            patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, return_value='{"hazards":[]}'),
        ):
            response = asyncio.run(self.service._validate_context_policy_document("session-1", self.session))

        self.assertEqual(response.step, "policy_summary")
        self.assertIn("No hazards found.", response.bot_message)
        self.assertIsNotNone(self.db.scalar(select(Policy).where(Policy.source == "user")))
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).scope, "policy_reference")
        self.service._policy_reference_context.assert_any_await(
            self.session, [self.new_doc_id], full_text=True
        )

    def test_hazard_analysis_failure_does_not_block_accepted_policy(self):
        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock, side_effect=RuntimeError("unavailable")):
            response = asyncio.run(self.service._new_policy_hazard_suggestions_step("session-1", self.session))

        self.assertEqual(response.step, "policy_summary")
        self.assertIn("No hazards found.", response.bot_message)
        self.assertIsNotNone(self.db.scalar(select(Policy).where(Policy.source == "user")))

    def test_sqlite_migration_creates_link_constraints(self):
        migration_engine = create_engine("sqlite://")
        try:
            with migration_engine.begin() as connection:
                _019_policy_hazard_links(connection)
                _019_policy_hazard_links(connection)
            columns = {row["name"] for row in inspect(migration_engine).get_columns("policy_hazard_links")}
            self.assertTrue({"policy_id", "system_hazard_id", "additional_hazard_id", "rationale"} <= columns)
        finally:
            migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
