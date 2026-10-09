"""Forms of the tag administration."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Tag
from .services import normalize_tag_key


class TagMergeForm(forms.Form):
    """Source and kept tags, given by name (case and accents ignored)."""

    source = forms.CharField(label=_("Tag to merge"), max_length=64)
    target = forms.CharField(label=_("Tag to keep"), max_length=64)
    confirm = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def _tag(self, field):
        name = self.cleaned_data[field]
        tag = Tag.objects.filter(key=normalize_tag_key(name)).first()
        if tag is None:
            raise forms.ValidationError(
                _("No tag named “%(name)s”."), code="unknown", params={"name": name}
            )
        return tag

    def clean_source(self):
        return self._tag("source")

    def clean_target(self):
        return self._tag("target")

    def clean(self):
        cleaned = super().clean()
        source, target = cleaned.get("source"), cleaned.get("target")
        if source is not None and source == target:
            raise forms.ValidationError(_("Choose two different tags."), code="same_tag")
        return cleaned
