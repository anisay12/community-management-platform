from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """An in-app notification (the notification centre UI arrives in L8)."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("recipient"),
    )
    category = models.CharField(_("category"), max_length=64)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("actor"),
    )
    target_type = models.CharField(_("target type"), max_length=64, blank=True)
    target_id = models.CharField(_("target identifier"), max_length=64, blank=True)
    community = models.ForeignKey(
        "communities.Community",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("community"),
    )
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient", "read_at", "-created_at"], name="notification_inbox_idx"
            )
        ]
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")

    def __str__(self) -> str:
        return f"{self.category} -> {self.recipient_id}"
