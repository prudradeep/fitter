import asyncio
import json
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import (
    AdditionalHazard,
    CustomHazardPolicyReference,
    KnowledgeChunk,
    KnowledgeDocument,
    MitigationMeasurePolicy,
    MitigationMeasurePolicyAdditionalHazard,
    MitigationMeasurePolicySystemHazard,
    Policy,
    SystemHazard,
)
from app.services.chat_service import ChatService
from app.services.chat_formatters import format_additional_hazards
from app.services.chat_session import ChatSession, ChatSessionStore
from app.services.knowledge_base import POLICY_REFERENCE_SCOPE
from app.services.custom_hazard_validation import validate_context_policy_document


class PolicyReferenceMitigationContextTests(unittest.TestCase):
    def test_policy_clarification_restores_its_form_after_saved_session_reload(self) -> None:
        session = ChatSession(
            country="Ireland", region="Leinster", sector="Energy",
            phase="policy_clarification",
            pending_context_policy_document_ids=["document-1"],
            context_policy_validation={"missing": ["twin_transition_fit"]},
        )
        restored = ChatSessionStore().put("session-1", asdict(session))

        self.assertEqual(restored.phase, "policy_clarification")
        prompt = self.service._repeat_current_options("session-1", restored, "", False)
        self.assertEqual(prompt.step, "policy_clarification")
        self.assertEqual(prompt.input_mode, "policy_reference")
        self.assertEqual([option.label for option in prompt.options], ["Show policy list"])

        # Earlier versions saved the failed response as the complete step.
        broken = asdict(session)
        broken.update(phase="wizard", current_step="complete", current_input_mode="text")
        recovered = ChatSessionStore().put("session-1", broken)
        self.assertEqual(recovered.phase, "policy_clarification")
        prompt = self.service._repeat_current_options("session-1", recovered, "", False)
        self.assertEqual((prompt.step, prompt.input_mode),
                         ("policy_clarification", "policy_reference"))

    def test_policy_clarification_accepts_replacement_url_or_file(self) -> None:
        session = ChatSession(phase="policy_clarification")
        self.service._handle_context_policy_reference = AsyncMock(
            return_value="replacement document handled"
        )

        for marker in ("Policy reference URL: https://example.org/policy\n",
                       "Policy reference file: policy.pdf\n"):
            with self.subTest(marker=marker):
                message = "Clarification text\n" + marker + "Policy reference document ID: doc-1"
                response = asyncio.run(self.service._handle_context_policy_clarification(
                    "session-1", session, message
                ))
                self.assertEqual(response, "replacement document handled")
                self.service._handle_context_policy_reference.assert_awaited_with(
                    "session-1", session, message
                )

    def test_policy_document_review_accepts_sector_fit_without_twin_fit(self) -> None:
        response = (
            '{"sector_objective_fit":{"status":"supported","reason":"Solar policy."},'
            '"twin_transition_fit":{"status":"unclear","reason":"No digital link."},'
            '"clarification_question":"How does the digital component support solar generation?"}'
        )
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            new_callable=AsyncMock, return_value=response,
        ) as ask_llm:
            review = asyncio.run(validate_context_policy_document(
                "Solar generation policy", policy_title="Solar policy", sector="Energy"
            ))

        self.assertEqual(review["missing"], [])
        self.assertEqual(review["clarification_question"], "")
        self.assertEqual(review["checks"]["twin_transition_fit"]["status"], "unclear")
        self.assertNotIn("green_energy_fit", review["checks"])
        self.assertIn("Transition towards renewable energy", ask_llm.call_args.kwargs["messages"][0]["content"])

    def test_policy_document_review_fallback_accepts_sector_fit_without_digital_fit(self) -> None:
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            new_callable=AsyncMock, return_value="LLM unavailable",
        ):
            review = asyncio.run(validate_context_policy_document(
                "The law supports solar renewable energy generation.",
                policy_title="Solar law", sector="Energy",
            ))

        self.assertEqual(review["checks"]["sector_objective_fit"]["status"], "supported")
        self.assertNotIn("green_energy_fit", review["checks"])
        self.assertEqual(review["missing"], [])

    def test_policy_document_review_requests_only_missing_sector_fit(self) -> None:
        response = (
            '{"sector_objective_fit":{"status":"unclear","reason":"Objective link unclear."},'
            '"twin_transition_fit":{"status":"unsupported","reason":"No digital measure."},'
            '"clarification_question":"How does the policy support the sectoral objective?"}'
        )
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            new_callable=AsyncMock, return_value=response,
        ):
            review = asyncio.run(validate_context_policy_document(
                "A policy with unclear sectoral provisions", policy_title="Policy", sector="Energy"
            ))

        self.assertEqual(review["missing"], ["sector_objective_fit"])
        self.assertIn("sectoral objective", review["clarification_question"])

    def test_policy_document_review_reaches_later_text_and_stops_on_support(self) -> None:
        responses = [
            '{"sector_objective_fit":{"status":"unsupported","reason":"No fit here."},'
            '"twin_transition_fit":{"status":"unsupported","reason":"No link."},'
            '"clarification_question":"Provide relevant provisions."}',
            '{"sector_objective_fit":{"status":"supported","reason":"Solar provision."},'
            '"twin_transition_fit":{"status":"unclear","reason":"No digital link."},'
            '"clarification_question":""}',
        ]
        content = "x" * 24000 + "Solar renewable energy generation." + "y" * 24000
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            new_callable=AsyncMock, side_effect=responses,
        ) as ask_llm:
            review = asyncio.run(validate_context_policy_document(
                content, policy_title="Solar policy", sector="Energy"
            ))

        self.assertEqual(ask_llm.await_count, 2)
        self.assertEqual(review["missing"], [])
        self.assertEqual(review["clarification_question"], "")
        second_payload = json.loads(ask_llm.await_args_list[1].kwargs["messages"][0]["content"])
        self.assertIn("Solar renewable energy generation.", second_payload["policy_document"])
        self.assertLessEqual(len(second_payload["policy_document"]), 24000)

    def test_policy_document_review_checks_all_text_before_requesting_clarification(self) -> None:
        response = (
            '{"sector_objective_fit":{"status":"unsupported","reason":"No objective fit."},'
            '"twin_transition_fit":{"status":"unsupported","reason":"No link."},'
            '"clarification_question":"Supply relevant provisions."}'
        )
        with patch(
            "app.services.custom_hazard_validation.ask_llm_chat",
            new_callable=AsyncMock, return_value=response,
        ) as ask_llm:
            review = asyncio.run(validate_context_policy_document(
                "x" * 50000, policy_title="Policy", sector="Energy"
            ))

        self.assertEqual(ask_llm.await_count, 3)
        self.assertEqual(review["missing"], ["sector_objective_fit"])

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

    def test_untyped_policy_keeps_country_sector_hazards(self) -> None:
        self.service.db = MagicMock()
        self.service.db.get.return_value = SimpleNamespace(
            policy_type="Other policy type"
        )
        session = ChatSession(
            selected_context_policy_id="policy-1",
            hazards=["Previously loaded hazard"],
            hazard_profiles={"Previously loaded hazard": [{"name": "Older people"}]},
        )

        self.service._limit_hazards_to_selected_policy(session)

        self.assertEqual(session.hazards, ["Previously loaded hazard"])
        self.assertEqual(
            session.hazard_profiles["Previously loaded hazard"],
            [{"name": "Older people"}],
        )

    def test_adjustment_policy_limits_hazards_to_mitigation_mappings(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Clean heat support",
            policy_type="Adjustment to existing policy",
        )
        mitigation_policy = MitigationMeasurePolicy(
            policy_code="DE_HEAT",
            policy_title="Clean heat support scheme",
            country_id="country-1",
            sector_id="sector-1",
        )
        system_hazard = SystemHazard(sector_id="sector-1", name="Heating costs increase")
        additional_hazard = AdditionalHazard(
            country_id="country-1",
            sector_id="sector-1",
            name="Tenant displacement",
        )
        self.db.add_all([policy, mitigation_policy, system_hazard, additional_hazard])
        self.db.flush()
        self.db.add_all(
            [
                MitigationMeasurePolicySystemHazard(
                    mitigation_measure_policy_id=mitigation_policy.id,
                    system_hazard_id=system_hazard.id,
                ),
                MitigationMeasurePolicyAdditionalHazard(
                    mitigation_measure_policy_id=mitigation_policy.id,
                    additional_hazard_id=additional_hazard.id,
                ),
            ]
        )
        self.db.commit()
        session = ChatSession(
            selected_context_policy_id=policy.id,
            hazards=["Heating costs increase", "Electricity bills increase"],
            additional_hazards=["Tenant displacement", "Digital exclusion"],
            custom_hazards=["Co-created risk"],
        )

        self.service._limit_hazards_to_selected_policy(session)

        self.assertEqual(session.hazards, ["Heating costs increase"])
        self.assertEqual(session.additional_hazards, ["Tenant displacement"])
        self.assertEqual(session.custom_hazards, [])

    def test_survey_case_policy_uses_all_sector_system_hazards_and_no_additional_hazards(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Survey policy",
            policy_type="Survey policy case study",
        )
        self.db.add(policy)
        self.db.commit()
        self.service._stored_hazard_items_for_context = MagicMock(
            return_value=[
                {"hazard": "System one", "profiles": [{"name": "Older people"}]},
                {"hazard": "System two", "profiles": [{"name": "Low-income households"}]},
            ]
        )
        session = ChatSession(
            selected_context_policy_id=policy.id,
            additional_hazards=["Expert-only hazard"],
            custom_hazards=["Co-created risk"],
        )

        self.service._limit_hazards_to_selected_policy(session)

        self.assertEqual(session.hazards, ["System one", "System two"])
        self.assertEqual(session.additional_hazards, [])
        self.assertEqual(session.custom_hazards, ["Co-created risk"])

    def test_policy_population_enrichment_preserves_all_catalogue_hazards(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Survey policy",
            policy_type="Survey policy case study",
        )
        self.db.add(policy)
        self.db.commit()
        session = ChatSession(
            selected_context_policy_id=policy.id,
            hazards=["Power cuts", "Unranked catalogue hazard"],
            hazard_profiles={
                "Power cuts": [{"name": "Bill confidence"}],
                "Unranked catalogue hazard": [{"name": "Older people"}],
            },
        )

        async def rank_hazards(target_session: ChatSession) -> None:
            target_session.hazards = ["Power cuts"]
            target_session.hazard_profiles = {
                "Power cuts": [
                    {
                        "name": "Bill confidence",
                        "regional_population_pct": 36.5,
                        "national_population_pct": 29.0,
                    }
                ]
            }

        self.service._rank_session_hazards = rank_hazards

        asyncio.run(self.service._enrich_policy_hazards_with_population_context(session))

        self.assertEqual(session.hazards, ["Power cuts", "Unranked catalogue hazard"])
        self.assertEqual(
            session.hazard_profiles["Power cuts"][0]["regional_population_pct"], 36.5
        )
        self.assertEqual(
            session.hazard_profiles["Unranked catalogue hazard"],
            [{"name": "Older people"}],
        )

    def test_policy_population_enrichment_preserves_additional_hazard_profiles(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Clean heat support",
            policy_type="Adjustment to existing policy",
        )
        self.db.add(policy)
        self.db.commit()
        session = ChatSession(
            selected_context_policy_id=policy.id,
            region="Bavaria",
            hazards=["Heating costs increase"],
            additional_hazards=["Tenancy & housing insecurity"],
            hazard_profiles={
                "Heating costs increase": [{"name": "Older people"}],
                "Tenancy & housing insecurity": [{"name": "Renting households"}],
            },
        )

        async def rank_hazards(target_session: ChatSession) -> None:
            target_session.hazard_profiles = {
                "Heating costs increase": [{"name": "Older people"}]
            }

        self.service._rank_session_hazards = rank_hazards

        asyncio.run(self.service._enrich_policy_hazards_with_population_context(session))

        self.assertEqual(
            session.hazard_profiles["Tenancy & housing insecurity"],
            [{"name": "Renting households"}],
        )
        table = format_additional_hazards(session)
        self.assertIn("Affected population profile", table)
        self.assertIn("Renting households", table)

    def test_policy_context_uses_policies_and_its_knowledge_document_link(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Clean electricity support",
        )
        self.db.add(policy)
        self.db.flush()
        document = KnowledgeDocument(
            title="Clean electricity policy",
            source_type="txt",
            scope="policy_document",
            policy_id=policy.id,
        )
        self.db.add(document)
        self.db.commit()

        session = ChatSession(country_id="country-1", sector_id="sector-1")

        self.assertEqual(
            self.service._policy_rows_for_selected_context(session),
            [(policy.id, "Clean electricity support")],
        )
        session.selected_context_policy_id = policy.id
        self.assertEqual(
            self.service._stored_context_policy_document_ids(session),
            [document.id],
        )

    def test_policy_selection_requests_document_even_with_catalog_details(self) -> None:
        policy = Policy(
            country_id="country-1",
            sector_id="sector-1",
            policy="Clean electricity support",
        )
        self.db.add(policy)
        self.db.commit()
        session = ChatSession(
            country_id="country-1",
            sector_id="sector-1",
            selected_context_policy_id=policy.id,
            selected_context_policy=policy.policy,
        )
        self.service._summarize_context_policy = AsyncMock()

        with patch(
            "app.services.document_language.get_settings",
            return_value=SimpleNamespace(enable_english_translation=False),
        ):
            response = asyncio.run(
                self.service._context_policy_details_step("session-1", session)
            )

        self.assertEqual(response.step, "policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertEqual(session.phase, "policy_reference")
        self.assertEqual([option.label for option in response.options], ["Show policy list"])
        self.assertIn("Please provide its URL or attach", response.bot_message)
        self.assertIn("Please provide an English document", response.bot_message)
        self.service._summarize_context_policy.assert_not_called()

    def test_policy_summary_requests_benefited_population_groups(self) -> None:
        session = ChatSession(selected_context_policy="Clean electricity support")
        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value="Policy summary with socio-demographic groups benefited.",
        ) as ask_llm:
            asyncio.run(
                self.service._summarize_context_policy(
                    session,
                    "Nearby residents can participate in the project.",
                    "Clean electricity support",
                )
            )

        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Socio-demographic groups benefited", prompt)
        self.assertIn("Briefly explain how each group benefits", prompt)
        self.assertIn("Nearby residents", prompt)

    def test_policy_summary_fallback_states_when_benefited_groups_are_unknown(self) -> None:
        session = ChatSession(selected_context_policy="Clean electricity support")
        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value="",
        ):
            summary = asyncio.run(
                self.service._summarize_context_policy(
                    session, "Policy text", "Clean electricity support"
                )
            )

        self.assertIn("Socio-demographic groups benefited", summary)
        self.assertIn("Not identified", summary)

    def test_policy_summary_renders_all_sections_from_structured_response(self) -> None:
        session = ChatSession(selected_context_policy="Clean electricity support")
        response = json.dumps({
            "policy_details": [{"label": "Scope", "text": "The policy funds solar generation."}],
            "mechanisms": [{"label": "Grants", "text": "Grants support rooftop installations."}],
            "intended_benefits": [{"label": "Renewable Supply", "text": "More renewable electricity."}],
            "benefited_groups": [{"label": "Low-income Households", "text": "They receive grants."}],
        })
        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock, return_value=response,
        ) as ask_llm:
            summary = asyncio.run(self.service._summarize_context_policy(
                session, "Solar grants for low-income households.", "Clean electricity support"
            ))

        self.assertEqual(ask_llm.await_count, 1)
        for heading in (
            "Policy details", "Mechanisms", "Intended benefits",
            "Socio-demographic groups benefited",
        ):
            self.assertIn(f"### {heading}\n\n- **", summary)
        self.assertIn("- **Low-income Households:** They receive grants.", summary)
        from app.services.message_renderer import markdown_to_html
        rendered = markdown_to_html(summary)
        self.assertEqual(rendered.count("<h3>"), 4)
        self.assertEqual(rendered.count("<li>"), 4)

    def test_policy_summary_retries_incomplete_response(self) -> None:
        session = ChatSession(selected_context_policy="Clean electricity support")
        complete = json.dumps({
            "policy_details": [{"label": "Scope", "text": "The policy funds solar generation."}],
            "mechanisms": [{"label": "Grants", "text": "Grants support rooftop installations."}],
            "intended_benefits": [{"label": "Renewable Supply", "text": "More renewable electricity."}],
            "benefited_groups": [{"label": "Not identified", "text": "Not stated in the supplied text."}],
        })
        incomplete = json.dumps({
            "policy_details": [{"label": "Scope", "text": "The regulation amends ("}],
            "mechanisms": [{"label": "Grants", "text": "Grants support rooftop installations."}],
            "intended_benefits": [{"label": "Renewable Supply", "text": "More renewable electricity."}],
            "benefited_groups": [{"label": "Not identified", "text": "Not stated in the supplied text."}],
        })
        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock,
            side_effect=[incomplete, complete],
        ) as ask_llm:
            summary = asyncio.run(self.service._summarize_context_policy(
                session, "Solar grants for households.", "Clean electricity support"
            ))

        self.assertEqual(ask_llm.await_count, 2)
        self.assertIn("### Intended benefits\n\n- **Renewable Supply:** More renewable electricity.", summary)
        self.assertNotIn("amends (", summary)

    def test_policy_summary_includes_cited_provisions_beyond_initial_limit(self) -> None:
        from app.services.chat_hazard_steps import _policy_summary_source

        source = (
            "A participation agreement under § 7 must reflect participation provided under § 8.\n"
            + "x" * 49000
            + "\n§ 7 Participation agreement\nResidents may receive a share of project revenue.\n"
            + "§ 8 Replacement participation\nThe operator may offer an annual payment.\n"
        )
        excerpt = _policy_summary_source(source, 48000)

        self.assertIn("Residents may receive a share of project revenue.", excerpt)
        self.assertIn("The operator may offer an annual payment.", excerpt)

    def test_policy_summary_sends_more_than_twelve_thousand_source_characters(self) -> None:
        session = ChatSession(selected_context_policy="Wind participation")
        source = "x" * 13000 + "Residents receive project revenue."
        response = json.dumps({
            "policy_details": [{"label": "Scope", "text": "Residents can participate."}],
            "mechanisms": [{"label": "Agreement", "text": "Residents share project revenue."}],
            "intended_benefits": [{"label": "Local Value", "text": "Residents receive value."}],
            "benefited_groups": [{"label": "Residents", "text": "They can participate."}],
        })
        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock, return_value=response,
        ) as ask_llm:
            asyncio.run(self.service._summarize_context_policy(
                session, source, "Wind participation",
            ))

        self.assertIn(
            "Residents receive project revenue.",
            ask_llm.call_args.kwargs["messages"][0]["content"],
        )

    def test_policy_summary_retries_section_references(self) -> None:
        session = ChatSession(selected_context_policy="Wind participation")
        def response(mechanism: str) -> str:
            return json.dumps({
                "policy_details": [{"label": "Scope", "text": "Residents can participate."}],
                "mechanisms": [{"label": "Agreement", "text": mechanism}],
                "intended_benefits": [{"label": "Local Value", "text": "Residents receive value."}],
                "benefited_groups": [{"label": "Residents", "text": "They can participate."}],
            })

        with patch(
            "app.services.chat_hazard_steps.ask_llm_chat",
            new_callable=AsyncMock,
            side_effect=[
                response("A participation agreement is required under § 7."),
                response("A participation agreement lets residents share project revenue."),
            ],
        ) as ask_llm:
            summary = asyncio.run(self.service._summarize_context_policy(
                session,
                "§ 7 Participation agreement\nResidents may receive a share of project revenue.",
                "Wind participation",
            ))

        self.assertEqual(ask_llm.await_count, 2)
        self.assertNotIn("§ 7", summary)
        self.assertIn("residents share project revenue", summary)
        self.assertIn("combine its actual rule", ask_llm.call_args.kwargs["messages"][0]["content"])

    def test_uploaded_policy_continues_when_only_sector_fit_is_supported(self) -> None:
        policy = Policy(country_id="country-1", sector_id="sector-1", policy="Clean energy policy")
        document = KnowledgeDocument(
            user_id="owner-1", title="Upload", source_type="txt",
            scope="temporary", session_key="session-1",
        )
        self.db.add_all([policy, document])
        self.db.flush()
        self.db.add(KnowledgeChunk(
            document_id=document.id, user_id="owner-1", chunk_index=0,
            content="The policy supports solar energy with a smart grid.", source_type="txt",
        ))
        self.db.commit()
        session = ChatSession(
            session_key="session-1", country_id="country-1", sector_id="sector-1",
            sector="Energy", selected_context_policy_id=policy.id,
            selected_context_policy=policy.policy, phase="policy_reference",
        )
        self.service._policy_reference_context = AsyncMock(return_value="Solar energy policy with smart grid")
        self.service._summarize_context_policy = AsyncMock(return_value="Policy summary")
        review = {
            "checks": {
                "sector_objective_fit": {"status": "supported", "reason": "Solar energy."},
                "twin_transition_fit": {"status": "unclear", "reason": "Grid link unclear."},
            },
            "missing": [],
            "clarification_question": "",
        }
        with patch(
            "app.services.chat_hazard_steps.validate_context_policy_document",
            new_callable=AsyncMock, return_value=review,
        ) as validate:
            response = asyncio.run(self.service._handle_context_policy_reference(
                "session-1", session, f"Policy reference document ID: {document.id}"
            ))

        self.assertEqual(response.step, "policy_summary")
        self.assertIn("Sectoral objective fit supported", response.bot_message)
        self.assertNotIn("twin transition fit supported", response.bot_message)
        self.assertEqual(document.scope, "policy_reference")
        validate.assert_awaited_once()
        self.assertNotIn("green energy", response.bot_message)

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

    def test_policy_review_can_retrieve_text_beyond_bounded_context(self) -> None:
        document = KnowledgeDocument(
            user_id="owner-1", title="Long policy", source_type="txt",
            scope="temporary", session_key="session-1",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add_all([
            KnowledgeChunk(
                document_id=document.id, user_id="owner-1", chunk_index=index,
                content=("x" * 1000 if index < 25 else "Final solar provision"),
                source_type="txt",
            )
            for index in range(26)
        ])
        self.db.commit()
        session = ChatSession(session_key="session-1")

        full_text = asyncio.run(self.service._policy_reference_context(
            session, [document.id], full_text=True,
        ))

        self.assertIn("Final solar provision", full_text)
        self.assertGreater(len(full_text), 24000)

    def test_evidence_review_can_retrieve_text_beyond_bounded_context(self) -> None:
        document = KnowledgeDocument(
            user_id="owner-1", title="Long evidence", source_type="txt",
            scope="temporary", session_key="session-1",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add_all([
            KnowledgeChunk(
                document_id=document.id, user_id="owner-1", chunk_index=index,
                content=("x" * 1000 if index < 25 else "Final evidence finding"),
                source_type="txt",
            )
            for index in range(26)
        ])
        self.db.commit()
        session = ChatSession(session_key="session-1")
        evidence = f"Temporary evidence document ID: {document.id}"

        bounded = asyncio.run(self.service._user_evidence_context_for_contradiction_check(
            session, evidence,
        ))
        full_text = asyncio.run(self.service._user_evidence_context_for_contradiction_check(
            session, evidence, full_text=True,
        ))

        self.assertNotIn("Final evidence finding", bounded)
        self.assertIn("Final evidence finding", full_text)

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
