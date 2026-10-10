"""Document routes (namespace ``documents``), mounted under ``/documents/``."""

from django.urls import path

from . import views_download

app_name = "documents"

urlpatterns = [
    path("<uuid:public_id>/download/", views_download.download, name="download"),
    path(
        "<uuid:public_id>/download/<int:number>/",
        views_download.download,
        name="download_version",
    ),
    path("<uuid:public_id>/preview/", views_download.preview, name="preview"),
    path(
        "<uuid:public_id>/preview/<int:number>/",
        views_download.preview,
        name="preview_version",
    ),
]
