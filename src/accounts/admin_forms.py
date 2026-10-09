from django import forms
from django.utils.translation import gettext_lazy as _

from .models import User
from .services import create_user, validate_new_user_fields

# Problems reported by ``validate_new_user_fields`` and the form field they belong to.
PROBLEM_FIELDS = {
    "invalid_email": "email",
    "email_taken": "email",
    "first_name_required": "first_name",
    "first_name_too_long": "first_name",
    "last_name_required": "last_name",
    "last_name_too_long": "last_name",
}


class UserAddForm(forms.ModelForm):
    """Admin "add user" form: users are invited, so no password is set at creation.

    The account is created by ``accounts.services.create_user`` (audit event, employee
    role and activation email), never by saving the form's instance directly.
    """

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                _("A user with this email address already exists."), code="duplicate_email"
            )
        return email

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        for code, message in validate_new_user_fields(
            email=cleaned.get("email", ""),
            first_name=cleaned.get("first_name", ""),
            last_name=cleaned.get("last_name", ""),
            email_taken=False,
        ):
            self.add_error(PROBLEM_FIELDS.get(code), message)
        return cleaned

    def create(self, *, actor) -> User:
        """Create the pending account through the audited service and send the invitation."""
        return create_user(
            actor=actor,
            email=self.cleaned_data["email"],
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
        )
