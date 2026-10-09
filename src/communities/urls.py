from django.urls import include, path

from . import views

app_name = "communities"

urlpatterns = [
    path("", views.catalogue, name="catalogue"),
    path("", include("communities.urls_actions")),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/members/", views.members, name="members"),
    path("<slug:slug>/admin-access/", views.admin_access, name="admin_access"),
    path("<slug:slug>/manage/", include("communities.urls_manage")),
]
