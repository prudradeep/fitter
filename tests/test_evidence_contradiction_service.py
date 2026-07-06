import asyncio
import unittest
from unittest.mock import AsyncMock

from app.services.evidence_contradiction_service import (
    EvidenceContradictionService,
    _normalize_verdict,
)


def _run(coro):
    return asyncio.run(coro)


def _service_with(
    verdict: dict[str, object],
    concepts: dict[str, object] | None = None,
) -> EvidenceContradictionService:
    service = EvidenceContradictionService(None, None)
    service.extract_evidence_concepts = AsyncMock(
        return_value=concepts or {
            "sector": "Energy",
            "policy": "coal phase-out",
            "hazard": "higher household heating costs",
            "affected_group": "low-income households",
            "claimed_mechanism": "fuel switching and bill increases",
            "location": "Saxony, Germany",
            "expected_outcome": "increased energy poverty",
            "causal_chain": (
                "coal phase-out -> local price pressure -> higher heating costs "
                "-> low-income households"
            ),
        }
    )
    service.retrieve_core_kb_matches = AsyncMock(
        return_value=[
            {
                "title": "Core KB concept",
                "score": 0.91,
                "content": "Coal phase-out can raise transition costs for low-income households.",
            }
        ]
    )
    service.detect_contraindications = AsyncMock(return_value=verdict)
    return service


