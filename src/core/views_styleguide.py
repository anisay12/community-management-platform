"""Living style guide: every component of ``templates/components/`` with sample data."""

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils.translation import gettext as _

from accounts.roles import Role


class SampleForm(forms.Form):
    name = forms.CharField(label="Full name", help_text="As it appears on your badge.")
    email = forms.EmailField(label="Email")
    role = forms.ChoiceField(label="Role", choices=[(r.value, r.label) for r in Role])
    agree = forms.BooleanField(label="I accept the code of conduct")


def styleguide(request):
    """Render the style guide; 404 unless ``settings.DEBUG`` is true (checked per request)."""
    if not settings.DEBUG:
        raise Http404
    form = SampleForm({"name": "", "email": "not-an-email", "role": Role.EMPLOYEE.value})
    form.is_valid()
    form.add_error("email", _("A second problem on the same field."))
    context = {
        "form": form,
        "page_obj": Paginator(range(200), 10).page(10),
        "roles": [role.value for role in Role],
        "breadcrumb_items": [
            (_("Home"), "/"),
            (_("Components"), "/styleguide/"),
            (_("Current page"), ""),
        ],
        "tab_items": [
            (_("Overview"), "#", True),
            (_("Members"), "#", False),
            (_("Settings"), "#", False),
        ],
    }
    return render(request, "core/styleguide.html", context)
