"""Keyboard behaviour of the navigation and touch target sizes (mobile viewport)."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.a11y

CONTROLS = "header nav a, header nav button, header nav select"
# Computed outline and shadow of an element, compared between its unfocused and focused states.
HELPERS_JS = """(selector) => {
  window.focusStyle = (el) => {
    const s = getComputedStyle(el);
    return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow].join("|");
  };
  const visible = [...document.querySelectorAll(selector)].filter(
    (el) => el.offsetParent !== null,
  );
  visible.forEach((el, i) => { el.dataset.probe = i; });
  return Object.fromEntries(visible.map((el, i) => [i, window.focusStyle(el)]));
}"""


@pytest.fixture
def mobile(world, open_page):
    return open_page(reverse("home"), role="employee", viewport="mobile")


def _active_id(page):
    return page.evaluate("document.activeElement.id || document.activeElement.tagName")


def test_skip_link_is_first_and_moves_focus_to_main(mobile):
    mobile.keyboard.press("Tab")
    assert mobile.evaluate("document.activeElement.className") == "skip-link"
    mobile.keyboard.press("Enter")
    assert _active_id(mobile) == "main"


def test_offcanvas_opens_closes_and_restores_focus(mobile):
    toggler = mobile.locator("button.navbar-toggler")
    toggler.focus()
    mobile.keyboard.press("Enter")
    menu = mobile.locator("#main-menu")
    menu.wait_for(state="visible")
    mobile.wait_for_function("document.querySelector('#main-menu').classList.contains('show')")
    mobile.wait_for_timeout(500)
    assert mobile.evaluate("document.querySelector('#main-menu').contains(document.activeElement)")
    mobile.keyboard.press("Escape")
    mobile.wait_for_function("!document.querySelector('#main-menu').classList.contains('show')")
    mobile.wait_for_timeout(500)
    assert mobile.evaluate("document.activeElement.classList.contains('navbar-toggler')")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_user_menu_opens_and_items_reachable_with_arrows(mobile, key):
    mobile.locator("button.navbar-toggler").click()
    mobile.wait_for_timeout(500)
    toggle = mobile.locator("#user-menu > button")
    toggle.focus()
    mobile.keyboard.press(key)
    assert toggle.get_attribute("aria-expanded") == "true"
    items = mobile.locator("#user-menu .dropdown-item")
    seen = []
    for _ in range(items.count()):
        mobile.keyboard.press("ArrowDown")
        seen.append(mobile.evaluate("document.activeElement.textContent.trim()"))
    assert len(set(seen)) == items.count()
    mobile.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"


def _focus_styles_by_tabbing(page):
    """Style of each visible navbar control when unfocused and when reached with Tab."""
    unfocused = page.evaluate(HELPERS_JS, CONTROLS)
    focused = {}
    for _ in range(len(unfocused) + 3):
        page.keyboard.press("Tab")
        state = page.evaluate(
            """() => {
              const el = document.activeElement;
              return el.closest("header nav") ? [el.dataset.probe, window.focusStyle(el)] : null;
            }"""
        )
        if state:
            focused[state[0]] = state[1]
    return unfocused, focused


def test_navbar_controls_show_a_focus_indicator(mobile):
    for open_menu in (False, True):
        if open_menu:
            mobile.locator("button.navbar-toggler").click()
            mobile.wait_for_timeout(500)
        unfocused, focused = _focus_styles_by_tabbing(mobile)
        assert focused, "Tab never reached the navigation"
        for probe, style in focused.items():
            assert style != unfocused[probe], f"no visible focus indicator on control {probe}"


def _assert_touch_targets(page, selector):
    elements = page.locator(selector)
    assert elements.count() > 0
    for i in range(elements.count()):
        element = elements.nth(i)
        if not element.is_visible():
            continue
        box = element.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, (
            f"{element.evaluate('el => el.outerHTML')[:90]} is {box['width']}x{box['height']}"
        )


def test_navigation_touch_targets(mobile):
    mobile.locator("button.navbar-toggler").click()
    mobile.wait_for_timeout(500)
    mobile.locator("#user-menu > button").click()
    _assert_touch_targets(mobile, "header nav a, header nav button:not(.btn-close)")
    _assert_touch_targets(mobile, "header nav .btn-close")


def test_pagination_touch_targets(world, open_page):
    page = open_page(reverse("audit:event_list"), role="auditor", viewport="mobile")
    _assert_touch_targets(page, "nav[aria-label] .pagination a.page-link")
