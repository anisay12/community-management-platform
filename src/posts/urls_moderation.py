"""Post moderation routes, included by ``posts.urls``."""

from django.urls import path

from . import views_moderation

urlpatterns = [
    path(
        "communities/<slug:slug>/moderation/",
        views_moderation.moderation_queue,
        name="moderation_queue",
    ),
    path("reports/<uuid:public_id>/decide/", views_moderation.report_decide, name="report_decide"),
]
