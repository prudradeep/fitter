from app.services.generated_claim_labels import label_generated_claims
from app.services.message_renderer import markdown_to_html, render_message
from app.services.chat_profile_rendering import ChatProfileRenderingMixin
from app.services.chat_service import ChatService
from app.services.chat_session import ChatSession
import asyncio
from unittest.mock import patch


def test_generated_claim_gets_conservative_label():
    message = "<p>Temporary grants can interrupt road safety training for transport workers.</p>"
    assert "evidence-basis-label\">[Inferred]" in label_generated_claims(message)


def test_question_and_instruction_are_not_claims():
    message = (
        "<p>How does this hazard affect transport workers in the selected region?</p>"
        "<p>Please choose Confirm affected groups if the reflections look right.</p>"
    )
    assert label_generated_claims(message) == message


def test_existing_label_is_not_duplicated():
    message = (
        "<p>The report identifies higher costs for rural drivers. "
        '<span class="evidence-basis-label">Documented — Moderate</span></p>'
    )
    assert 'evidence-basis-label">[Documented]</span>' in label_generated_claims(message)


def test_workflow_status_is_not_labelled_as_an_inferred_claim():
    message = (
        '<p class="workflow-status">Additional information is required before this '
        'custom hazard can be validated.</p>'
        '<p>Temporary grants can interrupt road safety training for transport workers.</p>'
    )
    rendered = label_generated_claims(message)

    assert "validated. [Inferred]" not in rendered
    assert rendered.count("[Inferred]") == 1


def test_saved_workflow_template_does_not_receive_inferred_label():
    saved_template = (
        "You have selected **{{ country }}**. Please select a region from the list to continue."
    )
    with patch("app.services.message_renderer.load_prompt_from_db", return_value=saved_template):
        rendered = render_message("country_selected.md", country="Germany")

    assert 'class="workflow-copy"' in rendered
    assert "[Inferred]" not in label_generated_claims(rendered)


def test_llm_selection_instruction_does_not_receive_inferred_label():
    service = ChatService.__new__(ChatService)
    session = ChatSession(country="Germany")
    with patch("app.services.chat_service.ask_llm_chat", return_value=(
        "You have selected Germany. Please select a region from the list to continue."
    )):
        rendered = asyncio.run(service._selection_message_from_llm(
            session, event="country_selected", fallback="<p>Fallback</p>",
        ))

    assert 'class="workflow-copy"' in rendered
    assert "[Inferred]" not in label_generated_claims(rendered)


def test_policy_and_mitigation_workflow_copy_is_not_labelled():
    with patch("app.services.message_renderer.load_prompt_from_db", return_value=None):
        policy = render_message(
            "policy_selection.md", country="Germany", region="Bavaria",
            sector="Energy", policies=[],
        )
        mitigation = render_message("mitigation_next.md", selected_policy="StromGVV")
        add_hazard = render_message("add_hazard.md", selected_policy="StromGVV")

    for message in (policy, mitigation, add_hazard):
        assert "[Inferred]" not in label_generated_claims(message)


def test_source_backed_context_does_not_receive_inferred_label():
    message = (
        '<p class="hazard-group-intro source-backed-context">'
        'The predictive model results are augmented with data for Bavaria.</p>'
        '<p>Temporary grants can raise costs for some households.</p>'
    )
    rendered = label_generated_claims(message)

    assert rendered.count("[Inferred]") == 1


def test_profile_intro_does_not_assert_policy_caused_the_hazard():
    markdown = ChatProfileRenderingMixin._format_hazard_profiles_markdown(
        ChatProfileRenderingMixin(), "HIGHER ELECTRICITY BILLS", [],
    )
    rendered = label_generated_claims(markdown_to_html(markdown))

    assert "policy proposal under analysis" not in rendered
    assert "profiles associated with the selected hazard" in rendered
    assert "[Inferred]" not in rendered
