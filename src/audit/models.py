from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AuditImmutableError(Exception):
    """Raised on any attempt to modify or delete an audit event."""


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditImmutableError("Audit events are append-only.")

    def delete(self):
        raise AuditImmutableError("Audit events are append-only.")


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("actor"),
    )
    action = models.CharField(_("action"), max_length=64, db_index=True)
    target_type = models.CharField(_("target type"), max_length=64)
    target_id = models.CharField(_("target identifier"), max_length=64)
    # Foreign key to communities is added in L3.
    community_id = models.BigIntegerField(_("community identifier"), null=True, blank=True)
    changes = models.JSONField(_("changes"), default=dict, blank=True)
    ip_hash = models.CharField(_("IP hash"), max_length=64, blank=True)
    request_id = models.CharField(_("request identifier"), max_length=64, blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now, db_index=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["target_type", "target_id"])]
        verbose_name = _("audit event")
        verbose_name_plural = _("audit events")

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditImmutableError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutableError("Audit events are append-only.")

    @classmethod
    def purge_older_than(cls, cutoff) -> int:
        """Retention-only deletion; bypasses the append-only guard on purpose."""
        return cls.objects.filter(created_at__lt=cutoff)._raw_delete(using="default")
