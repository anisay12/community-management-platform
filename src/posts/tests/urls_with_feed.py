"""Test URLconf where ``posts:feed`` resolves (the real route ships with Task 4)."""

from django.http import HttpResponse
from django.urls import include, path


def _feed(request, slug):
    return HttpResponse("feed")


urlpatterns = [
    path("communities/", include("communities.urls")),
    path(
        "",
        include(([path("communities/<slug:slug>/posts/", _feed, name="feed")], "posts")),
    ),
]
