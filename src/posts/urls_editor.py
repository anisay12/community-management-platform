"""Post editor and post action routes (Task 5), included by ``posts.urls``."""

from django.urls import path

from . import views_editor as views

POST = "communities/<slug:slug>/posts/<uuid:public_id>"

urlpatterns = [
    path("communities/<slug:slug>/posts/new/", views.create, name="create"),
    path("communities/<slug:slug>/posts/preview/", views.preview, name="preview"),
    path(
        "communities/<slug:slug>/posts/mentions/",
        views.mention_suggestions,
        name="mention_suggestions",
    ),
    path(f"{POST}/edit/", views.edit, name="edit"),
    path(f"{POST}/publish/", views.publish, name="publish"),
    path(f"{POST}/delete-draft/", views.delete_draft, name="delete_draft"),
    path(f"{POST}/pin/", views.pin, name="pin"),
    path(f"{POST}/unpin/", views.unpin, name="unpin"),
    path(f"{POST}/hide/", views.hide, name="hide"),
    path(f"{POST}/unhide/", views.unhide, name="unhide"),
    path(f"{POST}/archive/", views.archive, name="archive"),
    path(f"{POST}/accept-answer/", views.accept_answer, name="accept_answer"),
    path(f"{POST}/review/", views.review_decide, name="review_decide"),
    path(f"{POST}/share/", views.share, name="share"),
]
