from unittest.mock import patch

from app.services.message_renderer import render_message


def test_hazard_templates_use_created_wording():
    contexts = {
        "hazard_added.md": {
            "hazard": "Higher travel costs", "original_hazard": "",
            "visibility_notice": "", "summary": "", "reason": "Costs increase.",
            "evidence": "None", "affected_population_groups": "- Rural drivers",
        },
        "hazard_summary_review.md": {
            "hazard": "Higher travel costs", "generated_title": "Higher travel costs",
            "summary": "Travel costs may rise.", "error_reason": "",
        },
        "target_population_question.md": {
            "hazard": "Higher travel costs", "mitigation_proposal": False,
            "error_reason": "", "current": 1, "total": 1,
            "question": "Who is affected?",
        },
    }
    with patch("app.services.message_renderer.load_prompt_from_db", return_value=None):
        for template, context in contexts.items():
            message = render_message(template, **context)
            assert "Hazard to be created" in message
            assert "Hazard to be co-created" not in message


def test_saved_hazard_template_uses_created_wording():
    with patch(
        "app.services.message_renderer.load_prompt_from_db",
        return_value="Hazard to be co-created: {{ hazard }}",
    ):
        message = render_message("hazard_added.md", hazard="Higher travel costs")

    assert "Hazard to be created: Higher travel costs" in message
    assert "Hazard to be co-created" not in message