class EvidenceContradictionServiceTests(unittest.TestCase):
    def test_evidence_supports_claim(self):
        service = _service_with(
            {
                "verdict": "VALID",
                "confidence": 0.86,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": ["coal phase-out cost risk"],
                "matched_l2_concepts": ["household heating costs"],
                "reason": "L2 supports the same causal mechanism as L1.",
                "clarification_questions": [],
                "evidence_summary": "Evidence describes household bill increases.",
                "kb_support_summary": "L1 supports the transition cost pathway.",
            }
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text="Coal phase-out raises heating costs for low-income households.",
                evidence_text="Published evidence links coal phase-out to local heating bill increases.",
                sector="Energy",
                country="Germany",
                region="Saxony",
            )
        )

        self.assertEqual(result["verdict"], "VALID")
        self.assertFalse(result["contradiction_found"])

    def test_evidence_contradicts_core_kb(self):
        service = _service_with(
            {
                "verdict": "INVALID",
                "confidence": 0.9,
                "contradiction_found": True,
                "contraindication_found": False,
                "matched_l1_concepts": ["L1 says costs decrease"],
                "matched_l2_concepts": ["L2 says costs increase"],
                "reason": "L2 states the opposite expected outcome from L1.",
                "clarification_questions": [],
                "evidence_summary": "L2 claims price increases.",
                "kb_support_summary": "L1 states price decreases.",
            }
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text="Policy raises household heating costs.",
                evidence_text="Evidence claims the policy raises costs.",
                sector="Energy",
                country="Germany",
                region="Saxony",
            )
        )

        self.assertEqual(result["verdict"], "INVALID")
        self.assertTrue(result["contradiction_found"])

    def test_evidence_triggers_contraindication(self):
        service = _service_with(
            {
                "verdict": "INVALID",
                "confidence": 0.84,
                "contradiction_found": False,
                "contraindication_found": True,
                "matched_l1_concepts": ["retrofit without tenant protection contraindication"],
                "matched_l2_concepts": ["rent-increase retrofit subsidy"],
                "reason": "L1 contraindicates the proposed mitigation without tenant protection.",
                "clarification_questions": [],
                "evidence_summary": "L2 supports retrofit subsidy.",
                "kb_support_summary": "L1 warns of rent pass-through risk.",
            },
            concepts={
                "sector": "Housing",
                "policy": "retrofit subsidy",
                "hazard": "rent increases",
                "affected_group": "low-income tenants",
                "mitigation_measure": "retrofit subsidy",
                "claimed_mechanism": "rent pass-through risk",
                "location": "Berlin, Germany",
                "expected_outcome": "tenant displacement risk",
                "causal_chain": "retrofit subsidy -> rent pass-through -> tenant risk",
            },
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="mitigation",
                claim_text="Retrofit subsidy protects low-income tenants.",
                evidence_text="Evidence describes a retrofit subsidy with rent increases.",
                sector="Housing",
                country="Germany",
                region="Berlin",
            )
        )

        self.assertEqual(result["verdict"], "INVALID")
        self.assertTrue(result["contraindication_found"])

    def test_related_evidence_lacks_causal_mechanism(self):
        service = _service_with(
            {
                "verdict": "NEEDS_CLARIFICATION",
                "confidence": 0.42,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": ["EV policy transition pathway"],
                "matched_l2_concepts": ["EV adoption"],
                "reason": "Evidence supports the policy but not the claimed negative impact mechanism.",
                "clarification_questions": ["How does the policy create the claimed hazard?"],
                "evidence_summary": "L2 discusses EV adoption.",
                "kb_support_summary": "L1 needs a mechanism for distributional harm.",
            },
            concepts={
                "sector": "Transport",
                "policy": "EV adoption policy",
                "hazard": "transport affordability pressure",
                "affected_group": "rural non-car owners",
                "claimed_mechanism": "",
                "location": "Occitanie, France",
                "expected_outcome": "unclear distributional harm",
                "causal_chain": "EV adoption policy -> missing hazard mechanism",
            },
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text="EV policy harms rural non-car owners.",
                evidence_text="Evidence discusses EV adoption rates only.",
                sector="Transport",
                country="France",
                region="Occitanie",
            )
        )

        self.assertEqual(result["verdict"], "NEEDS_CLARIFICATION")
        self.assertIn("mechanism", result["reason"])

    def test_wrong_sector_returns_invalid(self):
        service = _service_with(
            {
                "verdict": "INVALID",
                "confidence": 0.77,
                "contradiction_found": True,
                "contraindication_found": False,
                "matched_l1_concepts": ["housing retrofit policy"],
                "matched_l2_concepts": ["transport fare subsidy"],
                "reason": "L2 evidence is about Transport, not the selected Housing policy.",
                "clarification_questions": [],
                "evidence_summary": "L2 describes transport fares.",
                "kb_support_summary": "L1 match concerns housing retrofit.",
            },
            concepts={
                "sector": "Transport",
                "policy": "fare subsidy",
                "hazard": "higher ticket costs",
                "affected_group": "commuters",
                "claimed_mechanism": "fare changes",
                "location": "Catalonia, Spain",
                "expected_outcome": "transport affordability pressure",
                "causal_chain": "fare policy -> ticket costs -> commuter impacts",
            },
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text="Housing retrofit policy raises rents.",
                evidence_text="Evidence describes transport fare subsidies.",
                sector="Housing",
                country="Spain",
                region="Catalonia",
            )
        )

        self.assertEqual(result["verdict"], "INVALID")
        self.assertIn("selected sector", result["reason"])

    def test_evidence_hazard_mismatch_returns_invalid_before_l1_detection(self):
        service = _service_with(
            {
                "verdict": "VALID",
                "confidence": 0.9,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": [],
                "matched_l2_concepts": [],
                "reason": "Should not be used.",
                "clarification_questions": [],
                "evidence_summary": "",
                "kb_support_summary": "",
            },
            concepts={
                "sector": "Energy",
                "policy": "energy efficiency retrofit",
                "hazard": "cold damp homes",
                "affected_group": "tenants",
                "claimed_mechanism": "poor insulation",
                "location": "Bavaria, Germany",
                "expected_outcome": "respiratory illness",
                "causal_chain": "poor insulation -> damp homes -> tenant illness",
            },
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text=(
                    "Claim type: hazard\n"
                    "Sector: Energy\n"
                    "Country: Germany\n"
                    "Region: Bavaria\n"
                    "Hazard: Heat stress\n"
                    "Reason: Heat waves increase cooling needs."
                ),
                evidence_text="Evidence discusses cold damp homes.",
                sector="Energy",
                country="Germany",
                region="Bavaria",
            )
        )

        self.assertEqual(result["verdict"], "INVALID")
        self.assertTrue(result["contradiction_found"])
        self.assertIn("provided hazard", result["reason"])
        service.detect_contraindications.assert_not_awaited()

    def test_mitigation_evidence_without_hazard_alignment_needs_clarification(self):
        service = _service_with(
            {
                "verdict": "VALID",
                "confidence": 0.9,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": [],
                "matched_l2_concepts": [],
                "reason": "Should not be used.",
                "clarification_questions": [],
                "evidence_summary": "",
                "kb_support_summary": "",
            },
            concepts={
                "sector": "Energy",
                "policy": "grant programme",
                "hazard": "",
                "affected_group": "low-income households",
                "mitigation_measure": "cooling grants",
                "claimed_mechanism": "subsidized cooling equipment",
                "location": "Bavaria, Germany",
                "expected_outcome": "lower exposure",
                "causal_chain": "grant programme -> cooling equipment -> missing hazard link",
            },
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="mitigation",
                claim_text=(
                    "Claim type: mitigation\n"
                    "Sector: Energy\n"
                    "Country: Germany\n"
                    "Region: Bavaria\n"
                    "Selected hazard: Heat stress\n"
                    "Mitigation measure: Cooling grants\n"
                    "Reason: Reduce heat exposure for vulnerable households."
                ),
                evidence_text="Evidence discusses cooling grants but does not identify heat stress.",
                sector="Energy",
                country="Germany",
                region="Bavaria",
            )
        )

        self.assertEqual(result["verdict"], "NEEDS_CLARIFICATION")
        self.assertIn("provided hazard", result["reason"])
        service.detect_contraindications.assert_not_awaited()

    def test_wrong_region_returns_needs_clarification(self):
        service = _service_with(
            {
                "verdict": "NEEDS_CLARIFICATION",
                "confidence": 0.51,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": ["Germany regional evidence requirement"],
                "matched_l2_concepts": ["evidence from Italy"],
                "reason": "L2 evidence refers to a different country/region.",
                "clarification_questions": ["Provide evidence for Germany or Saxony."],
                "evidence_summary": "L2 is Italy-specific.",
                "kb_support_summary": "L1 match is Germany-specific.",
            }
        )

        result = _run(
            service.validate_evidence_against_kb(
                claim_type="hazard",
                claim_text="Saxony household heating cost shock.",
                evidence_text="Evidence describes a similar Italian region.",
                sector="Energy",
                country="Germany",
                region="Saxony",
            )
        )

        self.assertEqual(result["verdict"], "NEEDS_CLARIFICATION")
        self.assertIn("different country", result["reason"])

    def test_valid_verdict_without_core_concept_match_needs_clarification(self):
        result = _normalize_verdict(
            {
                "verdict": "VALID",
                "confidence": 0.8,
                "contradiction_found": False,
                "contraindication_found": False,
                "matched_l1_concepts": [],
                "matched_l2_concepts": ["L2 heat stress concept"],
                "reason": "The evidence appears relevant but no L1 concept was matched.",
                "clarification_questions": [],
                "evidence_summary": "",
                "kb_support_summary": "",
            },
            fallback_l2={"hazard": "heat stress"},
            fallback_l1=[],
        )

        self.assertEqual(result["verdict"], "NEEDS_CLARIFICATION")


if __name__ == "__main__":
    unittest.main()
