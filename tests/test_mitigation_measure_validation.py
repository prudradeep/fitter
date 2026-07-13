import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()
