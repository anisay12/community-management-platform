"""The compiled front-end assets are committed and exposed through the static finders."""

import re

import pytest
from django.contrib.staticfiles import finders

TOKENS = [
    "color-primary",
    "color-primary-contrast",
    "color-secondary",
    "color-success",
    "color-warning",
    "color-danger",
    "color-info",
    "color-bg",
    "color-surface",
    "color-text",
    "color-text-muted",
    "color-border",
    "color-focus",
    "font-sans",
    "font-mono",
    "radius",
    "space-unit",
    "touch-target",
]


def _css() -> str:
    path = finders.find("core/dist/app.css")
    assert path
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_dist_files_are_served_by_the_finders():
    for name in (
        "app.css",
        "bootstrap.bundle.min.js",
        "htmx.min.js",
        "LICENSES.txt",
        "fonts/bootstrap-icons.woff2",
        "fonts/bootstrap-icons.woff",
    ):
        assert finders.find(f"core/dist/{name}"), name
    assert finders.find("core/brand/logo.svg")


def test_app_css_defines_every_token():
    css = _css()
    for token in TOKENS:
        assert re.search(rf"--tl-{token}:", css), token


def test_app_css_has_a_dark_mode_block():
    assert "prefers-color-scheme:dark" in _css().replace(" ", "")


def test_icon_fonts_use_a_relative_url_that_resolves():
    urls = re.findall(r'url\("?(fonts/bootstrap-icons\.woff2?)[?"#)]', _css())
    assert urls
    for url in set(urls):
        assert finders.find(f"core/dist/{url}"), url


@pytest.mark.parametrize("name", ["app.css", "bootstrap.bundle.min.js", "htmx.min.js"])
def test_dist_files_reference_no_missing_source_map(name):
    # ManifestStaticFilesStorage (collectstatic in the image build) fails on a dangling .map.
    path = finders.find(f"core/dist/{name}")
    with open(path, encoding="utf-8-sig") as handle:
        assert "sourceMappingURL" not in handle.read()
