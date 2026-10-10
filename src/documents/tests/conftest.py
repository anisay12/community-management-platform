import hashlib
import uuid

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from documents.models import Document, DocumentVersion


@pytest.fixture
def make_document():
    """Create a document directly (ORM only) with one reference version, clean unless
    ``scan_status`` says otherwise; the file content is saved to the (in-memory) storage."""

    def _make(community, owner, *, scan_status=DocumentVersion.ScanStatus.CLEAN, **extra):
        content = extra.pop("content", b"%PDF-1.4 test document\n")
        filename = extra.pop("filename", "guide.pdf")
        mime_type = extra.pop("mime_type", "application/pdf")
        extra.setdefault("title", "A document")
        extra.setdefault("owner_display", owner.get_full_name() if owner else "")
        document = Document.objects.create(community=community, owner=owner, **extra)
        prefix = "documents" if scan_status == DocumentVersion.ScanStatus.CLEAN else "quarantine"
        key = default_storage.save(f"{prefix}/{uuid.uuid4().hex}", ContentFile(content))
        version = DocumentVersion.objects.create(
            document=document,
            number=1,
            version_label="1",
            storage_key=key,
            original_filename=filename,
            mime_type=mime_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            scan_status=scan_status,
            uploaded_by=owner,
            is_reference=True,
        )
        document.current_version = version
        document.save(update_fields=["current_version"])
        return document

    return _make
