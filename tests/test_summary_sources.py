import json
import re
import asyncio
from html import unescape

from app.services.chat_mitigation_creation_guided import ChatMitigationCreationGuidedMixin
from app.services.chat_hazard_steps import ChatHazardStepsMixin
from app.services.chat_session import ChatSession
from app.services.message_renderer import markdown_to_html
from app.services.summary_sources import policy_point_sources, policy_summary_with_buttons


def test_policy_points_keep_distinct_document_context_in_chat():
    summary = (
        "### Policy details\n\n"
        "- **Energy grants:** Grants cover household retrofit costs.\n"
        "- **Community power:** Residents receive electricity discounts."
    )
    chunks = [
        {"title": "Grant policy.pdf", "source_uri": "https://example.com/grants.pdf",
         "page_number": 3, "content": "Household retrofit grants cover up-front insulation costs."},
        {"title": "Power policy.pdf", "source_uri": "https://example.com/power.pdf",
         "page_number": 8, "content": "Community power gives residents discounted electricity."},
    ]

    sources = policy_point_sources(summary, chunks)
    rendered = markdown_to_html(policy_summary_with_buttons(summary, sources))
    buttons = re.findall(r'data-source-table="([^"]+)"', rendered)

    assert len(buttons) == 2
    first, second = (json.loads(unescape(button)) for button in buttons)
    assert first[0] == {"field": "Document", "value": "Grant policy.pdf"}
    assert {"field": "Page", "value": "3"} in first
    assert "retrofit grants" in first[-1]["value"]
    assert second[0] == {"field": "Document", "value": "Power policy.pdf"}
    assert {"field": "Page", "value": "8"} in second


def test_mitigation_summary_shows_source_context_for_inputs_and_policy_effects():
    session = ChatSession(
        pending_mitigation_measure="Targeted insulation grants",
        mitigation_mechanisms=["Up-front retrofit costs"],
        mitigation_policy_effects=[{
            "problem": "Rents may rise", "user_position": "agreed",
            "policy_title": "Housing policy", "source_uri": "https://example.com/housing.pdf",
            "page_number": 12, "source_excerpt": "Retrofit costs can be passed to tenants.",
        }],
    )

    summary = asyncio.run(ChatMitigationCreationGuidedMixin()._generate_guided_summary(session))
    rendered = markdown_to_html(summary)
    buttons = re.findall(r'data-source-table="([^"]+)"', rendered)
    tables = [json.loads(unescape(button)) for button in buttons]

    assert len(tables) >= 7
    assert any({"field": "Context used", "value": "Targeted insulation grants"} in rows for rows in tables)
    assert any(
        {"field": "Page", "value": "12"} in rows
        and {"field": "Referenced excerpt", "value": "Retrofit costs can be passed to tenants."} in rows
        for rows in tables
    )


def test_policy_point_uses_user_clarification_when_it_is_the_matching_context():
    summary = "### Intended benefits\n\n- **Local access:** Rural residents receive local access."
    chunks = [{"title": "Policy.pdf", "content": "The policy sets tax rules for operators."}]
    clarification = "Rural residents receive local access to discounted electricity."

    sources = policy_point_sources(summary, chunks, [clarification])

    assert sources["intended benefits"][0] == [
        {"field": "Source", "value": "User-provided clarification"},
        {"field": "Context used", "value": clarification},
    ]


def test_saved_policy_summary_uses_renamed_heading_and_preserves_source():
    session = ChatSession(
        selected_context_policy_summary=(
            "### Target Socio demographic groups\n\n"
            "- **Citizens:** Residents can participate in renewable projects."
        ),
        selected_context_policy_sources={
            "target socio demographic groups": [[
                {"field": "Document", "value": "D4.2.pdf"},
                {"field": "Page", "value": "21"},
            ]],
        },
    )

    display = ChatHazardStepsMixin._policy_summary_display(session)

    assert "### Target actors/beneficiaries" in display
    assert "### Target Socio demographic groups" not in display
    assert "data-source-table=" in display
