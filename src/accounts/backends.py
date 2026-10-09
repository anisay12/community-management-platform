import structlog
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.core.mail import mail_admins

from audit.services import record

from .models import User

logger = structlog.get_logger(__name__)

LOCAL_PASSWORD_MODES = {"local", "mixed"}


def is_break_glass(user) -> bool:
    """Whether ``user`` is the emergency local account named by BREAK_GLASS_EMAIL."""
    break_glass = settings.BREAK_GLASS_EMAIL.strip().lower()
    return bool(break_glass) and user.email.lower() == break_glass


def local_password_login_allowed(user) -> bool:
    """Password sign-in is open in local/mixed modes, and always for the break-glass account."""
    return settings.AUTH_MODE in LOCAL_PASSWORD_MODES or is_break_glass(user)


def alert_break_glass_login(user) -> None:
    """Audit, log and email the administrators: the emergency account was just used."""
    record(actor=user, action="auth.break_glass_login", target=user)
    logger.warning("break_glass_login", user=str(user.public_id), auth_mode=settings.AUTH_MODE)
    # Plain-text operator alert in English; a mail failure is logged but never blocks
    # the emergency sign-in.
    try:
        mail_admins(
            "Break-glass account sign-in",
            f"The break-glass account {user.email} signed in with a password "
            f"(AUTH_MODE={settings.AUTH_MODE}). If this was not expected, suspend it and "
            "rotate its password.",
        )
    except Exception as exc:  # any transport error; must not block the login
        logger.error("break_glass_alert_failed", error_type=type(exc).__name__)


def axes_username(request, credentials) -> str | None:
    """Username used by django-axes, lowercased so case variants share one lockout counter."""
    if credentials:
        value = credentials.get(settings.AXES_USERNAME_FORM_FIELD)
    else:
        value = request.POST.get(settings.AXES_USERNAME_FORM_FIELD)
    return value.strip().lower() if value else None


def axes_skip_non_password_attempt(request, credentials) -> bool:
    """Keep django-axes to password attempts only.

    Single sign-on callbacks carry no username: the identity provider authenticates the
    user, and counting its refusals (e.g. a pending account) per IP would lock everyone
    behind a shared office address out of SSO.
    """
    return credentials is not None and settings.AXES_USERNAME_FORM_FIELD not in credentials


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
            if is_break_glass(user):
                alert_break_glass_login(user)
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        return getattr(user, "status", None) == User.Status.ACTIVE
