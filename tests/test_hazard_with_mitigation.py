import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.hazard_with_mitigation import (
    _new_policy_source_table,
    country_factsheet_reference,
)


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

    def test_new_policy_flow_shows_summary_and_source_without_policy_title(self) -> None:
        reference = country_factsheet_reference(
            self.db,
            country="Germany",
            sector="Housing",
            selected_policy="Reduce household energy costs",
            hazard="Unaffordable heating",
            proposal_type="new_policy",
        )

        self.assertIn("## Inspiration for New policy proposal", reference)
        self.assertIn("Selected policy: Reduce household energy costs", reference)
        self.assertIn("Selected hazard: Unaffordable heating", reference)
        self.assertIn("Selected context: Germany, Berlin, Housing", reference)
        self.assertIn("Fund local retrofit advice and tenant protections.", reference)
        self.assertIn("Tenant associations and municipal housing teams.", reference)
        self.assertIn("Limited local delivery capacity.", reference)
        self.assertIn("Municipal funding and trusted tenant groups.", reference)
        self.assertIn("factsheet-source-tag", reference)
        self.assertIn("data-source-table", reference)
        self.assertNotIn("**Summary:**", reference)
        self.assertNotIn("**Prioritised challenge addressed:**", reference)
        self.assertNotIn("**Policy description:**", reference)
        self.assertNotIn('"field": "Target population"', reference)
        self.assertNotIn("Use the factsheet structure", reference)
        self.assertNotIn("Relevant policy references", reference)
        self.assertNotIn("### Community retrofit service", reference)

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
