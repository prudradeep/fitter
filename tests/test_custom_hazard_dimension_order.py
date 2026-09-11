import unittest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import custom_hazard_validation as validator
from app.schemas import ChatResponse
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession


async def _unavailable(*args, **kwargs):
    return "LLM unavailable"


class CustomHazardDimensionOrderTests(unittest.TestCase):
    def test_policy_reference_relevance_fallback_accepts_transition_policy(self) -> None:
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_policy_reference_twin_transition(
                    "The national strategy mandates renewable energy targets."
                )
            )

        self.assertIsNotNone(result)
        self.assertTrue(result["related"])

    def test_policy_reference_relevance_fallback_rejects_unrelated_content(self) -> None:
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_policy_reference_twin_transition(
                    "This restaurant menu lists desserts and opening hours."
                )
            )

        self.assertIsNotNone(result)
        self.assertFalse(result["related"])

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

    def test_staged_validation_checks_only_the_requested_dimension(self) -> None:
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
                    dimensions_to_validate=("policy_objective_fit",),
                )
            )

        dimensions = result["dimension_scores"]
        self.assertGreaterEqual(dimensions["policy_objective_fit"]["score"], 7)
        for key in validator.DIMENSION_SEQUENCE[1:]:
            self.assertEqual(dimensions[key]["status"], "DEFERRED")
            self.assertFalse(dimensions[key]["needs_clarification"])

        cards = validator.build_custom_hazard_grounding_status(result)
        self.assertEqual(cards[1]["status"], "DEFERRED")
        self.assertIsNone(cards[1]["score"])

    def test_later_staged_validation_preserves_supported_objective(self) -> None:
        hazard = "Electric vehicle charging rules increase costs for rural taxi drivers."
        state = validator.default_custom_hazard_state()
        state["dimension_scores"] = {
            "policy_objective_fit": {
                "score": 8,
                "reason": "Supported objective fit.",
                "confidence": "high",
                "needs_clarification": False,
                "clarification_question": "",
            }
        }
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    hazard,
                    "Transport",
                    "Italy",
                    "Calabria",
                    [],
                    state,
                    policy_reference_context=(
                        "The policy requires electric vehicle charging infrastructure."
                    ),
                    dimensions_to_validate=("twin_transition_policy_fit",),
                )
            )

        dimensions = result["dimension_scores"]
        self.assertEqual(
            dimensions["policy_objective_fit"]["reason"],
            "Supported objective fit.",
        )
        self.assertGreaterEqual(dimensions["twin_transition_policy_fit"]["score"], 7)
        self.assertEqual(dimensions["hazard_definition_fit"]["status"], "DEFERRED")

    def test_evidence_pass_cannot_downgrade_supported_dimensions(self) -> None:
        state = validator.default_custom_hazard_state()
        state["dimension_scores"] = {
            key: {
                "score": 8,
                "status": "SUPPORTED",
                "reason": f"Established support for {key}.",
                "confidence": "high",
                "needs_clarification": False,
                "clarification_question": "",
            }
            for key in validator.DIMENSION_SEQUENCE
        }
        downgraded_result = {
            "dimension_scores": {
                key: {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": f"Second-pass doubt about {key}.",
                    "confidence": "low",
                    "needs_clarification": True,
                    "clarification_question": f"Clarify {key}.",
                }
                for key in validator.DIMENSION_SEQUENCE
            },
            "linkage_analysis": {
                "evidence_hazard_linkage": {
                    "supported": True,
                    "relationship": "The evidence finding supports the adverse impact in the hazard.",
                    "reason": "The evidence supports the hazard.",
                },
                "policy_evidence_linkage": {
                    "supported": True,
                    "causal_linkage": "Policy provision -> mechanism -> evidence finding",
                    "reason": "The policy and evidence share a mechanism.",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        with patch.object(
            validator,
            "_llm_dimension_validation",
            AsyncMock(return_value=downgraded_result),
        ):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    "Renewable-energy funding gaps increase costs for women.",
                    "Energy",
                    "Germany",
                    "Berlin",
                    [],
                    state,
                    policy_reference_context=(
                        "The renewable energy policy establishes transition funding."
                    ),
                    evidence_context=(
                        "A report finds unequal transition-funding access for women."
                    ),
                    dimensions_to_validate=validator.DIMENSION_SEQUENCE,
                )
            )

        for key in validator.DIMENSION_SEQUENCE:
            self.assertEqual(result["dimension_scores"][key], state["dimension_scores"][key])
        self.assertTrue(result["linkage_analysis"]["evidence_hazard_linkage"]["supported"])
        self.assertNotEqual(result["next_action"], "ask_clarification")

    def test_staged_validation_can_still_update_an_unresolved_dimension(self) -> None:
        state = validator.default_custom_hazard_state()
        state["dimension_scores"]["policy_objective_fit"] = {
            "score": 4,
            "status": "NEEDS CLARIFICATION",
            "reason": "The objective link is unresolved.",
            "needs_clarification": True,
            "clarification_question": "How is it linked?",
        }

        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    "Renewable energy transition costs increase household burdens.",
                    "Energy",
                    "Germany",
                    "Berlin",
                    [],
                    state,
                    dimensions_to_validate=("policy_objective_fit",),
                )
            )

        updated = result["dimension_scores"]["policy_objective_fit"]
        self.assertGreaterEqual(updated["score"], 7)
        self.assertFalse(updated["needs_clarification"])

    def test_flow_checks_objective_only_then_requests_policy_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(return_value="")
        session = ChatSession(
            session_key="staged-session",
            phase="custom_hazard_dimension_check",
            sector="Transport",
            country="Italy",
            region="Calabria",
            custom_hazard=validator.default_custom_hazard_state(),
        )
        session.pending_hazard = (
            "Electric vehicle charging rules increase costs for rural taxi drivers."
        )
        session.custom_hazard["resolved_hazard_text"] = session.pending_hazard
        objective_result = {
            "overall_score": 12,
            "dimension_scores": {
                "policy_objective_fit": {
                    "score": 8,
                    "reason": "The hazard is compatible with vehicle electrification.",
                    "confidence": "high",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                **{
                    key: validator._deferred_dimension()
                    for key in validator.DIMENSION_SEQUENCE[1:]
                },
            },
            "linkage_analysis": {},
            "affected_groups": [],
            "duplicate_candidates": [],
            "next_action": "ask_clarification",
            "status": "needs_clarification",
        }

        with patch(
            "app.services.chat_custom_hazard_grounding.validate_custom_hazard_dimensions",
            AsyncMock(return_value=objective_result),
        ) as dimension_check:
            response = asyncio.run(
                service._run_custom_hazard_dimension_check("staged-session", session)
            )

        self.assertEqual(
            dimension_check.await_args.kwargs["dimensions_to_validate"],
            ("policy_objective_fit",),
        )
        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertTrue(session.custom_hazard["awaiting_policy_reference"])

    def test_flow_checks_definition_sector_and_location_in_one_stage(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(
            return_value="The policy requires electric vehicle charging infrastructure."
        )
        expected_response = object()
        service._route_custom_hazard_next_action = AsyncMock(
            return_value=expected_response
        )
        state = validator.default_custom_hazard_state()
        state["dimension_scores"] = {
            "policy_objective_fit": {
                "score": 9,
                "status": "SUPPORTED",
                "needs_clarification": False,
            },
            "twin_transition_policy_fit": {
                "score": 8,
                "status": "SUPPORTED",
                "needs_clarification": False,
            },
            **{
                key: validator._deferred_dimension()
                for key in validator.DIMENSION_SEQUENCE[2:]
            },
        }
        session = ChatSession(
            session_key="grouped-stage-session",
            phase="custom_hazard_dimension_check",
            sector="Transport",
            country="Italy",
            region="Calabria",
            custom_hazard=state,
        )
        session.pending_hazard = (
            "Charging mandates increase costs for rural taxi drivers."
        )
        grouped_result = {
            "overall_score": 50,
            "dimension_scores": {
                **state["dimension_scores"],
                "hazard_definition_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The harm needs a more precise definition.",
                    "needs_clarification": True,
                    "clarification_question": "What specific harm occurs?",
                },
                "selected_sector_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The transport link needs clarification.",
                    "needs_clarification": True,
                    "clarification_question": "How is this linked to transport?",
                },
                "country_region_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The geographic relevance needs clarification.",
                    "needs_clarification": True,
                    "clarification_question": "Why is this relevant in Calabria?",
                },
            },
            "linkage_analysis": {},
            "affected_groups": [],
            "duplicate_candidates": [],
            "next_action": "ask_clarification",
            "status": "needs_clarification",
        }

        with patch(
            "app.services.chat_custom_hazard_grounding.validate_custom_hazard_dimensions",
            AsyncMock(return_value=grouped_result),
        ) as dimension_check:
            response = asyncio.run(
                service._run_custom_hazard_dimension_check(
                    "grouped-stage-session",
                    session,
                )
            )

        self.assertIs(response, expected_response)
        self.assertEqual(dimension_check.await_count, 1)
        self.assertEqual(
            dimension_check.await_args.kwargs["dimensions_to_validate"],
            (
                "hazard_definition_fit",
                "selected_sector_fit",
                "country_region_fit",
            ),
        )
        self.assertEqual(
            session.custom_hazard["active_validation_dimensions"],
            [
                "hazard_definition_fit",
                "selected_sector_fit",
                "country_region_fit",
            ],
        )

    def test_grouped_stage_shows_all_unresolved_questions_together(self) -> None:
        state = {
            "selected_sector": "Transport",
            "validation_mode": "strict",
            "active_validation_dimension": "hazard_definition_fit",
            "active_validation_dimensions": [
                "hazard_definition_fit",
                "selected_sector_fit",
                "country_region_fit",
            ],
            "dimension_scores": {
                "hazard_definition_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The harm is unclear.",
                    "needs_clarification": True,
                    "clarification_question": "What specific harm occurs?",
                },
                "selected_sector_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The sector relationship is unclear.",
                    "needs_clarification": True,
                    "clarification_question": "How does this affect transport?",
                },
                "country_region_fit": {
                    "score": 4,
                    "status": "NEEDS CLARIFICATION",
                    "reason": "The location relationship is unclear.",
                    "needs_clarification": True,
                    "clarification_question": "Why is this relevant in Calabria?",
                },
            },
        }

        details = ChatService._custom_hazard_missing_dimension_details(state)

        self.assertEqual(
            [detail[0] for detail in details],
            ["Hazard definition", "Sector fit", "Country / region fit"],
        )
        self.assertEqual(len(details), 3)

    def test_objective_stage_ignores_policy_reference_requests_from_llm(self) -> None:
        captured: dict[str, object] = {}

        async def contaminated_objective_response(*args, **kwargs):
            captured["context"] = kwargs["context"]
            captured["message"] = kwargs["messages"][0]["content"]
            return json.dumps(
                {
                    "dimension_scores": {
                        "policy_objective_fit": {
                            "score": 0,
                            "reason": (
                                "No policy reference content provided to evaluate "
                                "causal linkage."
                            ),
                            "confidence": "low",
                            "needs_clarification": True,
                            "clarification_question": (
                                "Please provide the URL or file path for the policy document."
                            ),
                        }
                    },
                    "affected_groups": [],
                    "duplicate_candidates": [],
                }
            )

        with patch.object(
            validator,
            "ask_llm_chat",
            contaminated_objective_response,
        ):
            result = asyncio.run(
                validator.validate_custom_hazard_dimensions(
                    (
                        "Female-led households face a capital-access gap for home "
                        "insulation and heat pumps."
                    ),
                    "Housing",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                    dimensions_to_validate=("policy_objective_fit",),
                )
            )

        objective = result["dimension_scores"]["policy_objective_fit"]
        self.assertGreaterEqual(objective["score"], 7)
        self.assertNotIn("policy reference", objective["reason"].casefold())
        self.assertNotIn("file path", objective["clarification_question"].casefold())
        self.assertNotIn("policy_reference_content", str(captured["message"]))
        self.assertNotIn("evidence_content", str(captured["message"]))
        self.assertIn(
            "No policy document, policy reference, evidence source, URL, upload, or file path is required.",
            str(captured["context"]),
        )

    def test_grounding_status_remains_visible_through_custom_hazard_flow(self) -> None:
        service = ChatService.__new__(ChatService)
        service._ensure_user_session = MagicMock()
        service._record_chat_message = MagicMock()
        state = validator.default_custom_hazard_state()
        state["dimension_scores"] = {
            "policy_objective_fit": {
                "score": 8,
                "reason": "The policy objective fit is supported.",
                "confidence": "high",
                "needs_clarification": False,
                "clarification_question": "",
            },
            **{
                key: validator._deferred_dimension()
                for key in validator.DIMENSION_SEQUENCE[1:]
            },
        }
        session = ChatSession(
            session_key="grounding-status-session",
            custom_hazard=state,
        )

        for step in (
            "custom_hazard_policy_reference",
            "custom_hazard_evidence_decision",
            "custom_hazard_group_review",
            "hazard_population_region_comparison_result",
            "custom_hazard_summary_review",
        ):
            with self.subTest(step=step):
                response = ChatResponse(
                    session_id="grounding-status-session",
                    step=step,
                    bot_message="Continue the custom-hazard flow.",
                    session=session.summary(),
                )
                service._finalize_chat_response(
                    "grounding-status-session",
                    session,
                    response,
                )

                self.assertIsNotNone(response.validation_details)
                self.assertTrue(response.custom_hazard_grounding_status)
                self.assertEqual(
                    response.custom_hazard_grounding_status[0]["title"],
                    "Policy Objective Fit",
                )

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
        self.assertIn("<ul>", first.bot_message)
        self.assertIn("<li>", first.bot_message)
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
            "supports the stated hazard",
            analysis["evidence_hazard_linkage"]["relationship"].lower(),
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
                        "relationship": "The survey documents the increased costs described by the hazard.",
                        "reason": "The evidence is materially relevant.",
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
        self.assertIn("survey documents", first.bot_message)
        self.assertNotIn("No supported causal linkage found", first.bot_message)
        self.assertNotIn("causal linkage", first.bot_message.lower())
        self.assertGreaterEqual(first.bot_message.count("<ul>"), 1)
        self.assertGreaterEqual(first.bot_message.count("<li>"), 1)
        self.assertNotIn("Evidence-to-hazard linkage", second.bot_message)

    def test_supported_evidence_without_relationship_uses_relevance_reason(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            custom_hazard={
                "show_evidence_linkages": True,
                "linkage_analysis": {
                    "evidence_hazard_linkage": {
                        "supported": True,
                        "relationship": "",
                        "reason": "The report materially supports the stated hazard.",
                    }
                },
            }
        )

        response = service._custom_hazard_response(
            session_id="session-1",
            session=session,
            step="custom_hazard_mechanism_confirmation",
            bot_message="<p>Continue.</p>",
            options=[],
        )

        self.assertIn("materially supports", response.bot_message)
        self.assertNotIn("causal linkage", response.bot_message.lower())
        self.assertNotIn("not established", response.bot_message.lower())

    def test_linkage_summary_is_limited_to_short_bullets(self) -> None:
        summary = ChatService._short_linkage_bullets(
            "First causal point. Second causal point. Third causal point. "
            "A fourth point that should not be shown."
        )

        self.assertEqual(summary.count("\n"), 2)
        self.assertIn("- First causal point.", summary)
        self.assertIn("- Third causal point.", summary)
        self.assertNotIn("fourth point", summary.lower())

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

    def test_failed_policy_linkage_offers_replacement_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            sector="Transport",
            custom_hazard={
                "active_validation_dimension": "twin_transition_policy_fit",
                "policy_reference_available": True,
                "dimension_scores": {
                    "policy_objective_fit": {
                        "score": 8,
                        "needs_clarification": False,
                    },
                    "twin_transition_policy_fit": {
                        "score": 4,
                        "needs_clarification": True,
                        "reason": "The supplied policy has no clear causal linkage.",
                        "clarification_question": (
                            "How do its specific provisions cause this hazard?"
                        ),
                    },
                },
            },
        )

        response = service._custom_hazard_clarification_step(
            "policy-session",
            session,
        )

        self.assertIn(
            "Add a Policy Reference",
            [option.label for option in response.options],
        )
        self.assertIn("replace the current document", response.bot_message)

    def test_policy_linkage_text_clarification_rechecks_current_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        expected_response = object()
        service._run_custom_hazard_dimension_check = AsyncMock(
            return_value=expected_response
        )
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={
                "active_validation_dimension": "twin_transition_policy_fit",
                "policy_reference_available": True,
                "pending_clarification_questions": [
                    "How do the policy provisions cause this hazard?"
                ],
            },
        )

        response = asyncio.run(
            service._handle_custom_hazard_clarification(
                "policy-session",
                session,
                "The charging mandate raises fleet conversion costs.",
            )
        )

        self.assertIs(response, expected_response)
        clarification = session.custom_hazard["clarifications"][0]
        self.assertEqual(
            clarification["dimension"],
            "twin_transition_policy_fit",
        )
        self.assertFalse(session.custom_hazard.get("replacing_policy_reference"))

    def test_add_policy_reference_option_opens_replacement_input(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={
                "active_validation_dimension": "twin_transition_policy_fit",
                "policy_reference_available": True,
                "policy_reference": "old-policy.txt",
                "policy_reference_document_ids": ["old-document"],
            },
        )

        response = asyncio.run(
            service._handle_custom_hazard_clarification(
                "policy-session",
                session,
                "Add a Policy Reference",
            )
        )

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertTrue(session.custom_hazard["awaiting_policy_reference"])
        self.assertTrue(session.custom_hazard["replacing_policy_reference"])
        self.assertEqual(
            session.custom_hazard["policy_reference_document_ids"],
            ["old-document"],
        )
        self.assertIn("discarded after", response.bot_message)

    def test_successful_policy_replacement_discards_old_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(
            return_value="The replacement policy requires charging infrastructure."
        )
        discarded_document_ids: list[str] = []
        service._discard_temporary_policy_references = MagicMock(
            side_effect=lambda current: discarded_document_ids.extend(
                current.custom_hazard["policy_reference_document_ids"]
            )
        )
        expected_response = object()
        service._run_custom_hazard_dimension_check = AsyncMock(
            return_value=expected_response
        )
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={
                "active_validation_dimension": "twin_transition_policy_fit",
                "awaiting_policy_reference": True,
                "replacing_policy_reference": True,
                "policy_reference_available": True,
                "policy_reference": "old-policy.txt",
                "policy_reference_document_ids": ["old-document"],
                "reason": "The old policy provisions raise costs.",
                "clarifications": [
                    {
                        "dimension": "twin_transition_policy_fit",
                        "questions": ["How do the specific provisions cause harm?"],
                        "answer": "The old policy provisions raise costs.",
                    }
                ],
                "dimension_scores": {
                    "policy_objective_fit": {
                        "score": 8,
                        "needs_clarification": False,
                    },
                    "twin_transition_policy_fit": {
                        "score": 4,
                        "needs_clarification": True,
                    },
                },
            },
        )

        with patch(
            "app.services.chat_custom_hazard_input.validate_policy_reference_twin_transition",
            AsyncMock(return_value={"related": True, "reason": "Relevant policy."}),
        ):
            response = asyncio.run(
                service._handle_custom_hazard_clarification(
                    "policy-session",
                    session,
                    "Policy reference file: replacement-policy.txt\n"
                    "Policy reference document ID: new-document",
                )
            )

        self.assertIs(response, expected_response)
        self.assertEqual(discarded_document_ids, ["old-document"])
        self.assertEqual(
            session.custom_hazard["policy_reference_document_ids"],
            ["new-document"],
        )
        self.assertEqual(
            session.custom_hazard["policy_reference"],
            "replacement-policy.txt",
        )
        self.assertNotIn(
            "twin_transition_policy_fit",
            session.custom_hazard["dimension_scores"],
        )
        self.assertEqual(session.custom_hazard["clarifications"], [])
        self.assertEqual(session.custom_hazard["reason"], "")
        self.assertFalse(session.custom_hazard["replacing_policy_reference"])

    def test_unreadable_policy_replacement_retains_old_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(return_value="")
        service._discard_temporary_policy_references = MagicMock()
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={
                "active_validation_dimension": "twin_transition_policy_fit",
                "awaiting_policy_reference": True,
                "replacing_policy_reference": True,
                "policy_reference_available": True,
                "policy_reference": "old-policy.txt",
                "policy_reference_document_ids": ["old-document"],
            },
        )

        response = asyncio.run(
            service._handle_custom_hazard_clarification(
                "policy-session",
                session,
                "Policy reference file: unreadable.txt\n"
                "Policy reference document ID: unreadable-document",
            )
        )

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        service._discard_temporary_policy_references.assert_not_called()
        self.assertEqual(session.custom_hazard["policy_reference"], "old-policy.txt")
        self.assertEqual(
            session.custom_hazard["policy_reference_document_ids"],
            ["old-document"],
        )
        self.assertTrue(session.custom_hazard["replacing_policy_reference"])

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

        with patch(
            "app.services.chat_custom_hazard_input.validate_policy_reference_twin_transition",
            AsyncMock(return_value={"related": True, "reason": "Relevant policy."}),
        ):
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

    def test_unrelated_policy_reference_is_retained_for_relevance_clarification(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(
            return_value="A restaurant menu lists desserts and opening hours."
        )
        discarded_document_ids: list[str] = []
        service._discard_temporary_policy_references = MagicMock(
            side_effect=lambda current: discarded_document_ids.extend(
                current.custom_hazard["policy_reference_document_ids"]
            )
        )
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={"awaiting_policy_reference": True},
        )

        with patch(
            "app.services.chat_custom_hazard_input.validate_policy_reference_twin_transition",
            AsyncMock(return_value={"related": False, "reason": "Unrelated content."}),
        ):
            response = asyncio.run(
                service._handle_custom_hazard_clarification(
                    "policy-session",
                    session,
                    "Policy reference URL: https://example.test/menu\n"
                    "Policy reference document ID: unrelated-document",
                )
            )

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(response.input_mode, "policy_reference")
        self.assertTrue(response.error)
        self.assertIn("does not appear to be related", response.bot_message)
        self.assertEqual(discarded_document_ids, [])
        self.assertEqual(
            session.custom_hazard["pending_policy_reference_document_ids"],
            ["unrelated-document"],
        )
        self.assertEqual(session.custom_hazard.get("policy_reference_document_ids", []), [])
        self.assertFalse(session.custom_hazard.get("policy_reference_available", False))
        self.assertTrue(session.custom_hazard["awaiting_policy_reference"])
        self.assertEqual(
            [option.label for option in response.options],
            [
                "Go back to list of hazards",
                "Clarify the relevance",
                "Provide policy again",
                "Revise mechanism",
            ],
        )

    def test_unrelated_replacement_preserves_existing_policy_reference(self) -> None:
        service = ChatService.__new__(ChatService)
        service._policy_reference_context = AsyncMock(return_value="Unrelated document.")
        discarded_document_ids: list[str] = []
        service._discard_temporary_policy_references = MagicMock(
            side_effect=lambda current: discarded_document_ids.extend(
                current.custom_hazard["policy_reference_document_ids"]
            )
        )
        session = ChatSession(
            session_key="policy-session",
            phase="custom_hazard_clarification",
            custom_hazard={
                "awaiting_policy_reference": True,
                "replacing_policy_reference": True,
                "policy_reference_available": True,
                "policy_reference": "existing-policy.txt",
                "policy_reference_document_ids": ["existing-document"],
            },
        )

        with patch(
            "app.services.chat_custom_hazard_input.validate_policy_reference_twin_transition",
            AsyncMock(return_value={"related": False, "reason": "Unrelated content."}),
        ):
            response = asyncio.run(
                service._handle_custom_hazard_clarification(
                    "policy-session",
                    session,
                    "Policy reference file: unrelated.txt\n"
                    "Policy reference document ID: unrelated-document",
                )
            )

        self.assertEqual(response.step, "custom_hazard_policy_reference")
        self.assertEqual(discarded_document_ids, [])
        self.assertEqual(session.custom_hazard["policy_reference"], "existing-policy.txt")
        self.assertEqual(
            session.custom_hazard["policy_reference_document_ids"],
            ["existing-document"],
        )
        self.assertEqual(
            session.custom_hazard["pending_policy_reference_document_ids"],
            ["unrelated-document"],
        )
        self.assertTrue(session.custom_hazard["policy_reference_available"])
        self.assertTrue(session.custom_hazard["replacing_policy_reference"])


if __name__ == "__main__":
    unittest.main()
