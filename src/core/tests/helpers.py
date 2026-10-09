"""Shared test helpers."""

import re


def assert_single_h1(response) -> None:
    """Assert the rendered page has exactly one ``h1`` (WCAG heading structure)."""
    html = response.content.decode()
    assert len(re.findall(r"<h1[\s>]", html)) == 1, "expected exactly one <h1>"
