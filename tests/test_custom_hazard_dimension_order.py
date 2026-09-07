import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from app.services import custom_hazard_validation as validator
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


async def _unavailable(*args, **kwargs):
    return "LLM unavailable"


class CustomHazardDimensionOrderTests(unittest.TestCase):
    def test_dimension_checks_and_status_cards_use_requested_order(self) -> None:
        expected_keys = [
            "policy_objective_fit",
            "twin_transition_policy_fit",
            "hazard_definition_fit",
        ]
        self.assertEqual(list(validator.DIMENSION_WEIGHTS)[:3], expected_keys)
        self.assertEqual(list(validator.CRITICAL_DIMENSIONS)[:3], expected_keys)

        dimension_scores = {
            key: {"score": 8, "needs_clarification": False}
            for key in validator.DIMENSION_WEIGHTS
        }
        cards = validator.build_custom_hazard_grounding_status(
            {"dimension_scores": dimension_scores}
        )
        self.assertEqual(
            [card["title"] for card in cards[:3]],
            [
                "Policy Objective Fit",
                "Twin transition policy fit",
                "Hazard definition",
            ],
        )

    def test_hazard_definition_clarification_follows_objective_and_transition_fit(self) -> None:
        state = {
            "selected_sector": "Transport",
            "validation_mode": "strict",
            "dimension_scores": {
                key: {"score": 8, "needs_clarification": False}
                for key in validator.DIMENSION_WEIGHTS
            },
        }
        state["dimension_scores"]["policy_objective_fit"] = {
            "score": 3,
            "needs_clarification": True,
        }
        state["dimension_scores"]["twin_transition_policy_fit"] = {
            "score": 3,
            "needs_clarification": True,
        }
        state["dimension_scores"]["hazard_definition_fit"] = {
            "score": 3,
            "needs_clarification": True,
        }

        first_details = ChatService._custom_hazard_missing_dimension_details(state)
        self.assertEqual(
            [detail[0] for detail in first_details],
            ["Policy Objective Fit"],
        )

        for key in ("policy_objective_fit", "twin_transition_policy_fit"):
            state["dimension_scores"][key] = {
                "score": 8,
                "needs_clarification": False,
            }
        next_details = ChatService._custom_hazard_missing_dimension_details(state)
        self.assertEqual(next_details[0][0], "Hazard definition")

    def test_twin_transition_fit_requires_and_uses_policy_document_content(self) -> None:
        hazard = "Electric vehicle charging rules increase costs for rural taxi drivers."
        with patch.object(validator, "ask_llm_chat", _unavailable):
            without_reference = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    hazard,
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    None,
                )
            )
            with_reference = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    hazard,
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    None,
                    policy_reference_context=(
                        "The electric vehicle policy requires charging infrastructure "
                        "and electrification of taxi fleets."
                    ),
                )
            )

        missing = without_reference["dimension_scores"]["twin_transition_policy_fit"]
        matched = with_reference["dimension_scores"]["twin_transition_policy_fit"]
        self.assertEqual(missing["score"], 0)
        self.assertIn("policy document", missing["clarification_question"].lower())
        self.assertGreaterEqual(matched["score"], 7)
        self.assertIn("policy reference", matched["reason"].lower())
        self.assertIn("charging", matched["causal_linkage"].lower())

    def test_country_fit_does_not_require_region_in_hazard_text(self) -> None:
        hazard = "Electric vehicle charging rules increase costs for rural taxi drivers."
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    hazard,
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    None,
                    policy_reference_context=(
                        "The electric vehicle policy requires charging infrastructure."
                    ),
                )
            )

        country_fit = result["dimension_scores"]["country_region_fit"]
        self.assertGreaterEqual(country_fit["score"], 7)
        self.assertFalse(country_fit["needs_clarification"])
        self.assertNotIn("region", country_fit["reason"].lower())

    def test_policy_causal_linkage_is_shown_once_after_document_analysis(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            session_key="policy-session",
            sector="Transport",
            custom_hazard={
                "policy_reference_available": True,
                "show_policy_hazard_causal_linkage": True,
                "dimension_scores": {
                    "twin_transition_policy_fit": {
                        "score": 8,
                        "needs_clarification": False,
                        "causal_linkage": (
                            "Charging-infrastructure requirements increase fleet conversion "
                            "costs, raising costs for rural taxi drivers."
                        ),
                    }
                },
            },
        )

        first = service._custom_hazard_response(
            session_id="policy-session",
            session=session,
            step="custom_hazard_review",
            bot_message="<p>Continue reviewing the hazard.</p>",
            options=[],
        )
        second = service._custom_hazard_response(
            session_id="policy-session",
            session=session,
            step="custom_hazard_review",
            bot_message="<p>Continue reviewing the hazard.</p>",
            options=[],
        )

        self.assertIn("Causal linkage to the provided policy document", first.bot_message)
        self.assertIn("Charging-infrastructure requirements", first.bot_message)
        self.assertNotIn("Causal linkage to the provided policy document", second.bot_message)

    def test_evidence_and_policy_linkages_are_derived_from_document_content(self) -> None:
        hazard = "Charging mandates increase fleet costs for rural taxi drivers."
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    hazard,
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    None,
                    policy_reference_context=(
                        "The electric vehicle policy requires charging infrastructure."
                    ),
                    evidence_context=(
                        "A survey found electric vehicle charging mandates increased fleet "
                        "costs for rural taxi drivers."
                    ),
                )
            )

        analysis = result["linkage_analysis"]
        self.assertTrue(analysis["evidence_hazard_linkage"]["supported"])
        self.assertTrue(analysis["policy_evidence_linkage"]["supported"])
        self.assertIn(
            "evidence finding",
            analysis["evidence_hazard_linkage"]["causal_linkage"].lower(),
        )

    def test_unrelated_evidence_does_not_create_causal_linkages(self) -> None:
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    "Charging mandates increase fleet costs for rural taxi drivers.",
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    None,
                    policy_reference_context=(
                        "The electric vehicle policy requires charging infrastructure."
                    ),
                    evidence_context="A rainfall report describes wildfire recovery in forests.",
                )
            )

        analysis = result["linkage_analysis"]
        self.assertFalse(analysis["evidence_hazard_linkage"]["supported"])
        self.assertFalse(analysis["policy_evidence_linkage"]["supported"])

    def test_evidence_linkage_results_are_shown_once(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            custom_hazard={
                "show_evidence_linkages": True,
                "linkage_analysis": {
                    "evidence_hazard_linkage": {
                        "supported": True,
                        "causal_linkage": "Survey finding -> increased costs -> hazard",
                        "reason": "Supported.",
                    },
                    "policy_evidence_linkage": {
                        "supported": False,
                        "causal_linkage": "",
                        "reason": "The documents describe different mechanisms.",
                    },
                },
            }
        )

        first = service._custom_hazard_response(
            session_id="session-1",
            session=session,
            step="custom_hazard_review",
            bot_message="<p>Continue.</p>",
            options=[],
        )
        second = service._custom_hazard_response(
            session_id="session-1",
            session=session,
            step="custom_hazard_review",
            bot_message="<p>Continue.</p>",
            options=[],
        )

        self.assertIn("Evidence-to-hazard linkage", first.bot_message)
        self.assertIn("Survey finding", first.bot_message)
        self.assertIn("Policy-to-evidence causal linkage", first.bot_message)
        self.assertIn("No supported causal linkage found", first.bot_message)
        self.assertNotIn("Evidence-to-hazard linkage", second.bot_message)

    def test_unsupported_policy_fit_does_not_show_a_causal_linkage(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            session_key="policy-session",
            custom_hazard={
                "policy_reference_available": True,
                "show_policy_hazard_causal_linkage": True,
                "dimension_scores": {
                    "twin_transition_policy_fit": {
                        "score": 4,
                        "needs_clarification": True,
                        "causal_linkage": "An unverified relationship.",
                    }
                },
            },
        )

        response = service._custom_hazard_response(
            session_id="policy-session",
            session=session,
            step="custom_hazard_clarification",
            bot_message="<p>No supported linkage was found.</p>",
            options=[],
        )

        self.assertNotIn("Causal linkage to the provided policy document", response.bot_message)

    def test_policy_reference_step_is_separate_from_evidence(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            sector="Transport",
            custom_hazard={
                "validation_mode": "strict",
                "dimension_scores": {
                    "policy_objective_fit": {"score": 8, "needs_clarification": False},
                    "twin_transition_policy_fit": {"score": 0, "needs_clarification": True},
                },
            },
        )

        response = service._custom_hazard_clarification_step("policy-session", session)

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertIn("policy reference only", response.bot_message.lower())
        self.assertIn("not be treated as evidence", response.bot_message.lower())

    def test_policy_reference_submission_does_not_populate_hazard_evidence(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(
            return_value="The policy requires electric vehicle charging infrastructure."
        )
        expected_response = object()
        service._run_custom_hazard_dimension_check = AsyncMock(
            return_value=expected_response
        )
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            sector="Transport",
            custom_hazard={"awaiting_policy_reference": True},
        )

        response = asyncio.run(
            service._handle_custom_hazard_clarification(
                "policy-session",
                session,
                "Policy reference file: transport-policy.txt\n"
                "Policy reference document ID: policy-doc-1",
            )
        )

        self.assertIs(response, expected_response)
        self.assertEqual(session.custom_hazard["policy_reference"], "transport-policy.txt")
        self.assertNotIn("evidence", session.custom_hazard)
        self.assertIsNone(session.pending_hazard_evidence)


if __name__ == "__main__":
    unittest.main()
