from html.parser import HTMLParser

import pytest
from django.template import Context, Template

from core.markdown import ALLOWED_TAGS, render


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "[click](javascript:alert(1))",
        "[click](JaVaScRiPt:alert(1))",
        '<img src=x onerror="alert(1)">',
        '<a href="https://x" onclick="alert(1)">x</a>',
        "[data](data:text/html;base64,PHNjcmlwdD4=)",
        "![img](https://example.com/x.png)",
        '<iframe src="https://evil"></iframe>',
        "<div style='background:url(javascript:alert(1))'>x</div>",
    ],
)
def test_xss_payloads_are_neutralised(payload):
    parser = _TagCollector()
    parser.feed(render(payload))
    for tag, attrs in parser.tags:
        assert tag in ALLOWED_TAGS
        for name, value in attrs:
            assert not name.startswith("on")
            assert name != "style" or "url" not in (value or "")
            if name == "href":
                assert value.split(":", 1)[0].lower() in {"https", "http", "mailto"}


def test_lists_code_tables_and_links():
    html = render(
        "# Title\n\n- one\n- two\n\n1. first\n\n```python\nprint('x')\n```\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n> quote\n\n**bold** *em* `code`\n\n---\n\n"
        "[site](https://example.com) <mailto:a@example.com>"
    )
    for tag in ("<h1>", "<ul>", "<ol>", "<li>", "<pre>", "<table>", "<th>", "<td>"):
        assert tag in html
    for tag in ("<blockquote>", "<strong>", "<em>", "<code", "<hr>"):
        assert tag in html
    assert 'class="language-python"' in html
    assert 'href="https://example.com"' in html
    assert 'rel="noopener noreferrer nofollow"' in html
    assert 'href="mailto:a@example.com"' in html


def test_sanitiser_strips_html_even_if_the_parser_let_it_through():
    import nh3

    from core import markdown

    dirty = (
        '<p><img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a><script>y</script></p>'
    )
    clean = nh3.clean(
        dirty,
        tags=markdown.ALLOWED_TAGS,
        attributes=markdown.ALLOWED_ATTRIBUTES,
        url_schemes=markdown.ALLOWED_URL_SCHEMES,
        link_rel=markdown.LINK_REL,
    )
    assert "<img" not in clean and "javascript" not in clean and "<script" not in clean


def test_raw_html_is_escaped_not_rendered():
    assert render("<b>x</b>") == "<p>&lt;b&gt;x&lt;/b&gt;</p>\n"


def test_empty_text():
    assert render("") == ""


def test_template_filter():
    html = Template("{% load markdown_tags %}{{ text|markdown }}").render(
        Context({"text": "**hi** <script>x</script>", "none": None})
    )
    assert "<strong>hi</strong>" in html
    assert "<script" not in html
    assert Template("{% load markdown_tags %}{{ none|markdown }}").render(Context({})) == ""
