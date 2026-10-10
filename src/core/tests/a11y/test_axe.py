"""axe-core scan of every page, in both colour schemes and at desktop and mobile widths."""

import pytest
from django.urls import reverse

from core.tests.a11y.conftest import SCHEMES, VIEWPORTS

pytestmark = pytest.mark.a11y

BLOCKING = {"serious", "critical"}

# name -> (role, path builder taking the ``world`` fixture)
PAGES = {
    "login": (None, lambda w: reverse("accounts:login")),
    "password-reset": (None, lambda w: reverse("accounts:password_reset")),
    "not-found": (None, lambda w: "/this-page-does-not-exist/"),
    "home": ("employee", lambda w: reverse("home")),
    "profile": ("employee", lambda w: reverse("accounts:profile_me")),
    "profile-edit": ("employee", lambda w: reverse("accounts:profile_edit")),
    "preferences": ("employee", lambda w: reverse("accounts:preferences")),
    "data-export": ("employee", lambda w: reverse("accounts:data_export")),
    "manage-user-list": ("admin", lambda w: reverse("manage:user_list")),
    "manage-user-detail": (
        "admin",
        lambda w: reverse("manage:user_detail", args=[w["target"].public_id]),
    ),
    "audit-list": ("auditor", lambda w: reverse("audit:event_list")),
    "audit-detail": (
        "auditor",
        lambda w: reverse("audit:event_detail", args=[w["event"].pk]),
    ),
    "community-catalogue": ("employee", lambda w: reverse("communities:catalogue")),
    "community-detail-member": (
        "employee",
        lambda w: reverse("communities:detail", args=[w["community"].slug]),
    ),
    "community-detail-non-member": (
        "employee",
        lambda w: reverse("communities:detail", args=[w["other_community"].slug]),
    ),
    "community-members": (
        "employee",
        lambda w: reverse("communities:members", args=[w["community"].slug]),
    ),
    "community-manage-settings": (
        "employee",
        lambda w: reverse("communities:manage_settings", args=[w["community"].slug]),
    ),
    "community-manage-members": (
        "employee",
        lambda w: reverse("communities:manage_members", args=[w["community"].slug]),
    ),
    "community-create": ("admin", lambda w: reverse("communities:create")),
    "category-list": ("admin", lambda w: reverse("manage:category_list")),
    "styleguide": ("employee", lambda w: reverse("styleguide")),
    # Posts and interactions (L4).
    "post-feed-member": ("employee", lambda w: reverse("posts:feed", args=[w["community"].slug])),
    "post-feed-non-member": (
        "employee",
        lambda w: reverse("posts:feed", args=[w["open_community"].slug]),
    ),
    "home-feed": ("employee", lambda w: reverse("posts:home_feed")),
    "post-detail-member": ("member", lambda w: _post_url("detail", w["question"])),
    "post-detail-moderator": ("employee", lambda w: _post_url("detail", w["question"])),
    "post-detail-announcement": ("member", lambda w: _post_url("detail", w["announcement"])),
    "post-editor": ("employee", lambda w: reverse("posts:create", args=[w["community"].slug])),
    "post-revisions": ("employee", lambda w: _post_url("revisions", w["article"])),
    "bookmarks": ("employee", lambda w: reverse("posts:bookmarks")),
    "report-form": (
        "member",
        lambda w: reverse("posts:report", args=["post", w["announcement"].public_id]),
    ),
    "moderation-queue-reports": (
        "employee",
        lambda w: reverse("posts:moderation_queue", args=[w["community"].slug]),
    ),
    "moderation-queue-review": (
        "employee",
        lambda w: reverse("posts:moderation_queue", args=[w["community"].slug]) + "?tab=review",
    ),
    "tag-list": ("admin", lambda w: reverse("manage:tag_list")),
}


def _post_url(name, post):
    return reverse(f"posts:{name}", args=[post.community.slug, post.public_id])


def _format(violations):
    lines = []
    for v in violations:
        targets = ", ".join(str(n["target"]) for n in v["nodes"])
        for node in v["nodes"]:
            data = (node.get("any") or [{}])[0].get("data") or {}
            if isinstance(data, dict) and "contrastRatio" in data:
                colors = f"{data.get('fgColor')} on {data.get('bgColor')}"
                targets += f" ({colors}, ratio {data['contrastRatio']})"
        lines.append(f"{v['id']} [{v['impact']}] {targets}\n    {v['helpUrl']}")
    return "\n".join(lines)


def _assert_no_blocking(name, violations):
    minor = [v for v in violations if v["impact"] not in BLOCKING]
    if minor:
        print(f"\n{name}: non-blocking findings\n{_format(minor)}")
    blocking = [v for v in violations if v["impact"] in BLOCKING]
    assert not blocking, f"{name}:\n{_format(blocking)}"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("name", list(PAGES))
def test_no_serious_or_critical_violation(
    name, scheme, viewport, world, open_page, run_axe, settings
):
    role, build = PAGES[name]
    settings.DEBUG = name == "styleguide"  # the style guide exists only in debug mode
    page = open_page(build(world), role=role, scheme=scheme, viewport=viewport)
    assert page.locator("h1").count() >= 1
    _assert_no_blocking(f"{name} ({scheme}, {viewport})", run_axe(page))


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
@pytest.mark.parametrize("scheme", SCHEMES)
def test_editor_with_preview(scheme, viewport, world, open_page, run_axe):
    """The editor once the HTMX preview has rendered Markdown into its live region."""
    path = reverse("posts:create", args=[world["community"].slug])
    page = open_page(path, role="employee", scheme=scheme, viewport=viewport)
    page.locator("textarea[name=body]").fill("# Title\n\nSome **bold** text and a [link](/).")
    page.get_by_role("button", name="Preview").click()
    page.locator("#post-preview strong").wait_for()
    _assert_no_blocking(f"editor preview ({scheme}, {viewport})", run_axe(page))


@pytest.mark.parametrize("scheme", SCHEMES)
def test_open_confirmation_modal(scheme, world, open_page, run_axe):
    """A destructive action's confirmation modal, open (hide a post, with its reason)."""
    page = open_page(_post_url("detail", world["question"]), role="employee", scheme=scheme)
    page.locator("[data-tl-confirm=hide-post-modal]").click()
    page.locator("#hide-post-modal textarea").wait_for()
    page.evaluate("Promise.all(document.getAnimations().map(a => a.finished))")
    _assert_no_blocking(f"hide modal ({scheme})", run_axe(page))
