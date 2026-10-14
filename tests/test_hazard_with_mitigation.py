import unittest
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument, Policy
from app.services.hazard_with_mitigation import (
    _new_policy_source_table,
    country_existing_policy_adaptation_sources,
    country_factsheet_inspiration_fields,
    country_factsheet_policy_objectives,
    country_factsheet_reference,
    inferred_adaptations_reference,
)
from app.services.chat_mitigation_creation_guided import ChatMitigationCreationGuidedMixin
from app.services.chat_session import ChatSession
from app.services.chat_service import ChatService


FACTSHEET_TEXT = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Clean Heat Act
Sectoral focus
Housing
Original policy objectives
Improve heating systems.
Proposed adaptations
Add targeted grants for low-income tenants.
Policy type(s)
Economic incentives
Target population: only one question is allowed per answer
Gender:
x Woman   ☐ Male
Tenancy status:
☐ Homeowner   x Tenant
Systemic focus
Policy & Governance
POLICY FACTSHEETS FOR NEW POLICY PROPOSALS
Title of the policy (new proposal)
Community retrofit service
Sectoral focus
Housing
Prioritised challenge addressed
Tenants cannot afford efficient homes.
Policy description
Fund local retrofit advice and tenant protections.
Policy type(s)
Public services
Participatory dimension & stakeholders
Tenant associations and municipal housing teams.
Target population: only one question is allowed per answer
Tenancy status:
x Tenant   ☐ Homeowner
Systemic focus
Housing access
Potential risks/barriers
Limited local delivery capacity.
Drivers/enablers
Municipal funding and trusted tenant groups.
"""


class HazardWithMitigationFactsheetTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        document = KnowledgeDocument(
            title="Hazards with mitigation — Germany",
            source_type="hazard_with_mitigation",
            source_uri="kb/additional/hazards with mitigation/germany.docx",
            scope="main",
            scope_level="global",
        )
        self.db.add(document)
        self.db.flush()
        self.db.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_index=0,
                content=FACTSHEET_TEXT,
                source_type="hazard_with_mitigation",
                source_uri=document.source_uri,
                scope_level="global",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_selected_policy_objectives_come_from_matching_factsheet(self) -> None:
        objectives = country_factsheet_policy_objectives(
            self.db, country="Germany", sector="Housing", selected_policy="Clean Heat Act",
        )
        self.assertEqual(objectives, "Improve heating systems.")
        self.assertEqual(country_factsheet_policy_objectives(
            self.db, country="Germany", sector="Energy", selected_policy="Clean Heat Act",
        ), "")
        self.assertEqual(country_factsheet_policy_objectives(
            self.db, country="Germany", sector="Housing", selected_policy="Another policy",
        ), "")

    def test_factsheet_objectives_match_short_policy_title_with_amendment_note(self) -> None:
        factsheet = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Energy Industry Act, Energiewirtschaftsgesetz (EnWG) – Amendment 2025*
*This Act was amended in several policy cycles.
Sectoral focus
Energy
Original policy objectives
Make the grid-based energy supply secure and affordable.
Prioritised challenge addressed
Energy sharing
Proposed adaptations
Support citizen energy.
"""
        with patch("app.services.hazard_with_mitigation._country_factsheet_text", return_value=factsheet):
            objectives = country_factsheet_policy_objectives(
                self.db, country="Germany", sector="Energy",
                selected_policy="Energiewirtschaftsgesetz (EnWG)",
            )
            wrong_sector = country_factsheet_policy_objectives(
                self.db, country="Germany", sector="Housing",
                selected_policy="Energiewirtschaftsgesetz (EnWG)",
            )

        self.assertEqual(objectives, "Make the grid-based energy supply secure and affordable.")
        self.assertEqual(wrong_sector, "")

    def test_factsheet_objectives_match_paraphrased_policy_title(self) -> None:
        factsheet = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Citizen participation laws (Bürgerbeteiligungsgesetze) Standardisation of and liability in participation laws, from federal/state level to national level
