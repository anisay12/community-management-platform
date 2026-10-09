"""Functional-admin pages, appended to ``accounts.urls.manage_patterns`` (``manage`` namespace)."""

from django.urls import path

from . import views_admin

urlpatterns = [
    path("categories/", views_admin.category_list, name="category_list"),
    path("categories/new/", views_admin.category_create, name="category_create"),
    path("categories/<slug:slug>/edit/", views_admin.category_edit, name="category_edit"),
    path("creation-requests/", views_admin.creation_request_list, name="creation_request_list"),
    path(
        "creation-requests/<uuid:public_id>/decide/",
        views_admin.creation_request_decide,
        name="creation_request_decide",
    ),
]
