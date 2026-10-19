from app.services.chat_mitigation_steps import ChatMitigationStepsMixin


def test_normalization_keeps_causal_validity_independent_from_evidence():
    result = ChatMitigationStepsMixin._normalize_policy_adaptation_mitigation(
        {
            "status": "clear",
            "adaptation_results": [{
                "adaptation_id": "a1",
                "policy_action": "Add an automatic grant route",
                "mechanism_changed": "Removes an application barrier",
                "direct_effect": "Eligible households can receive support",
                "hazard_reduction": "Lowers exposure to unaffordable bills",
                "causal_link_valid": True,
                "evidence_support": "partially_supported",
                "affected_groups": ["Low-income households"],
                "conditions": ["The route must be funded"],
            }],
            "combined_explanation": "Automatic grants could reduce bill exposure.",
        },
        "Energy poverty",
    )

    adaptation = result["adaptation_results"][0]
    assert result["status"] == "clear"
    assert adaptation["causal_link_valid"] is True
    assert adaptation["evidence_support"] == "partially_supported"
    assert adaptation["conditions"] == ["The route must be funded"]


def test_malformed_response_is_incomplete_without_fabricated_pathway():
    result = ChatMitigationStepsMixin._normalize_policy_adaptation_mitigation(
        {"status": "unexpected", "adaptation_results": "bad"},
        "Flooding",
    )

    assert result == {
        "status": "clarify",
        "selected_hazard": "Flooding",
        "adaptation_results": [],
        "combined_explanation": "",
    }


def test_render_hides_irrelevant_claims_and_shows_missing_link():
    html = ChatMitigationStepsMixin._render_policy_adaptation_mitigation({
        "status": "clarify",
        "combined_explanation": "A valid adaptation may reduce exposure.",
        "adaptation_results": [
            {"causal_link_valid": True, "policy_action": "Add warnings", "mechanism_changed": "Early notice", "direct_effect": "People can prepare", "hazard_reduction": "May reduce injury"},
            {"causal_link_valid": False, "explanation": "Irrelevant", "missing_link": "No link from reporting to flood exposure."},
        ],
    })

    assert "How the Proposed Measure Mitigates the Selected Hazard" in html
    assert "View Causal Pathway" in html
    assert "No link from reporting to flood exposure." in html
    assert "Irrelevant" not in html


def test_mixed_adaptations_render_valid_path_and_unresolved_link_separately():
    html = ChatMitigationStepsMixin._render_policy_adaptation_mitigation({
        "status": "clarify",
        "combined_explanation": "A warning system may reduce exposure by giving residents time to leave.",
        "adaptation_results": [
            {"adaptation_id": "adaptation-1", "causal_link_valid": True,
             "policy_action": "Issue flood warnings", "mechanism_changed": "Earlier alerts",
             "direct_effect": "Residents have time to move", "hazard_reduction": "Reduced exposure"},
            {"adaptation_id": "adaptation-2", "causal_link_valid": False,
             "policy_action": "Publish a policy summary", "missing_link": "How publication changes flood exposure."},
        ],
    })

    assert "Reduced exposure" in html
    assert "How publication changes flood exposure." in html
    assert "Publish a policy summary" not in html
