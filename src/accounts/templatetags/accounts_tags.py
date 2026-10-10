from django import template
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.translation import gettext

from accounts import policies
from accounts.avatars import version_token
from accounts.roles import Role

register = template.Library()


@register.filter
def can_manage_users(user) -> bool:
    return policies.can_manage_users(user)


@register.filter
def role_labels(user) -> list[str]:
    """Translated role labels of ``user``, read from its (prefetched) groups."""
    names = {group.name for group in user.groups.all()}
    return [role.label for role in Role if role.value in names]


@register.filter
def role_codes(user) -> list[str]:
    """Role codes of ``user`` in the canonical order, read from its (prefetched) groups."""
    names = {group.name for group in user.groups.all()}
    return [role.value for role in Role if role.value in names]


@register.filter
def initials(user) -> str:
    """Up to two initials for the avatar placeholder (shown when there is no photo)."""
    words = user.get_full_name().split() or [user.email]
    letters = [words[0][0], words[-1][0]] if len(words) > 1 else [words[0][0]]
    return "".join(letters).upper()


@register.simple_tag
def manage_tabs(active: str, user=None) -> list[tuple[str, str, bool]]:
    """Items of the administration tabs (``components/tabs.html``); ``active`` is the key."""
    entries = [
        ("list", gettext("Users"), "manage:user_list"),
        ("create", gettext("Create a user"), "manage:user_create"),
        ("import", gettext("Import users"), "manage:user_import"),
    ]
    if user is not None:
        from communities import policies as community_policies  # same guards as the pages

        if community_policies.can_manage_categories(user):
            entries.append(("categories", gettext("Categories"), "manage:category_list"))
        if community_policies.is_functional_admin(user):
            entries.append(
                ("creation_requests", gettext("Creation requests"), "manage:creation_request_list")
            )
            entries.append(("tags", gettext("Tags"), "manage:tag_list"))
    return [(label, reverse(name), key == active) for key, label, name in entries]


@register.simple_tag
def manage_breadcrumb(account, current: str = "") -> list[tuple[str, str]]:
    """Breadcrumb items (``components/breadcrumb.html``) of an administration account page."""
    name = account.get_full_name() or account.email
    items = [(gettext("Users"), reverse("manage:user_list"))]
    if current:
        items.append((name, reverse("manage:user_detail", args=[account.public_id])))
        items.append((current, ""))
    else:
        items.append((name, ""))
    return items


@register.filter
def avatar_url(user) -> str:
    """URL of ``user``'s clean profile photo (with a cache-busting ``v``), or "" when there is
    none. Reads ``user.profile``: lists should ``select_related("profile")``."""
    try:
        name = user.profile.avatar.name if user.profile.avatar else ""
    except (ObjectDoesNotExist, AttributeError):
        return ""
    if not name:
        return ""
    url = reverse("accounts:avatar", args=[user.public_id])
    return f"{url}?v={version_token(name)}"
