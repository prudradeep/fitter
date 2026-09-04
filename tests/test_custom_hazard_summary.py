import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, inspect, text

from app.db.sqlite_migrations import _009_custom_hazard_summary
from app.services.chat_formatters import evidence_for_display
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.enums import ChatPhase


class CustomHazardSummaryTests(unittest.IsolatedAsyncioTestCase):
    def test_final_evidence_hides_temporary_document_id(self) -> None:
        evidence = (
            "Evidence URL: https://example.org/report.pdf\n"
            "Temporary evidence document ID: d4d6ff1e-4fec-4161-8728-ba722dd5f887"
        )

        displayed = evidence_for_display(evidence)

        self.assertEqual(displayed, "Evidence URL: https://example.org/report.pdf")
        self.assertNotIn("Temporary evidence document ID", displayed)

    def test_summary_is_injected_when_database_prompt_is_outdated(self) -> None:
        service = ChatService.__new__(ChatService)
        old_prompt_output = (
            "<p>You have successfully co-created a hazard.</p>"
            "<p><strong>Generated title/name:</strong> Energy poverty</p>"
            "<p><strong>Reason:</strong> Rising costs</p>"
        )

        message = service._ensure_custom_hazard_summary_visible(
            old_prompt_output,
            "Women & lower-income households are disproportionately affected.",
        )

        self.assertIn("<strong>Summary:</strong>", message)
        self.assertIn("Women &amp; lower-income households", message)
        self.assertLess(message.index("Summary:"), message.index("Reason:"))

    def test_summary_is_not_duplicated_when_prompt_already_renders_it(self) -> None:
        service = ChatService.__new__(ChatService)
        current_prompt_output = (
            "<p><strong>Summary:</strong> Existing summary.</p>"
            "<p><strong>Reason:</strong> Rising costs</p>"
        )

        message = service._ensure_custom_hazard_summary_visible(
            current_prompt_output,
            "Existing summary.",
        )

        self.assertEqual(message.count("<strong>Summary:</strong>"), 1)

    async def test_llm_summary_receives_confirmed_user_context(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Berlin",
            sector="Energy",
            accepted_custom_hazard_reason="Rising energy costs deepen energy poverty.",
            custom_hazard={
                "reason": "Rising energy costs deepen energy poverty.",
                "clarifications": [
                    {
                        "questions": ["Who is most affected?"],
                        "answer": "Single mothers and older women are especially exposed.",
                    }
                ],
                "confirmed_affected_groups": [
                    {
                        "group": "Women",
                        "reason": "Women are overrepresented in lower-income households.",
                    }
                ],
            },
        )

        with patch(
            "app.services.chat_custom_hazard_grounding.ask_llm_chat",
            AsyncMock(
                return_value=json.dumps(
                    {
                        "summary": (
                            "Rising transition-related energy costs deepen energy poverty "
                            "among women in Berlin. Single mothers and older women are "
                            "especially exposed because they are overrepresented in "
                            "lower-income households."
                        )
                    }
                )
            ),
        ) as ask_llm:
            summary = await service._generate_custom_hazard_summary(
                session,
                "Disproportionate energy poverty impact on women",
                "Energy poverty impact on women in Berlin",
            )

        self.assertIn("Single mothers and older women", summary)
        payload = json.loads(ask_llm.await_args.kwargs["messages"][0]["content"])
        self.assertEqual(
            payload["clarifications"][0]["answer"],
            session.custom_hazard["clarifications"][0]["answer"],
        )
        self.assertEqual(payload["confirmed_affected_groups"][0]["group"], "Women")

    async def test_unavailable_llm_uses_confirmed_context_fallback(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Berlin",
            sector="Energy",
            accepted_custom_hazard_reason="Rising costs deepen energy poverty.",
            custom_hazard={
                "reason": "Rising costs deepen energy poverty.",
                "clarifications": [
                    {
                        "questions": ["Who is most affected?"],
                        "answer": "Single mothers are especially exposed.",
                    }
                ],
                "confirmed_affected_groups": [
                    {"group": "Women", "reason": "Higher exposure to energy poverty."}
                ],
            },
        )

        with patch(
            "app.services.chat_custom_hazard_grounding.ask_llm_chat",
            AsyncMock(return_value="LLM unavailable"),
        ):
            summary = await service._generate_custom_hazard_summary(
                session,
                "Disproportionate energy poverty impact on women",
                "Energy poverty impact on women in Berlin",
            )

        self.assertIn("Rising costs deepen energy poverty", summary)
        self.assertIn("Single mothers are especially exposed", summary)
        self.assertIn("Confirmed affected groups: Women", summary)

    async def test_summary_review_is_shown_before_hazard_is_saved(self) -> None:
        service = ChatService.__new__(ChatService)
        service._generate_custom_hazard_title = AsyncMock(
            return_value="Energy poverty impact in Berlin"
        )
        service._generate_custom_hazard_summary = AsyncMock(
            return_value="Validated energy costs disproportionately affect low-income renters."
        )
        service._prepare_custom_hazard_added_profiles = MagicMock(
            return_value="Energy poverty impact in Berlin"
        )
        service._stored_hazard_profiles = MagicMock(return_value=[])
        service._additional_profiles_with_population_context = AsyncMock(return_value=[])
        service._crowd_sourcing_visibility_notice = MagicMock(return_value="")
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW.value,
            country="Germany",
            region="Berlin",
            sector="Energy",
            accepted_custom_hazard="Higher transition energy costs",
            custom_hazard={
                "raw_text": "Higher transition energy costs",
                "confirmed_affected_groups": [
                    {"group": "Low-income renters", "reason": "Higher bills."}
                ],
            },
        )

        response = await service._custom_hazard_summary_review_step("session-1", session)

        self.assertEqual(response.step, "custom_hazard_summary_review")
        self.assertEqual(response.input_mode, "textarea")
        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW.value)
        self.assertIn("Review Summary", response.bot_message)
        self.assertIn("Validated energy costs", response.bot_message)
        self.assertEqual(
            [option.label for option in response.options],
            ["Continue", "Regenerate summary", "Back to affected groups"],
        )
        service._prepare_custom_hazard_added_profiles.assert_not_called()

        final_response = await service._handle_custom_hazard_summary_review(
            "session-1",
            session,
            "Continue",
        )

        self.assertEqual(final_response.step, "hazards")
        self.assertIn("Validated energy costs", final_response.bot_message)
        service._prepare_custom_hazard_added_profiles.assert_called_once_with(
            "session-1", session
        )

    async def test_llm_summary_revision_receives_current_summary_and_user_context(self) -> None:
        service = ChatService.__new__(ChatService)
        session = ChatSession(
            country="Germany",
            region="Berlin",
            sector="Energy",
            accepted_custom_hazard_reason="Transition tariffs increase household costs.",
            custom_hazard={
                "confirmed_affected_groups": [
                    {"group": "Low-income renters", "reason": "Limited ability to absorb costs."}
                ]
            },
        )

        with patch(
            "app.services.chat_custom_hazard_grounding.ask_llm_chat",
            AsyncMock(return_value=json.dumps({"summary": "A clearer revised summary."})),
        ) as ask_llm:
            summary = await service._revise_custom_hazard_summary(
                session,
                "Higher transition energy costs",
                "Energy affordability risk in Berlin",
                "The current draft.",
                "Emphasise renters and use simpler language.",
            )

        self.assertEqual(summary, "A clearer revised summary.")
        payload = json.loads(ask_llm.await_args.kwargs["messages"][0]["content"])
        self.assertEqual(payload["current_summary"], "The current draft.")
        self.assertEqual(
            payload["user_revision_request"],
            "Emphasise renters and use simpler language.",
        )
        self.assertEqual(payload["confirmed_affected_groups"][0]["group"], "Low-income renters")

    async def test_summary_revision_loops_until_continue_confirms_it(self) -> None:
        service = ChatService.__new__(ChatService)
        final_response = SimpleNamespace(step="hazards")
        service._revise_custom_hazard_summary = AsyncMock(
            return_value="Revised summary focused on low-income renters."
        )
        service._custom_hazard_added_step = AsyncMock(return_value=final_response)
        session = ChatSession(
            phase=ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW.value,
            accepted_custom_hazard="Higher transition energy costs",
            accepted_custom_hazard_summary="Initial summary.",
            generated_custom_hazard_title="Energy affordability risk",
            custom_hazard={"generated_summary": "Initial summary."},
        )

        revised = await service._handle_custom_hazard_summary_review(
            "session-1",
            session,
            "Make renters more prominent.",
        )

        self.assertEqual(revised.step, "custom_hazard_summary_review")
        self.assertEqual(session.phase, ChatPhase.CUSTOM_HAZARD_SUMMARY_REVIEW.value)
        self.assertEqual(
            session.accepted_custom_hazard_summary,
            "Revised summary focused on low-income renters.",
        )
        service._custom_hazard_added_step.assert_not_awaited()

        confirmed = await service._handle_custom_hazard_summary_review(
            "session-1",
            session,
            "Continue",
        )

        self.assertIs(confirmed, final_response)
        self.assertTrue(session.custom_hazard["summary_confirmed"])
        service._custom_hazard_added_step.assert_awaited_once_with("session-1", session)


