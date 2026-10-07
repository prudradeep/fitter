import asyncio
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.db.sqlite_migrations import _019_policy_hazard_links
from app.models import Country, KnowledgeChunk, KnowledgeDocument, Policy, PolicyHazardLink, Sector, SystemHazard
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession, ChatSessionStore


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
            context_policy_summary_confirmed=True,
            pending_context_policy_document_ids=[new_doc.id],
            hazards=[hazard.name], hazard_profiles={hazard.name: [{"name": "Low-income households"}]},
        )
        self.service._policy_reference_context = AsyncMock(return_value="New smart tariff policy uses dynamic prices to improve efficiency.")
        self.service._summarize_context_policy = AsyncMock(return_value="Mechanisms: dynamic prices. Intended benefits: efficiency.")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_found_hazards_are_linked_after_summary_confirmation(self):
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

    def test_duplicate_policy_names_existing_title_country_and_sector(self):
        country = Country(name="Germany")
        sector = Sector(name="Energy")
        self.db.add_all([country, sector])
        self.db.flush()
        old_policy = self.db.scalar(select(Policy).where(Policy.policy == "Existing energy policy"))
        old_policy.country_id = country.id
        old_policy.sector_id = sector.id
        self.session.country_id = country.id
        self.session.sector_id = sector.id
        self.session.country = ""
        self.session.sector = ""
        self.session.selected_context_policy = old_policy.policy
        self.db.commit()

        response = asyncio.run(
            self.service._new_policy_hazard_suggestions_step("session-1", self.session)
        )
        self.assertIn("Policy title:", response.bot_message)
        self.assertIn("Existing energy policy", response.bot_message)
        self.assertIn("Country:", response.bot_message)
        self.assertIn("Germany", response.bot_message)
        self.assertIn("Sector:", response.bot_message)
        self.assertIn("Energy", response.bot_message)
        self.assertEqual([option.label for option in response.options], ["Show policy list"])

        saved_response = asyncio.run(self.service._save_new_context_policy("session-1", self.session, []))
        self.assertIn("Existing energy policy", saved_response.bot_message)
        self.assertIn("Germany", saved_response.bot_message)
        self.assertIn("Energy", saved_response.bot_message)
        self.assertEqual([option.label for option in saved_response.options], ["Show policy list"])

        policy_list = asyncio.run(self.service._handle_other_nav_action(
            "session-1", self.session, response.options[0].label
        ))
        self.assertEqual(policy_list.step, "policy")
        self.assertEqual(self.session.phase, "policy")
        self.assertIn("Existing energy policy", [option.label for option in policy_list.options])
        self.assertFalse(self.session.adding_context_policy)
        self.assertIsNone(self.session.pending_context_policy_document_ids)

    def test_add_new_policy_prompt_can_return_to_policy_list(self):
        self.session.phase = "policy"
        prompt = asyncio.run(self.service._select_context_policy(
            "session-1", self.session, "Add a new policy"
        ))
        self.assertEqual(prompt.step, "policy_reference")
        self.assertEqual([option.label for option in prompt.options], ["Show policy list"])

        policy_list = asyncio.run(self.service._handle_other_nav_action(
            "session-1", self.session, prompt.options[0].label
        ))
        self.assertEqual(policy_list.step, "policy")
        self.assertFalse(self.session.adding_context_policy)

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

    def test_accepted_document_waits_for_summary_confirmation_before_saving(self):
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

        self.assertEqual(response.step, "policy_summary_review")
        self.assertFalse(self.session.context_policy_summary_confirmed)
        self.assertEqual(
            [option.label for option in response.options],
            ["Confirm summary", "Add more details"],
        )
        self.assertIn("Mechanisms: dynamic prices", response.bot_message)
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).scope, "temporary")
        self.service._policy_reference_context.assert_any_await(
            self.session, [self.new_doc_id], full_text=True
        )

        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock, return_value='{"hazards":[]}',
        ):
            saved = asyncio.run(self.service._handle_new_policy_summary_review(
                "session-1", self.session, "Confirm summary",
            ))
        self.assertEqual(saved.step, "policy_summary")
        self.assertFalse(self.session.context_policy_summary_confirmed)
        self.assertIn("No hazards found.", saved.bot_message)
        self.assertIn("Mechanisms: dynamic prices", saved.bot_message)
        self.assertEqual(self.service._summarize_context_policy.await_count, 1)
        self.assertIsNotNone(self.db.scalar(select(Policy).where(Policy.source == "user")))
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).scope, "policy_reference")

    def test_added_detail_is_checked_then_summary_is_regenerated_before_save(self):
        first = asyncio.run(self.service._new_policy_summary_review_step("session-1", self.session))
        self.assertEqual(first.step, "policy_summary_review")
        details = asyncio.run(self.service._handle_new_policy_summary_review(
            "session-1", self.session, "Add more details",
        ))
        self.assertEqual(details.step, "policy_summary_details")

        self.service._check_new_policy_detail = AsyncMock(return_value={
            "status": "relevant", "reason": "Related to dynamic prices.", "question": "",
        })
        self.service._summarize_context_policy.return_value = (
            "### Policy details\n\n- **Tariffs:** Peak prices may raise bills.\n\n"
            "### Mechanisms\n\n- **Dynamic rates:** Rates change by time.\n\n"
            "### Intended benefits\n\n- **Efficiency:** Demand shifts.\n\n"
            "### Socio-demographic groups benefited\n\n- **Residents:** They can shift use."
        )
        revised = asyncio.run(self.service._handle_new_policy_summary_detail(
            "session-1", self.session, "Peak-hour bills may increase.",
        ))

        self.assertEqual(revised.step, "policy_summary_review")
        self.assertIn("Peak prices may raise bills", revised.bot_message)
        self.assertIn("Peak-hour bills may increase.", self.session.context_policy_clarifications)
        self.assertEqual(self.service._summarize_context_policy.await_count, 2)
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))

    def test_unclear_detail_requires_clarification_before_resummarizing(self):
        asyncio.run(self.service._new_policy_summary_review_step("session-1", self.session))
        self.session.phase = "policy_summary_details"
        self.service._check_new_policy_detail = AsyncMock(side_effect=[
            {"status": "unclear", "reason": "No link found.",
             "question": "Which provision concerns tariffs?"},
            {"status": "relevant", "reason": "The link is clear.", "question": ""},
        ])

        clarification = asyncio.run(self.service._handle_new_policy_summary_detail(
            "session-1", self.session, "The policy supports home batteries.",
        ))
        self.assertEqual(clarification.step, "policy_summary_clarification")
        self.assertIn("Which provision concerns tariffs?", clarification.bot_message)
        self.assertEqual(self.service._summarize_context_policy.await_count, 1)
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))

        revised = asyncio.run(self.service._handle_new_policy_summary_detail(
            "session-1", self.session, "The tariff provision also covers battery use.",
        ))
        self.assertEqual(revised.step, "policy_summary_review")
        self.assertIn("Clarification: The tariff provision", self.session.context_policy_clarifications[-1])
        self.assertEqual(self.service._summarize_context_policy.await_count, 2)
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))

    def test_detail_check_searches_late_document_text(self):
        from app.services.chat_hazard_steps import _policy_detail_source

        source = "Early background. " + "x" * 50000 + " Batteries can shift demand from peak hours."
        context = _policy_detail_source(source, "Batteries shift demand from peak hours")

        self.assertIn("Batteries can shift demand from peak hours.", context)

    def test_user_can_return_from_detail_clarification_without_saving(self):
        asyncio.run(self.service._new_policy_summary_review_step("session-1", self.session))
        self.session.phase = "policy_summary_clarification"
        self.session.pending_context_policy_detail = "Unclear detail"

        response = asyncio.run(self.service._handle_new_policy_summary_detail(
            "session-1", self.session, "Back to summary",
        ))

        self.assertEqual(response.step, "policy_summary_review")
        self.assertIsNone(self.session.pending_context_policy_detail)
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))

    def test_internal_save_does_not_persist_unconfirmed_policy(self):
        self.session.context_policy_summary_confirmed = False

        response = asyncio.run(self.service._save_new_context_policy(
            "session-1", self.session, [],
        ))

        self.assertEqual(response.step, "policy_summary_review")
        self.assertIsNone(self.db.scalar(select(Policy).where(Policy.source == "user")))
        self.assertEqual(self.db.get(KnowledgeDocument, self.new_doc_id).scope, "temporary")

    def test_summary_review_survives_session_reload(self):
        asyncio.run(self.service._new_policy_summary_review_step("session-1", self.session))

        restored = ChatSessionStore().put("session-1", asdict(self.session))

        self.assertEqual(restored.phase, "policy_summary_review")
        self.assertEqual(
            restored.selected_context_policy_summary,
            "Mechanisms: dynamic prices. Intended benefits: efficiency.",
        )
        self.assertEqual(restored.pending_context_policy_document_ids, [self.new_doc_id])

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
