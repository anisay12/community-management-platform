from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views_auth, views_mfa

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
