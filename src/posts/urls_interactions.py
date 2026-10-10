"""Interaction routes (comments, reactions, bookmarks, reports), included by ``posts.urls``."""

from django.urls import path

from . import views_documents
from . import views_interactions as views

POST = "communities/<slug:slug>/posts/<uuid:public_id>/"

urlpatterns = [
    path(f"{POST}comments/", views.comment_create, name="comment_create"),
    path("comments/<uuid:public_id>/edit/", views.comment_edit, name="comment_edit"),
    path("comments/<uuid:public_id>/hide/", views.comment_hide, name="comment_hide"),
    path("comments/<uuid:public_id>/unhide/", views.comment_unhide, name="comment_unhide"),
    path("react/<str:target>/<uuid:public_id>/", views.react, name="react"),
    path(f"{POST}bookmark/", views.bookmark_toggle, name="bookmark_toggle"),
    # Before the generic report route, which would read "document" as an unknown target.
    path(
        "report/document/<uuid:public_id>/",
        views_documents.report_document,
        name="report_document",
    ),
    path("report/<str:target>/<uuid:public_id>/", views.report, name="report"),
    path("bookmarks/", views.bookmarks, name="bookmarks"),
    path("bookmarks/<uuid:public_id>/remove/", views.bookmark_remove, name="bookmark_remove"),
    path("bookmarks/<uuid:public_id>/move/", views.collection_move, name="collection_move"),
    path(
        "bookmarks/documents/<uuid:public_id>/",
        views_documents.bookmark_document,
        name="bookmark_document",
    ),
    path(
        "bookmarks/documents/<uuid:public_id>/remove/",
        views_documents.bookmark_document_remove,
        name="bookmark_document_remove",
    ),
    path(
        "bookmarks/documents/<uuid:public_id>/move/",
        views_documents.collection_move_document,
        name="collection_move_document",
    ),
    path("bookmarks/collections/", views.collection_create, name="collection_create"),
    path(
        "bookmarks/collections/<uuid:public_id>/rename/",
        views.collection_rename,
        name="collection_rename",
    ),
    path(
        "bookmarks/collections/<uuid:public_id>/delete/",
        views.collection_delete,
        name="collection_delete",
    ),
]
