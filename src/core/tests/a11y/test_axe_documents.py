"""axe-core scan of the document pages (L5): Resources tab, document page, upload form and
the archive confirmation modal, in both colour schemes and at desktop and mobile widths."""

import hashlib

import pytest
from django.urls import reverse

from core.tests.a11y.conftest import SCHEMES, VIEWPORTS
from core.tests.a11y.test_axe import _assert_no_blocking

pytestmark = pytest.mark.a11y


@pytest.fixture
def documents(world):
    """A clean guide with two versions linked to a post, and a restricted template, in the data
    guild (owner: the employee, who leads it)."""
    from documents.models import Document, DocumentLink, DocumentVersion
    from taxonomy.models import Tag

    lead, community = world["employee"], world["community"]
    guide = Document.objects.create(
        community=community,
        owner=lead,
        owner_display="Employee Tester",
        title="Data quality handbook",
        description="How we check our datasets.\n\nRead it before a release.",
        doc_type=Document.DocType.GUIDE,
        download_count=3,
    )
    tag, _ = Tag.objects.get_or_create(slug="data", defaults={"name": "Data"})
    guide.tags.add(tag)
    for number in (1, 2):
        content = f"%PDF-1.4 v{number}".encode()
        version = DocumentVersion.objects.create(
            document=guide,
            number=number,
            version_label=str(number),
            storage_key=f"documents/a11y{number}",
            original_filename="handbook.pdf",
            mime_type="application/pdf",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            scan_status=DocumentVersion.ScanStatus.CLEAN,
            uploaded_by=lead,
            change_note="Initial" if number == 1 else "Added lineage",
            is_reference=number == 2,
        )
    guide.current_version = version
    guide.save(update_fields=["current_version"])
    DocumentLink.objects.create(document=guide, post=world["article"], created_by=lead)
    restricted = Document.objects.create(
        community=community,
        owner=lead,
        owner_display="Employee Tester",
        title="Project template",
        doc_type=Document.DocType.TEMPLATE,
        visibility=Document.Visibility.RESTRICTED,
    )
    version = DocumentVersion.objects.create(
        document=restricted,
        number=1,
        version_label="1",
        storage_key="documents/a11y3",
        original_filename="template.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size=10,
        sha256="0" * 64,
        scan_status=DocumentVersion.ScanStatus.PENDING,
        uploaded_by=lead,
        is_reference=True,
    )
    restricted.current_version = version
    restricted.save(update_fields=["current_version"])
    return {"guide": guide, "restricted": restricted}


PAGES = {
    "resources-lead": ("employee", lambda w, d: _resources(w)),
    "resources-scanning": ("employee", lambda w, d: _resources(w) + "?status=scanning"),
    "resources-member": ("member", lambda w, d: _resources(w)),
    "document-lead": ("employee", lambda w, d: _detail(d["guide"])),
    "document-member": ("member", lambda w, d: _detail(d["guide"])),
    "document-pending": ("employee", lambda w, d: _detail(d["restricted"])),
    "document-upload": (
        "employee",
        lambda w, d: reverse("documents:create", args=[w["community"].slug]),
    ),
    "document-edit": (
        "employee",
        lambda w, d: reverse("documents:edit", args=[d["guide"].public_id]),
    ),
    "document-new-version": (
        "employee",
        lambda w, d: reverse("documents:add_version", args=[d["guide"].public_id]),
    ),
    "post-linked-documents": (
        "member",
        lambda w, d: reverse("posts:detail", args=[w["community"].slug, w["article"].public_id]),
    ),
}


def _resources(world):
    return reverse("documents:community_documents", args=[world["community"].slug])


def _detail(document):
    return reverse("documents:detail", args=[document.public_id])


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("name", list(PAGES))
def test_no_serious_or_critical_violation(
    name, scheme, viewport, world, documents, open_page, run_axe
):
    role, build = PAGES[name]
    page = open_page(build(world, documents), role=role, scheme=scheme, viewport=viewport)
    assert page.locator("h1").count() == 1
    _assert_no_blocking(f"{name} ({scheme}, {viewport})", run_axe(page))


@pytest.mark.parametrize("scheme", SCHEMES)
def test_archive_modal(scheme, world, documents, open_page, run_axe):
    page = open_page(_detail(documents["guide"]), role="employee", scheme=scheme)
    page.get_by_role("button", name="Archive").first.click()
    modal = page.locator("#archive-document")
    modal.wait_for(state="visible")
    page.wait_for_function("document.activeElement.closest('#archive-document') !== null")
    _assert_no_blocking(f"archive modal ({scheme})", run_axe(page))


def test_restricted_field_follows_visibility(world, documents, open_page):
    page = open_page(reverse("documents:create", args=[world["community"].slug]), role="employee")
    min_role = page.locator("#id_min_role")
    assert not min_role.is_visible()
    restricted = page.locator('input[name="visibility"][value="restricted"]')
    restricted.focus()
    page.keyboard.press("Space")
    assert restricted.is_checked()
    assert min_role.is_visible()
