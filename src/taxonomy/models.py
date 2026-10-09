from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


class Tag(models.Model):
    name = models.CharField(_("name"), max_length=64)
    slug = models.SlugField(_("slug"), unique=True)
    # Lowercase, accent-free form of the name (``services.normalize_tag_key``): the lookup key.
    key = models.CharField(_("key"), max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="taxonomy_tag_name_ci_unique"),
        ]
        verbose_name = _("tag")
        verbose_name_plural = _("tags")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from .services import normalize_tag_key

        self.key = normalize_tag_key(self.name)
        super().save(*args, **kwargs)
