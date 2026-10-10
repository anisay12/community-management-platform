"""Keyboard behaviour of the navigation and touch target sizes (mobile viewport)."""

import re

import pytest
from django.urls import reverse
from playwright.sync_api import expect

pytestmark = pytest.mark.a11y

CONTROLS = "header nav a, header nav button, header nav select"
# Computed outline and shadow of an element, compared between its unfocused and focused states.
HELPERS_JS = """(selector) => {
  window.focusStyle = (el) => {
    const s = getComputedStyle(el);
    return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow].join("|");
  };
  const visible = [...document.querySelectorAll(selector)].filter(
    (el) => el.offsetParent !== null && el.checkVisibility({ visibilityProperty: true }),
  );
  visible.forEach((el, i) => { el.dataset.probe = i; });
  return Object.fromEntries(visible.map((el, i) => [i, window.focusStyle(el)]));
}"""


@pytest.fixture
def mobile(world, open_page):
    return open_page(reverse("home"), role="employee", viewport="mobile")


def _open_offcanvas(page):
    """Open the mobile menu and wait until its slide-in transition has finished."""
    page.locator("button.navbar-toggler").click()
    menu = page.locator("#main-menu")
    expect(menu).to_be_visible()
    expect(menu).to_have_class(re.compile(r"\bshow\b"))
    expect(menu).not_to_have_class(re.compile(r"showing"))


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
    expect(menu).to_be_visible()
    expect(menu).to_have_class(re.compile(r"\bshow\b"))
    expect(menu).not_to_have_class(re.compile(r"showing|hiding"))
    assert mobile.evaluate("document.querySelector('#main-menu').contains(document.activeElement)")
    mobile.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    assert mobile.evaluate("document.activeElement.classList.contains('navbar-toggler')")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_user_menu_opens_and_items_reachable_with_arrows(mobile, key):
    _open_offcanvas(mobile)
    toggle = mobile.locator("#user-menu > button")
    toggle.focus()
    mobile.keyboard.press(key)
    assert toggle.get_attribute("aria-expanded") == "true"
    items = mobile.locator("#user-menu .dropdown-item")
    seen = []
    for _ in range(items.count()):
        mobile.keyboard.press("ArrowDown")
        assert mobile.evaluate("document.activeElement.matches('#user-menu .dropdown-item')"), (
            "ArrowDown moved focus outside the user menu items"
        )
        seen.append(mobile.evaluate("document.activeElement.textContent.trim()"))
    assert len(set(seen)) == items.count()
    mobile.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"


def _focus_styles_by_tabbing(page, controls):
    """Style of each visible navbar control when unfocused and when reached with Tab."""
    unfocused = page.evaluate(HELPERS_JS, controls)
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
        controls = CONTROLS
        if open_menu:
            _open_offcanvas(mobile)
            # The open offcanvas traps focus: only its own controls are reachable.
            controls = "#main-menu a, #main-menu button, #main-menu select"
        unfocused, focused = _focus_styles_by_tabbing(mobile, controls)
        assert set(focused) == set(unfocused), (
            f"Tab never reached controls {sorted(set(unfocused) - set(focused))}"
        )
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
    _open_offcanvas(mobile)
    mobile.locator("#user-menu > button").click()
    expect(mobile.locator("#user-menu .dropdown-menu")).to_have_class(re.compile(r"\bshow\b"))
    _assert_touch_targets(mobile, "header nav a, header nav button:not(.btn-close)")
    _assert_touch_targets(mobile, "header nav .btn-close")


def test_pagination_touch_targets(world, open_page):
    page = open_page(reverse("audit:event_list"), role="auditor", viewport="mobile")
    _assert_touch_targets(page, "nav[aria-label] .pagination a.page-link")


# --- Posts and interactions (L4) -------------------------------------------------------------


def _post_url(post):
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _focused_id(page):
    return page.evaluate("document.activeElement.id")


def test_confirmation_modal_takes_and_returns_the_focus(world, open_page):
    page = open_page(_post_url(world["question"]), role="employee")
    trigger = page.locator("[data-tl-confirm=hide-post-modal]")
    trigger.focus()
    page.keyboard.press("Enter")
    modal = page.locator("#hide-post-modal")
    expect(modal).to_be_visible()
    expect(page.locator("#hide-post-modal textarea")).to_be_focused()
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    expect(trigger).to_be_focused()


def test_mention_suggestions_with_the_keyboard(world, open_page):
    page = open_page(reverse("posts:create", args=[world["community"].slug]), role="employee")
    body = page.locator("textarea[name=body]")
    body.focus()
    page.keyboard.type("Hello @jea")
    option = page.locator("#mention-suggestions [role=option]")
    expect(option).to_have_count(1)
    expect(page.locator("#mention-suggestions")).to_be_visible()
    expect(page.locator("#mention-status")).to_have_text(
        "1 member suggested: use the arrow keys, then Enter."
    )
    expect(body).to_have_attribute("aria-autocomplete", "list")
    page.keyboard.press("ArrowDown")
    expect(body).to_have_attribute("aria-activedescendant", option.get_attribute("id"))
    expect(option).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    expect(body).to_have_value("Hello @jean.dupont ")
    expect(body).to_be_focused()
    expect(page.locator("#mention-suggestions")).to_be_hidden()
    page.keyboard.type("and @jea")
    expect(option).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("#mention-suggestions")).to_be_hidden()
    expect(body).to_have_value("Hello @jean.dupont and @jea")


def test_reaction_keeps_the_focus_after_the_swap(world, open_page):
    page = open_page(_post_url(world["announcement"]), role="member")
    button_id = f"react-post-{world['announcement'].public_id}-useful"
    button = page.locator(f"#{button_id}")
    expect(button).to_have_attribute("aria-pressed", "false")
    button.focus()
    page.keyboard.press("Enter")
    expect(button).to_have_attribute("aria-pressed", "true")
    expect(button).to_contain_text("1")
    expect(button).to_be_focused()
    page.keyboard.press("Space")
    expect(button).to_have_attribute("aria-pressed", "false")
    expect(button).to_be_focused()


def test_bookmark_keeps_the_focus_after_the_swap(world, open_page):
    page = open_page(_post_url(world["announcement"]), role="member")
    button = page.locator(f"#bookmark-{world['announcement'].public_id}")
    expect(button).to_have_attribute("aria-pressed", "false")
    button.focus()
    page.keyboard.press("Enter")
    expect(button).to_have_attribute("aria-pressed", "true")
    expect(button).to_be_focused()


def test_comment_published_with_htmx_is_announced_by_a_toast(world, open_page):
    page = open_page(_post_url(world["announcement"]), role="member")
    page.locator("#comment-body").fill("Hello everyone, glad to be here.")
    page.get_by_role("button", name="Publish the comment").click()
    expect(page.locator("p", has_text="Hello everyone, glad to be here.")).to_be_visible()
    toast = page.locator("#tl-toasts [role=status]", has_text="Your comment has been published.")
    expect(toast).to_be_visible()
    expect(page.locator("#tl-toasts")).to_have_attribute("aria-live", "polite")
    # A fresh, empty form follows the new comment.
    expect(page.locator("#comment-body")).to_have_value("")
