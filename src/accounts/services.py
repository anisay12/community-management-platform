from importlib import import_module

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import UserProfile
from .tasks import send_email
from .tokens import activation_token_generator


def site_url(path: str) -> str:
    """Absolute URL for ``path`` on the configured public site (never the Host header)."""
    return settings.SITE_URL.rstrip("/") + path


def user_language(user, default: str | None = None) -> str:
    try:
        language = user.profile.language
    except UserProfile.DoesNotExist:
        language = ""
    return language or default or settings.LANGUAGE_CODE


def enqueue_email(*, user, subject_template: str, body_template: str, context: dict, language):
    """Send the email through Celery once the current transaction commits."""
    kwargs = {
        "to": user.email,
        "subject_template": subject_template,
        "body_template": body_template,
        "context": {"first_name": user.first_name, **context},
        "language": language,
    }
    transaction.on_commit(lambda: send_email.delay(**kwargs))


def send_activation_email(user, *, request=None) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = activation_token_generator.make_token(user)
    url = site_url(reverse("accounts:activate", args=[uid, token]))
    enqueue_email(
        user=user,
        subject_template="emails/activation_subject.txt",
        body_template="emails/activation_body.txt",
        context={
            "activation_url": url,
            "expiry_hours": settings.ACCOUNT_ACTIVATION_TIMEOUT // 3600,
        },
        language=user_language(user),
    )


def end_all_sessions(user) -> int:
    """Delete every session opened by ``user``; return how many were ended."""
    store = import_module(settings.SESSION_ENGINE).SessionStore
    tracked = list(user.tracked_sessions.all())
    for row in tracked:
        store(session_key=row.session_key).delete()
    user.tracked_sessions.filter(pk__in=[row.pk for row in tracked]).delete()
    return len(tracked)
