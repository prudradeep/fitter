import asyncio
import json
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument, Policy
from app.services.chat_hazard_steps import ChatHazardStepsMixin, _expand_inline_summary_bullets
from app.services.chat_mitigation_creation_guided import ChatMitigationCreationGuidedMixin
from app.services.chat_session import ChatSession
from app.services.message_renderer import markdown_to_html
from app.services.policy_summary_references import d42_policy_reference


POLICY = "Economic incentives and concessions for local public transport"


class SummaryService(ChatHazardStepsMixin):
    def __init__(self, db):
        self.db = db
        self.user_id = "test-user"


class AdaptationService(ChatMitigationCreationGuidedMixin):
    def __init__(self, db):
        self.db = db

    def _stored_context_policy_document_ids(self, session):
        return []


def _db_with_d42(objective="Improve access to public transport."):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    document = KnowledgeDocument(title="FITTER D4.2 FINAL.pdf", source_type="pdf",
                                 source_uri="kb/FITTER D4.2 FINAL.pdf", scope="main")
    db.add(document)
    db.flush()
    db.add_all([
        KnowledgeChunk(document_id=document.id, chunk_index=0, page_number=72,
                       content="Annex III: The co-created policy portfolios 6. German policy portfolio",
                       source_type="pdf", source_uri=document.source_uri),
        KnowledgeChunk(document_id=document.id, chunk_index=1, page_number=115,
                       content=("9. Italian policy portfolio Policy code IT_TR_AD_1 Country Italy "
                                "Sector Transport Policy Title: Economic incentives and concessions "
                                "for local public transport Policy Type Economic incentives "
                                f"Original Policy Objective {objective} "
                                "Existing Gaps Limited concessions. "
                                "Proposed Adjustment Extend the discounts to adults. "
                                "Description of the Proposal Offer reduced annual fares to eligible riders."),
                       source_type="pdf", source_uri=document.source_uri),
    ])
    db.commit()
    return db, document.id


def test_d42_requires_country_and_sector_before_flexible_title_match():
    db, _ = _db_with_d42()
    try:
        for title in (
            POLICY,
            "Concessions for local public transport",
            "Economic incentives and concessions for urban public transport",
        ):
            reference = d42_policy_reference(db, country="Italy", sector="Transport", selected_policy=title)
            assert reference is not None
            assert reference.page_number == 115
            assert reference.fields["Original Policy Objective"] == "Improve access to public transport."
        assert d42_policy_reference(db, country="Germany", sector="Transport", selected_policy=POLICY) is None
        assert d42_policy_reference(db, country="Italy", sector="Energy", selected_policy=POLICY) is None
    finally:
        db.close()


def test_d42_rejects_ambiguous_titles_in_same_country_and_sector():
    db, document_id = _db_with_d42()
    try:
        db.add(KnowledgeChunk(
            document_id=document_id, chunk_index=2, page_number=116,
            content=("Policy code IT_TR_AD_2 Country Italy Sector Transport "
                     f"Policy Title: {POLICY} Policy Type Grants "),
            source_type="pdf", source_uri="kb/FITTER D4.2 FINAL.pdf",
        ))
        db.commit()
        assert d42_policy_reference(db, country="Italy", sector="Transport", selected_policy=POLICY) is None
    finally:
        db.close()


