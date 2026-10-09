from django import template
from django.utils.safestring import mark_safe

from core.markdown import render

register = template.Library()


@register.filter(name="markdown")
def markdown_filter(text) -> str:
    """``{{ text|markdown }}``: sanitised HTML from Markdown (see ``core.markdown``)."""
    return mark_safe(render(str(text or "")))  # noqa: S308  # nosec B308 B703 - sanitised by nh3
