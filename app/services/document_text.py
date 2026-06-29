import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

from pypdf import PdfReader


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


def compact_text(text: str, max_chars: int | None = None) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted[:max_chars] if max_chars is not None else compacted


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)
