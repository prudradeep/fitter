"""Conservative evidence labels for generated claims in chat responses."""

import html
import re


_BLOCK = re.compile(r"(<(p|li)\b[^>]*>)(.*?)(</\2>)", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_PROMPT_START = re.compile(
    r"^(?:please|choose|select|type|enter|upload|provide|confirm|continue|"
    r"review|tell me|how |what |which |would |do you|you can|if you|"
    r"for example|examples?:|click|add |remove |revise |edit )\b",
    re.I,
)
_LABEL = re.compile(r"\b(?:Documented|Inferred)\b", re.I)
_CLAIM_VERB = re.compile(
    r"\b(?:is|are|was|were|has|have|may|can|will|face|faces|affect|affects|"
    r"increase|increases|reduce|reduces|depend|depends|receive|receives|"
    r"lack|lacks|experience|experiences|support|supports|prevent|prevents|"
    r"result|results|lead|leads)\b",
    re.I,
)


def label_generated_claims(message: str) -> str:
    """Mark unverified generated statements without changing instructions or questions.

    Source-backed renderers add their own verified label. This fallback never
    upgrades a claim merely because a source button is present.
    """
    def label(match: re.Match[str]) -> str:
        opening, tag, body, closing = match.groups()
        if "evidence-basis-label" in body or _LABEL.search(body):
            return match.group(0)
        if "<li" in body.casefold() or "<ul" in body.casefold() or "<ol" in body.casefold():
            return match.group(0)
        plain = html.unescape(_TAG.sub(" ", body))
        plain = re.sub(r"\s+", " ", plain).strip()
        if len(plain) < 25 or "?" in plain or _PROMPT_START.match(plain):
            return match.group(0)
        if not _CLAIM_VERB.search(plain):
            return match.group(0)
        return f'{opening}{body} <span class="evidence-basis-label">Inferred</span>{closing}'

    return _BLOCK.sub(label, message)
