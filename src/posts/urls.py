"""Post routes (namespace ``posts``), mounted at the site root.

The read routes live here; editor, interaction and moderation routes are included from their
own modules.
"""

from django.urls import include, path

from . import views

app_name = "posts"

urlpatterns = [
    path("feed/", views.home_feed, name="home_feed"),
    path("communities/<slug:slug>/posts/", views.feed, name="feed"),
    path("communities/<slug:slug>/posts/<uuid:public_id>/", views.detail, name="detail"),
    path(
        "communities/<slug:slug>/posts/<uuid:public_id>/revisions/",
        views.revisions,
        name="revisions",
    ),
    path("", include("posts.urls_editor")),
    path("", include("posts.urls_interactions")),
    path("", include("posts.urls_moderation")),
]
