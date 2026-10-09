"""Helpers for the component library (``templates/components/``).

Small, dependency-free replacements for what django-widget-tweaks would provide.
"""

from django import template
from django.core.paginator import Paginator
from django.forms import CheckboxInput, CheckboxSelectMultiple, RadioSelect, Select, SelectMultiple
from django.utils.http import urlencode

from accounts.roles import Role

register = template.Library()

# Bootstrap colour (``text-bg-*``) used to badge each role.
ROLE_COLOURS = {
    Role.EMPLOYEE: "secondary",
    Role.COMMUNITY_CREATOR: "info",
    Role.FUNCTIONAL_ADMIN: "primary",
    Role.TECHNICAL_ADMIN: "dark",
    Role.AUDITOR: "warning",
}


def _widget_class(widget) -> str:
    if isinstance(widget, CheckboxInput):
        return "form-check-input"
    if isinstance(widget, SelectMultiple | Select):
        return "form-select"
    if isinstance(widget, RadioSelect | CheckboxSelectMultiple):
        return "form-check-input"
    return "form-control"


@register.simple_tag
def field_widget(field):
    """Render the widget of a bound field with Bootstrap classes and ARIA wiring.

    ``aria-describedby`` lists the help text id and one distinct id per error
    (``<auto_id>_helptext``, ``<auto_id>_error_1``, ...), matching ``form_field.html``.
    """
    attrs = dict(field.field.widget.attrs)
    css = attrs.get("class", "")
    attrs["class"] = f"{css} {_widget_class(field.field.widget)}".strip()
    described = []
    if field.help_text:
        described.append(f"{field.auto_id}_helptext")
    errors = field.errors
    described.extend(f"{field.auto_id}_error_{n}" for n in range(1, len(errors) + 1))
    if described:
        attrs["aria-describedby"] = " ".join(described)
    if errors:
        attrs["aria-invalid"] = "true"
    return field.as_widget(attrs=attrs)


@register.simple_tag(takes_context=True)
def querystring_without_page(context) -> str:
    """The current request's query string, minus ``page`` (for pagination links)."""
    params = [
        (key, value)
        for key, values in context["request"].GET.lists()
        if key != "page"
        for value in values
    ]
    return urlencode(params)


@register.simple_tag
def elided_pages(page_obj):
    """Page numbers to display; ``Paginator.ELLIPSIS`` marks a gap."""
    return list(
        page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)
    )


@register.simple_tag
def ellipsis_marker() -> str:
    return str(Paginator.ELLIPSIS)


@register.filter
def role_label(code) -> str:
    """Translated label of a role code; an unknown code is returned unchanged."""
    try:
        return str(Role(code).label)
    except ValueError:
        return str(code)


@register.filter
def role_colour(code) -> str:
    try:
        return ROLE_COLOURS[Role(code)]
    except (ValueError, KeyError):
        return "secondary"
