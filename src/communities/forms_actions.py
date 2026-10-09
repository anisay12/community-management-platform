"""Forms of the member actions and of community creation (Task 5)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from taxonomy.models import Tag

from .models import Community, CommunityCategory

REQUEST_MESSAGE_MAX_LENGTH = 500


class JoinRequestForm(forms.Form):
    message = forms.CharField(
        label=_("Message to the facilitators"),
        required=False,
        max_length=REQUEST_MESSAGE_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class _CategoryField(forms.ModelChoiceField):
    def __init__(self, **kwargs):
        super().__init__(
            label=_("Category"),
            queryset=CommunityCategory.objects.filter(is_active=True),
            **kwargs,
        )


class CommunityCreateForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=120)
    category = _CategoryField()
    tagline = forms.CharField(label=_("Tagline"), max_length=160)
    description = forms.CharField(
        label=_("Description"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Markdown is supported."),
    )
    access_mode = forms.ChoiceField(
        label=_("Access mode"),
        choices=Community.AccessMode.choices,
        initial=Community.AccessMode.OPEN,
    )
    listed = forms.BooleanField(
        label=_("Show in the catalogue even when invitation-only"), required=False
    )
    tags = forms.ModelMultipleChoiceField(
        label=_("Tags"), queryset=Tag.objects.all(), required=False
    )


class CreationRequestForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=120)
    category = _CategoryField()
    justification = forms.CharField(
        label=_("Justification"),
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_("Explain the purpose of the community and who would take part."),
    )
