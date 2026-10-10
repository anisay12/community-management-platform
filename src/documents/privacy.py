"""Personal data of documents (GDPR), plugged into ``accounts.privacy``.

- Export (section ``documents``): the documents the user owns with their versions, the
  versions they uploaded to other documents, and their download history.
- Anonymization: the download history is deleted and the frozen owner name of their documents
  becomes "Former employee" (the documents themselves stay, like posts).
"""

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext

from .models import Document, DocumentVersion, DownloadLog


def _version_row(version) -> dict:
    return {
        "version": version.version_label,
        "filename": version.original_filename,
        "size": version.size,
        "mime_type": version.mime_type,
        "created_at": version.created_at,
    }


def export_documents(user) -> dict:
    """The ``documents`` section of the export of ``user``."""
    owned = (
        Document.objects.filter(owner=user)
        .select_related("community")
        .prefetch_related("versions")
        .order_by("created_at", "pk")
    )
    other_versions = (
        DocumentVersion.objects.filter(uploaded_by=user)
        .exclude(document__owner=user)
        .select_related("document__community")
        .order_by("created_at", "pk")
    )
    downloads = (
        DownloadLog.objects.filter(user=user)
        .select_related("document__community", "version")
        .order_by("created_at", "pk")
    )
    return {
        "owned": [
            {
                "title": document.title,
                "community": document.community.name,
                "status": document.status,
                "created_at": document.created_at,
                "versions": [
                    _version_row(version)
                    for version in sorted(document.versions.all(), key=lambda v: v.number)
                ],
            }
            for document in owned
        ],
        "versions_uploaded_to_other_documents": [
            {
                "document": version.document.title,
                "community": version.document.community.name,
                **_version_row(version),
            }
            for version in other_versions
        ],
        "downloads": [
            {
                "document": log.document.title,
                "community": log.document.community.name,
                "version": log.version.version_label,
                "preview": log.is_preview,
                "created_at": log.created_at,
            }
            for log in downloads
        ],
    }


def anonymize_documents(user) -> None:
    """Delete the download history of ``user`` and replace the owner name of their documents
    (inside the anonymization transaction; the name is stored in the default language)."""
    DownloadLog.objects.filter(user=user).delete()
    with translation.override(settings.LANGUAGE_CODE):
        name = gettext("Former employee")
    Document.objects.filter(owner=user).update(owner_display=name)
