"""Forms of the moderation queue."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .services_moderation import DISMISS, RESOLVE, RESOLVE_AND_HIDE

NOTE_MAX_LENGTH = 1000


class ReportDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (RESOLVE_AND_HIDE, _("Resolve and hide")),
            (RESOLVE, _("Resolve")),
            (DISMISS, _("Dismiss")),
        ]
    )
    note = forms.CharField(label=_("Note"), required=False, max_length=NOTE_MAX_LENGTH)
