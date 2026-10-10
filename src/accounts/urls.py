from django.contrib.auth.views import LogoutView
from django.urls import include, path

from communities.urls_admin import urlpatterns as communities_admin_patterns
from taxonomy.urls_admin import urlpatterns as taxonomy_admin_patterns

from . import views_auth, views_manage, views_mfa, views_privacy, views_profile

app_name = "accounts"

# Authentication pages, mounted under /accounts/.
auth_patterns = [
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

urlpatterns = [
    path("accounts/", include(auth_patterns)),
    path("me/", views_profile.profile_me, name="profile_me"),
    path("me/edit/", views_profile.profile_edit, name="profile_edit"),
    path("me/avatar/", views_profile.avatar_upload, name="avatar_upload"),
    path("me/avatar/remove/", views_profile.avatar_remove, name="avatar_remove"),
    path("me/preferences/", views_profile.preferences, name="preferences"),
    path("me/data-export/", views_privacy.data_export, name="data_export"),
    path(
        "me/data-export/<uuid:public_id>/download/",
        views_privacy.data_export_download,
        name="data_export_download",
    ),
    path("people/<uuid:public_id>/", views_profile.profile_detail, name="profile_detail"),
    path("people/<uuid:public_id>/avatar/", views_profile.avatar, name="avatar"),
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
        path(
            "users/<uuid:public_id>/anonymize/",
            views_manage.user_anonymize,
            name="user_anonymize",
        ),
        *communities_admin_patterns,
        *taxonomy_admin_patterns,
    ],
    "manage",
)
