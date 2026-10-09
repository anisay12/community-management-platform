from django.conf import settings
from django.urls import path
from django.views.generic import TemplateView
from django.views.i18n import set_language

from core import views

handler403 = "core.views_errors.permission_denied"
handler404 = "core.views_errors.page_not_found"
handler500 = "core.views_errors.server_error"

urlpatterns = [
    path(
        "",
        TemplateView.as_view(
            template_name="core/home.html", extra_context={"login_url": settings.LOGIN_URL}
        ),
        name="home",
    ),
    path("i18n/setlang/", set_language, name="set_language"),
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
    path("metrics", views.metrics, name="metrics"),
]
