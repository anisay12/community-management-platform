"""Community management pages, mounted under ``<slug>/manage/``."""

from django.urls import path

from . import views_manage as views

urlpatterns = [
    path("", views.settings, name="manage_settings"),
    path("members/", views.members, name="manage_members"),
    path("members/<uuid:user_public_id>/role/", views.member_role, name="manage_member_role"),
    path(
        "members/<uuid:user_public_id>/remove/",
        views.member_remove,
        name="manage_member_remove",
    ),
    path("requests/", views.requests, name="manage_requests"),
    path(
        "requests/<uuid:public_id>/decide/",
        views.request_decide,
        name="manage_request_decide",
    ),
    path("invitations/", views.invitations, name="manage_invitations"),
    path(
        "invitations/<uuid:public_id>/revoke/",
        views.invitation_revoke,
        name="manage_invitation_revoke",
    ),
    path("status/", views.status, name="manage_status"),
]
