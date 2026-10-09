from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views_auth, views_manage, views_mfa

app_name = "accounts"

urlpatterns = [
    path("login/", views_auth.LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password-reset/", views_auth.PasswordResetView.as_view(), name="password_reset"),
    path(
        "password-reset/done/",
        views_auth.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        views_auth.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        views_auth.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("mfa/setup/", views_mfa.mfa_setup, name="mfa_setup"),
    path("mfa/verify/", views_mfa.mfa_verify, name="mfa_verify"),
    path("activate/<uidb64>/<token>/", views_auth.activate, name="activate"),
]

# Account administration, mounted at /manage/ under the "manage" namespace.
manage_patterns = (
    [
        path("users/", views_manage.user_list, name="user_list"),
        path("users/new/", views_manage.user_create, name="user_create"),
        path("users/import/", views_manage.user_import, name="user_import"),
        path("users/import/result/", views_manage.user_import_result, name="user_import_result"),
        path("users/<uuid:public_id>/", views_manage.user_detail, name="user_detail"),
        path("users/<uuid:public_id>/status/", views_manage.user_status, name="user_status"),
        path("users/<uuid:public_id>/roles/", views_manage.user_roles_update, name="user_roles"),
        path(
            "users/<uuid:public_id>/deactivate/",
            views_manage.user_deactivate_confirm,
            name="user_deactivate_confirm",
        ),
    ],
    "manage",
)
