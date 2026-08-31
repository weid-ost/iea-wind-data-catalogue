"""HTML sanitisation to a safe subset — stdlib only, no new dependency.

Applied in **two** places, and forgetting either is a bug:

1. Before a description is stored or rendered. Zenodo descriptions are
   author-supplied HTML and fixture ``zen-07`` contains a ``<script>`` tag.
2. Before any text is sent to a model. Prompt injection through a harvested
   page is a real attack surface for a system that then writes records; the
   model never sees markup, script, or attributes.

Policy: an allow-list of structural tags, an allow-list of attributes,
``script``/``style`` dropped **with their content**, all ``on*`` handlers
dropped, and only ``http``/``https``/``mailto`` URL schemes permitted.
Anything not on a list is removed; text content is always kept.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

__all__ = [
    "ALLOWED_TAGS",
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_SCHEMES",
    "VOID_TAGS",
    "sanitize_html",
    "html_to_text",
]

#: Tags kept. Deliberately small: a catalogue description is prose and links.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a", "abbr", "b", "blockquote", "br", "code", "dd", "dl", "dt", "em",
        "h3", "h4", "h5", "h6", "i", "li", "ol", "p", "pre", "s", "small",
        "span", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead",
        "tr", "u", "ul",
    }
)

#: Attributes kept, per tag. Everything else — including every ``on*`` handler,
#: ``style``, ``class`` and ``id`` — is dropped.
ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "abbr": frozenset({"title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
}

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

#: Tags that never have a closing partner.
VOID_TAGS: frozenset[str] = frozenset({"br"})

#: Tags whose *content* is discarded along with the tag.
_DROP_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "template", "svg"})

_SCHEME_RE = re.compile(r"^\s*([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def _safe_url(value: str) -> str | None:
    """Return the URL if its scheme is allowed (or it is relative), else ``None``."""
    candidate = (value or "").strip().replace("\x00", "")
    if not candidate:
        return None
    # Strip whitespace that "javas\tcript:" style obfuscation relies on.
    probe = "".join(candidate.split())
    match = _SCHEME_RE.match(probe)
    if match:
        return candidate if match.group(1).lower() in ALLOWED_SCHEMES else None
    if probe.startswith("//"):
        return None  # protocol-relative: scheme unknown, so refuse
    return candidate


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppress_depth = 0
        self._open: list[str] = []

    # -- tags ---------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._suppress_depth += 1
            return
        if self._suppress_depth or tag not in ALLOWED_TAGS:
            return
        rendered = self._render_attrs(tag, attrs)
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}{rendered} />")
        else:
            self.parts.append(f"<{tag}{rendered}>")
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._suppress_depth or tag in _DROP_CONTENT or tag not in ALLOWED_TAGS:
            return
        self.parts.append(f"<{tag}{self._render_attrs(tag, attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self._open:
            while self._open:
                open_tag = self._open.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    # -- content ------------------------------------------------------------
    def handle_data(self, data: str) -> None:
        if self._suppress_depth:
            return
        self.parts.append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return  # comments are always dropped

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _render_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = ALLOWED_ATTRIBUTES.get(tag, frozenset())
        rendered: list[str] = []
        for name, value in attrs:
            name = (name or "").lower()
            if name.startswith("on") or name not in allowed:
                continue
            value = value or ""
            if name in ("href", "src"):
                safe = _safe_url(value)
                if safe is None:
                    continue
                value = safe
            rendered.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a" and any(part.startswith(" href=") for part in rendered):
            rendered.append(' rel="nofollow noopener"')
        return "".join(rendered)

    def close_all(self) -> None:
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")


def sanitize_html(html: str | None) -> str:
    """Reduce ``html`` to the safe subset. Never raises; never returns ``None``."""
    if not html:
        return ""
    parser = _Sanitizer()
    parser.feed(str(html))
    parser.close()
    parser.close_all()
    return "".join(parser.parts)


class _Texter(HTMLParser):
    _BLOCK = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
         "blockquote", "pre", "table", "section", "article"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._suppress += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._suppress = max(0, self._suppress - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self.parts.append(data)


def html_to_text(html: str | None) -> str:
    """Plain text with markup removed — the only shape sent to a model.

    Script and style content is discarded, block tags become newlines, runs of
    whitespace collapse. Combined with trafilatura's main-content extraction
    this is what keeps token cost and prompt-injection surface down (fixtures
    ``zen-07``, ``iea-10``).
    """
    if not html:
        return ""
    parser = _Texter()
    parser.feed(str(html))
    parser.close()
    text = "".join(parser.parts)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()
