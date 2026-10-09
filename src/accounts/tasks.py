from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import translation


@shared_task(autoretry_for=(SMTPException, OSError), retry_backoff=True, max_retries=5)
def send_email(
    *, to: str, subject_template: str, body_template: str, context: dict, language: str
) -> None:
    """Render a plain-text email in ``language`` and send it to ``to``."""
    with translation.override(language):
        subject = render_to_string(subject_template, context)
        body = render_to_string(body_template, context)
    subject = " ".join(subject.split())
    EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to]).send()