Sectoral focus
Energy
Original policy objectives
Participation for citizens in renewable energy projects; rise in acceptance rates.
Prioritised challenge addressed
Lack of citizen participation.
Title of the policy (existing)
Electricity Tax Act
Sectoral focus
Energy
Original policy objectives
Reduce electricity taxes.
Proposed adaptations
Change the tax rules.
"""
        with patch("app.services.hazard_with_mitigation._country_factsheet_text", return_value=factsheet):
            objectives = country_factsheet_policy_objectives(
                self.db, country="Germany", sector="Energy",
                selected_policy=(
                    "Citizen Participation Laws (Bürgerbeteiligungsgesetze) — "
                    "Standardising participation and liability rules nationwide."
                ),
            )

        self.assertEqual(
            objectives,
            "Participation for citizens in renewable energy projects; rise in acceptance rates.",
        )

    def test_factsheet_objectives_do_not_use_ambiguous_title_match(self) -> None:
        factsheet = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Citizen participation laws for wind energy
Sectoral focus
Energy
Original policy objectives
Wind objective.
Title of the policy (existing)
Citizen participation laws for solar energy
Sectoral focus
Energy
Original policy objectives
Solar objective.
"""
        with patch("app.services.hazard_with_mitigation._country_factsheet_text", return_value=factsheet):
            objectives = country_factsheet_policy_objectives(
                self.db, country="Germany", sector="Energy",
                selected_policy="Citizen participation laws for renewable energy",
            )

        self.assertEqual(objectives, "")

    def test_policy_summary_uses_document_when_d42_has_no_match(self) -> None:
        service = ChatService.__new__(ChatService)
        service.db = self.db
        session = ChatSession(
            country="Germany", sector="Housing", selected_context_policy="Clean Heat Act",
        )
        response = json.dumps({
            "policy_details": [{"label": "Scope", "text": "The policy covers heating."}],
            "original_objectives": [{"label": "Heating", "text": "The policy aims to improve heating systems."}],
            "mechanisms": [{"label": "Grants", "text": "Grants support tenants."}],
            "intended_benefits": [{"label": "Warm homes", "text": "Homes become warmer."}],
            "benefited_groups": [{"label": "Tenants", "text": "Tenants receive support."}],
        })
        with patch("app.services.hazard_with_mitigation._country_factsheet_text",
                   side_effect=AssertionError("Factsheet lookup is forbidden")), \
             patch("app.services.chat_hazard_steps.ask_llm_chat",
                   new_callable=AsyncMock, return_value=response) as ask_llm:
            summary = asyncio.run(service._summarize_context_policy(
                session, "The policy aims to improve heating systems.", "",
            ))

        self.assertIn("### Policy Objectives\n\n- **Heating:** The policy aims to improve heating systems.", summary)
        self.assertIn("The policy aims to improve heating systems.",
                      ask_llm.call_args.kwargs["messages"][0]["content"])
        self.assertIn("### Mechanisms", summary)

    def test_existing_policy_flow_uses_proposed_adaptations_heading(self) -> None:
        reference = country_factsheet_reference(
            self.db,
            country="Germany",
            region="Berlin",
            sector="Housing",
            selected_policy="Clean Heat Act",
            hazard="Unaffordable heating",
            proposal_type="existing_policy",
            disadvantage_groups=["Low-income households"],
        )

        self.assertIn("## Proposed adaptations", reference)
        self.assertIn("Add targeted grants for low-income tenants.", reference)
        self.assertIn("factsheet-source-tag", reference)
        self.assertIn("data-source-table", reference)
        self.assertNotIn("### Clean Heat Act", reference)
        self.assertNotIn("Germany · Housing · Clean Heat Act", reference)
        self.assertIn("## Disadvantage groups to consider for this mitigation measure", reference)
        self.assertIn("Low-income households", reference)
        self.assertIn("Gender: Woman", reference)
        self.assertIn("Tenancy status: Tenant", reference)

    def test_existing_adaptations_use_flexible_title_and_show_source_fields(self) -> None:
        factsheet = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Clean Heat Act - Amendment 2025*
