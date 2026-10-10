"""Forms of the document pages (L5).

Forms only shape and bound the input; the business rules (rights, file checks, tag creation,
expiry in the future, version labels) are enforced by ``documents.services`` and reported back
as form errors. Dates are typed as days and interpreted in the user's time zone (activated by
``core.middleware`` from ``UserProfile.timezone``): an expiry date is the end of that day, a
review date its start.
"""

import datetime

from django import forms
from django.conf import settings
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from communities.roles import CommunityRole
from core import idempotency
from posts.forms import TAGS_MAX_LENGTH, split_tags

from .models import CHANGE_NOTE_MAX_LENGTH, DESCRIPTION_MAX_LENGTH, Document, DocumentVersion
from .selectors_pages import SORTS, STATUS_FILTERS
from .validation import ALLOWED_EXTENSIONS

TITLE_MAX_LENGTH = Document._meta.get_field("title").max_length
LABEL_MAX_LENGTH = DocumentVersion._meta.get_field("version_label").max_length
ACCEPT = ",".join(f".{extension}" for extension in sorted(ALLOWED_EXTENSIONS))

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def to_end_of_day(day):
    """The last instant of ``day`` in the current time zone (``None`` stays ``None``)."""
    if day is None:
        return None
    moment = datetime.datetime.combine(day, datetime.time.max)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def to_start_of_day(day):
    """The first instant of ``day`` in the current time zone (``None`` stays ``None``)."""
    if day is None:
        return None
    moment = datetime.datetime.combine(day, datetime.time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def to_local_day(moment):
    """The day of ``moment`` in the current time zone (for the initial value of a field)."""
    return timezone.localdate(moment) if moment else None


def file_help_text() -> str:
    return _("Accepted formats: %(formats)s. At most %(size)s.") % {
        "formats": ", ".join(sorted(ALLOWED_EXTENSIONS)),
        "size": filesizeformat(settings.DOCUMENT_MAX_UPLOAD_BYTES),
    }


class IdempotentForm(forms.Form):
    """A form carrying the hidden ``idempotency_key`` of ``core.idempotency``."""

    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("idempotency_key", idempotency.new_key())


class DocumentMetadataForm(forms.Form):
    """Title, description, type, tags, visibility, roles and dates of a document."""

    title = forms.CharField(label=_("Title"), max_length=TITLE_MAX_LENGTH)
    description = forms.CharField(
        label=_("Description"),
        required=False,
        max_length=DESCRIPTION_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_("Plain text: what the document contains and who it is for."),
    )
    doc_type = forms.ChoiceField(label=_("Type"), choices=Document.DocType.choices)
    tags = forms.CharField(
        label=_("Tags"),
        required=False,
        max_length=TAGS_MAX_LENGTH,
        help_text=_("Separate tags with commas."),
    )
    visibility = forms.ChoiceField(
        label=_("Who can read it"),
        choices=Document.Visibility.choices,
        widget=forms.RadioSelect,
    )
    min_role = forms.ChoiceField(
        label=_("Minimum role to read"),
        choices=CommunityRole.choices,
        initial=CommunityRole.MEMBER,
        help_text=_("Only used when the document is restricted to members with a minimum role."),
    )
    download_min_role = forms.ChoiceField(
        label=_("Minimum role to download"),
        choices=CommunityRole.choices,
        initial=CommunityRole.MEMBER,
        help_text=_("Readers below this role see the document but cannot download it."),
    )
    expires_at = forms.DateField(
        label=_("Expiry date"),
        required=False,
        widget=DATE_WIDGET,
        help_text=_("Optional. The document is no longer offered to readers after this day."),
    )
    review_due_at = forms.DateField(
        label=_("Review date"),
        required=False,
        widget=DATE_WIDGET,
        help_text=_("Optional. From this day, the document appears in the documents to review."),
    )

    def __init__(self, *args, can_create_tags=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("visibility", Document.Visibility.COMMUNITY)
        self.initial.setdefault("doc_type", Document.DocType.OTHER)
        self.fields["tags"].widget.attrs.update({"list": "tag-suggestions", "autocomplete": "off"})
        self.fields["tags"].help_text = (
            _("Separate tags with commas. Pick an existing tag or type a new one.")
            if can_create_tags
            else _("Separate tags with commas. Only existing tags can be used.")
        )
        self.fields["min_role"].widget.attrs["data-documents-min-role"] = "1"

    def metadata(self) -> dict:
        """Keyword arguments of ``create_document`` / ``update_document``."""
        data = self.cleaned_data
        return {
            "title": data["title"],
            "description": data["description"],
            "doc_type": data["doc_type"],
            "tags": split_tags(data.get("tags", "")),
            "visibility": data["visibility"],
            "min_role": data["min_role"],
            "download_min_role": data["download_min_role"],
            "expires_at": to_end_of_day(data.get("expires_at")),
            "review_due_at": to_start_of_day(data.get("review_due_at")),
        }

    @classmethod
    def initial_for(cls, document) -> dict:
        return {
            "title": document.title,
            "description": document.description,
            "doc_type": document.doc_type,
            "tags": ", ".join(tag.name for tag in document.tags.order_by("name")),
            "visibility": document.visibility,
            "min_role": document.min_role,
            "download_min_role": document.download_min_role,
            "expires_at": to_local_day(document.expires_at),
            "review_due_at": to_local_day(document.review_due_at),
        }


def _file_field():
    return forms.FileField(
        label=_("File"),
        widget=forms.ClearableFileInput(attrs={"accept": ACCEPT}),
    )


def _change_note_field(label):
    return forms.CharField(
        label=label,
        required=False,
        max_length=CHANGE_NOTE_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


def _version_label_field():
    return forms.CharField(
        label=_("Version label"),
        required=False,
        max_length=LABEL_MAX_LENGTH,
        help_text=_("Optional, for example 2.0 or 2026-Q3. The version number by default."),
    )


class DocumentUploadForm(IdempotentForm, DocumentMetadataForm):
    """The upload page: the file, its metadata and the note of version 1."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"] = _file_field()
        self.fields["file"].help_text = file_help_text()
        self.fields["version_label"] = _version_label_field()
        self.fields["change_note"] = _change_note_field(_("Note"))
        self.order_fields(["file", "title", "description", "doc_type", "tags"])


class VersionUploadForm(IdempotentForm):
    """A new version of a document."""

    make_reference = forms.BooleanField(
        label=_("Make it the reference version once scanned"), required=False, initial=True
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"] = _file_field()
        self.fields["file"].help_text = file_help_text()
        self.fields["version_label"] = _version_label_field()
        self.fields["change_note"] = _change_note_field(_("What changed"))
        self.order_fields(["file", "version_label", "change_note", "make_reference"])


STATUS_LABELS = (
    _("Active"),
    _("Archived"),
    _("Expired"),
    _("Scan in progress or failed"),
    _("All"),
)


class ResourcesFilterForm(forms.Form):
    """Filters of the Resources tab (invalid values are ignored)."""

    q = forms.CharField(label=_("Search in titles"), required=False, max_length=200)
    doc_type = forms.ChoiceField(
        label=_("Type"),
        required=False,
        choices=[("", _("All types")), *Document.DocType.choices],
    )
    tag = forms.ChoiceField(label=_("Tag"), required=False)
    sort = forms.ChoiceField(
        label=_("Sort by"),
        required=False,
        choices=[
            ("recent", _("Most recent")),
            ("downloads", _("Most downloaded")),
            ("title", _("Title")),
        ],
    )
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=list(
            zip(
                STATUS_FILTERS,
                [
                    _("Active"),
                    _("Archived"),
                    _("Expired"),
                    _("Scan in progress or failed"),
                    _("All"),
                ],
                strict=True,
            )
        ),
    )

    def __init__(self, *args, tags=(), show_status=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tag"].choices = [("", _("All tags")), *((t.slug, t.name) for t in tags)]
        if not show_status:
            del self.fields["status"]

    def filters(self) -> dict:
        data = self.cleaned_data if self.is_valid() else {}
        sort = data.get("sort") or "recent"
        return {
            "query": (data.get("q") or "").strip(),
            "doc_type": data.get("doc_type") or "",
            "tag": data.get("tag") or "",
            "sort": sort if sort in SORTS else "recent",
            "status": data.get("status") or "",
        }


class LinkPostForm(forms.Form):
    post = forms.ChoiceField(label=_("Post"))

    def __init__(self, *args, posts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["post"].choices = [(str(post.public_id), post.title) for post in posts]
