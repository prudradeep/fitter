import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

from pypdf import PdfReader

BOILERPLATE_TAGS = {
    "aside",
    "footer",
    "form",
    "header",
    "nav",
}

BOILERPLATE_ATTR_TERMS = {
    "ad",
    "ads",
    "advert",
    "advertisement",
    "advertising",
    "banner",
    "cookie",
    "cookies",
    "consent",
    "footer",
    "header",
    "modal",
    "nav",
    "navigation",
    "newsletter",
    "paywall",
    "popup",
    "promo",
    "promoted",
    "recommend",
    "related",
    "share",
    "sidebar",
    "social",
    "sponsor",
    "sponsored",
    "subscribe",
}

BOILERPLATE_LINE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*advertisement\s*$",
        r"^\s*sponsored\s+(content|links?)\s*$",
        r"^\s*(accept|reject|manage)\s+(all\s+)?cookies?\b",
        r"\b(cookie|privacy)\s+(settings|preferences|policy)\b",
        r"\bsubscribe\s+(to|for)\s+.*newsletter\b",
        r"\bsign\s+up\s+(for|to)\b",
        r"\bshare\s+(this|on)\b",
        r"\bfollow\s+us\s+on\b",
        r"\brelated\s+(articles?|stories|content)\b",
        r"\bmore\s+from\s+.+$",
        r"\bcontinue\s+reading\b",
    )
)

BLOCK_TAGS = {
    "article",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "p",
    "section",
    "td",
    "th",
    "tr",
}

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def extract_pdf_page_texts(content: bytes, max_pages: int | None = None) -> list[str]:
    reader = PdfReader(io.BytesIO(content))
    pages = reader.pages if max_pages is None else reader.pages[:max_pages]
    return [page.extract_text() or "" for page in pages]


def extract_docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def html_to_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def remove_web_boilerplate_text(text: str) -> str:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "") if line.strip()]
    if len(lines) <= 1:
        lines = [part.strip() for part in re.split(r"\s{2,}", text or "") if part.strip()]
    kept: list[str] = []
    for line in lines:
        if _is_boilerplate_line(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def compact_text(text: str, max_chars: int | None = None) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted[:max_chars] if max_chars is not None else compacted


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self.skip_stack:
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if self._should_skip(tag, attrs):
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.skip_stack:
            self.skip_stack.pop()
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_stack:
            self.parts.append(data)

    def text(self) -> str:
        return remove_web_boilerplate_text("".join(self.parts))

    @staticmethod
    def _should_skip(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "iframe", "svg", "canvas"}:
            return True
        if tag in BOILERPLATE_TAGS:
            return True
        attr_values: list[str] = []
        for name, value in attrs:
            name_key = str(name or "").casefold()
            value_key = str(value or "").casefold()
            if name_key in {"class", "id", "role", "aria-label", "data-testid", "data-component"}:
                attr_values.append(value_key)
        attr_text = " ".join(attr_values)
        if not attr_text:
            return False
        tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", attr_text)
            if token
        }
        return bool(tokens & BOILERPLATE_ATTR_TERMS)


def _is_boilerplate_line(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", line).strip()
    if not normalized:
        return True
    if len(normalized) <= 160 and any(
        pattern.search(normalized) for pattern in BOILERPLATE_LINE_PATTERNS
    ):
        return True
    words = re.findall(r"[A-Za-z]{3,}", normalized)
    if len(words) <= 5 and re.search(
        r"\b(advertisement|sponsored|subscribe|cookie|newsletter|share|follow)\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        return True
    return False
