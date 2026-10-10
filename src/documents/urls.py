"""Document routes (namespace ``documents``), mounted under ``/documents/``."""

from django.urls import path

from . import views, views_download

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
    # Pages (community Resources tab, upload, document page and its actions).
    path("community/<slug:slug>/", views.community_documents, name="community_documents"),
    path("community/<slug:slug>/new/", views.create, name="create"),
    path("<uuid:public_id>/", views.detail, name="detail"),
    path("<uuid:public_id>/edit/", views.edit, name="edit"),
    path("<uuid:public_id>/versions/new/", views.add_version, name="add_version"),
    path(
        "<uuid:public_id>/versions/<int:number>/reference/",
        views.set_reference,
        name="set_reference",
    ),
    path("<uuid:public_id>/archive/", views.archive, name="archive"),
    path("<uuid:public_id>/restore/", views.restore, name="restore"),
    path("<uuid:public_id>/links/", views.link, name="link"),
    path("<uuid:public_id>/links/<int:link_id>/delete/", views.unlink, name="unlink"),
]
