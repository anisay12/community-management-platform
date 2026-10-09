from typing import ClassVar

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from audit.services import record
from organizations.models import OrganizationUnit

from .backends import local_password_login_allowed
from .models import User
from .roles import Role
from .services import (
    EMAIL_MAX_LENGTH,
    NAME_MAX_LENGTH,
    SUPERUSER_ONLY_ROLES,
    enqueue_email,
    site_url,
    user_language,
    validate_new_user_fields,
)
from .tokens import password_reset_token_generator


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("Email"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )

    error_messages: ClassVar[dict] = {
        "invalid_login": _("Invalid email or password."),
        User.Status.PENDING: _(
            "Your account is not active yet. Check your activation email or contact an "
            "administrator."
        ),
        User.Status.SUSPENDED: _("Your account is suspended. Contact an administrator."),
    }

    def get_invalid_login_error(self):
        code = self._failure_code()
        return ValidationError(self.error_messages[code], code=code)

    @sensitive_variables("password")
    def _failure_code(self) -> str:
        """Reveal a pending/suspended status only to someone who knows the password.

        Every failed attempt hashes the password exactly once here (dummy hash for an
        unknown email), so response time does not tell whether the account exists.
        """
        if getattr(self.request, "axes_locked_out", False):
            return "invalid_login"
        password = self.cleaned_data.get("password")
        user = User.objects.filter(email=self.cleaned_data["username"].lower()).first()
        if user is None:
            User().set_password(password)
            return "invalid_login"
        if (
            user.check_password(password)
            and user.status in (User.Status.PENDING, User.Status.SUSPENDED)
            and local_password_login_allowed(user)
        ):
            return user.status
        return "invalid_login"


class EmailPasswordResetForm(PasswordResetForm):
    """Queue a reset email (built on ``SITE_URL``) for each active local-password account."""

    def get_users(self, email):
        return (user for user in super().get_users(email) if local_password_login_allowed(user))

    def save(self, *, token_generator=password_reset_token_generator, **kwargs):
        for user in self.get_users(self.cleaned_data["email"]):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = token_generator.make_token(user)
            enqueue_email(
                user=user,
                subject_template="emails/password_reset_subject.txt",
                body_template="emails/password_reset_body.txt",
                context={
                    "reset_url": site_url(
                        reverse("accounts:password_reset_confirm", args=[uid, token])
                    ),
                },
                language=user_language(user, get_language()),
            )


class AuditedSetPasswordForm(SetPasswordForm):
    """Password reset confirmation form that records the change in the audit log."""

    def save(self, commit=True):
        with transaction.atomic():
            user = super().save(commit=commit)
            record(actor=user, action="auth.password_reset", target=user)
        return user


class OTPTokenForm(forms.Form):
    """A 6-digit TOTP code; any mismatch is reported with the same neutral message."""

    INVALID = _("Invalid code. Please try again.")

    otp_token = forms.RegexField(
        label=_("Verification code"),
        regex=r"^\d{6}$",
        max_length=6,
        strip=True,
        error_messages={"required": INVALID, "invalid": INVALID, "max_length": INVALID},
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
            }
        ),
    )

    def reject(self) -> None:
        self.add_error("otp_token", self.INVALID)


# Account administration ---------------------------------------------------------


def role_choices_for(actor) -> list[tuple[str, str]]:
    """Roles ``actor`` may tick: only superusers are offered the superuser-only roles."""
    if getattr(actor, "is_superuser", False):
        return list(Role.choices)
    return [(code, label) for code, label in Role.choices if code not in SUPERUSER_ONLY_ROLES]


class RolesField(forms.MultipleChoiceField):
    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Roles"))
        super().__init__(choices=Role.choices, **kwargs)


class ActorRolesMixin:
    """Restrict the role choices to those the acting user may grant (re-checked server side)."""

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["roles"].choices = role_choices_for(actor)


class UserCreateForm(ActorRolesMixin, forms.Form):
    # Field for each problem code of ``validate_new_user_fields``.
    PROBLEM_FIELDS: ClassVar[dict[str, str]] = {
        "invalid_email": "email",
        "email_taken": "email",
        "first_name_required": "first_name",
        "first_name_too_long": "first_name",
        "last_name_required": "last_name",
        "last_name_too_long": "last_name",
        "manager_self": "manager_email",
        "manager_requires_unit": "unit",
        "unknown_manager": "manager_email",
    }

    email = forms.EmailField(label=_("Email"), max_length=EMAIL_MAX_LENGTH)
    first_name = forms.CharField(label=_("First name"), max_length=NAME_MAX_LENGTH)
    last_name = forms.CharField(label=_("Last name"), max_length=NAME_MAX_LENGTH)
    unit = forms.ModelChoiceField(
        label=_("Unit"), queryset=OrganizationUnit.objects.all(), required=False
    )
    manager_email = forms.EmailField(
        label=_("Manager's email"),
        required=False,
        help_text=_("Requires a unit. The manager must already have an account."),
    )
    roles = RolesField(initial=[Role.EMPLOYEE.value])

    def clean(self):
        cleaned = super().clean()
        problems = validate_new_user_fields(
            email=cleaned.get("email") or self.data.get("email", ""),
            first_name=cleaned.get("first_name"),
            last_name=cleaned.get("last_name"),
            unit=cleaned.get("unit"),
            manager_email=cleaned.get("manager_email"),
        )
        for code, message in problems:
            field = self.PROBLEM_FIELDS[code]
            if field not in self.errors:
                self.add_error(field, message)
        manager_email = self.cleaned_data.get("manager_email")
        cleaned["manager"] = (
            User.objects.filter(email__iexact=manager_email).first() if manager_email else None
        )
        return cleaned


class UserRolesForm(ActorRolesMixin, forms.Form):
    roles = RolesField(required=False)


class UserStatusForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("suspend", _("Suspend")),
            ("reactivate", _("Reactivate")),
            ("resend_activation", _("Resend activation email")),
        ]
    )


class UserImportForm(forms.Form):
    file = forms.FileField(
        label=_("CSV file"),
        help_text=_(
            "UTF-8, comma or semicolon separated, at most 5000 rows and 2 MB. First line: "
            "email,first_name,last_name,unit_code,manager_email"
        ),
    )
