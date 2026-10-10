from django.apps import AppConfig


class PostsConfig(AppConfig):
    name = "posts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.utils.translation import gettext_lazy as _

        from accounts.privacy import register_anonymizer
        from communities import tabs
        from communities.policies import can_view_content
        from core import navigation

        from .privacy import anonymize_author

        tabs.register(
            tabs.Tab(
                key="feed",
                label=_("Feed"),
                url_name="posts:feed",
                order=5,
                is_visible=can_view_content,
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