def test_summary_uses_d42_only_for_every_section_and_source_modal():
    db, _ = _db_with_d42()
    try:
        service = SummaryService(db)
        session = ChatSession(country="Italy", sector="Transport", selected_context_policy=POLICY)
        session.context_policy_clarifications = ["Clarification-only policy detail."]
        response = json.dumps({
            "policy_details": [{"label": "Not identified", "text": "Not stated in the supplied text."}],
            "original_objectives": [{"label": "Wrong", "text": "A document-only objective."}],
            "mechanisms": [{"label": "Not identified", "text": "Not stated in the supplied text."}],
            "intended_benefits": [{"label": "Access", "text": "Improve access to public transport."}],
            "benefited_groups": [{"label": "Not identified", "text": "Not stated in the supplied text."}],
        })
        with patch("app.services.hazard_with_mitigation._country_factsheet_text",
                   side_effect=AssertionError("Factsheet lookup is forbidden")), \
             patch("app.services.chat_hazard_steps.ask_llm_chat",
                   new=AsyncMock(return_value=response)) as ask_llm:
            summary = asyncio.run(service._summarize_context_policy(
                session, "Document-only policy detail.", POLICY,
                clarifications=session.context_policy_clarifications,
            ))
            session.selected_context_policy_summary = summary
            service._set_policy_summary_sources(session)
        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        assert "Matched D4.2 policy portfolio record:" in prompt
        assert "Proposed Adjustment: Extend the discounts to adults." in prompt
        assert "Document-only policy detail" not in prompt
        assert "Clarification-only policy detail" not in prompt
        assert "### Policy Objectives\n\n- Improve access to public transport." in summary
        assert "### Policy details\n\n- **Policy type:** Economic incentives" in summary
        assert "A document-only objective" not in summary
        for rows in session.selected_context_policy_sources.values():
            for point in rows:
                assert {"field": "Document", "value": "FITTER D4.2 FINAL.pdf"} in point or point[0]["value"] == "Source passage unavailable"
                assert not any("factsheet" in row["value"].casefold() for row in point)
                assert not any("clarification" in row["value"].casefold() for row in point)
        assert {"field": "Page", "value": "115"} in session.selected_context_policy_sources["policy objectives"][0]
    finally:
        db.close()


def test_unmatched_d42_uses_linked_policy_document():
    db, _ = _db_with_d42()
    try:
        service = SummaryService(db)
        session = ChatSession(country="Italy", sector="Transport",
                              selected_context_policy="Unlisted transport policy")
        document_text = "The unlisted policy offers transport passes to residents."
        with patch("app.services.chat_hazard_steps.ask_llm_chat",
                   new=AsyncMock(return_value="")) as ask_llm:
            asyncio.run(service._summarize_context_policy(session, document_text, ""))
        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        assert document_text in prompt
        assert "Matched D4.2" not in prompt
    finally:
        db.close()


def test_d42_objective_bullets_are_separate_and_each_keeps_its_source():
    objective = (
        "• Make public transport more accessible through discounts. "
        "• Support urban mobility through demand-side incentives. "
        "• Currently, only the under-18 bonus is in effect."
    )
    db, _ = _db_with_d42(objective)
    try:
        service = SummaryService(db)
        session = ChatSession(country="Italy", sector="Transport", selected_context_policy=POLICY)
        with patch("app.services.chat_hazard_steps.ask_llm_chat",
                   new=AsyncMock(return_value="")):
            summary = asyncio.run(service._summarize_context_policy(session, "", POLICY))
            session.selected_context_policy_summary = summary
            service._set_policy_summary_sources(session)
        objectives = summary.split("### Policy Objectives\n\n", 1)[1].split("\n\n### ", 1)[0]
        assert objectives.splitlines() == [
            "- Make public transport more accessible through discounts.",
            "- Support urban mobility through demand-side incentives.",
            "- Currently, only the under-18 bonus is in effect.",
        ]
        assert len(session.selected_context_policy_sources["policy objectives"]) == 3
        for point in session.selected_context_policy_sources["policy objectives"]:
            assert {"field": "Page", "value": "115"} in point
        assert service._policy_summary_display(session).count("data-source-table=") >= 3
    finally:
        db.close()


def test_saved_inline_objective_bullets_render_as_aligned_items():
    saved = "### Policy Objectives\n\n- • First objective. • Second objective."
    source = [{"field": "Document", "value": "FITTER D4.2 FINAL.pdf"}]
    summary, sources = _expand_inline_summary_bullets(
        saved, {"policy objectives": [source]},
    )
    assert summary == "### Policy Objectives\n\n- First objective.\n- Second objective."
    assert sources["policy objectives"] == [source, source]


