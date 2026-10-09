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

from .backends import local_password_login_allowed
from .models import User
from .services import enqueue_email, site_url, user_language
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
