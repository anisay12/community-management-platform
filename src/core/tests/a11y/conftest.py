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


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    """Set ``DJANGO_ALLOW_ASYNC_UNSAFE`` for the whole life of an a11y test, and only then.

    pytest-playwright's sync API keeps an event loop running in the test thread, which trips
    Django's async-unsafe guard on ORM calls (also during the table flush after each test).
    Wrapping the full protocol covers fixture teardown; the previous value is restored
    afterwards so the variable never reaches non-a11y tests, whatever markers are selected.
    """
    if not item.get_closest_marker("a11y"):
        yield
        return
    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


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
    users["member"] = make_user("jean@example.com", first_name="Jean", last_name="Dupont")
    users.update(_communities(users))
    users.update(_posts(users))
    return users


def _communities(users):
    """A community the employee belongs to (as lead) and an on-request one they do not."""
    from communities.models import Community, CommunityCategory, CommunityMembership

    category, _ = CommunityCategory.objects.get_or_create(
        slug="a11y-category", defaults={"name": "Accessibility category"}
    )
    joined = Community.objects.create(
        name="Data guild",
        slug="data-guild",
        category=category,
        tagline="Everything about data",
        description="## Purpose\n\nShare **practices** and [links](https://example.com).",
        rules="Be kind.",
        member_count=1,
    )
    other = Community.objects.create(
        name="Cloud circle",
        slug="cloud-circle",
        category=category,
        tagline="Cloud practices",
        access_mode=Community.AccessMode.REQUEST,
    )
    forum = Community.objects.create(
        name="Design forum",
        slug="design-forum",
        category=category,
        tagline="Design practices",
        member_count=1,
    )
    CommunityMembership.objects.create(
        community=joined, user=users["employee"], role=CommunityMembership.Role.OWNER
    )
    CommunityMembership.objects.create(community=joined, user=users["member"])
    CommunityMembership.objects.create(
        community=forum, user=users["member"], role=CommunityMembership.Role.OWNER
    )
    return {"community": joined, "other_community": other, "open_community": forum}


def _posts(users):
    """Posts, comments, reactions, reports, bookmarks and tags (L4), made through the services.

    In the data guild (lead: the employee, member: Jean Dupont): a pinned announcement, a
    question with an accepted answer and a reply, an edited article (revisions), a reported
    discussion and a post awaiting review; in the design forum (open, the employee is no
    member) a discussion by Jean.
    """
    from django.utils import timezone

    from posts import services_interactions as interactions
    from posts import services_posts
    from posts.models import Post
    from posts.rendering import render_body

    lead, member = users["employee"], users["member"]
    community, forum = users["community"], users["open_community"]
    announcement = services_posts.create_post(
        actor=lead,
        community=community,
        kind=Post.Kind.ANNOUNCEMENT,
        title="Welcome to the data guild",
        body="Read the **rules** and introduce yourself, @jean.dupont.",
        tags=["Data", "Governance"],
    )
    services_posts.pin_post(actor=lead, post=announcement)
    question = services_posts.create_post(
        actor=member,
        community=community,
        kind=Post.Kind.QUESTION,
        title="How do you version datasets?",
        body="We keep *copies* of every file. Is there a better way?",
        tags=["Data"],
    )
    answer = interactions.add_comment(actor=lead, post=question, body="We use **DVC**.")
    interactions.add_comment(
        actor=member, post=question, body="Thanks, I will try it.", parent=answer
    )
    services_posts.accept_answer(actor=member, post=question, comment=answer)
    interactions.set_reaction(actor=lead, target=question, kind="useful", present=True)
    interactions.set_reaction(actor=member, target=answer, kind="thanks", present=True)
    article = services_posts.create_post(
        actor=lead,
        community=community,
        kind=Post.Kind.ARTICLE,
        title="Data quality checklist",
        body="1. Validate schemas\n2. Monitor freshness",
    )
    services_posts.update_post(
        actor=lead,
        post=article,
        title="Data quality checklist",
        body="1. Validate schemas\n2. Monitor freshness\n3. Track lineage",
        tags=["Data"],
        version=article.version,
    )
    discussion = services_posts.create_post(
        actor=member,
        community=community,
        kind=Post.Kind.DISCUSSION,
        title="Buy my course",
        body="Cheap data courses.",
    )
    interactions.report(actor=lead, target=discussion, reason="spam", details="Advertising.")
    body = "Draft guidelines for naming tables."
    Post.objects.create(
        community=community,
        author=member,
        author_display="Jean Dupont",
        kind=Post.Kind.DISCUSSION,
        title="Table naming guidelines",
        body=body,
        body_html=render_body(body),
        status=Post.Status.PENDING_REVIEW,
        last_activity_at=timezone.now(),
    )
    collection = interactions.create_collection(actor=lead, name="Reading list")
    interactions.set_bookmark(actor=lead, post=question, present=True)
    interactions.set_bookmark(actor=lead, post=article, present=True, collection=collection)
    shared = services_posts.create_post(
        actor=member,
        community=forum,
        kind=Post.Kind.DISCUSSION,
        title="Colour contrast tips",
        body="Aim for a 4.5:1 ratio for body text.",
    )
    return {
        "announcement": announcement,
        "question": question,
        "article": article,
        "discussion": discussion,
        "forum_post": shared,
    }


@pytest.fixture
def session_cookies(world, verified_login):
    """Session cookie per signed-in role (employees have no MFA requirement)."""
    cookies = {}
    for key in ("employee", "member", "admin", "auditor"):
        client = Client()
        if key in ("employee", "member"):
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
        # Reduced motion turns off Bootstrap's fade transitions, so axe never
        # measures contrast on a toast that is still fading in.
        context = browser.new_context(
            viewport=VIEWPORTS[viewport], color_scheme=scheme, reduced_motion="reduce"
        )
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
