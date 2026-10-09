"""Membership actions (join, leave, invitations) and community creation."""

from django.urls import path

from . import views_actions

urlpatterns = [
    path("new/", views_actions.create, name="create"),
    path("request/", views_actions.creation_request, name="creation_request"),
    path("invitations/", views_actions.my_invitations, name="my_invitations"),
    path(
        "invitations/<uuid:public_id>/respond/",
        views_actions.invitation_respond,
        name="invitation_respond",
    ),
    path("<slug:slug>/join/", views_actions.join, name="join"),
    path("<slug:slug>/leave/", views_actions.leave, name="leave"),
    path("<slug:slug>/cancel-request/", views_actions.cancel_request, name="cancel_request"),
]
