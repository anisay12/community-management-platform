from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class OrganizationUnit(models.Model):
    name = models.CharField(_("name"), max_length=200)
    code = models.CharField(_("code"), max_length=50, unique=True)
    parent = models.ForeignKey(
        "self",
        verbose_name=_("parent unit"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = _("organization unit")
        verbose_name_plural = _("organization units")

    def __str__(self):
        return f"{self.code} — {self.name}"


class Employment(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="employment",
    )
    unit = models.ForeignKey(
        OrganizationUnit,
        verbose_name=_("unit"),
        on_delete=models.PROTECT,
        related_name="employments",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("manager"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(manager=models.F("user")),
                name="organizations_employment_manager_not_self",
            ),
        ]
        verbose_name = _("employment")
        verbose_name_plural = _("employments")

    def __str__(self):
        return f"{self.user} @ {self.unit}"
