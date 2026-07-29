import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_mitigation_creation import ChatMitigationCreationMixin
from app.services.chat_mitigation_steps import ChatMitigationStepsMixin
from app.services.chat_session import ChatSession
from app.services.validation_service import ChatValidationServiceMixin


class _MitigationMeasureEngine(ChatMitigationStepsMixin, ChatValidationServiceMixin):
    invalid_message = "Invalid"

    def _clear_mitigation_clarity_state(self, session):
        return None

    def _clear_mitigation_validation_state(self, session):
        return None

    def _is_invalid_user_text(self, value):
        return False

    def _local_mitigation_measure_error(self, mitigation_measure):
        return None

    def _local_mitigation_duplicate_check(self, session, mitigation_measure):
        return None

    async def _semantic_mitigation_duplicate_check(self, session, mitigation_measure):
        return None


class _MitigationReviewEngine(ChatMitigationCreationMixin):
    def _scope_instruction(self, session):
        return "Stay in the selected context."

    async def _sector_prompt_rag_context(self, *args, **kwargs):
        return "- Sector context excerpt."

    async def _mitigation_knowledge_context(self, *args, **kwargs):
        return "- Generic mitigation KB excerpt."

    def _mitigation_measure_examples(self, sector_id):
        return "- Example mitigation measure."

    def _update_mitigation_review_details(self, *args, **kwargs):
        return None

    def _mitigation_target_affected_groups_json(self, session):
        return "[]"

    def _affected_profile_target_population_labels(self, session):
        return []

    def _normalize_population_group_labels(self, labels):
        return list(labels or [])

    def _group_target_population_labels(self, labels):
        return list(labels or [])

    def _grounding_validation_details(self, session):
        return {}


