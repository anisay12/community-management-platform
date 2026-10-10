"""Rendering of post and comment bodies (Markdown, then spans on resolved ``@first.last``
mentions)."""

import re

from core.markdown import render

from .mentions import HANDLE_RE

_TAG_RE = re.compile(r"(<[^>]+>)")
# Inside these elements a mention stays untouched (code samples, links).
_SKIPPED = {"code", "pre", "a"}


def render_body(text: str, resolved_handles=()) -> str:
    """Sanitised HTML of ``text`` where each ``@first.last`` of ``resolved_handles`` (handles
    of members the mention resolves to, see ``mentions.resolve_mentions``) is a
    ``<span class="mention">``; any other ``@first.last`` stays plain text (Decision 15)."""
    html = render(text)
    resolved = {handle.lower() for handle in resolved_handles}
    if not resolved:
        return html

    def wrap(match: re.Match) -> str:
        if match.group(1).lower() not in resolved:
            return match.group(0)
        return f'<span class="mention">@{match.group(1)}</span>'

    parts = _TAG_RE.split(html)
    depth = 0
    for index, part in enumerate(parts):
        if index % 2:  # a tag
            name = part.strip("</>").split()[0].lower() if part.strip("</>") else ""
            if name in _SKIPPED:
                depth += -1 if part.startswith("</") else 1
        elif depth == 0 and "@" in part:
            parts[index] = HANDLE_RE.sub(wrap, part)
    return "".join(parts)
