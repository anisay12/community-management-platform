from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import SetPasswordForm
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from audit.services import record

from .backends import local_password_login_allowed
from .forms import AuditedSetPasswordForm, EmailPasswordResetForm, LoginForm
from .models import User
from .tokens import activation_token_generator

LOCAL_BACKEND = "accounts.backends.EmailBackend"


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = EmailPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = AuditedSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


def _user_from_uid(uidb64: str) -> User | None:
    try:
        return User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None


def _invalid_link(request):
    return render(request, "accounts/activation_invalid.html", status=400)


@sensitive_post_parameters()
@never_cache
def activate(request, uidb64, token):
    """Let a pending user choose a password, then activate and sign them in."""
    user = _user_from_uid(uidb64)
    if not activation_token_generator.check_token(user, token):
        return _invalid_link(request)
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Lock the row and re-check, so a link cannot be used twice concurrently.
            locked = User.objects.select_for_update().get(pk=user.pk)
            if not activation_token_generator.check_token(locked, token):
                return _invalid_link(request)
            form.user = locked
            locked = form.save(commit=False)
            locked.status = User.Status.ACTIVE
            locked.activated_at = timezone.now()
            locked.save()
            record(
                actor=locked,
                action="user.activated",
                target=locked,
                changes={"status": [User.Status.PENDING.value, User.Status.ACTIVE.value]},
            )
        if not local_password_login_allowed(locked):
            messages.success(request, _("Your account is active. You can now sign in."))
            return redirect(settings.LOGIN_URL)
        login(request, locked, backend=LOCAL_BACKEND)
        messages.success(request, _("Your account is active. Welcome!"))
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/activate.html", {"form": form})
