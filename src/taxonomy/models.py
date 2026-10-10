from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


class Tag(models.Model):
    """A free-form label shared by communities and posts.

    ``key`` is kept in sync with ``name`` only by ``save()``. Never change a name with
    ``QuerySet.update(name=...)`` or create tags with ``bulk_create``: both bypass ``save()``
    and leave a stale or empty ``key``, which breaks lookups and the uniqueness of keys.
    """

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
