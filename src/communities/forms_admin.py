"""Forms of the functional administration: community categories and creation decisions."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CommunityCategory

# Bootstrap Icons names an administrator may pick for a category.
CATEGORY_ICONS = [
    "people",
    "cpu",
    "cloud",
    "shield-lock",
    "code-slash",
    "diagram-3",
    "palette",
    "kanban",
    "briefcase",
    "graph-up",
    "broadcast",
    "tree",
    "heart",
    "lightbulb",
    "book",
    "globe",
]


class CategoryForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=100)
    slug = forms.SlugField(
        label=_("Slug"),
        max_length=100,
        required=False,
        help_text=_("Leave blank to derive it from the name."),
    )
    description = forms.CharField(label=_("Description"), required=False, widget=forms.Textarea)
    icon = forms.ChoiceField(label=_("Icon"), choices=[(icon, icon) for icon in CATEGORY_ICONS])
    order = forms.IntegerField(label=_("Order"), initial=0)
    is_active = forms.BooleanField(
        label=_("Active (offered when creating a community)"), required=False, initial=True
    )

    def __init__(self, *args, instance: CommunityCategory | None = None, **kwargs):
        self.instance = instance
        if instance is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                field: getattr(instance, field)
                for field in ("name", "slug", "description", "icon", "order", "is_active")
            }
        super().__init__(*args, **kwargs)

    def _others(self):
        others = CommunityCategory.objects.all()
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        return others

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self._others().filter(name__iexact=name).exists():
            raise forms.ValidationError(_("A category with this name already exists."))
        return name

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if slug and self._others().filter(slug=slug).exists():
            raise forms.ValidationError(_("A category with this slug already exists."))
        return slug


class CreationDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("approve", _("Approve")), ("reject", _("Reject"))])
    note = forms.CharField(label=_("Note"), required=False, max_length=1000)
