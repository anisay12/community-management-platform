from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from . import views

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
    path("v1/tags/", views.TagListView.as_view(), name="tags"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerSplitView.as_view(url_name="api:schema"), name="docs"),
]
