from functools import lru_cache
from pathlib import Path
import re

import bleach
import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.services.prompt_store import load_prompt_from_db

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "chat"

ALLOWED_TAGS = [
    "a",
    "article",
    "blockquote",
    "br",
    "button",
    "code",
    "details",
    "div",
    "dd",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "small",
    "strong",
    "summary",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "rel", "target", "title", "class"],
    "article": ["class"],
    "button": [
        "class",
        "type",
        "data-open-methodology",
        "data-open-survey-results",
        "data-open-platform-users",
        "data-evidence-url",
        "data-evidence-text",
        "data-source-table",
        "data-metric",
        "data-source-key",
        "data-hazard-name",
        "aria-label",
    ],
    "details": ["class", "open"],
    "summary": ["class"],
    "div": [
        "class", "data-affected", "data-categories", "data-labels",
        "data-mitigation", "data-series", "data-value", "data-values", "role", "aria-label",
    ],
    "dl": ["class"],
    "h2": ["class"],
    "h3": ["class"],
    "p": ["class"],
    "span": ["class", "aria-hidden", "aria-label", "title", "tabindex"],
    "table": ["class"],
    "th": ["scope"],
}


@lru_cache
def get_message_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default_for_string=False, default=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_message(template_name: str, **context: object) -> str:
    prompt = load_prompt_from_db(f"chat/{template_name}")
    if template_name in {
        "hazard_added.md", "hazard_summary_review.md", "target_population_question.md",
    } and prompt:
        prompt = prompt.replace("Hazard to be co-created", "Hazard to be created")
    if template_name == "policy_selection.md" and prompt:
        prompt = prompt.replace(
            'If you are not able to find the policy linked to your context from the above list click on "Add a new policy" to add the policy document for your region (The policy added will be visible to the other users of the system analysing the same geographical context).',
            '<div class="policy-selection-note">\n'
            '<strong>Don\'t see your policy?</strong>\n'
            '<p>Select <strong>Add a new policy</strong> below to provide its document. '
            'The policy will be visible to others working in the same region.</p>\n'
            '</div>',
        )
        prompt = prompt.replace(
            '<li><strong class="add-policy-list-label">Add a new policy</strong> '
            '<small>Provide a document for review</small></li>',
            "",
        )
        if "policy.document_url" not in prompt:
            prompt = prompt.replace(
                '</span>{% if policy.description %}',
                '</span>{% if policy.document_url %} '
                '<a href="{{ policy.document_url }}">View document</a>{% endif %}'
                '{% if policy.description %}',
            )
        prompt = prompt.replace(
            '<a href="{{ policy.document_url }}">View document</a>',
            '<a class="policy-document-link" href="{{ policy.document_url }}">View document</a>',
        )
    if (
        template_name == "reason_confirmation.md"
        and prompt
        and "Adopt mitigation proposal suggested above" in prompt
    ):
        # Older databases can retain this superseded template and override the
        # packaged Yes/No copy. Fall back to the current file in that case.
        prompt = None
    if (
        template_name == "socio_demographic_next.md"
        and prompt
        and prompt.lstrip().startswith(
            "Add more socio-demographic profiles severely affected by the hazard"
        )
    ):
        # Older databases can retain the former option-based instruction and
        # override the packaged free-text review instructions.
        prompt = None
    template = (
        get_message_environment().from_string(prompt)
        if prompt is not None
        else get_message_environment().get_template(template_name)
    )
    return markdown_to_html(template.render(**context).strip())


def markdown_to_html(content: str) -> str:
    content = _separate_markdown_lists(content)
    html = markdown.markdown(
        content,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    return re.sub(
        r'<a\s+href="(https?://[^"]+)"',
        r'<a href="\1" target="_blank" rel="noopener noreferrer"',
        cleaned,
    )


def _separate_markdown_lists(content: str) -> str:
    """Start Markdown list markers in their own block, including after prose."""
    list_item = re.compile(r"^ {0,3}(?:[*+-]|\d+[.)])[ \t]+")
    fence = re.compile(r"^ {0,3}(?:`{3,}|~{3,})")
    lines = content.splitlines()
    normalized: list[str] = []
    in_fence = False
    list_block_active = False

    for line in lines:
        if fence.match(line):
            in_fence = not in_fence
            list_block_active = False
        if not line.strip():
            list_block_active = False
        elif not in_fence and list_item.match(line):
            if normalized and normalized[-1].strip() and not list_block_active:
                normalized.append("")
            list_block_active = True
        normalized.append(line)

    return "\n".join(normalized)