Sectoral focus
Housing
Original policy objectives
Improve heating systems.
Prioritised challenge addressed
Unaffordable heating for tenants.
Proposed adaptations
Add targeted grants for low-income tenants.
Policy type(s)
Economic incentives
Participatory dimension & stakeholders
Tenant associations and municipal housing teams.
"""
        with patch("app.services.hazard_with_mitigation._country_factsheet_text", return_value=factsheet):
            reference = country_factsheet_reference(
                self.db, country="Germany", sector="Housing", selected_policy="Clean Heat Act",
                hazard="Unaffordable heating", proposal_type="existing_policy",
            )
            wrong_sector = country_factsheet_reference(
                self.db, country="Germany", sector="Energy", selected_policy="Clean Heat Act",
                hazard="Unaffordable heating", proposal_type="existing_policy",
            )
        self.assertIn("Add targeted grants for low-income tenants.", reference)
        self.assertIn("Unaffordable heating for tenants.", reference)
        self.assertIn("Tenant associations and municipal housing teams.", reference)
        self.assertEqual(wrong_sector, "")

    def test_existing_adaptations_reject_ambiguous_similar_titles(self) -> None:
        factsheet = """
POLICY FACTSHEETS TO ADAPT EXISTING POLICIES
Title of the policy (existing)
Citizen participation laws for wind energy
Sectoral focus
Energy
Proposed adaptations
Wind adaptation.
Title of the policy (existing)
Citizen participation laws for solar energy
Sectoral focus
Energy
Proposed adaptations
Solar adaptation.
"""
        with patch("app.services.hazard_with_mitigation._country_factsheet_text", return_value=factsheet):
            reference = country_factsheet_reference(
                self.db, country="Germany", sector="Energy",
                selected_policy="Citizen participation laws for renewable energy",
                hazard="Low participation", proposal_type="existing_policy",
            )
        self.assertEqual(reference, "")

    def test_unmatched_policy_sources_and_rendering_keep_factsheet_text(self) -> None:
        matched, sources = country_existing_policy_adaptation_sources(
            self.db, country="Germany", sector="Housing", selected_policy="New heating policy",
        )
        self.assertFalse(matched)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["Proposed adaptations"], "Add targeted grants for low-income tenants.")
        reference = inferred_adaptations_reference(
            sources, adaptation_ids=[1, 42], challenge_ids=[], stakeholder_ids=[]
        )
        self.assertIn("## Challenges to be addressed", reference)
        self.assertIn("## Proposed adaptations for the policy", reference)
        self.assertIn("- Add targeted grants for low-income tenants.", reference)
        self.assertIn("kb/additional/hazards with mitigation/germany.docx", reference)
        self.assertEqual(reference.count("data-source-table="), 1)

    def test_existing_policy_without_factsheet_keeps_mechanisms_and_groups(self) -> None:
        reference = country_factsheet_reference(
            self.db, country="Germany", sector="Housing",
            selected_policy="Another housing policy", hazard="Unaffordable heating",
            proposal_type="existing_policy",
            disadvantage_groups=["Low-income households"],
            mechanism_suggestions=[{
                "mechanism": "Upfront retrofit costs",
                "causal_linkage": "Costs prevent tenants from improving homes.",
            }],
        )
        self.assertIn("## Policy mechanisms to be considered for mitigation", reference)
        self.assertIn("Upfront retrofit costs", reference)
        self.assertIn("## Disadvantage groups to consider for this mitigation measure", reference)
        self.assertIn("Low-income households", reference)

    def test_catalog_policy_without_factsheet_uses_semantic_adaptations(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        service.db = SimpleNamespace(get=lambda model, policy_id: SimpleNamespace(source="xlsx"))
        session = SimpleNamespace(
            selected_context_policy_id="catalog-policy",
            selected_context_policy="Another housing policy",
            selected_context_policy_summary=(
                "### Policy Objectives\n\n- Improve access to affordable heating.\n\n"
                "### Intended benefits\n\n- Warm homes."
            ),
            selected_hazard="Unaffordable heating", accepted_custom_hazard=None,
            country="Germany", sector="Housing",
        )
        sources = [{
            "Title of the policy (existing)": "Clean Heat Act",
            "Original policy objectives": "Improve heating systems.",
            "Proposed adaptations": "Add targeted grants for low-income tenants.",
            "Prioritised challenge addressed": "Tenants cannot afford heating.",
            "Participatory dimension & stakeholders": "Tenant groups and housing teams.",
        }]
        with patch(
            "app.services.chat_mitigation_creation_guided.country_existing_policy_adaptation_sources",
            return_value=(False, sources),
        ) as source_lookup, patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(side_effect=[
                '{"ids":[1]}', '{"challenge_ids":[1],"stakeholder_ids":[1]}'
            ]),
        ):
            reference = asyncio.run(service._inferred_existing_policy_adaptations(session))
        self.assertFalse(source_lookup.call_args.kwargs["ignore_title_match"])
        self.assertIn("Add targeted grants for low-income tenants.", reference)
        self.assertIn("Tenants cannot afford heating.", reference)
        self.assertIn("Tenant groups and housing teams.", reference)

    def test_catalog_policy_with_factsheet_does_not_infer_other_adaptations(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        service.db = SimpleNamespace(get=lambda model, policy_id: SimpleNamespace(source="xlsx"))
        session = SimpleNamespace(
            selected_context_policy_id="catalog-policy", selected_context_policy="Clean Heat Act",
            selected_context_policy_summary="### Policy Objectives\n\n- Improve heating systems.",
            selected_hazard="Unaffordable heating", accepted_custom_hazard=None,
            country="Germany", sector="Housing",
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.country_existing_policy_adaptation_sources",
            return_value=(True, []),
        ), patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new_callable=AsyncMock,
        ) as ask_llm:
            reference = asyncio.run(service._inferred_existing_policy_adaptations(session))
        self.assertEqual(reference, "")
        ask_llm.assert_not_called()

    def test_user_policy_semantic_selection_uses_objectives_and_hazard(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        service.db = SimpleNamespace(get=lambda model, policy_id: SimpleNamespace(source="user"))
        session = SimpleNamespace(
            selected_context_policy_id="new-policy", selected_context_policy="New heating policy",
            selected_context_policy_summary="### Policy Objectives\n\n- Improve access to affordable heating.\n\n### Intended benefits\n\n- Warm homes.",
            selected_hazard="Unaffordable heating", accepted_custom_hazard=None,
            country="Germany", sector="Housing",
        )
        sources = [{
            "Title of the policy (existing)": "Clean Heat Act",
            "Original policy objectives": "Improve heating systems.",
            "Proposed adaptations": "Add targeted grants for low-income tenants.",
            "Prioritised challenge addressed": "Tenants cannot afford heating.",
            "Participatory dimension & stakeholders": "Tenant groups and housing teams.",
        }]
        with patch(
            "app.services.chat_mitigation_creation_guided.country_existing_policy_adaptation_sources",
            return_value=(False, sources),
        ), patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(side_effect=[
                '{"ids":[1]}', '{"challenge_ids":[1],"stakeholder_ids":[1]}'
            ]),
        ) as ask_llm:
            result = asyncio.run(service._inferred_existing_policy_adaptations(session))
        self.assertIn("Add targeted grants for low-income tenants.", result)
        self.assertIn("Tenants cannot afford heating.", result)
        self.assertIn("Tenant groups and housing teams.", result)
        first_request = json.loads(ask_llm.call_args_list[0].kwargs["messages"][0]["content"])
        self.assertIn("Improve access to affordable heating", first_request["selected_objectives"])
        self.assertEqual(first_request["selected_hazard"], "Unaffordable heating")

    def test_user_policy_mitigation_flow_uses_related_adaptation_factsheets(self) -> None:
        policy = Policy(country_id="test-country", sector_id="test-sector",
                        policy="New heating policy", source="user")
        self.db.add(policy)
        self.db.commit()
        service = ChatMitigationCreationGuidedMixin()
        service.db = self.db
        service._policy_document_adaptation_reference = AsyncMock(
            side_effect=AssertionError("Document fallback must not run after a factsheet match")
        )
        session = ChatSession(
            selected_context_policy_id=policy.id,
            selected_context_policy="New heating policy",
            selected_context_policy_summary="### Policy Objectives\n\n- Improve access to affordable heating.",
            selected_hazard="Unaffordable heating", country="Germany", sector="Housing",
            socio_demographic_profiles=["Low-income households"],
        )
        factsheet = FACTSHEET_TEXT.replace(
            "Proposed adaptations\nAdd targeted grants",
            "Prioritised challenge addressed\nTenants cannot afford efficient homes.\n"
            "Proposed adaptations\n• Add targeted grants",
        ).replace(
            "for low-income tenants.\nPolicy type(s)",
            "for low-income tenants.\n• Replace heating systems in public offices.\nPolicy type(s)",
        ).replace(
            "Policy type(s)\nEconomic incentives\nTarget population",
            "Policy type(s)\nEconomic incentives\nParticipatory dimension & stakeholders\n"
            "Tenant associations and municipal housing teams.\nTarget population",
        )
        with patch("app.services.hazard_with_mitigation._country_factsheet_text",
                   return_value=factsheet), patch("app.services.chat_mitigation_creation_guided.ask_llm_chat",
                   new=AsyncMock(side_effect=[
                       '{"ids":[1]}', '{"challenge_ids":[1],"stakeholder_ids":[1]}'
                   ])):
            reference = asyncio.run(service._existing_policy_adaptation_reference(session))
        self.assertTrue(reference.startswith("# Important concepts for your new proposal\n\n"))
        self.assertIn("## Challenges to be addressed", reference)
        self.assertIn("Tenants cannot afford efficient homes.", reference)
        self.assertIn("## Proposed adaptations for the policy", reference)
        self.assertIn("Add targeted grants for low-income tenants.", reference)
        self.assertNotIn("Replace heating systems in public offices.", reference)
        self.assertIn("## Stakeholders to involve", reference)
        self.assertIn("Tenant associations and municipal housing teams.", reference)
        self.assertNotIn("## Proposed adaptations\n\n", reference)
        self.assertIn("kb/additional/hazards with mitigation/germany.docx", reference)
        self.assertEqual(reference.count("data-source-table="), 3)
        groups = reference.split("## Disadvantage groups to consider for this mitigation measure", 1)[1]
        self.assertNotIn("data-source-table=", groups)

    def test_new_policy_flow_shows_interpretation_with_sources(self) -> None:
        reference = country_factsheet_reference(
            self.db,
            country="Germany",
            region="Berlin",
            sector="Housing",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
            proposal_type="new_policy",
            disadvantage_groups=["Low-income households"],
            mechanism_suggestions=[{
                "mechanism": "High upfront costs",
                "causal_linkage": "Costs prevent tenants from improving homes.",
            }],
            new_policy_interpretation={
                "challenge": [
                    "Renters may struggle to access affordable home energy improvements.",
                    "Rising costs can make those improvements harder to sustain.",
                ],
                "approach": [
                    "Local advice could help households plan home energy improvements.",
                    "Tenant safeguards could make participation more affordable.",
                ],
                "stakeholders": [
                    "Housing teams could help deliver the support.",
                    "Tenant groups could represent renters in its design.",
                ],
            },
        )

        self.assertIn("# Inspiration for New policy proposal", reference)
        self.assertNotIn("## Proposed adaptations\n\n", reference)
        self.assertNotIn("### Stakeholders to involve", reference)
        self.assertIn("## Policy mechanisms to be considered for mitigation", reference)
        self.assertIn("**Selected policy**: Reduce household energy costs", reference)
        self.assertIn("**Selected hazard**: Unaffordable heating", reference)
        self.assertIn("**Selected context**: Germany, Berlin, Housing", reference)
        self.assertIn("# Important concepts for your new proposal", reference)
        self.assertIn("### Challenges to be addressed", reference)
        self.assertIn("### Proposed adaptations for the policy", reference)
        self.assertIn("### Possible stakeholders to involve", reference)
        self.assertIn("### Challenges to be addressed\n\n- Renters may struggle", reference)
        self.assertIn("\n- Rising costs can make", reference)
        self.assertIn("### Proposed adaptations for the policy\n\n- Local advice", reference)
        self.assertIn("\n- Tenant safeguards could make", reference)
        self.assertIn("### Possible stakeholders to involve\n\n- Housing teams", reference)
        self.assertIn("\n- Tenant groups could represent", reference)
        self.assertIn("Tenants cannot afford efficient homes.", reference)
        self.assertIn("Fund local retrofit advice and tenant protections.", reference)
        self.assertIn("Tenant associations and municipal housing teams.", reference)
        self.assertNotIn("This proposal addresses", reference)
        self.assertIn("factsheet-source-tag", reference)
        self.assertEqual(reference.count('data-source-table='), 7)
        groups = reference.split("## Disadvantage groups to consider for this mitigation measure", 1)[1]
        self.assertIn("Low-income households", groups)
        self.assertNotIn('data-source-table=', groups)
        self.assertNotIn("**Summary:**", reference)
        self.assertNotIn("**Prioritised challenge addressed:**", reference)
        self.assertNotIn("**Policy description:**", reference)
        self.assertNotIn('"field": "Target population"', reference)
        self.assertNotIn("Use the factsheet structure", reference)
        self.assertNotIn("Relevant policy references", reference)
        self.assertNotIn("### Community retrofit service", reference)

    def test_new_policy_omits_source_fields_if_interpretation_is_unavailable(self) -> None:
        reference = country_factsheet_reference(
            self.db,
            country="Germany",
            sector="Housing",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
            proposal_type="new_policy",
        )
        self.assertNotIn("Fund local retrofit advice and tenant protections.", reference)
        self.assertNotIn("Tenants cannot afford efficient homes.", reference)

    def test_interpreter_synthesizes_fields_and_rejects_verbatim_output(self) -> None:
        fields = country_factsheet_inspiration_fields(
            self.db,
            country="Germany",
            sector="Housing",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
        )
        self.assertEqual(fields[0]["Policy description"], "Fund local retrofit advice and tenant protections.")
        service = ChatMitigationCreationGuidedMixin()
        service.db = self.db
        session = SimpleNamespace(
            mitigation_proposal_type="new_policy",
            country="Germany",
            sector="Housing",
            selected_context_policy="Reduce household energy costs",
            selected_hazard="Unaffordable heating",
            accepted_custom_hazard=None,
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value=(
                '{"challenge":["Renters face barriers to efficient homes."],'
                '"approach":["Local guidance could improve access.",'
                '"Safeguards could protect tenants."],'
                '"stakeholders":["Tenant groups could help deliver support."]}'
            )),
        ):
            interpretation = asyncio.run(service._interpret_new_policy_factsheet(session))
        self.assertEqual(set(interpretation), {"challenge", "approach", "stakeholders"})
        self.assertEqual(len(interpretation["approach"]), 2)
        self.assertIn("Local guidance", interpretation["approach"][0])
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value=(
                '{"challenge":["Renters face barriers to efficient homes."],'
                '"approach":["Fund local retrofit advice and tenant protections.",'
                '"Local guidance could improve access."],'
                '"stakeholders":["Tenant groups could help deliver support."]}'
            )),
        ):
            copied = asyncio.run(service._interpret_new_policy_factsheet(session))
        self.assertEqual(copied["approach"], ["Local guidance could improve access."])
        self.assertIn("challenge", copied)
        self.assertIn("stakeholders", copied)

    def test_inspiration_omits_factsheets_from_other_sectors(self) -> None:
        fields = country_factsheet_inspiration_fields(
            self.db,
            country="Germany",
            sector="Transport",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
        )
        self.assertEqual(fields, [])

    def test_inspiration_prompts_include_sectoral_objective(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        service.db = self.db
        session = SimpleNamespace(
            mitigation_proposal_type="new_policy",
            country="Germany",
            sector="Housing",
            selected_context_policy="Reduce household energy costs",
            selected_hazard="Unaffordable heating",
            accepted_custom_hazard=None,
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value='{"challenge":[],"approach":[],"stakeholders":[]}'),
        ) as ask_llm:
            asyncio.run(service._interpret_new_policy_factsheet(session))

        payload = json.loads(ask_llm.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(payload["sectoral_objective"], "Adaptation of housing to climate change")
        self.assertIn("green and/or digital", ask_llm.call_args.kwargs["context"])

    def test_other_considerations_prompt_filters_unrelated_excerpts(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value='{"summaries":[""]}'),
        ) as ask_llm:
            result = asyncio.run(service._summarize_other_considerations(
                [{"title": "Source", "content": "Housing adaptation context."}],
                selected_hazard="Unaffordable heating",
                sectoral_objective="Adaptation of housing to climate change",
            ))

        self.assertEqual(result, "")
        payload = json.loads(ask_llm.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(payload["sectoral_objective"], "Adaptation of housing to climate change")
        self.assertIn("empty string", ask_llm.call_args.kwargs["context"])

    def test_mechanism_planning_uses_sectoral_objective(self) -> None:
        service = ChatMitigationCreationGuidedMixin()
        service._mitigation_mechanism_policy_context = lambda session: ("", [])
        service._sector_prompt_rag_context = AsyncMock(return_value="")
        service._fallback_mitigation_mechanism_overview = lambda session, policies: {}
        session = ChatSession(
            sector="Energy",
            selected_hazard="High household electricity bills",
            selected_context_policy="Renewable electricity support",
        )
        with patch(
            "app.services.chat_mitigation_creation_guided.ask_llm_chat",
            new=AsyncMock(return_value="{}"),
        ) as ask_llm:
            asyncio.run(service._mitigation_mechanism_planning_overview(session))

        prompt = ask_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Sectoral objective: Transition towards renewable energy", prompt)
        self.assertIn("Twin transition focus", prompt)
        self.assertIn("Exclude generic financial or governance problems", ask_llm.call_args.kwargs["context"])

    def test_new_policy_source_table_omits_non_summary_fields(self) -> None:
        table = _new_policy_source_table(
            FACTSHEET_TEXT.split("POLICY FACTSHEETS FOR NEW POLICY PROPOSALS", 1)[1]
        )

        self.assertIn("Prioritised challenge addressed", table)
        self.assertIn("Policy description", table)
        self.assertIn("Potential risks/barriers", table)
        self.assertIn("Drivers/enablers", table)
        self.assertNotIn("Sectoral focus", table)
        self.assertNotIn("Policy type(s)", table)
        self.assertNotIn("Participatory dimension & stakeholders", table)
        self.assertNotIn("Target population", table)
        self.assertNotIn("Systemic focus", table)
        self.assertNotIn("Time horizon", table)
        self.assertNotIn("Feasibility & resources", table)


if __name__ == "__main__":
    unittest.main()