def test_existing_policy_adaptations_use_only_matched_d42_with_source_for_each_point():
    db, _ = _db_with_d42()
    try:
        service = AdaptationService(db)
        session = ChatSession(country="Italy", sector="Transport", selected_context_policy=POLICY,
                              socio_demographic_profiles=["Low-income riders"])
        with patch("app.services.hazard_with_mitigation._country_factsheet_text",
                   side_effect=AssertionError("Factsheets must not be read")):
            reference = asyncio.run(service._existing_policy_adaptation_reference(session))
        assert reference.startswith("# Important concepts for your new proposal\n\n")
        assert "Extend the discounts to adults." in reference
        assert "Limited concessions." not in reference
        assert "## Current policy gaps" not in reference
        assert "## Proposed adaptations" not in reference
        assert "## Challenges to be addressed" in reference
        assert "## Proposed adaptations for the policy" in reference
        assert "Offer reduced annual fares to eligible riders." in reference
        assert "Improve access to public transport." in reference
        assert "- Low-income riders\n" in reference or reference.endswith("- Low-income riders")
        assert "Selected affected profile" not in reference
        assert reference.count("<button") == sum(line.startswith("- ") for line in reference.splitlines()) - 1
        assert markdown_to_html(reference).count("data-source-table=") == reference.count("<button")
        assert "FITTER D4.2 FINAL.pdf" in reference
        assert "115" in reference
        assert "policy factsheet" not in reference.casefold()
    finally:
        db.close()


def test_existing_policy_mechanisms_show_knowledge_source_and_page():
    db, _ = _db_with_d42()
    try:
        service = AdaptationService(db)
        session = ChatSession(
            country="Italy", sector="Transport", selected_context_policy=POLICY,
            selected_hazard="Unaffordable travel",
        )
        service._shared_knowledge_results = AsyncMock(return_value=[{
            "title": "Transport affordability study",
            "source_uri": "kb/transport-affordability.pdf",
            "page_number": 9,
            "content": "Up-front annual pass costs can exclude low-income riders from public transport.",
            "source_type": "pdf",
        }])

        async def respond(**kwargs):
            request = json.loads(kwargs["messages"][0]["content"])
            source_id = next(source["id"] for source in request["sources"]
                             if "Up-front annual pass costs" in source["text"])
            return json.dumps({"mechanisms": [{
                "mechanism": "Up-front annual pass costs",
                "causal_linkage": "High initial costs can exclude low-income riders.",
                "source_id": source_id,
            }]})

        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(side_effect=respond)):
            reference = asyncio.run(service._existing_policy_mechanisms_reference(session))
        assert "## Policy mechanisms to be considered for mitigation" in reference
        assert "Up-front annual pass costs" in reference
        assert "Transport affordability study" in reference
        assert "kb/transport-affordability.pdf" in reference
        assert "&quot;Page&quot;, &quot;value&quot;: &quot;9&quot;" in reference
        assert reference.count("data-source-table=") == 1
    finally:
        db.close()


def test_existing_policy_adaptations_fall_back_to_linked_document():
    db, _ = _db_with_d42()
    try:
        document = KnowledgeDocument(
            title="Transport policy document", source_type="pdf",
            source_uri="https://example.org/transport-policy.pdf", scope="policy_reference",
        )
        db.add(document)
        db.flush()
        db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, page_number=4,
            content="Annual passes cover younger residents, while low-income adults face unaffordable fares.",
            source_type="pdf", source_uri=document.source_uri,
        ))
        db.commit()
        service = AdaptationService(db)
        session = ChatSession(
            country="Italy", sector="Transport",
            selected_context_policy="Unlisted transport policy",
            selected_hazard="Unaffordable travel",
            pending_context_policy_document_ids=[document.id],
        )
        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(return_value=json.dumps({
                       "challenges": [{"text": "Low-income adults face unaffordable fares.", "source_id": 1}],
                       "adaptations": [{"text": "Extend annual passes to low-income adults.", "source_id": 1}],
                   }))):
            reference = asyncio.run(service._existing_policy_adaptation_reference(session))
        assert reference.startswith("# Important concepts for your new proposal\n\n")
        assert "## Challenges to be addressed" in reference
        assert "Low-income adults face unaffordable fares." in reference
        assert "Extend annual passes to low-income adults." in reference
        assert "## Proposed adaptations for the policy" in reference
        assert "## Proposed adaptations" not in reference
        assert "Transport policy document" in reference
        assert "https://example.org/transport-policy.pdf" in reference
        assert "&quot;Page&quot;, &quot;value&quot;: &quot;4&quot;" in reference
        assert "FITTER D4.2 FINAL.pdf" not in reference
        assert reference.count("data-source-table=") == 2
    finally:
        db.close()


