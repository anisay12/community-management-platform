"""Browser fixtures for the accessibility tests: live server, signed-in contexts, axe-core.

Sessions are created with Django's test client (the verified-MFA helper for the functional
administrator and the auditor) against the live server's database, then the session cookie is
copied into the Playwright context. Chromium comes from ``PLAYWRIGHT_BROWSERS_PATH`` when set.
"""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import Group
from django.test import Client
from django.utils import timezone
from pytest_django.plugin import blocking_manager_key

from accounts.roles import Role
from audit.models import AuditEvent

# pytest-playwright's sync API runs an event loop in the test thread, which trips Django's
# async-unsafe guard on ORM calls made from the same thread.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

AXE_PATH = Path(__file__).resolve().parents[4] / "node_modules" / "axe-core" / "axe.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
SCHEMES = ["light", "dark"]


def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/a11y/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.a11y)
            # The live server runs in another thread and connection, so data must be committed.
            item.add_marker(pytest.mark.django_db(transaction=True))


def _ensure_role_groups():
    for role in Role:
        Group.objects.get_or_create(name=role.value)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    """Recreate the role groups (created by a migration) that the table flush just removed."""
    yield
    if item.get_closest_marker("a11y"):
        with item.config.stash[blocking_manager_key].unblock():
            _ensure_role_groups()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "args": ["--no-sandbox"]}


@pytest.fixture
def world(db, make_user, verified_login):
    """Users of each role, a target account and enough audit events to paginate."""
    _ensure_role_groups()
    users = {}
    for key, email, role in [
        ("employee", "employee@example.com", Role.EMPLOYEE),
        ("admin", "fadmin@example.com", Role.FUNCTIONAL_ADMIN),
        ("auditor", "auditor@example.com", Role.AUDITOR),
    ]:
        user = make_user(email, first_name=key.capitalize(), last_name="Tester")
        user.groups.add(Group.objects.get(name=role))
        users[key] = user
    users["target"] = make_user("target@example.com", first_name="Target", last_name="Person")
    base = timezone.now()
    AuditEvent.objects.bulk_create(
        AuditEvent(
            actor=users["employee"],
            action="auth.login" if i % 2 else "user.status_changed",
            target_type="user",
            target_id=str(i),
            changes={"note": f"change {i}"},
            created_at=base - timedelta(minutes=i),
        )
        for i in range(55)
    )
    users["event"] = AuditEvent.objects.order_by("-created_at").first()
    return users


@pytest.fixture
def session_cookies(world, verified_login):
    """Session cookie per signed-in role (the employee has no MFA requirement)."""
    cookies = {}
    for key in ("employee", "admin", "auditor"):
        client = Client()
        if key == "employee":
            client.force_login(world[key])
        else:
            verified_login(client, world[key])
        cookie = client.cookies[django_settings.SESSION_COOKIE_NAME]
        cookies[key] = cookie.value
    return cookies


@pytest.fixture
def open_page(browser, live_server, session_cookies):
    """Return ``open(path, role=None, scheme="light", viewport="desktop")`` -> loaded page."""
    contexts = []

    def _open(path, role=None, scheme="light", viewport="desktop"):
        context = browser.new_context(viewport=VIEWPORTS[viewport], color_scheme=scheme)
        contexts.append(context)
        if role:
            context.add_cookies(
                [
                    {
                        "name": django_settings.SESSION_COOKIE_NAME,
                        "value": session_cookies[role],
                        "url": live_server.url,
                    }
                ]
            )
        page = context.new_page()
        page.goto(live_server.url + path)
        return page

    yield _open
    for context in contexts:
        context.close()


@pytest.fixture
def run_axe():
    """Return ``run_axe(page)`` -> list of violations (dicts) for the WCAG 2.x A/AA rules."""

    def _run(page):
        # Toasts fade in on load: wait for the transitions, axe reads mid-fade colours otherwise.
        page.evaluate(
            "Promise.all(document.getAnimations().map(a => a.finished.catch(() => null)))"
        )
        page.add_script_tag(path=str(AXE_PATH))
        result = page.evaluate(
            "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS
        )
        return result["violations"]

    return _run
