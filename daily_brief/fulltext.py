"""arXiv HTML for one deep-read paper. The text stays in memory and is not saved."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BODY_CHARS = 80_000
_MIN_CHARS = 400
_MAX_DOWNLOAD = 2_000_000
_ID = re.compile(r"(\d{4}\.\d{4,5})")
_REFERENCES = re.compile(r"(?im)^(?:\d+\.?\s*)?(references|bibliography|参考文献)\s*$")
_CONCLUSION = re.compile(r"(?im)^(?:\d+\.?\s*)?(conclusion|conclusions|结论)\s*$")
_AFTER_CONCLUSION = re.compile(
    r"(?im)^(?:\d+\.?\s*)?(appendix|acknowledgements|acknowledgments|references|bibliography|参考文献)\s*$"
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag in {"p", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_url(paper_id: str) -> str | None:
    match = _ID.search(paper_id or "")
    if match is None:
        return None
    return f"https://arxiv.org/html/{match.group(1)}"


def html_to_text(raw: str) -> tuple[str, bool]:
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return _limit_body("\n".join(" ".join(part.split()) for part in "".join(parser.parts).splitlines()))


def load_source(paper: dict, fetch_bytes) -> tuple[str, str]:
    """Return the quotable source and a short basis label for the card."""
    abstract = " ".join((paper.get("abstract") or "").split())
    url = html_url(str(paper.get("id") or ""))
    if fetch_bytes is None or url is None:
        return abstract, "仅摘要（全文页尚未生成）"
    try:
        payload = fetch_bytes(url)
    except Exception:
        return abstract, "仅摘要（全文页尚未生成）"
    if isinstance(payload, bytes):
        raw = payload[:_MAX_DOWNLOAD].decode("utf-8", "replace")
    else:
        raw = str(payload)[:_MAX_DOWNLOAD]
    text, trimmed = html_to_text(raw)
    if len(text) < _MIN_CHARS:
        return abstract, "仅摘要（全文页尚未生成）"
    if trimmed:
        return text, "全文（附录未送入）"
    return text, "全文"


def _limit_body(text: str) -> tuple[str, bool]:
    match = _REFERENCES.search(text)
    if match is not None:
        text = text[: match.start()]
    text = text.strip()
    if len(text) <= _BODY_CHARS:
        return text, False
    window = text[:_BODY_CHARS]
    conclusion = _CONCLUSION.search(window)
    if conclusion is not None:
        rest = window[conclusion.end() :]
        following = _AFTER_CONCLUSION.search(rest)
        if following is not None:
            window = window[: conclusion.end() + following.start()]
    return window.strip(), True
