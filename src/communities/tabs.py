"""Registries of a community page: its tabs, and the links of its header.

Each package registers its tab when its pages ship (About and Members in L3; Feed, Resources,
Lessons learned and Events later), so no tab ever leads to an empty placeholder screen. Other
apps add links to the header shown on every tab (``posts``: the moderation queue) by
registering the template that renders them.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from . import policies


@dataclass(frozen=True)
class Tab:
    key: str
    label: object  # lazy translated string
    url_name: str  # takes the community slug as its only argument
    order: int
    is_visible: Callable[[object, object], bool] = policies.can_view_metadata


_registry: dict[str, Tab] = {}


def register(tab: Tab) -> None:
    if tab.key in _registry:
        raise ValueError(f"Community tab {tab.key!r} is already registered.")
    _registry[tab.key] = tab


def tabs_for(user, community, current: str) -> list[tuple[object, str, bool]]:
    """``(label, url, is_active)`` of the tabs ``user`` may see, for ``components/tabs.html``."""
    return [
        (tab.label, reverse(tab.url_name, args=[community.slug]), tab.key == current)
        for tab in sorted(_registry.values(), key=lambda entry: (entry.order, entry.key))
        if tab.is_visible(user, community)
    ]


_header_links: list[str] = []


def register_header_link(template_name: str) -> None:
    """Show ``template_name`` among the header links of every community page. It renders with
    the page context (``community``, ``request``) and renders nothing when the viewer may not
    use the link."""
    if template_name in _header_links:
        raise ValueError(f"Community header link {template_name!r} is already registered.")
    _header_links.append(template_name)


def header_links() -> tuple[str, ...]:
    """The registered header link templates, in registration order."""
    return tuple(_header_links)


register(Tab(key="about", label=_("About"), url_name="communities:detail", order=10))
register(
    Tab(
        key="members",
        label=_("Members"),
        url_name="communities:members",
        order=90,
        is_visible=policies.can_view_content,
    )
)
