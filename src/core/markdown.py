"""Safe Markdown rendering for user-authored text (community descriptions, rules, ...)."""

import nh3
from markdown_it import MarkdownIt

ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "a", "code", "pre",
    "blockquote", "table", "thead", "tbody", "tr", "th", "td", "em", "strong", "hr", "br",
}  # fmt: skip
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "code": {"class"},
    "th": {"style"},
    "td": {"style"},
    "ol": {"start"},
}
ALLOWED_URL_SCHEMES = {"https", "http", "mailto"}
LINK_REL = "noopener noreferrer nofollow"

# Raw HTML is disabled at the parser level; nh3 is the defence in depth.
_parser = MarkdownIt("commonmark", {"html": False}).enable("table")


def render(text: str) -> str:
    """Render Markdown to sanitised HTML (no raw HTML, no images, safe link schemes)."""
    if not text:
        return ""
    return nh3.clean(
        _parser.render(text),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=LINK_REL,
        filter_style_properties={"text-align"},
    )
