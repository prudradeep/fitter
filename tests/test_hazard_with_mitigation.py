import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.hazard_with_mitigation import (
    _new_policy_source_table,
    country_factsheet_inspiration_fields,
    country_factsheet_reference,
)
from app.services.chat_mitigation_creation_guided import ChatMitigationCreationGuidedMixin


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

    def test_new_policy_flow_shows_interpretation_without_source_excerpt(self) -> None:
        reference = country_factsheet_reference(
            self.db,
            country="Germany",
            region="Berlin",
            sector="Housing",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
            proposal_type="new_policy",
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
        self.assertIn("**Selected policy**: Reduce household energy costs", reference)
        self.assertIn("**Selected hazard**: Unaffordable heating", reference)
        self.assertIn("**Selected context**: Germany, Berlin, Housing", reference)
        self.assertIn("# Important concepts for your new proposal", reference)
        self.assertIn("### Challenges to be addressed", reference)
        self.assertIn("### Possible ways to address the challenges", reference)
        self.assertIn("### Possible stakeholders to involve", reference)
        self.assertIn("### Challenges to be addressed\n\n- Renters may struggle", reference)
        self.assertIn("\n- Rising costs can make", reference)
        self.assertIn("### Possible ways to address the challenges\n\n- Local advice", reference)
        self.assertIn("\n- Tenant safeguards could make", reference)
        self.assertIn("### Possible stakeholders to involve\n\n- Housing teams", reference)
        self.assertIn("\n- Tenant groups could represent", reference)
        self.assertNotIn("Tenants cannot afford efficient homes.", reference)
        self.assertNotIn("Fund local retrofit advice and tenant protections.", reference)
        self.assertNotIn("Tenant associations and municipal housing teams.", reference)
        self.assertNotIn("This proposal addresses", reference)
        self.assertNotIn("factsheet-source-tag", reference)
        self.assertNotIn("data-source-table", reference)
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
