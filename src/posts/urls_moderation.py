"""Post moderation routes, included by ``posts.urls``."""

from django.urls import path

from . import views_moderation

urlpatterns = [
    path(
        "communities/<slug:slug>/moderation/",
        views_moderation.moderation_queue,
        name="moderation_queue",
    ),
    path(
        "communities/<slug:slug>/moderation/review/<uuid:public_id>/",
        views_moderation.review_decide,
        name="queue_review_decide",
    ),
    path("reports/<uuid:public_id>/decide/", views_moderation.report_decide, name="report_decide"),
]
