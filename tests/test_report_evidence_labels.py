from app.services.report_export import _measure_summary_lines, _policy_summary_lines


def test_export_keeps_policy_point_basis():
    lines = _policy_summary_lines(
        "### Challenges\n- **Grant gap:** Households receive no monthly grant.",
        {"challenges": [[
            {"field": "Document", "value": "Programme.pdf"},
            {"field": "Referenced excerpt", "value": "The programme supports households."},
        ]]},
    )
    assert any("Grant gap" in line and "[Inferred]" in line for line in lines)


def test_export_labels_generated_measure_summary():
    lines = _measure_summary_lines("### Expected effects\n- Households may pay less for energy.")
    assert any("Households may pay less for energy. [Inferred]" in line for line in lines)
