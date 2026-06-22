from functools import lru_cache
from pathlib import Path

import bleach
import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "chat"

ALLOWED_TAGS = [
    "a",
    "article",
    "blockquote",
    "br",
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
    "a": ["href", "rel", "target", "title"],
    "article": ["class"],
    "details": ["class", "open"],
    "div": ["class", "data-categories", "data-labels", "data-value", "data-values", "role", "aria-label"],
    "dl": ["class"],
    "h3": ["class"],
    "span": ["class", "aria-hidden", "aria-label", "title"],
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
    template = get_message_environment().get_template(template_name)
    return markdown_to_html(template.render(**context).strip())


def markdown_to_html(content: str) -> str:
    html = markdown.markdown(
        content,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
