"""Forms of the community management pages."""

from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from taxonomy.models import Tag

from .models import Community, CommunityCategory
from .roles import CommunityRole, role_at_least

MARKDOWN_HELP = _(
    "Markdown is supported: **bold**, *italic*, lists, links, headings and tables. "
    "Images and raw HTML are removed."
)


class CommunitySettingsForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        label=_("Tags"), queryset=Tag.objects.all(), required=False
    )

    class Meta:
        model = Community
        fields = (
            "name",
            "category",
            "tagline",
            "description",
            "rules",
            "objectives",
            "access_mode",
            "listed",
            "allow_member_uploads",
            "require_post_review",
            "tags",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, rows in (("description", 6), ("rules", 4), ("objectives", 4)):
            self.fields[name].widget = forms.Textarea(attrs={"rows": rows})
            self.fields[name].help_text = MARKDOWN_HELP
        self.fields["listed"].help_text = _(
            "Invitation-only communities are hidden from the catalogue unless listed."
        )
        current = self.instance.category_id
        self.fields["category"].queryset = CommunityCategory.objects.filter(
            is_active=True
        ) | CommunityCategory.objects.filter(pk=current)

    def validate_unique(self):
        """Name uniqueness among active communities is enforced by the service."""


class MemberSearchForm(forms.Form):
    q = forms.CharField(label=_("Search members"), required=False, max_length=100)


class RoleForm(forms.Form):
    role = forms.ChoiceField(label=_("Role"), choices=CommunityRole.choices)


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("accept", _("Accept")), ("reject", _("Reject"))])
    note = forms.CharField(
        label=_("Note to the applicant"), required=False, max_length=500, widget=forms.TextInput
    )


class StatusForm(forms.Form):
    action = forms.ChoiceField(
        choices=[(name, name) for name in ("suspend", "reactivate", "archive", "unarchive")]
    )


def grantable_invitation_roles(can_change_roles: bool) -> list[tuple[str, str]]:
    """Roles an invitation may offer: never lead; facilitator only for leads and admins."""
    return [
        (value, label)
        for value, label in CommunityRole.choices
        if value != CommunityRole.OWNER
        and (can_change_roles or not role_at_least(value, CommunityRole.ANIMATOR))
    ]


class InvitationForm(forms.Form):
    email = forms.EmailField(label=_("E-mail address of the employee"))
    role = forms.ChoiceField(label=_("Role"))

    def __init__(self, *args, can_change_roles=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = grantable_invitation_roles(can_change_roles)
        self.fields["role"].initial = CommunityRole.MEMBER

    def clean_email(self):
        email = self.cleaned_data["email"]
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            raise forms.ValidationError(_("No active employee has this e-mail address."))
        self.invited_user = user
        return email
