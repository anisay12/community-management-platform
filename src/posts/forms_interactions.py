"""Forms of the interaction pages: comments, hiding, reports and bookmark collections.

The forms only shape the input; business rules (empty body, length, duplicates) stay in
``posts.services_interactions``, whose ``DomainError`` messages reach the user as toasts.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import COMMENT_BODY_MAX_LENGTH, REPORT_DETAILS_MAX_LENGTH, ContentReport
from .services_interactions import COLLECTION_NAME_MAX_LENGTH


class CommentForm(forms.Form):
    body = forms.CharField(
        label=_("Your comment"),
        required=False,  # an empty body is refused by the service (``body_required``)
        strip=False,
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": COMMENT_BODY_MAX_LENGTH}),
        help_text=_("Markdown is supported. Mention a member with @first.last."),
    )
    parent = forms.UUIDField(required=False, widget=forms.HiddenInput)


class HideForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason for hiding"),
        required=False,  # a blank reason is refused by the service (``reason_required``)
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "maxlength": 500}),
    )


class ReportForm(forms.Form):
    reason = forms.ChoiceField(
        label=_("Reason"), choices=ContentReport.Reason.choices, widget=forms.RadioSelect
    )
    details = forms.CharField(
        label=_("Details"),
        required=False,
        max_length=REPORT_DETAILS_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Optional: what should the moderators look at?"),
    )


class CollectionForm(forms.Form):
    name = forms.CharField(
        label=_("Collection name"),
        required=False,  # ``name_required`` / ``name_too_long`` come from the service
        max_length=COLLECTION_NAME_MAX_LENGTH * 2,
        widget=forms.TextInput(attrs={"maxlength": COLLECTION_NAME_MAX_LENGTH}),
    )
