from django import forms
from django.utils.translation import gettext_lazy as _

from .models import REASON_MIN_LENGTH, Community, CommunityCategory

SORT_CHOICES = [
    ("activity", _("Recent activity")),
    ("members", _("Members")),
    ("name", _("Alphabetical")),
]
SORT_ORDERING = {
    "activity": ("-last_activity_at", "name"),
    "members": ("-member_count", "name"),
}


class CatalogueFilterForm(forms.Form):
    q = forms.CharField(label=_("Search by name"), required=False, max_length=100)
    category = forms.ModelChoiceField(
        label=_("Category"),
        queryset=CommunityCategory.objects.filter(is_active=True),
        to_field_name="slug",
        required=False,
        empty_label=_("All categories"),
    )
    access_mode = forms.ChoiceField(
        label=_("Access mode"),
        choices=[("", _("All access modes")), *Community.AccessMode.choices],
        required=False,
    )
    sort = forms.ChoiceField(label=_("Sort by"), choices=SORT_CHOICES, required=False)
    mine = forms.BooleanField(label=_("My communities"), required=False)


class AdminAccessForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        min_length=REASON_MIN_LENGTH,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Recorded in the audit log. Access lasts one hour."),
    )
