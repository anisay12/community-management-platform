from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)


class AuditFilterForm(forms.Form):
    """Read-only filters of the audit log, bound to the query string."""

    action = forms.ChoiceField(label=_("Action"), required=False)
    actor = forms.CharField(label=_("Actor"), required=False, max_length=100)
    target = forms.CharField(label=_("Target"), required=False, max_length=100)
    date_from = forms.DateField(
        label=_("From"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label=_("To"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, actions=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [("", _("All actions")), *((a, a) for a in actions)]

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                self.fields[name].widget.attrs["aria-describedby"] = f"id_{name}_error"

    def _clean_date(self, name):
        value = self.cleaned_data.get(name)
        if value and not MIN_DATE <= value <= MAX_DATE:
            raise forms.ValidationError(
                _("Enter a date between %(min)s and %(max)s."),
                params={"min": MIN_DATE.isoformat(), "max": MAX_DATE.isoformat()},
            )
        return value

    def clean_date_from(self):
        return self._clean_date("date_from")

    def clean_date_to(self):
        return self._clean_date("date_to")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError(_("The start date must not be after the end date."))
        return cleaned
