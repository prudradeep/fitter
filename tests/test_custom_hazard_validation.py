import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import custom_hazard_validation as validator
from app.schemas import ChatResponse
from app.services.chat_formatters import format_hazards
from app.services.chat_options import HAZARD_POPULATION_REVIEW_OPTIONS
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.message_renderer import render_message


def _run(coro):
    return asyncio.run(coro)


async def _unavailable(*args, **kwargs):
    return "LLM unavailable"


class CustomHazardValidationTests(unittest.TestCase):
    def test_generic_groups_are_not_extracted(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Communities face higher costs from a vague transition risk.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(result["affected_groups"], [])
        self.assertTrue(
            result["dimension_scores"]["affected_groups_fit"]["needs_clarification"]
        )

    def test_policy_specific_group_is_extracted(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Renewable energy communities in Saxony face higher grid fees from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(
            [group["group"].casefold() for group in result["affected_groups"]],
            ["renewable energy communities"],
        )

    def test_duplicate_warning_does_not_force_rejection(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "High energy bills",
                    "Energy",
                    "Germany",
                    "Saxony",
                    ["High energy bills"],
                    None,
                )
            )

        self.assertTrue(result["duplicate_candidates"])
        self.assertEqual(result["next_action"], "ask_duplicate_confirmation")
        self.assertEqual(result["status"], "needs_duplicate_confirmation")

    def test_easy_validation_mode_accepts_borderline_dimension_scores(self):
        dimensions = {
            key: {"score": 4}
            for key in [
                "hazard_definition_fit",
                "twin_transition_policy_fit",
                "selected_sector_fit",
                "country_region_fit",
                "affected_groups_fit",
            ]
        }
        result = {
            "overall_score": 40,
            "dimension_scores": dimensions,
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        strict_action = validator._recommended_action(result, {}, "strict")
        easy_action = validator._recommended_action(result, {}, "easy")

        self.assertEqual(strict_action.value, "ask_clarification")
        self.assertEqual(easy_action.value, "validate")

    def test_easy_mode_accepts_soft_input_quality_rejection(self):
        service = ChatService.__new__(ChatService)
        service._scope_instruction = lambda session: "Stay in scope."
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Energy",
            selected_hazard="Energy price shock",
            validation_mode="easy",
        )

        with patch(
            "app.services.validation_service.ask_llm_chat",
            AsyncMock(return_value='{"valid": false, "reason": "Please clarify the policy link."}'),
        ):
            result = _run(
                service._validate_input_quality(
                    session=session,
                    purpose="a reason explaining the selected hazard",
                    fields={"Reason": "Coal phase-out increases local household heating costs."},
                )
            )

        self.assertTrue(result["valid"])
        self.assertIn("Easy validation accepted", result["reason"])

    def test_easy_mode_relaxes_mitigation_grounding_required_dimensions(self):
        service = ChatService.__new__(ChatService)
        service.settings = SimpleNamespace(mitigation_support_score_floor=0.2)
        parsed = {
            "dimensions": {
                "hazard_fit": {
                    "status": "SUPPORTED",
                    "citation_ids": ["S1"],
                    "explanation": "The measure targets the selected hazard.",
                },
                "justification_soundness": {
                    "status": "INSUFFICIENT_INFO",
                    "citation_ids": [],
                    "explanation": "The justification is only partly grounded.",
                },
            },
            "verdict_stability": 1.0,
            "sample_count": 1,
        }

        strict = service._score_mitigation_grounding(
            parsed,
            support_context="- [S1] Source, score 0.11: Evidence text.",
            support_label=service.mitigation_support_label_curated_knowledge_base,
            validation_mode="strict",
        )
        easy = service._score_mitigation_grounding(
            parsed,
            support_context="- [S1] Source, score 0.11: Evidence text.",
            support_label=service.mitigation_support_label_curated_knowledge_base,
            validation_mode="easy",
        )

        self.assertFalse(strict["valid"])
        self.assertTrue(easy["valid"])

    def test_same_sector_duplicate_universe_includes_system_additional_custom_and_user_hazards(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["System employment shock"]),
                    SimpleNamespace(all=lambda: ["Additional tariff shock"]),
                    SimpleNamespace(all=lambda: ["Shared custom affordability shock"]),
                    SimpleNamespace(all=lambda: ["User energy poverty shock"]),
                ]
            )
        )
        session = ChatSession(
            sector_id=1,
            hazards=["In-session system hazard"],
            custom_hazards=["In-session custom hazard"],
            additional_hazards=["In-session additional hazard"],
        )

        names = service._same_sector_hazard_names_for_duplicate_check(session)

        self.assertIn("System employment shock", names)
        self.assertIn("Additional tariff shock", names)
        self.assertIn("Shared custom affordability shock", names)
        self.assertIn("User energy poverty shock", names)
        self.assertIn("In-session system hazard", names)

    def test_custom_hazard_duplicate_status_updates_from_same_sector_system_hazard(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Regional employment shock and left-behind energy regions"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_dimension_check",
            custom_hazard={
                "raw_text": "Regional employment shock and left-behind energy regions",
                "reason": "Coal phase-out causes job losses in fossil-dependent regions.",
                "evidence": "",
                "affected_groups": [],
                "duplicate_candidates": [],
            },
        )

        with patch.object(validator, "ask_llm_chat", _unavailable):
            response = _run(
                service._run_custom_hazard_dimension_check("session-1", session)
            )

        self.assertEqual(response.step, "custom_hazard_duplicate_confirmation")
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Regional employment shock", duplicate_card["reason"])

    def test_valid_initial_custom_hazard_updates_duplicate_status_from_user_hazards(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(
                        all=lambda: [
                            'Regional employment shock and "left-behind" energy regions'
                        ]
                    ),
                ]
            )
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                'Regional employment shock and "left-behind" energy regions',
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_validation")
        self.assertEqual(response.input_mode, "reason_evidence")
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Regional employment shock", duplicate_card["reason"])

    def test_hazard_creation_input_is_not_routed_to_grounded_questions(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._is_invalid_user_text = MagicMock(return_value=False)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._handle_anytime_grounded_question = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_input",
                bot_message="Grounded answer",
                options=[],
                session={},
                error=False,
            )
        )
        service._capture_custom_hazard = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="custom_hazard_validation",
                bot_message="Hazard validation",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Transport",
            phase="custom_hazard_input",
        )

        response = _run(
            service._chat_response(
                "session-1",
                session,
                "EV home-charging disadvantage for renters and apartment dwellers",
            )
        )

        self.assertEqual(response.bot_message, "Hazard validation")
        service._capture_custom_hazard.assert_awaited_once()
        service._handle_anytime_grounded_question.assert_not_awaited()

    def test_hazard_selection_is_not_routed_to_grounded_questions(self):
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._is_invalid_user_text = MagicMock(return_value=False)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._handle_anytime_grounded_question = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="hazard_profile_selection",
                bot_message="Grounded answer",
                options=[],
                session={},
                error=False,
            )
        )
        service._is_saved_custom_hazard = MagicMock(return_value=False)
        service._record_activity = MagicMock()
        service._hazard_profiles_response = AsyncMock(
            return_value=ChatResponse(
                session_id="session-1",
                step="socio_demographic_review",
                bot_message="### Socio-demographic profiles most affected by Heat stress",
                options=[],
                session={},
                error=False,
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            phase="hazard_profile_selection",
            hazards=["Heat stress", "Energy poverty"],
            hazard_profiles={"Heat stress": [{"name": "Older adults"}]},
        )

        response = _run(
            service._chat_response("session-1", session, "Heat stress")
        )

        self.assertEqual(response.step, "socio_demographic_review")
        self.assertEqual(session.selected_hazard, "Heat stress")
        service._hazard_profiles_response.assert_awaited_once()
        service._handle_anytime_grounded_question.assert_not_awaited()

    def test_early_invalid_custom_hazard_still_updates_duplicate_status(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Heating and cooling costs increase"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": (
                    "This input does not discuss a hazard arising from Green and Digital "
                    "Transition policies in Europe for the selected sector of Energy."
                ),
            }
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_input",
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Heating and cooling costs increase",
            )
        )

        self.assertTrue(response.error)
        self.assertTrue(session.custom_hazard["duplicate_candidates"])
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        policy_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Twin transition policy fit"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Heating and cooling costs increase", duplicate_card["reason"])
        self.assertEqual(policy_card["status"], "REJECTED")

    def test_reason_step_failure_still_updates_duplicate_status(self):
        service = ChatService.__new__(ChatService)
        service.db = SimpleNamespace(
            scalars=MagicMock(
                side_effect=[
                    SimpleNamespace(all=lambda: ["Heating and cooling costs increase"]),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                    SimpleNamespace(all=lambda: []),
                ]
            )
        )
        service._validate_input_quality = AsyncMock(
            return_value={
                "valid": False,
                "reason": "The reason does not connect the hazard to twin-transition policy impacts.",
            }
        )
        session = ChatSession(
            sector_id=1,
            sector="Energy",
            country="Germany",
            region="Baden-Württemberg",
            phase="custom_hazard_validation",
            pending_hazard="Heating and cooling costs increase",
            custom_hazard={
                "raw_text": "Heating and cooling costs increase",
                "duplicate_candidates": [],
                "dimension_scores": {},
            },
        )

        response = _run(
            service._validate_custom_hazard(
                "session-1",
                session,
                "Reason: because it is bad",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_validation")
        duplicate_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Duplicate check"
        )
        policy_card = next(
            card
            for card in response.custom_hazard_grounding_status
            if card["title"] == "Twin transition policy fit"
        )
        self.assertEqual(duplicate_card["status"], "WARNING")
        self.assertIn("Heating and cooling costs increase", duplicate_card["reason"])
        self.assertEqual(policy_card["status"], "REJECTED")

    def test_score_threshold_moves_to_validation(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Low-income households in Saxony face higher renewable energy grid costs from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {
                        "duplicate_override_confirmed": True,
                        "confirmed_affected_groups": [
                            {"group": "Low-income households", "reason": "Confirmed."}
                        ],
                    },
                )
            )

        self.assertGreaterEqual(result["overall_score"], 75)
        self.assertEqual(result["next_action"], "validate")

    def test_high_score_with_unconfirmed_groups_moves_to_group_review(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Low-income households in Saxony face higher renewable energy grid costs from green transition policy.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"duplicate_override_confirmed": True},
                )
            )

        self.assertGreaterEqual(result["overall_score"], 75)
        self.assertEqual(result["next_action"], "review_groups")

    def test_score_ten_overrides_llm_needs_clarification_flag(self):
        llm_payload = {
            "dimension_scores": {
                "twin_transition_policy_fit": {
                    "score": 10,
                    "reason": "Strong policy fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain policy fit?",
                },
                "selected_sector_fit": {
                    "score": 10,
                    "reason": "Strong sector fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain sector fit?",
                },
                "country_region_fit": {
                    "score": 10,
                    "reason": "Strong place fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain place fit?",
                },
                "affected_groups_fit": {
                    "score": 10,
                    "reason": "Strong group fit.",
                    "needs_clarification": True,
                    "clarification_question": "Explain groups?",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        with patch.object(validator, "_llm_dimension_validation", AsyncMock(return_value=llm_payload)):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Retrofit cost burdens from green transition policy.",
                    "Housing",
                    "Germany",
                    "Saxony",
                    [],
                    None,
                )
            )

        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["next_action"], "validate")
        for dimension in result["dimension_scores"].values():
            self.assertFalse(dimension["needs_clarification"])
            self.assertEqual(dimension["clarification_question"], "")

        cards = validator.build_custom_hazard_grounding_status(
            {
                "dimension_scores": result["dimension_scores"],
                "overall_score": result["overall_score"],
                "status": result["status"],
            }
        )
        dimension_cards = cards[:3]
        self.assertTrue(all(card["status"] == "SUPPORTED" for card in dimension_cards))
        self.assertTrue(all(card["score"] == 100 for card in dimension_cards))

    def test_flat_score_after_round_two_moves_to_validation_when_required_dimensions_resolved(self):
        llm_payload = {
            "dimension_scores": {
                "twin_transition_policy_fit": {
                    "score": 6,
                    "reason": "Enough policy fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "selected_sector_fit": {
                    "score": 6,
                    "reason": "Enough sector fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "country_region_fit": {
                    "score": 6,
                    "reason": "Enough place fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
                "affected_groups_fit": {
                    "score": 6,
                    "reason": "Enough group fit.",
                    "needs_clarification": False,
                    "clarification_question": "",
                },
            },
            "affected_groups": [],
            "duplicate_candidates": [],
        }

        with patch.object(validator, "_llm_dimension_validation", AsyncMock(return_value=llm_payload)):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 2, "scores": [42, 58]},
                )
            )

        self.assertLess(result["overall_score"] - 58, 3)
        self.assertEqual(result["next_action"], "validate")

    def test_flat_score_with_unresolved_required_gap_rejects_instead_of_validating(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 2, "scores": [42, 55]},
                )
            )

        self.assertLess(result["overall_score"] - 55, 3)
        self.assertEqual(result["next_action"], "reject")

    def test_clarification_can_continue_beyond_round_three_when_score_is_improving(self):
        with patch.object(validator, "ask_llm_chat", _unavailable):
            result = _run(
                validator.validate_custom_hazard_dimensions(
                    "Transition policy creates local uncertainty.",
                    "Energy",
                    "Germany",
                    "Saxony",
                    [],
                    {"validation_round": 4, "scores": [35, 42, 48, 51]},
                )
            )

        self.assertGreaterEqual(result["overall_score"] - 51, 3)
        self.assertEqual(result["next_action"], "ask_clarification")

    def test_invalid_hazard_does_not_continue_to_reason_step(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": "This is not a hazard related to the selected transition context.",
            }
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "I like sunny weather",
            )
        )

        self.assertTrue(response.error)
        self.assertNotEqual(response.input_mode, "reason_evidence")
        self.assertIn("not a hazard", response.bot_message)
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(
            response.custom_hazard["dimension_scores"]["twin_transition_policy_fit"]["status"],
            "REJECTED",
        )
        status_cards = {
            card["title"]: card
            for card in response.custom_hazard_grounding_status
        }
        self.assertEqual(
            status_cards["Twin transition policy fit"]["status"],
            "REJECTED",
        )

    def test_sector_mismatch_updates_sector_fit_dimension(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Electric vehicle charging costs for taxi drivers",
            )
        )

        self.assertTrue(response.error)
        self.assertNotEqual(response.input_mode, "reason_evidence")
        self.assertEqual(
            response.custom_hazard["dimension_scores"]["selected_sector_fit"]["status"],
            "REJECTED",
        )
        status_cards = {
            card["title"]: card
            for card in response.custom_hazard_grounding_status
        }
        self.assertEqual(status_cards["Sector fit"]["status"], "REJECTED")

    def test_sector_mismatch_suggests_rewrite_for_selected_sector(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Italy",
            region="Abruzzo",
            sector="Transport",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )

        response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Households lose access to affordable clean heating",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("Suggested rewrite direction", response.bot_message)
        self.assertIn("For Transport", response.bot_message)
        self.assertIn("Keep the affected group and harm", response.bot_message)
        self.assertIn("selected-sector transition policy", response.bot_message)

    def test_transition_policy_rejection_maps_to_policy_fit_dimension(self):
        reason = (
            "This input does not discuss a hazard arising from the Green and "
            "Digital Transition policies in Europe for the selected sector of Energy."
        )

        self.assertEqual(
            ChatService._custom_hazard_rejection_dimension(reason),
            "twin_transition_policy_fit",
        )

    def test_profile_impact_reason_card_is_insufficient_until_user_adds_reason(self):
        cards = validator.build_custom_hazard_grounding_status({})
        status_cards = {card["title"]: card for card in cards}

        self.assertEqual(
            status_cards["Custom profile impact reason"]["status"],
            "INSUFFICIENT INFO",
        )
        self.assertIn(
            "No user-added",
            status_cards["Custom profile impact reason"]["reason"],
        )

    def test_profile_impact_reason_card_needs_clarification_when_added_group_lacks_reason(self):
        cards = validator.build_custom_hazard_grounding_status(
            {"added_affected_groups": [{"group": "Coal workers", "reason": ""}]}
        )
        status_cards = {card["title"]: card for card in cards}

        self.assertEqual(
            status_cards["Custom profile impact reason"]["status"],
            "NEEDS CLARIFICATION",
        )
        self.assertEqual(
            status_cards["Custom profile impact reason"]["clarification_question"],
            "How does this hazard affect 'Coal workers'?",
        )

    def test_profile_impact_reason_card_confirms_when_user_added_reason_exists(self):
        cards = validator.build_custom_hazard_grounding_status(
            {
                "added_affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses affect income."}
                ]
            }
        )
        status_cards = {card["title"]: card for card in cards}

        self.assertEqual(
            status_cards["Custom profile impact reason"]["status"],
            "CONFIRMED",
        )

    def test_valid_hazard_after_invalid_hazard_clears_stale_dimension_status(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Saxony",
            sector="Housing",
            phase="custom_hazard_input",
        )
        service._review_custom_hazard_input = AsyncMock(
            return_value={
                "valid": False,
                "reason": "This is not a hazard related to the selected transition context.",
            }
        )

        invalid_response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "I like sunny weather",
            )
        )
        self.assertTrue(invalid_response.error)
        self.assertEqual(
            invalid_response.custom_hazard["dimension_scores"]["twin_transition_policy_fit"]["status"],
            "REJECTED",
        )

        service._review_custom_hazard_input = AsyncMock(
            return_value={"valid": True, "reason": "The hazard is meaningful."}
        )
        valid_response = _run(
            service._capture_custom_hazard(
                "session-1",
                session,
                "Renovation cost burden from green retrofit mandates for low-income tenants",
            )
        )

        self.assertFalse(valid_response.error)
        self.assertEqual(valid_response.input_mode, "reason_evidence")
        self.assertEqual(valid_response.custom_hazard["dimension_scores"], {})

    def test_fuzzy_confirmation_does_not_show_mitigation_adoption_option(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(phase="custom_hazard_input")

        response = service._fuzzy_confirmation_step(
            "session-1",
            session,
            "Go back to list of hazards",
        )

        labels = [option.label for option in response.options]
        self.assertEqual(labels, ["Yes", "No"])
        self.assertNotIn("Adopt mitigation proposal suggested above", labels)

    def test_custom_hazard_added_message_shows_only_added_hazard_details(self):
        message = render_message(
            "hazard_added.md",
            hazard="Regional employment shock",
            reason="Coal phase-out causes job losses.",
            evidence="Not provided",
            affected_population_groups="- **Coal workers**",
        )

        self.assertIn("Custom hazard added successfully", message)
        self.assertIn("Regional employment shock", message)
        self.assertIn("Coal workers", message)
        self.assertNotIn("Updated Hazard List", message)
        self.assertNotIn("following hazards", message)

    def test_additional_hazards_methodology_cta_survives_message_sanitizer(self):
        session = ChatSession(
            sector="Energy",
            region="Saxony",
            additional_hazards=["Regional employment shock"],
            hazard_profiles={
                "Regional employment shock": [{"name": "Coal workers"}],
            },
        )

        message = render_message(
            "hazards_overview.md",
            sector="Energy",
            region="Saxony",
            hazards=format_hazards(session),
        )

        self.assertIn("<button", message)
        self.assertIn("additional-hazards-methodology-cta", message)
        self.assertIn('data-open-methodology="true"', message)
        self.assertIn("Show methodologies", message)

    def test_custom_hazard_added_summary_keeps_left_panel_hazard_context(self):
        session = ChatSession(
            phase="hazards",
            accepted_custom_hazard="New custom hazard",
            hazards=["Top hazard 1", "Top hazard 2", "Top hazard 3", "Top hazard 4"],
            additional_hazards=["Expert-added hazard"],
            custom_hazards=["New custom hazard"],
            hazard_profiles={
                "Top hazard 1": [{"name": "Workers"}],
                "Top hazard 2": [{"name": "Residents"}],
                "Top hazard 3": [{"name": "Households"}],
                "Top hazard 4": [{"name": "Firms"}],
                "Expert-added hazard": [{"name": "Tenants"}],
                "New custom hazard": [{"name": "Coal workers"}],
            },
            hazard_rankings={
                "Top hazard 1": {"regional_population_pct": 10, "national_population_pct": 8},
                "Top hazard 2": {"regional_population_pct": 7, "national_population_pct": 5},
                "Top hazard 3": {"regional_population_pct": 6, "national_population_pct": 4},
                "Expert-added hazard": {"regional_population_pct": 3, "national_population_pct": 2},
            },
        )

        summary = session.summary()

        self.assertEqual(
            [row["hazard"] for row in summary.top_hazards],
            ["Top hazard 1", "Top hazard 2", "Top hazard 3"],
        )
        self.assertEqual(summary.additional_hazards, ["Expert-added hazard"])
        self.assertEqual(summary.custom_hazards, ["New custom hazard"])
        self.assertEqual(
            [row["hazard"] for row in summary.additional_hazard_population],
            ["Expert-added hazard"],
        )

    def test_population_review_options_use_open_conversation_for_add_remove(self):
        labels = [option.label for option in HAZARD_POPULATION_REVIEW_OPTIONS]

        self.assertEqual(labels, ["Confirm affected groups"])
        self.assertNotIn("Add affected group", labels)
        self.assertNotIn("Remove affected group", labels)
        self.assertNotIn("Edit group reason", labels)

    def test_removing_last_custom_affected_group_keeps_review_open_without_required_error(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses.", "source": "user_added"}
                ],
                "added_affected_groups": [
                    {"group": "Coal workers", "reason": "Job losses.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove affected group: Coal workers",
            )
        )

        self.assertEqual(session.custom_hazard["affected_groups"], [])
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertNotIn("At least one affected population group is required", response.bot_message)

    def test_typing_existing_custom_affected_group_name_removes_that_group_only(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "Coal-dependent communities", "reason": "Local dependence."},
                    {
                        "group": "Coal miners, power plant workers, and related supply chain jobs",
                        "reason": "Job losses.",
                    },
                    {
                        "group": "households with utility arrears: Add households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    },
                ],
                "added_affected_groups": [
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    }
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "households with utility arrears",
            )
        )

        remaining = [
            group["group"]
            for group in session.custom_hazard["affected_groups"]
        ]
        self.assertEqual(
            remaining,
            [
                "Coal-dependent communities",
                "Coal miners, power plant workers, and related supply chain jobs",
            ],
        )
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertIn("Coal-dependent communities", response.bot_message)

    def test_extracted_custom_affected_group_cannot_be_removed(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "Coal miners",
                        "reason": "Explicitly named in the hazard or clarification text.",
                    },
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    },
                ],
                "added_affected_groups": [
                    {
                        "group": "households with utility arrears",
                        "reason": "Energy costs.",
                        "source": "user_added",
                    }
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove Coal miners",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("can't be removed because it was found by the system", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["Coal miners", "households with utility arrears"],
        )

    def test_custom_affected_group_review_cleans_add_echo_from_label(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "households with utility arrears: Add households with utility arrears",
                        "reason": "Energy costs.",
                    },
                ],
            },
        )

        response = service._custom_hazard_population_review_step("session-1", session)

        self.assertIn("households with utility arrears", response.bot_message)
        self.assertNotIn(
            "households with utility arrears: Add households with utility arrears",
            response.bot_message,
        )

    def test_confirming_empty_custom_affected_groups_asks_to_add_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Confirm affected groups",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("No affected groups are selected", response.bot_message)
        self.assertEqual(response.step, "custom_hazard_group_review")

    def test_user_added_affected_group_reason_is_validated_before_storage(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "Coal workers",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "bad",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(session.custom_hazard["added_affected_groups"], [])
        self.assertIn("meaningful impact reason", response.bot_message)

    def test_user_added_affected_group_reason_is_saved_when_valid(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "Coal workers",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )
        service._validate_input_quality = AsyncMock(
            return_value={"valid": True, "reason": "Reason is meaningful."}
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Coal workers face job losses and lower income when coal plants close.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(
            session.custom_hazard["added_affected_groups"][0]["reason"],
            "Coal workers face job losses and lower income when coal plants close.",
        )
        self.assertEqual(
            session.custom_hazard["affected_groups"][0]["group"],
            "Coal workers",
        )

    def test_open_text_add_affected_groups_prompts_for_first_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Add low-income renters, older adults",
            )
        )

        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("low-income renters", response.bot_message)
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "low-income renters",
        )
        self.assertEqual(session.custom_hazard["pending_profile_reason_queue"], ["older adults"])
        self.assertEqual(session.custom_hazard["affected_groups"], [])

    def test_open_text_add_rejects_non_population_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Add electricity price",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "custom_hazard_group_review")
        self.assertIn("does not look like an affected population group", response.bot_message)
        self.assertNotIn("pending_profile_reason_group", session.custom_hazard)
        self.assertEqual(session.custom_hazard["affected_groups"], [])

    def test_valid_reason_for_queued_group_asks_next_group_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_profile_reason",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "reason": "Coal phase-out causes local job losses and income shocks.",
                "pending_profile_reason_group": "low-income renters",
                "pending_profile_reason_queue": ["older adults"],
                "affected_groups": [],
                "added_affected_groups": [],
            },
        )
        service._validate_input_quality = AsyncMock(
            return_value={"valid": True, "reason": "Reason is meaningful."}
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Renters face higher utility arrears when coal phase-out raises local energy costs.",
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("older adults", response.bot_message)
        self.assertEqual(
            session.custom_hazard["affected_groups"][0]["group"],
            "low-income renters",
        )
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "older adults",
        )

    def test_open_text_remove_and_add_updates_groups_then_prompts_for_reason(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"},
                    {"group": "Coal workers", "reason": "Job losses."},
                ],
                "added_affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove tenants and add households with utility arrears",
            )
        )

        self.assertEqual(response.step, "custom_hazard_profile_reason")
        self.assertIn("households with utility arrears", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["Coal workers"],
        )
        self.assertEqual(
            session.custom_hazard["pending_profile_reason_group"],
            "households with utility arrears",
        )

    def test_remove_and_invalid_add_does_not_remove_existing_group(self):
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"},
                ],
                "added_affected_groups": [
                    {"group": "tenants", "reason": "Rent pressure.", "source": "user_added"}
                ],
            },
        )

        response = _run(
            service._handle_custom_hazard_population_review(
                "session-1",
                session,
                "Remove tenants and add coal plant closure",
            )
        )

        self.assertTrue(response.error)
        self.assertIn("does not look like an affected population group", response.bot_message)
        self.assertEqual(
            [group["group"] for group in session.custom_hazard["affected_groups"]],
            ["tenants"],
        )

    def test_extracted_custom_groups_are_mapped_to_target_population_options(self):
        service = ChatService.__new__(ChatService)
        service._target_population_option_rows = lambda: [
            SimpleNamespace(id=1, question="Level of income", option="Low income"),
            SimpleNamespace(id=2, question="Tenancy status", option="Tenant"),
            SimpleNamespace(id=3, question="Age range", option="65+"),
        ]
        service._ensure_custom_hazard = lambda *args, **kwargs: SimpleNamespace(id=123)
        service._record_activity = lambda *args, **kwargs: None
        service._custom_hazard_added_step = AsyncMock(return_value="done")
        session = ChatSession(
            session_key="session-1",
            phase="custom_hazard_group_review",
            accepted_custom_hazard="Coal phase-out job shock",
            custom_hazard={
                "raw_text": "Coal phase-out job shock",
                "affected_groups": [
                    {
                        "group": "low-income renters",
                        "reason": "Higher energy bills increase arrears risk.",
                        "source_text": "low-income renters",
                    },
                    {
                        "group": "older adults",
                        "reason": "Older adults face greater exposure to heating cost shocks.",
                        "source_text": "older adults",
                    },
                ],
                "confirmed_affected_groups": [],
            },
        )

        response = _run(
            service._finalize_custom_hazard_from_grounding("session-1", session)
        )

        self.assertEqual(response, "done")
        profiles = session.hazard_profiles["Coal phase-out job shock"]
        self.assertEqual(profiles[0]["target_population_option_ids"], ["1", "2"])
        self.assertEqual(profiles[1]["target_population_option_ids"], ["3"])
        self.assertIn("Level of income: Low income", profiles[0]["target_population_labels"])
        self.assertIn("Age range: 65+", profiles[1]["target_population_labels"])

    def test_user_added_utility_arrears_group_maps_to_low_income_population(self):
        service = ChatService.__new__(ChatService)
        service._target_population_option_rows = lambda: [
            SimpleNamespace(id=1, question="Level of income", option="Low income"),
            SimpleNamespace(id=2, question="Tenancy status", option="Tenant"),
        ]

        profiles = service._attach_target_population_matches_to_profiles(
            [
                {
                    "name": "households with utility arrears",
                    "profile": "households with utility arrears",
                    "explanation": "Energy cost increases worsen arrears risk.",
                    "source": "user_added",
                }
            ]
        )

        self.assertEqual(profiles[0]["target_population_option_ids"], ["1"])
        self.assertIn("Level of income: Low income", profiles[0]["target_population_labels"])


if __name__ == "__main__":
    unittest.main()
