from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from audit.services import record

from .models import User, UserProfile, UserSession
from .roles import ROLES_CACHE_ATTR


@receiver(post_save, sender=User, dispatch_uid="accounts_create_user_profile")
def create_profile(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        UserProfile.objects.get_or_create(user=instance)


@receiver(user_logged_in, dispatch_uid="accounts_track_login")
def on_login(sender, request, user, **kwargs):
    if request is not None and request.session.session_key:
        UserSession.objects.update_or_create(
            session_key=request.session.session_key, defaults={"user": user}
        )
    record(actor=user, action="auth.login", target=user)


@receiver(user_logged_out, dispatch_uid="accounts_track_logout")
def on_logout(sender, request, user, **kwargs):
    if request is not None and request.session.session_key:
        UserSession.objects.filter(session_key=request.session.session_key).delete()
    if user is not None:
        record(actor=user, action="auth.logout", target=user)


@receiver(user_login_failed, dispatch_uid="accounts_track_login_failure")
def on_login_failed(sender, credentials, request=None, **kwargs):
    email = str(credentials.get("username") or "").strip().lower()
    user = User.objects.filter(email=email).first() if email else None
    record(
        actor=None,
        action="auth.login_failed",
        target=user if user is not None else ("accounts.user", "unknown"),
    )


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="accounts_reset_role_cache")
def reset_role_cache(sender, instance, reverse, **kwargs):
    """Drop the memoised role codes of a user whose groups changed."""
    if not reverse:
        instance.__dict__.pop(ROLES_CACHE_ATTR, None)
