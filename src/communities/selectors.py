from .models import Community, CommunityCategory


def visible_communities(user):
    """Communities whose metadata ``user`` may see, with their category."""
    return Community.objects.visible_to(user).select_related("category")


def active_categories():
    return CommunityCategory.objects.filter(is_active=True)
