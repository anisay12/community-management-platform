from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from accounts.urls import manage_patterns
from core import views, views_i18n

handler403 = "core.views_errors.permission_denied"
handler404 = "core.views_errors.page_not_found"
handler500 = "core.views_errors.server_error"

urlpatterns = [
    path(
        "",
        TemplateView.as_view(template_name="core/home.html"),
        name="home",
    ),
    path("", include("accounts.urls")),
    path("manage/", include(manage_patterns)),
    path(settings.DJANGO_ADMIN_PATH, admin.site.urls),
    path("i18n/setlang/", views_i18n.set_language, name="set_language"),
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
    path("metrics", views.metrics, name="metrics"),
]

# Single sign-on endpoints exist only in the mixed and sso_only modes.
if settings.AUTH_MODE != "local":
    urlpatterns.append(path("oidc/", include("mozilla_django_oidc.urls")))
