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
}


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
    violations = run_axe(page)
    minor = [v for v in violations if v["impact"] not in BLOCKING]
    if minor:
        print(f"\n{name} {scheme} {viewport}: non-blocking findings\n{_format(minor)}")
    blocking = [v for v in violations if v["impact"] in BLOCKING]
    assert not blocking, f"{name} ({scheme}, {viewport}):\n{_format(blocking)}"
