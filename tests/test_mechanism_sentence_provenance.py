import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.chat_service import ChatService
from app.services.chat_hazard_steps import (
    _complete_policy_mechanism,
    _policy_summary_for_display,
)
from app.services.custom_hazard_validation import suggest_custom_hazard_mechanisms
from app.services.chat_session import ChatSession
from app.services.summary_sources import policy_point_sources


class MechanismSentenceProvenanceTests(unittest.TestCase):
    def test_policy_fragment_uses_complete_source_sentence(self):
        source = (
            "The policy requires all road users to follow the written guidelines "
            "when operating vehicles on public roads."
        )
        fragment = source[:-8]
        with patch("app.services.chat_hazard_steps.ask_llm_chat", new_callable=AsyncMock) as llm:
            sentence, basis = asyncio.run(_complete_policy_mechanism(fragment, source))

        self.assertEqual(sentence, source)
        self.assertEqual(basis, "Documented")
        llm.assert_not_awaited()

    def test_policy_fragment_without_source_completion_is_labelled_inferred(self):
        fragment = "The policy requires all road users to follow the written guidelines on publ"
        repaired = "The policy requires all road users to follow its written guidelines."
        response = json.dumps({"sentence": repaired, "supporting_quote": ""})
        with patch("app.services.chat_hazard_steps.ask_llm_chat", AsyncMock(return_value=response)):
            sentence, basis = asyncio.run(_complete_policy_mechanism(
                fragment, "The policy contains written guidelines for road users."
            ))

        self.assertEqual(sentence, repaired)
        self.assertEqual(basis, "Inferred")

    def test_long_hazard_mechanism_is_not_cut_mid_sentence(self):
        sentence = (
            "The policy requires transport operators to install charging equipment "
            "at selected depots and to recover those installation costs through higher "
            "fares paid by rural passengers who depend on those services for work and care."
        )
        self.assertGreater(len(sentence), 220)
        response = json.dumps({"mechanisms": [sentence]})
        with patch("app.services.custom_hazard_validation.ask_llm_chat", AsyncMock(return_value=response)) as llm:
            mechanisms = asyncio.run(suggest_custom_hazard_mechanisms(
                "Higher fares for rural passengers", "Transport", "Electric mobility",
            ))

        self.assertEqual(mechanisms, [sentence])
        llm.assert_awaited_once()

    def test_incomplete_hazard_suggestion_is_repaired_before_display(self):
        fragment = "The policy requires operators to install equipment on publ"
        repaired = "The policy requires operators to install equipment on public roads."
        responses = [
            json.dumps({"mechanisms": [fragment]}),
            json.dumps({"mechanism": repaired}),
        ]
        with patch("app.services.custom_hazard_validation.ask_llm_chat", AsyncMock(side_effect=responses)):
            mechanisms = asyncio.run(suggest_custom_hazard_mechanisms(
                "Higher operator costs", "Transport", "Electric mobility",
            ))

        self.assertEqual(mechanisms, [repaired])

    def test_policy_summary_shows_mechanism_and_basis_in_source_modal(self):
        summary = (
            "### Mechanisms\n\n- **Road-user rules:** Road users must follow the written rules. "
            "**Documented**"
        )
        self.assertIn("### Mechanisms", _policy_summary_for_display(summary))
        sources = policy_point_sources(summary, [{
            "title": "Highway Code",
            "content": "Road users must follow the written rules.",
        }])
        self.assertEqual(sources["mechanisms"][0][0], {
            "field": "Basis", "value": "Documented",
        })

    def test_policy_summary_replaces_truncated_mechanism_with_source_sentence(self):
        service = ChatService.__new__(ChatService)
        service._policy_summary_reference = Mock(return_value=None)
        source = (
            "Road users must follow written guidelines when operating vehicles "
            "on public roads."
        )
        response = json.dumps({
            "policy_details": [{"label": "Rule", "text": "Road users follow written guidelines."}],
            "original_objectives": [{"label": "Safety", "text": "The policy aims to improve road safety."}],
            "mechanisms": [{"label": "Mandatory adherence", "text": source[:-5]}],
            "intended_benefits": [{"label": "Safety", "text": "Road safety may improve."}],
            "benefited_groups": [{"label": "Drivers", "text": "Drivers may face fewer crashes."}],
        })
        session = ChatSession(selected_context_policy="Highway Code")
        with patch("app.services.chat_hazard_steps.ask_llm_chat", AsyncMock(return_value=response)):
            summary = asyncio.run(service._summarize_context_policy(session, source, ""))

        self.assertIn(f"{source} **Documented**", summary)
        self.assertNotIn("public ro", summary.replace(source, ""))


if __name__ == "__main__":
    unittest.main()
