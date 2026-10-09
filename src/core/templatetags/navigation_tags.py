from django import template

from core import navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def main_navigation(context) -> list:
    """Navigation items the current viewer may see, with the current one flagged."""
    return navigation.items_for(context["request"])
