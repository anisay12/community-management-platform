"""Template helpers of the post pages."""

from django import template

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
