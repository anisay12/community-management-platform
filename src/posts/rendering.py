"""Rendering of post and comment bodies (Markdown, then ``@first.last`` mention spans)."""

import re

from core.markdown import render

from .mentions import HANDLE_RE

_TAG_RE = re.compile(r"(<[^>]+>)")
# Inside these elements a mention stays untouched (code samples, links).
_SKIPPED = {"code", "pre", "a"}


def _wrap(match: re.Match) -> str:
    return f'<span class="mention">@{match.group(1)}</span>'


def render_body(text: str) -> str:
    """Sanitised HTML of ``text`` where each ``@first.last`` is a ``<span class="mention">``."""
    html = render(text)
    parts = _TAG_RE.split(html)
    depth = 0
    for index, part in enumerate(parts):
        if index % 2:  # a tag
            name = part.strip("</>").split()[0].lower() if part.strip("</>") else ""
            if name in _SKIPPED:
                depth += -1 if part.startswith("</") else 1
        elif depth == 0 and "@" in part:
            parts[index] = HANDLE_RE.sub(_wrap, part)
    return "".join(parts)