def test_user_created_policy_uses_its_document_even_when_title_matches_d42():
    db, _ = _db_with_d42()
    try:
        policy = Policy(country_id="test-country", sector_id="test-sector", policy=POLICY, source="user")
        db.add(policy)
        db.flush()
        document = KnowledgeDocument(
            title="User policy document", source_type="pdf",
            source_uri="https://example.org/user-policy.pdf", scope="policy_reference",
            policy_id=policy.id,
        )
        db.add(document)
        db.flush()
        db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, page_number=2,
            content="Low-income adults cannot afford the current annual fare.",
            source_type="pdf", source_uri=document.source_uri,
        ))
        db.commit()
        service = AdaptationService(db)
        session = ChatSession(
            country="Italy", sector="Transport", selected_context_policy=POLICY,
            selected_context_policy_id=policy.id, selected_hazard="Unaffordable travel",
            pending_context_policy_document_ids=[document.id],
        )
        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(return_value=json.dumps({
                       "challenges": [{"text": "The annual fare excludes low-income adults.", "source_id": 1}],
                       "adaptations": [],
                   }))):
            reference = asyncio.run(service._existing_policy_adaptation_reference(session))
        assert reference.startswith("# Important concepts for your new proposal\n\n")
        assert "## Challenges to be addressed" in reference
        assert "The annual fare excludes low-income adults." in reference
        assert "User policy document" in reference
        assert "&quot;Page&quot;, &quot;value&quot;: &quot;2&quot;" in reference
        assert "FITTER D4.2 FINAL.pdf" not in reference
        assert "## Proposed adaptations for the policy" in reference
        service._shared_knowledge_results = AsyncMock(return_value=[])
        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(return_value=json.dumps({"mechanisms": []}))) as ask_llm:
            asyncio.run(service._existing_policy_mechanisms_reference(session))
        mechanism_sources = json.loads(ask_llm.call_args.kwargs["messages"][0]["content"])["sources"]
        assert any("Low-income adults cannot afford" in source["text"] for source in mechanism_sources)
        assert not any("FITTER D4.2" in source["text"] for source in mechanism_sources)
    finally:
        db.close()


def test_unmatched_policy_without_linked_document_still_shows_challenges_heading():
    db, _ = _db_with_d42()
    try:
        service = AdaptationService(db)
        session = ChatSession(country="Italy", sector="Transport", selected_context_policy="Unlisted policy")
        reference = asyncio.run(service._existing_policy_adaptation_reference(session))
        assert "## Challenges to be addressed" in reference
        assert "No linked policy document is available." in reference
        assert "## Proposed adaptations for the policy" in reference
    finally:
        db.close()


def test_existing_policy_mechanisms_use_document_when_d42_is_unmatched():
    db, _ = _db_with_d42()
    try:
        document = KnowledgeDocument(
            title="Unlisted transport decree", source_type="pdf",
            source_uri="https://example.org/unlisted-transport.pdf", scope="policy_reference",
        )
        db.add(document)
        db.flush()
        db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=0, page_number=5,
            content="The unlisted transport policy requires riders to pay for an annual pass upfront.",
            source_type="pdf", source_uri=document.source_uri,
        ))
        db.commit()
        service = AdaptationService(db)
        service._shared_knowledge_results = AsyncMock(return_value=[])
        session = ChatSession(
            country="Italy", sector="Transport", selected_context_policy="Unlisted transport policy",
            selected_hazard="Unaffordable travel",
            pending_context_policy_document_ids=[document.id],
        )
        with patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(return_value=json.dumps({"mechanisms": [{
                       "mechanism": "Upfront pass payments",
                       "causal_linkage": "Upfront costs can make travel unaffordable.",
                       "source_id": 1,
                   }]}))):
            reference = asyncio.run(service._existing_policy_mechanisms_reference(session))
        assert "Upfront pass payments" in reference
        assert "Unlisted transport decree" in reference
        assert "&quot;Page&quot;, &quot;value&quot;: &quot;5&quot;" in reference
    finally:
        db.close()
