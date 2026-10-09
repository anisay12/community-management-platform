"""Post routes (namespace ``posts``), mounted at the site root.

Read routes (``feed``, ``detail``, ``revisions``, ``home_feed``) are added by Task 4.
"""

from django.urls import include, path

app_name = "posts"

urlpatterns = [
    path("", include("posts.urls_editor")),
    path("", include("posts.urls_interactions")),
    path("", include("posts.urls_moderation")),
]
