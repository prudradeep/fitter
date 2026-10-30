from app.services.generated_claim_labels import label_generated_claims


def test_generated_claim_gets_conservative_label():
    message = "<p>Temporary grants can interrupt road safety training for transport workers.</p>"
    assert "evidence-basis-label\">Inferred" in label_generated_claims(message)


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
    assert label_generated_claims(message) == message
