from django.apps import AppConfig
from django.urls import NoReverseMatch, reverse


def _routable(url_name: str, *args) -> bool:
    try:
        reverse(url_name, args=args)
    except NoReverseMatch:
        return False
    return True


def feed_tab_visible(user, community) -> bool:
    """The Feed tab: content readers only, once the feed page exists."""
    from communities.policies import can_view_content

    return _routable("posts:feed", community.slug) and can_view_content(user, community)


class PostsConfig(AppConfig):
    name = "posts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from accounts.privacy import register_anonymizer
        from communities import tabs
        from core import navigation

        from .privacy import anonymize_author

        tabs.register(
            tabs.Tab(
                key="feed",
                label=_("Feed"),
                url_name="posts:feed",
                order=5,
                is_visible=feed_tab_visible,
            )
        )
        navigation.register(
            navigation.NavItem(
                key="home_feed",
                label=_("Feed"),
                url_name="posts:home_feed",
                icon="newspaper",
                order=25,
                active_prefixes=("/feed/",),
            )
        )
        navigation.register(
            navigation.NavItem(
                key="bookmarks",
                label=_("Bookmarks"),
                url_name="posts:bookmarks",
                icon="bookmark",
                order=90,
                active_prefixes=("/bookmarks/",),
            )
        )
        register_anonymizer(anonymize_author)
