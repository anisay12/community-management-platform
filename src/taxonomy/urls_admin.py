"""Tag administration, appended to ``accounts.urls.manage_patterns`` (``manage`` namespace)."""

from django.urls import path

from . import views

urlpatterns = [
    path("tags/", views.tag_list, name="tag_list"),
    path("tags/merge/", views.tag_merge, name="tag_merge"),
]