class MitigationMeasureValidationTests(unittest.TestCase):
    def test_weak_measure_requests_clarification_before_reason_step(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._capture_mitigation_measure(
                "test-session",
                session,
                "Government should help",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("too vague", response.bot_message)

    def test_restatement_of_hazard_is_invalid(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )

        response = asyncio.run(
            engine._capture_mitigation_measure(
                "test-session",
                session,
                "Heat stress",
            )
        )

        self.assertTrue(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertIsNone(session.pending_mitigation_measure)
        self.assertIn("restates the hazard", response.bot_message)

    def test_concrete_measure_uses_measure_only_prompt_and_moves_to_reason(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Heat stress",
        )
        llm_payload = {
            "status": "VALID",
            "summary": "The measure is concrete and relevant.",
            "checks": {
                "hazard_fit": True,
                "sector_fit": True,
                "country_region_fit": True,
                "twin_transition_fit": True,
                "policy_quality": True,
            },
            "clarification_question": "",
            "suggested_improvement": "",
        }

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(return_value=json.dumps(llm_payload)),
        ) as ask_mock:
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    "Introduce targeted grants for low-income households to install heat pumps.",
                )
            )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_reason")
        self.assertEqual(
            session.pending_mitigation_measure,
            "Introduce targeted grants for low-income households to install heat pumps.",
        )
        call = ask_mock.await_args.kwargs
        self.assertIn("validate ONLY the mitigation measure itself", call["context"])
        self.assertIn("Be reasonably permissive", call["context"])
        self.assertIn('"country_region_fit": true', call["context"])
        user_content = call["messages"][0]["content"]
        self.assertIn("Mitigation Measure:", user_content)
        self.assertIn("Do NOT evaluate or request the implementation reason or justification.", user_content)
        self.assertNotIn("Reason:", user_content)
        self.assertNotIn("Justification:", user_content)

    def test_short_meaningful_measure_is_sent_to_validator_not_locally_rejected(self):
        engine = _MitigationMeasureEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Energy poverty",
        )
        llm_payload = {
            "status": "VALID",
            "summary": "The measure is meaningful for the context.",
            "checks": {
                "hazard_fit": True,
                "sector_fit": True,
                "country_region_fit": True,
                "twin_transition_fit": True,
                "policy_quality": True,
            },
            "clarification_question": "",
            "suggested_improvement": "",
        }

        with patch(
            "app.services.validation_service.ask_llm_chat",
            new=AsyncMock(return_value=json.dumps(llm_payload)),
        ) as ask_mock:
            response = asyncio.run(
                engine._capture_mitigation_measure(
                    "test-session",
                    session,
                    "Introduce grants",
                )
            )

        self.assertTrue(ask_mock.await_args)
        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_reason")
        self.assertEqual(session.pending_mitigation_measure, "Introduce grants")

    def test_practical_considerations_ignore_schema_placeholder_heading(self):
        payload = {
            "title": "# Practical Considerations",
            "themes": [
                {
                    "heading": "## <Dynamic Theme Heading>",
                    "summary": "Placeholder text should not become a checklist item.",
                    "concerns": ["- Placeholder concern."],
                },
                {
                    "heading": "## Targeted Mobility Access",
                    "summary": "Real theme summary.",
                    "concerns": ["- Real implementation concern."],
                },
            ],
        }

        markdown, panel_items = ChatMitigationCreationMixin._practical_considerations_json_to_markdown(
            json.dumps(payload)
        )

        self.assertEqual(panel_items, ["Targeted Mobility Access"])
        self.assertNotIn("Dynamic Theme Heading", markdown)

    def test_mitigation_review_starts_open_discussion_before_evaluation(self):
        engine = _MitigationReviewEngine()
        engine._mitigation_review_response = AsyncMock(
            return_value=(
                "### What is covered\n"
                "- The measure targets affordability.\n\n"
                "### Pros\n"
                "- It is targeted before evaluation."
            )
        )
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            mitigation_validation={},
            mitigation_grounded_synthesis=(
                "Existing grounded synthesis should be context, not the final review."
            ),
        )

        response = asyncio.run(engine._mitigation_review_step("test-session", session))

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_review")
        self.assertEqual(session.phase, "mitigation_review")
        self.assertIn("Concept Comparision", response.bot_message)
        self.assertIn("What is covered", response.bot_message)
        engine._mitigation_review_response.assert_awaited_once()
        prompt = engine._mitigation_review_response.await_args.args[1]
        self.assertIn("Compare the conceptual design", prompt)
        self.assertIn("Do not ask evaluation questions yet", prompt)
        self.assertIn("do not name the source document", prompt)

    def test_mitigation_review_prompt_includes_d23_page_range_context(self):
        engine = _MitigationReviewEngine()
        session = ChatSession(
            country="Germany",
            region="Bavaria",
            sector="Energy",
            selected_hazard="Higher electricity bills from renewable grid upgrade tariffs",
            mitigation_measure="Targeted electricity bill support for low-income households.",
            mitigation_reason="It offsets tariff increases while grid upgrades are implemented.",
            mitigation_target_population=["Low-income households"],
            mitigation_grounded_synthesis=(
                "The previous grounding result supported affordability targeting."
            ),
        )

        with patch(
            "app.services.chat_mitigation_creation._d23_conceptual_review_page_texts",
            MagicMock(
                return_value=(
                    (25, "This page is outside the requested range."),
                    (
                        26,
                        "Energy poverty concepts include affordability, distributional fairness, "
                        "and social vulnerability in transition policy design.",
                    ),
                    (
                        91,
                        "Policy design should consider targeting, trade-offs, feasibility, "
                        "and vulnerable household coverage.",
                    ),
                    (92, "This page is outside the requested range."),
                )
            ),
        ):
            context, messages = asyncio.run(
                engine._build_mitigation_review_messages(
                    session,
                    "Open the conversational discussion before evaluation.",
                )
            )

        self.assertIn("Conceptual source excerpts", context)
        self.assertIn("[Source p. 26]", context)
        self.assertIn("[Source p. 91]", context)
        self.assertNotIn("[Source p. 25]", context)
        self.assertNotIn("[Source p. 92]", context)
        self.assertNotIn("FITTER D2.3", context)
        self.assertIn("Grounded validation synthesis", context)
        self.assertIn("supported affordability targeting", context)
        self.assertIn("Generic mitigation KB excerpt", context)
        self.assertIn("What the mitigation measure covers well", messages[0]["content"])
        self.assertIn("Pros, strengths", messages[0]["content"])
        self.assertIn("Do not name the source document", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
