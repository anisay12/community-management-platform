"""Template helpers of the post pages."""

from django import template
from django.utils.translation import gettext as _

from posts.hiding import AUTOMATIC_REASON
from posts.models import Reaction

register = template.Library()


@register.filter
def reaction_items(counts) -> list[tuple[str, str, int]]:
    """``(kind, translated label, count)`` of the non-zero reaction counts, in kind order."""
    counts = counts or {}
    return [
        (kind, str(label), counts[kind])
        for kind, label in Reaction.Kind.choices
        if isinstance(counts.get(kind), int) and counts[kind] > 0
    ]


@register.filter
def hidden_reason_label(reason) -> str:
    """The reason shown for a hidden post or comment: automatic hiding (report threshold)
    stores a fixed code, shown as a translated sentence."""
    if reason == AUTOMATIC_REASON:
        return _("Hidden automatically after several reports")
    return reason or ""
