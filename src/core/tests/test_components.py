import re

import pytest
from django import forms
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.utils import translation


def _page(number, total=200, per_page=10):
    return Paginator(range(total), per_page).page(number)


# --- pagination -----------------------------------------------------------------------------


def test_pagination_links_carry_querystring_and_current_page():
    html = render_to_string(
        "components/pagination.html", {"page_obj": _page(5), "querystring": "q=django&sort=name"}
    )
    assert 'aria-label="Pagination"' in html
    assert 'href="?q=django&amp;sort=name&amp;page=4"' in html
    assert 'href="?q=django&amp;sort=name&amp;page=6"' in html
    assert html.count('aria-current="page"') == 1
    assert re.search(r'<a [^>]*aria-current="page">5</a>', html)


def test_pagination_without_querystring():
    html = render_to_string("components/pagination.html", {"page_obj": _page(2), "querystring": ""})
    assert 'href="?page=1"' in html


def test_pagination_first_page_has_no_previous_link_and_disabled_is_not_focusable():
    html = render_to_string("components/pagination.html", {"page_obj": _page(1), "querystring": ""})
    assert "page=0" not in html
    assert 'href="?page=2"' in html
    assert 'aria-disabled="true"' in html
    disabled = re.findall(r'<[^>]*aria-disabled="true"[^>]*>', html)
    assert disabled and all(not tag.startswith("<a") for tag in disabled)


def test_pagination_has_ellipsis_for_long_ranges():
    html = render_to_string(
        "components/pagination.html", {"page_obj": _page(10), "querystring": ""}
    )
    assert "…" in html
    assert 'href="?page=20"' in html
    assert 'href="?page=15"' not in html


def test_pagination_short_range_has_no_ellipsis():
    html = render_to_string(
        "components/pagination.html", {"page_obj": _page(1, total=30), "querystring": ""}
    )
    assert "…" not in html


def test_querystring_without_page_helper():
    from django.test import RequestFactory

    from core.templatetags.component_tags import querystring_without_page

    request = RequestFactory().get("/", {"q": "a b", "page": "3", "tag": ["x", "y"]})
    assert querystring_without_page({"request": request}) == "q=a+b&tag=x&tag=y"
    assert querystring_without_page({"request": RequestFactory().get("/?page=2")}) == ""


# --- form field -----------------------------------------------------------------------------


class SampleForm(forms.Form):
    name = forms.CharField(label="Name", help_text="Your full name")
    agree = forms.BooleanField(label="Agree")
    kind = forms.ChoiceField(label="Kind", choices=[("a", "A")])


def _bound(data):
    form = SampleForm(data)
    form.is_valid()
    return form


def test_form_field_errored_has_aria_invalid_and_describedby_for_every_error():
    form = _bound({"name": "", "agree": "on", "kind": "a"})
    form.add_error("name", "Second problem")
    html = render_to_string("components/form_field.html", {"field": form["name"]})
    assert 'aria-invalid="true"' in html
    described = re.search(r'aria-describedby="([^"]+)"', html).group(1).split()
    assert "id_name_helptext" in described
    error_ids = [i for i in described if i != "id_name_helptext"]
    assert len(error_ids) == 2 and len(set(error_ids)) == 2
    for error_id in error_ids:
        assert f'id="{error_id}"' in html
    assert 'class="form-control' in html
    assert 'for="id_name">Name</label>' in html


def test_form_field_without_error_is_valid_markup():
    form = SampleForm()
    html = render_to_string("components/form_field.html", {"field": form["name"]})
    assert "aria-invalid" not in html
    assert 'aria-describedby="id_name_helptext"' in html


def test_form_field_widget_classes():
    form = _bound({})
    assert "form-check-input" in render_to_string(
        "components/form_field.html", {"field": form["agree"]}
    )
    assert "form-select" in render_to_string("components/form_field.html", {"field": form["kind"]})


def test_form_errors_summary_links_to_each_field():
    form = _bound({"name": "", "kind": "zzz"})
    html = render_to_string("components/form_errors.html", {"form": form})
    assert 'tabindex="-1"' in html
    assert "<h2" in html
    assert 'href="#id_name"' in html and 'href="#id_kind"' in html


def test_form_errors_renders_nothing_for_valid_form():
    form = _bound({"name": "x", "agree": "on", "kind": "a"})
    assert render_to_string("components/form_errors.html", {"form": form}).strip() == ""


# --- misc components ------------------------------------------------------------------------


def test_breadcrumb_last_item_is_current():
    html = render_to_string(
        "components/breadcrumb.html",
        {"items": [("Home", "/"), ("Users", "/manage/"), ("Alice", "")]},
    )
    assert html.count('aria-current="page"') == 1
    assert re.search(r'aria-current="page"[^>]*>Alice<', html)
    assert 'href="/manage/"' in html


def test_modal_labelled_by_title():
    html = render_to_string(
        "components/modal.html",
        {
            "id": "confirm-x",
            "title": "Delete it?",
            "body": "Sure?",
            "confirm_action": "/do/",
            "confirm_label": "Delete",
            "cancel_label": "Cancel",
        },
    )
    assert 'aria-labelledby="confirm-x-title"' in html
    assert 'id="confirm-x-title"' in html
    assert 'action="/do/"' in html
    assert 'method="post"' in html


def test_toast_error_is_alert_and_other_is_status():
    error = render_to_string("components/toast.html", {"message": "Boom", "level": "error"})
    assert 'role="alert"' in error
    ok = render_to_string("components/toast.html", {"message": "Fine", "level": "success"})
    assert 'role="status"' in ok and 'role="alert"' not in ok


