from app.services.chat_custom_hazard_grounding import ChatCustomHazardGroundingMixin
from app.services.message_renderer import markdown_to_html
from app.services.custom_hazard_validation import _heuristic_dimension_validation
from app.services.generated_claim_labels import label_generated_claims


def test_fallback_clarification_highlights_an_actual_question():
    message = ChatCustomHazardGroundingMixin._custom_hazard_clarification_message(
        {},
        ["Can you explain how this hazard is linked to the transition policy?"],
        [],
        rejected=True,
    )
    html = label_generated_claims(markdown_to_html(message))

    assert 'class="custom-hazard-clarification-question"' in html
    assert "Can you explain how this hazard is linked to the transition policy?" in html
    assert "Please clarify the hazard, its transition-policy link." not in html
    assert "highlighted question" in html
    assert "[Inferred]" not in html


def test_specific_clarification_questions_render_in_separate_panels():
    details = [
        ("Policy link", "How does this hazard relate to the policy?", "The link is unclear."),
        ("Affected group", "Who is affected by this hazard?", "No group was identified."),
    ]
    message = ChatCustomHazardGroundingMixin._custom_hazard_clarification_message(
        {}, [item[1] for item in details], details, rejected=False,
    )
    html = markdown_to_html(message)

    assert html.count('class="custom-hazard-clarification-question"') == 2
    assert "How does this hazard relate to the policy?" in html
    assert "Who is affected by this hazard?" in html
    assert "highlighted questions" in html


def test_additional_information_status_does_not_receive_evidence_label():
    message = ChatCustomHazardGroundingMixin._custom_hazard_clarification_message(
        {}, ["How is the selected policy linked to this hazard?"],
        [("Policy link", "How is the selected policy linked to this hazard?", "The link is unclear.")],
        rejected=False,
    )
    html = label_generated_claims(markdown_to_html(message))

    assert '<p class="workflow-status">Additional information is required' in html
    assert "validated.</p>" in html
    assert "validated. <span" not in html


def test_policy_summary_is_not_appended_to_fallback_question():
    result = _heuristic_dimension_validation(
        "Electric vehicle charging raises costs for drivers.",
        "Transport", "Spain", "Asturias",
        {
            "selected_policy": "Plan MOVE",
            "selected_policy_summary": "### Policy details\n- **Incentive Program:** Vehicle grants are offered.",
        },
    )
    question = result["dimension_scores"]["policy_objective_fit"]["clarification_question"]

    assert question.endswith("causes or worsens this hazard?")
    assert "Policy details" not in question
    assert "Incentive Program" not in question


def test_saved_policy_question_drops_appended_summary():
    state = {
        "active_validation_dimensions": ["policy_objective_fit"],
        "dimension_scores": {"policy_objective_fit": {
            "score": 0,
            "needs_clarification": True,
            "clarification_question": (
                "Which objective in the selected policy causes this hazard? "
                "### Policy details\n- **Incentive Program:** Vehicle grants are offered."
            ),
            "reason": "The policy link is unclear.",
        }},
    }
    details = ChatCustomHazardGroundingMixin._custom_hazard_missing_dimension_details(state)

    assert details[0][1] == "Which objective in the selected policy causes this hazard?"
