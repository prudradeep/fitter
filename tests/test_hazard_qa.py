import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import SystemHazard, SystemHazardSocioDemographic
from app.services.chat_grounded_question_steps import ChatGroundedQuestionStepsMixin
from app.services.chat_hazard_steps import ChatHazardStepsMixin
from app.services.chat_options import SOCIO_DEMOGRAPHIC_OPTIONS
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
from app.services.knowledge_base import MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE
from app.schemas import ChatResponse


class HazardQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_sector_context_uses_full_selected_hazard_block(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        prompt = (
            "SECTION 3. HAZARDS IN THE ENERGY SECTOR\n"
            "HAZARD 1. HEATING AND COOLING COSTS INCREASE\n"
            "Mean concern = 15.37.\n"
            "HAZARD 2. HIGHER ELECTRICITY BILLS\n"
            "Mean concern = 14.96.\n"
            "SECTION 4. CONFIRMED PREDICTORS\n"
            "SECTION 5. PER-HAZARD CONFIRMED PREDICTORS\n"
            "HAZARD 1. HEATING AND COOLING COSTS INCREASE\n"
            "Predictor A: utility arrears.\n"
            "Predictor B: home problems count.\n"
            "HAZARD 2. HIGHER ELECTRICITY BILLS\n"
            "Predictor C: electricity consumption.\n"
            "SECTION 6. COUNTRY COMPARISON\n"
        )
        session = ChatSession(
            sector="Energy", selected_hazard="HEATING AND COOLING COSTS INCREASE"
        )
        with patch("app.services.chat_grounded_question_steps.load_sector_prompt", return_value=prompt):
            context, sources = await service._hazard_qa_sector_context(
                session, "What predicts concern?"
            )

        self.assertIn("Predictor A: utility arrears.", context)
        self.assertIn("Predictor B: home problems count.", context)
        self.assertIn("Mean concern = 15.37.", context)
        self.assertNotIn("Predictor C", context)
        self.assertNotIn("Mean concern = 14.96.", context)
        self.assertIn("SP1", sources)
        self.assertIn("SP2", sources)

    async def test_database_context_is_scoped_to_selected_hazard(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            selected = SystemHazard(sector_id="sector-1", name="Heat costs")
            other = SystemHazard(sector_id="sector-1", name="Power cuts")
            db.add_all([selected, other])
            db.flush()
            db.add_all([
                SystemHazardSocioDemographic(
                    system_hazard_id=selected.id, sector_id="sector-1",
                    profile="Households with unpaid utility bills",
                    explanation="Utility arrears predict higher concern.",
                    statistical_basis="Odds ratio 3.00.",
                ),
                SystemHazardSocioDemographic(
                    system_hazard_id=other.id, sector_id="sector-1",
                    profile="Unrelated profile",
                ),
            ])
            db.commit()
            service = ChatGroundedQuestionStepsMixin()
            service.db = db
            session = ChatSession(sector_id="sector-1", selected_hazard="Heat costs")

            context, sources = service._hazard_qa_database_context(session)

            self.assertIn("Households with unpaid utility bills", context)
            self.assertIn("Odds ratio 3.00.", context)
            self.assertNotIn("Unrelated profile", context)
            self.assertTrue(sources)
        finally:
            db.close()
            engine.dispose()

    async def test_displayed_hazard_data_and_knowledge_are_available_to_llm(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            socio_demographic_findings=(
                "<table><tr><td>Households with unpaid utility bills</td>"
                "<td>24.0%</td><td>12.0%</td></tr></table>"
            ),
        )
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "The regional share is 24.0%.", "source_id": "DB1", "quote": "24.0%",
            }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session,
                "What regional share is shown for unpaid utility bills?",
            )

        self.assertIn("24.0%", response.bot_message)
        self.assertIn("source-citation", response.bot_message)
        self.assertIn("24.0%", ask_llm.await_args.kwargs["messages"][0]["content"])
        self.assertEqual(service._hazard_qa_knowledge_context.await_count, 3)

    async def test_affected_profile_question_uses_the_selected_hazards_visible_profiles(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            hazard_qa_active=True,
            hazard_profiles={
                "HEATING AND COOLING COSTS INCREASE": [
                    {"name": "Households with unpaid utility bills"},
                    {"name": "Households with Higher Home problems count"},
                ],
                "Different hazard": [{"name": "Unrelated profile"}],
            },
        )

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "The affected profiles are households with unpaid utility bills and households with higher home problems count.",
                "source_id": "DB1",
                "quote": "Displayed affected profile names: Households with unpaid utility bills; Households with Higher Home problems count",
            }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session,
                "Which socio demographic profiles are affected by this hazard?",
            )

        self.assertIn("Households with unpaid utility bills", response.bot_message)
        self.assertIn("Households with Higher Home problems count", response.bot_message)
        self.assertNotIn("Unrelated profile", response.bot_message)
        self.assertNotIn("Information not available", response.bot_message)
        self.assertEqual(response.options, SOCIO_DEMOGRAPHIC_OPTIONS)
        self.assertIn("Displayed affected profile names", ask_llm.await_args.kwargs["messages"][0]["content"])
        ask_llm.assert_awaited_once()

    async def test_profile_count_uses_displayed_rows_instead_of_raw_profiles(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            hazard_qa_active=True,
            hazard_profiles={"HEATING AND COOLING COSTS INCREASE": [
                {"name": "Households with unpaid utility bills"},
                {"name": "Households with home problems"},
                {"name": "Protective profile excluded from table"},
            ]},
            selected_hazard_displayed_profiles=[
                "Households with unpaid utility bills", "Households with home problems",
            ],
        )

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "2 socio-demographic profiles are shown as affected.",
                "source_id": "DB1", "quote": "Displayed affected profile count: 2",
            }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "How many socio demographic profiles are affected?",
            )

        self.assertIn("2 socio-demographic profiles", response.bot_message)
        self.assertNotIn("Protective profile", response.bot_message)
        self.assertNotIn(
            "Households with unpaid utility bills",
            response.bot_message.split('<span class="source-citation"', 1)[0],
        )
        self.assertEqual(response.options, SOCIO_DEMOGRAPHIC_OPTIONS)
        self.assertIn("Displayed affected profile count: 2", ask_llm.await_args.kwargs["messages"][0]["content"])
        ask_llm.assert_awaited_once()

    async def test_profile_count_reads_existing_displayed_table(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(
            selected_hazard="Heat costs",
            socio_demographic_findings=(
                '<table><tr><th scope="row"><strong>First group</strong></th></tr>'
                '<tr><th scope="row"><strong>Second group</strong></th></tr></table>'
            ),
        )

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "2 affected profiles are shown.",
                "source_id": "DB1", "quote": "Displayed affected profile count: 2",
            }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "How many affected profiles are there?",
            )

        self.assertIn("2 affected profiles", response.bot_message)
        ask_llm.assert_awaited_once()

    async def test_profile_count_rejects_statistical_high_labels_and_retries(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(side_effect=lambda session, question, scope: (
            "[S1] Statistical category: HIGH",
            {"S1": {"title": "Statistical category", "evidence": "HIGH"}},
        ) if scope == MAIN_KB_SCOPE else ("", {}))
        names = ["Households with unpaid bills", "Households with home problems", "Higher consumption households"]
        session = ChatSession(
            selected_hazard="Heat costs", selected_hazard_displayed_profiles=names,
        )
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            side_effect=[
                json.dumps({"claims": [{
                    "answer": "HIGH", "source_id": "S1", "quote": "HIGH",
                }]}),
                json.dumps({"claims": [{
                    "answer": "3 socio-demographic profiles are shown as affected.",
                    "source_id": "DB1", "quote": "Displayed affected profile count: 3",
                }]}),
            ],
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "How many socio demographic profiles affected?",
            )

        self.assertEqual(ask_llm.await_count, 2)
        self.assertIn("HIGH", ask_llm.await_args_list[0].kwargs["messages"][0]["content"])
        self.assertNotIn("HIGH", ask_llm.await_args_list[1].kwargs["messages"][0]["content"])
        self.assertIn("3 socio-demographic profiles", response.bot_message)
        self.assertNotIn("HIGH", response.bot_message)

    async def test_profile_count_accepts_number_written_as_a_word(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        self.assertTrue(service._hazard_qa_profile_summary_supported(
            "Three socio-demographic profiles are affected. [DB1]",
            "count", ["One group", "Two group", "Three group"], "DB1",
        ))
        self.assertFalse(service._hazard_qa_profile_summary_supported(
            "HIGH [DB1]", "count", ["One group", "Two group", "Three group"], "DB1",
        ))

    async def test_profile_names_retry_uses_displayed_names(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        names = ["Households with unpaid bills", "Households with home problems"]
        session = ChatSession(
            selected_hazard="Heat costs", selected_hazard_displayed_profiles=names,
        )
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            side_effect=[
                json.dumps({"claims": []}),
                json.dumps({"claims": [{
                    "answer": "The affected profiles are Households with unpaid bills and Households with home problems.",
                    "source_id": "DB1",
                    "quote": "Displayed affected profile names: Households with unpaid bills; Households with home problems",
                }]}),
            ],
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session,
                "Which socio demographic profiles are affected by this hazard?",
            )

        self.assertEqual(ask_llm.await_count, 2)
        for name in names:
            self.assertIn(name, response.bot_message)
        self.assertNotIn("Information not available", response.bot_message)

    async def test_profile_count_without_profiles_does_not_quote_unrelated_knowledge(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(selected_hazard="Heat costs", hazard_qa_active=True)

        with patch("app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "How many socio demographic profiles are affected?",
            )

        self.assertIn("Information not available to answer this.", response.bot_message)
        service._hazard_qa_sector_context.assert_awaited_once()
        self.assertEqual(service._hazard_qa_knowledge_context.await_count, 3)
        ask_llm.assert_not_awaited()

    async def test_llm_answers_displayed_profile_regional_national_and_dataset_questions(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        rendered = ChatService.__new__(ChatService)._format_hazard_profiles_markdown(
            "HEATING AND COOLING COSTS INCREASE",
            [
                {
                    "name": "Households with unpaid utility bills",
                    "regional_population_pct": 24,
                    "national_population_pct": 12,
                    "metadata": {"indicator_mapping": {
                        "proposed_eurostat_dataset": "demo_r_pjangroup (mocked)",
                    }},
                },
                {
                    "name": "Households with Higher Home problems count",
                    "regional_population_pct": 11,
                    "national_population_pct": 25,
                    "metadata": {"indicator_mapping": {
                        "proposed_eurostat_dataset": "ilc_di11_r (mocked)",
                    }},
                },
            ],
        )
        session = ChatSession(
            selected_hazard="HEATING AND COOLING COSTS INCREASE",
            socio_demographic_findings=rendered,
            hazard_qa_active=True,
        )

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            side_effect=[
                json.dumps({"claims": [{
                    "answer": "The regional population share for households with higher home problems count is 11.0%.",
                    "source_id": "DB1", "quote": "Regional population share: 11.0%",
                }]}),
                json.dumps({"claims": [{
                    "answer": "The proposed Eurostat dataset for households with unpaid utility bills is demo_r_pjangroup (mocked).",
                    "source_id": "DB1", "quote": "Proposed Eurostat dataset: demo_r_pjangroup (mocked)",
                }]}),
                json.dumps({"claims": [{
                    "answer": "The national population share for households with higher home problems count is 25.0%.",
                    "source_id": "DB1", "quote": "National population share: 25.0%",
                }]}),
            ],
        ) as ask_llm:
            regional = await service._handle_hazard_qa_question(
                "session-1", session,
                "What's the regional population for the Households with Higher Home problems count?",
            )
            dataset = await service._handle_hazard_qa_question(
                "session-1", session,
                "Which Proposed Eurostat dataset is used for Households with unpaid utility bills?",
            )
            national = await service._handle_hazard_qa_question(
                "session-1", session,
                "What's the national population for the Households with Higher Home problems count?",
            )

        self.assertIn("11.0%", regional.bot_message)
        self.assertIn("regional population share", regional.bot_message)
        self.assertNotIn("24.0%", regional.bot_message.split('<span class="source-citation"', 1)[0])
        self.assertIn("demo_r_pjangroup (mocked)", dataset.bot_message)
        self.assertIn("proposed Eurostat dataset", dataset.bot_message)
        self.assertNotIn("ilc_di11_r", dataset.bot_message)
        self.assertIn("25.0%", national.bot_message)
        self.assertNotIn("11.0%", national.bot_message.split('<span class="source-citation"', 1)[0])
        self.assertEqual(regional.options, SOCIO_DEMOGRAPHIC_OPTIONS)
        self.assertEqual(dataset.options, SOCIO_DEMOGRAPHIC_OPTIONS)
        self.assertEqual(national.options, SOCIO_DEMOGRAPHIC_OPTIONS)
        self.assertEqual(ask_llm.await_count, 3)
        for call in ask_llm.await_args_list:
            context = call.kwargs["messages"][0]["content"]
            self.assertIn("Regional population share: 11.0%", context)
            self.assertIn("National population share: 25.0%", context)
            self.assertIn("Proposed Eurostat dataset: demo_r_pjangroup (mocked)", context)

    async def test_existing_session_profile_details_gain_national_share_from_table(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        rendered = ChatService.__new__(ChatService)._format_hazard_profiles_markdown(
            "Heat costs",
            [{
                "name": "Households with home problems",
                "regional_population_pct": 11,
                "national_population_pct": 25,
            }],
        )
        session = ChatSession(
            selected_hazard="Heat costs",
            socio_demographic_findings=rendered,
            selected_hazard_displayed_profile_details=[{
                "name": "Households with home problems", "regional": "11.0%",
            }],
        )

        context, sources = service._hazard_qa_database_context(session, "National population for households with home problems")

        self.assertIn("National population share: 25.0%", context)
        self.assertIn("DB1", sources)

    async def test_profile_value_question_retries_with_displayed_row_after_empty_llm_answer(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=(
            "[SP1] Sector stats: Statistical background.",
            {"SP1": {"title": "Sector stats", "evidence": "Statistical background."}},
        ))
        service._hazard_qa_knowledge_context = AsyncMock(side_effect=lambda session, question, scope: (
            "[S1] Main KB: Other background.",
            {"S1": {"title": "Main KB", "evidence": "Other background."}},
        ) if scope == MAIN_KB_SCOPE else ("", {}))
        session = ChatSession(
            selected_hazard="Heat costs",
            selected_hazard_displayed_profiles=["Households with home problems"],
            selected_hazard_displayed_profile_details=[{
                "name": "Households with home problems",
                "regional": "11.0%", "national": "25.0%",
            }],
        )

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            side_effect=[
                json.dumps({"claims": []}),
                json.dumps({"claims": [{
                    "answer": "The national population share shown is 25.0%.",
                    "source_id": "DB1", "quote": "National population share: 25.0%",
                }]}),
            ],
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session,
                "What's the national population for the Households with home problems?",
            )

        self.assertEqual(ask_llm.await_count, 2)
        first_context = ask_llm.await_args_list[0].kwargs["messages"][0]["content"]
        retry_context = ask_llm.await_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("National population share: 25.0%", first_context)
        self.assertIn("Statistical background.", first_context)
        self.assertIn("Other background.", first_context)
        self.assertIn("National population share: 25.0%", retry_context)
        self.assertNotIn("Statistical background.", retry_context)
        self.assertIn("25.0%", response.bot_message)
        self.assertNotIn("Information not available", response.bot_message)

    async def test_active_qa_routes_text_without_general_question_detection(self) -> None:
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._common_user_input_quality_response = AsyncMock(return_value=None)
        service._could_be_fuzzy_selection = lambda session, message: True
        session = ChatSession(
            country="Germany", region="Baden-Württemberg", sector="Energy",
            selected_hazard="Heat stress", phase="socio_demographic_review",
            hazard_qa_active=True,
        )
        response = ChatResponse(
            session_id="session-1", step="socio_demographic_review",
            bot_message="Answer", session=session.summary(),
        )
        service._handle_hazard_qa_question = AsyncMock(return_value=response)
        service._handle_anytime_grounded_question = AsyncMock()

        result = await service._chat_response("session-1", session, "Tell me why")

        self.assertIs(result, response)
        service._handle_hazard_qa_question.assert_awaited_once()
        service._handle_anytime_grounded_question.assert_not_awaited()

    async def test_cta_opens_qa_and_keeps_existing_actions(self) -> None:
        service = ChatHazardStepsMixin()
        session = ChatSession(selected_hazard="Heat stress", phase="socio_demographic_review")

        response = await service._handle_socio_demographic_review(
            "session-1", session, "Know more about the hazard"
        )

        self.assertTrue(session.hazard_qa_active)
        self.assertEqual(response.step, "socio_demographic_review")
        self.assertEqual(
            [option.label for option in response.options],
            [option.label for option in SOCIO_DEMOGRAPHIC_OPTIONS],
        )
        self.assertIn("Heat stress", response.bot_message)

    async def test_existing_action_leaves_qa(self) -> None:
        service = ChatService.__new__(ChatService)
        service._handle_other_nav_action = AsyncMock(return_value=None)
        service._open_selection_response_from_any_step = AsyncMock(return_value=None)
        service._common_user_input_quality_response = AsyncMock(return_value=None)
        service._could_be_fuzzy_selection = lambda session, message: True
        session = ChatSession(
            country="Germany", region="Baden-Württemberg", sector="Energy",
            selected_hazard="Heat stress", phase="socio_demographic_review",
            hazard_qa_active=True,
        )
        response = ChatResponse(
            session_id="session-1", step="socio_demographic_review",
            bot_message="Continuing", session=session.summary(),
        )
        service._handle_socio_demographic_review = AsyncMock(return_value=response)

        result = await service._chat_response("session-1", session, "Add more DGs")

        self.assertIs(result, response)
        self.assertFalse(session.hazard_qa_active)
        service._handle_socio_demographic_review.assert_awaited_once_with(
            "session-1", session, "Add more DGs"
        )

    async def test_question_returns_exact_fallback_without_sources(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        service._question_stats_context = AsyncMock(return_value=("", {}))
        session = ChatSession(selected_hazard="Heat stress", hazard_qa_active=True)

        with patch("app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What causes it?"
            )

        ask_llm.assert_not_awaited()
        self.assertIn("Information not available to answer this.", response.bot_message)
        self.assertEqual(response.options, SOCIO_DEMOGRAPHIC_OPTIONS)

    async def test_question_accepts_only_a_claim_with_a_matching_source_quote(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        source = {
            "S1": {
                "id": "S1", "title": "Hazard report", "source_type": "Knowledge Base",
                "source_uri": "", "page": "2", "excerpt": "Heating costs rose in winter.",
                "evidence": "Heating costs rose in winter.",
            }
        }
        service._hazard_qa_knowledge_context = AsyncMock(side_effect=lambda session, question, scope: (
            ("- [S1] Hazard report: Heating costs rose in winter.", source)
            if scope == MAIN_KB_SCOPE else ("", {})
        ))
        service._question_stats_context = AsyncMock(return_value=("", {}))
        session = ChatSession(selected_hazard="Heating costs increase", hazard_qa_active=True)

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "Heating costs increased during winter.",
                "source_id": "S1", "quote": "Heating costs rose in winter.",
            }]}),
        ):
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What happened to heating costs?"
            )

        self.assertIn("Heating costs increased during winter.", response.bot_message)
        self.assertNotIn("“Heating costs rose in winter.”", response.bot_message)
        self.assertIn("source-citation", response.bot_message)
        self.assertEqual(response.options, SOCIO_DEMOGRAPHIC_OPTIONS)

        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "Costs doubled.", "source_id": "S1", "quote": "Costs doubled.",
            }]}),
        ):
            unsupported = await service._handle_hazard_qa_question(
                "session-1", session, "Did costs double?"
            )

        self.assertIn("Information not available to answer this.", unsupported.bot_message)
        self.assertNotIn("Costs doubled.", unsupported.bot_message)

    async def test_llm_bracketed_source_id_and_source_heading_keep_verified_answer(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        evidence = (
            "Selected hazard: Heat costs Profile: Households with home problems "
            "| Regional population share: 11.0% | National population share: 25.0%"
        )
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "The national population share shown is 25.0%.",
                "source_id": "[DB1]",
                "quote": "Displayed profile data: " + evidence,
            }]}),
        ):
            answer = await service._hazard_qa_answer_from_sources(
                ChatSession(selected_hazard="Heat costs"),
                "What's the national population for households with home problems?",
                "", f"- [DB1] Displayed profile data: {evidence}",
                {"DB1": {"evidence": evidence}},
            )

        self.assertIn("25.0%", answer)
        self.assertIn("[DB1]", answer)
        self.assertFalse(service._hazard_qa_quote_in_excerpt(
            "Displayed profile data: National population share: 99.0%", evidence,
        ))

    async def test_sector_stats_and_knowledge_base_are_both_searched(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._question_stats_context = AsyncMock(return_value=(
            "- [SP1] Sector stats: 24% of regional households were affected.",
            {"SP1": {"evidence": "24% of regional households were affected."}},
        ))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=("", {}))
        session = ChatSession(selected_hazard="Heating costs increase")
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "24% of regional households were affected.",
                "source_id": "SP1", "quote": "24% of regional households were affected.",
            }]}),
        ):
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What share was affected?"
            )

        self.assertIn("24%", response.bot_message)
        self.assertEqual(service._hazard_qa_knowledge_context.await_count, 3)

    async def test_falls_through_main_kb_to_validated_evidence(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._question_stats_context = AsyncMock(return_value=("", {}))
        searched: list[str] = []

        async def retrieve(session, question, scope):
            searched.append(scope)
            if scope == VALIDATED_EVIDENCE_SCOPE:
                return (
                    "- [S1] Validated evidence: Cooling bills increased.",
                    {"S1": {"evidence": "Cooling bills increased."}},
                )
            return "", {}

        service._hazard_qa_knowledge_context = retrieve
        session = ChatSession(selected_hazard="Heating costs increase")
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "Cooling bills increased.",
                "source_id": "S1", "quote": "Cooling bills increased.",
            }]}),
        ):
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What happened to cooling bills?"
            )

        self.assertEqual(searched, [MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, "temporary"])
        self.assertIn("Cooling bills increased.", response.bot_message)

    async def test_sector_stats_and_main_kb_reach_one_llm_answer(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._question_stats_context = AsyncMock(return_value=(
            "- [SP1] Sector stats: A regional share is reported.",
            {"SP1": {"evidence": "A regional share is reported."}},
        ))
        service._hazard_qa_knowledge_context = AsyncMock(return_value=(
            "- [S1] Knowledge Base: Heating costs rose in winter.",
            {"S1": {"evidence": "Heating costs rose in winter."}},
        ))
        session = ChatSession(selected_hazard="Heating costs increase")
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat",
            new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                    "answer": "Heating costs rose in winter.",
                    "source_id": "S1", "quote": "Heating costs rose in winter.",
                }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What happened to heating costs?"
            )

        self.assertEqual(ask_llm.await_count, 1)
        self.assertEqual(
            [call.args[2] for call in service._hazard_qa_knowledge_context.await_args_list],
            [MAIN_KB_SCOPE, VALIDATED_EVIDENCE_SCOPE, "temporary"],
        )
        self.assertIn("A regional share is reported.", ask_llm.await_args.kwargs["messages"][0]["content"])
        self.assertIn("Heating costs rose in winter.", ask_llm.await_args.kwargs["messages"][0]["content"])
        self.assertIn("Heating costs rose in winter.", response.bot_message)

    async def test_duplicate_knowledge_ids_keep_both_sources_available(self) -> None:
        service = ChatGroundedQuestionStepsMixin()
        service._hazard_qa_sector_context = AsyncMock(return_value=("", {}))

        async def retrieve(session, question, scope):
            if scope == MAIN_KB_SCOPE:
                return "[S1] Main KB: General hazard context.", {
                    "S1": {"evidence": "General hazard context."},
                }
            if scope == VALIDATED_EVIDENCE_SCOPE:
                return "[S1] Validated evidence: National share is 25.0%.", {
                    "S1": {"evidence": "National share is 25.0%."},
                }
            return "", {}

        service._hazard_qa_knowledge_context = retrieve
        session = ChatSession(selected_hazard="Heat costs")
        with patch(
            "app.services.chat_grounded_question_steps.ask_llm_chat", new_callable=AsyncMock,
            return_value=json.dumps({"claims": [{
                "answer": "The national share is 25.0%.",
                "source_id": "S1_2", "quote": "National share is 25.0%.",
            }]}),
        ) as ask_llm:
            response = await service._handle_hazard_qa_question(
                "session-1", session, "What is the national share?",
            )

        context = ask_llm.await_args.kwargs["messages"][0]["content"]
        self.assertIn("[S1] Main KB", context)
        self.assertIn("[S1_2] Validated evidence", context)
        self.assertIn("25.0%", response.bot_message)
        self.assertIn("source-citation", response.bot_message)