def test_messages_component_reuses_toast():
    from django.contrib.messages.storage.base import Message

    html = render_to_string(
        "components/messages.html", {"messages": [Message(40, "Bad"), Message(25, "Good")]}
    )
    assert 'role="alert"' in html and 'role="status"' in html
    assert "tl-toast-error" in html and "tl-toast-success" in html


def test_role_badge_translated_label_in_french():
    with translation.override("fr"):
        html = render_to_string("components/role_badge.html", {"role": "employee"})
    assert "Collaborateur" in html
    assert "Employee" not in html


def test_role_badge_unknown_code_is_escaped():
    html = render_to_string("components/role_badge.html", {"role": "<b>x</b>"})
    assert "<b>" not in html


def test_avatar_sizes_and_hidden():
    html = render_to_string(
        "components/avatar.html", {"initials": "AB", "size": "lg", "hidden": True}
    )
    assert "tl-avatar-lg" in html and 'aria-hidden="true"' in html and "AB" in html
    shown = render_to_string("components/avatar.html", {"initials": "AB", "label": "Alice B"})
    assert "tl-avatar-md" in shown and 'aria-hidden="true"' not in shown and "Alice B" in shown


def test_tabs_mark_active():
    html = render_to_string(
        "components/tabs.html",
        {"items": [("One", "/1/", True), ("Two", "/2/", False)], "label": "Sections"},
    )
    assert html.count('aria-current="page"') == 1
    assert 'aria-label="Sections"' in html


def test_alert_levels():
    danger = render_to_string(
        "components/alert.html", {"level": "danger", "title": "T", "text": "x", "dismissible": True}
    )
    assert 'role="alert"' in danger and "btn-close" in danger
    info = render_to_string("components/alert.html", {"level": "info", "text": "x"})
    assert 'role="status"' in info and "btn-close" not in info


def test_empty_state_action_optional():
    with_action = render_to_string(
        "components/empty_state.html",
        {"title": "Nothing", "text": "t", "action_url": "/new/", "action_label": "Create"},
    )
    assert 'href="/new/"' in with_action
    without = render_to_string("components/empty_state.html", {"title": "Nothing"})
    assert "<a " not in without


def test_community_card():
    html = render_to_string(
        "components/community_card.html",
        {
            "name": "Python",
            "url": "/c/python/",
            "tagline": "Snakes",
            "category": "Tech",
            "member_count": 12,
            "access_mode": "open",
            "access_mode_label": "Open",
        },
    )
    assert (
        'href="/c/python/"' in html
        and "Snakes" in html
        and "Tech" in html
        and "12" in html
        and "Open" in html
    )


@pytest.mark.parametrize(
    ("template", "context"),
    [
        (
            "components/card.html",
            {
                "title": "<script>alert(1)</script>",
                "url": "/x/",
                "meta": "<i>m</i>",
                "body": "<b>b</b>",
                "footer": "<u>f</u>",
            },
        ),
        (
            "components/community_card.html",
            {
                "name": "<script>x</script>",
                "url": "/x/",
                "tagline": "<b>t</b>",
                "category": "<i>c</i>",
                "member_count": 1,
                "access_mode": "open",
                "access_mode_label": "<u>o</u>",
            },
        ),
        ("components/empty_state.html", {"title": "<script>x</script>", "text": "<b>t</b>"}),
        (
            "components/alert.html",
            {"level": "info", "title": "<script>x</script>", "text": "<b>t</b>"},
        ),
        ("components/toast.html", {"message": "<script>x</script>", "level": "info"}),
        (
            "components/modal.html",
            {
                "id": "m",
                "title": "<script>x</script>",
                "body": "<b>t</b>",
                "confirm_action": "/x/",
                "confirm_label": "<i>",
                "cancel_label": "<u>",
            },
        ),
        ("components/avatar.html", {"initials": "<script>"}),
        ("components/breadcrumb.html", {"items": [("<script>x</script>", "/"), ("<b>", "")]}),
        ("components/tabs.html", {"items": [("<script>x</script>", "/", True)]}),
    ],
)
def test_html_in_arguments_is_escaped(template, context):
    html = render_to_string(template, context)
    assert "<script>" not in html and "<b>" not in html and "<i>" not in html and "<u>" not in html


# --- style guide ----------------------------------------------------------------------------


def test_styleguide_404_unless_debug(client, settings):
    settings.DEBUG = False
    assert client.get("/styleguide/").status_code == 404


def test_styleguide_renders_every_component_in_debug(client, settings):
    settings.DEBUG = True
    response = client.get("/styleguide/")
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("<h1") == 1
    for marker in (
        "tl-card",
        "tl-community-card",
        "tl-empty-state",
        "tl-alert",
        "tl-toast",
        "modal-dialog",
        "pagination",
        "tl-role-badge",
        "tl-avatar",
        "breadcrumb",
        "nav-tabs",
        'aria-invalid="true"',
        "tl-error-summary",
    ):
        assert marker in html, marker


def test_form_field_keeps_the_descriptions_set_on_the_widget():
    """A widget may already point at an element of the page (e.g. a character counter)."""
    form = SampleForm()
    form.fields["name"].widget.attrs["aria-describedby"] = "name-counter"
    html = render_to_string("components/form_field.html", {"field": form["name"]})
    described = re.search(r'aria-describedby="([^"]+)"', html).group(1).split()
    assert described == ["name-counter", "id_name_helptext"]
