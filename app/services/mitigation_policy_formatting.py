import re

from app.services.chat_formatters import normalize_markdown_text


def normalize_current_policy_measure_title(title: str) -> str:
    cleaned = normalize_markdown_text(str(title or "")).strip()
    cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    if not cleaned:
        return "Current implementation example"
    return cleaned[:1].upper() + cleaned[1:]


def mitigation_reference_link_values(reference_links: str) -> list[str]:
    links = re.findall(r"https?://[^\s;,]+", reference_links)
    if links:
        return links
    cleaned = re.sub(r"\s+", " ", reference_links or "").strip()
    return [cleaned] if cleaned else []


def simplify_mitigation_implementation_summary(summary: str) -> str:
    cleaned = normalize_markdown_text(str(summary or "")).strip()
    if not cleaned:
        return ""
    cleaned = re.sub(
        r'(?i)^for\s+the\s+profile\s+["“][^"”]+["”]\s*,?\s*',
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)^for\s+the\s+profile\s+[^,]+,\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    return cleaned[:1].upper() + cleaned[1:]


def format_mitigation_reference_links(reference_links: str) -> str:
    links = re.findall(r"https?://[^\s;,]+", reference_links)
    if not links:
        return reference_links.strip()
    return "; ".join(f"[Reference {index}]({link})" for index, link in enumerate(links, start=1))
