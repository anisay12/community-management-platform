"""Navigation registry: each app declares its entries, the navbar renders what the viewer may see.

An entry is shown only when its URL name resolves and ``is_visible(user)`` allows it, so the
entries of later packages stay hidden until their pages exist.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


def authenticated(user) -> bool:
    return bool(getattr(user, "is_authenticated", False))


@dataclass(frozen=True)
class NavItem:
    key: str
    label: object  # lazy translated string
    url_name: str
    icon: str  # Bootstrap Icons name, without the ``bi-`` prefix
    order: int
    is_visible: Callable[[object], bool] = authenticated
    # URL path prefixes marking the item as current; empty means the item's own URL.
    active_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedNavItem:
    key: str
    label: object
    url: str
    icon: str
    is_current: bool


_registry: dict[str, NavItem] = {}


def register(item: NavItem) -> None:
    if item.key in _registry:
        raise ValueError(f"Navigation item {item.key!r} is already registered.")
    _registry[item.key] = item


def _match_length(item: NavItem, url: str, path: str) -> int:
    """Length of the longest prefix of ``item`` matching ``path`` (0 when none)."""
    best = 0
    for prefix in item.active_prefixes or (url,):
        # The site root only matches itself, otherwise it would be current everywhere.
        matches = path == prefix if prefix == "/" else path.startswith(prefix)
        if matches:
            best = max(best, len(prefix))
    return best


def items_for(request) -> list[ResolvedNavItem]:
    """Visible items of the viewer in display order; at most one is current."""
    visible = []
    for item in sorted(_registry.values(), key=lambda entry: (entry.order, entry.key)):
        if not item.is_visible(request.user):
            continue
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            continue
        visible.append((item, url, _match_length(item, url, request.path)))
    current = max((length for _, _, length in visible), default=0)
    current_key = next(
        (item.key for item, _, length in visible if current and length == current), None
    )
    return [
        ResolvedNavItem(item.key, item.label, url, item.icon, item.key == current_key)
        for item, url, _ in visible
    ]