class CustomHazardSummaryPersistenceTests(unittest.TestCase):
    def test_sqlite_migration_adds_summary_column(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE custom_hazards (id CHAR(36) PRIMARY KEY)")
            )
            _009_custom_hazard_summary(connection)
            columns = {
                str(column["name"])
                for column in inspect(connection).get_columns("custom_hazards")
            }

        self.assertIn("summary", columns)

    def test_custom_hazard_summary_is_saved_on_record(self) -> None:
        service = ChatService.__new__(ChatService)
        service.user_id = "user-1"
        record = SimpleNamespace(
            id="hazard-1",
            name="Existing name",
            region_id=None,
            region_scope_key="",
            reason=None,
            evidence=None,
            summary=None,
            validation_mode="strict",
            is_crowd_sourced=False,
        )
        service.db = SimpleNamespace(
            scalar=MagicMock(return_value=record),
            add=MagicMock(),
            commit=MagicMock(),
            refresh=MagicMock(),
            rollback=MagicMock(),
        )
        session = ChatSession(
            country_id="country-1",
            region_id="region-1",
            sector_id="sector-1",
        )

        saved = service._ensure_custom_hazard(
            session,
            "Energy poverty impact on women in Berlin",
            reason="Rising costs deepen energy poverty.",
            evidence="Not provided",
            summary="A concise confirmed hazard summary.",
        )

        self.assertIs(saved, record)
        self.assertEqual(record.summary, "A concise confirmed hazard summary.")
        service.db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
