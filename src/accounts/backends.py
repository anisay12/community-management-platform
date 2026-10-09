from django.conf import settings
from django.contrib.auth.backends import ModelBackend

from .models import User

LOCAL_PASSWORD_MODES = {"local", "mixed"}


def local_password_login_allowed(user) -> bool:
    """Password sign-in is open in local/mixed modes, and always for the break-glass account."""
    if settings.AUTH_MODE in LOCAL_PASSWORD_MODES:
        return True
    break_glass = settings.BREAK_GLASS_EMAIL.strip().lower()
    return bool(break_glass) and user.email.lower() == break_glass


def axes_username(request, credentials) -> str | None:
    """Username used by django-axes, lowercased so case variants share one lockout counter."""
    if credentials:
        value = credentials.get(settings.AXES_USERNAME_FORM_FIELD)
    else:
        value = request.POST.get(settings.AXES_USERNAME_FORM_FIELD)
    return value.strip().lower() if value else None


class EmailBackend(ModelBackend):
    """Email + password authentication for users whose status is active."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        try:
            user = User._default_manager.get_by_natural_key(username)
        except User.DoesNotExist:
            # Run the default password hasher once to reduce the timing difference
            # between an existing and a nonexistent user.
            User().set_password(password)
            return None
        if (
            user.check_password(password)
            and self.user_can_authenticate(user)
            and local_password_login_allowed(user)
        ):
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        return getattr(user, "status", None) == User.Status.ACTIVE
