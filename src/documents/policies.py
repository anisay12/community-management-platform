"""Authorization rules for documents: pure predicates that never raise.

Reading: a document the user may not see (``can_view_document`` false) answers 404; a visible
document where the action is refused answers 403. Mirrored in SQL by
``selectors.visible_documents_q`` (the visibility tests check that they agree).

- A document is *readable* by whoever reads its community's content
  (``communities.policies.can_view_content``), restricted to members with ``min_role`` or
  above when its visibility is ``restricted``; and only once its reference version is clean.
- Its *managers* (the owner, the community's moderators and above, and a functional admin
  who can read the content) also see it while it is being scanned, archived or expired.
- Downloading needs ``download_min_role`` (managers always may) and a clean version.
- Uploading needs a current membership of an ``active`` community: contributor or above, or
  any member when the community sets ``allow_member_uploads`` (framing § 4.4, note 2).
"""

from communities.models import Community
from communities.policies import can_view_content, is_functional_admin, membership_of
from communities.roles import CommunityRole, role_at_least

from .models import Document, DocumentVersion


def _is_active_user(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def _is_live(community) -> bool:
    return community.status == Community.Status.ACTIVE


def _role_at_least(user, community, minimum) -> bool:
    membership = membership_of(user, community)
    return membership is not None and role_at_least(membership.role, minimum)


def _is_owner(user, document) -> bool:
    return document.owner_id is not None and document.owner_id == getattr(user, "pk", None)


def is_document_moderator(user, community) -> bool:
    """Moderator+ of ``community``, or a functional admin who can read its content."""
    if not _is_active_user(user):
        return False
    if _role_at_least(user, community, CommunityRole.MODERATOR):
        return True
    return is_functional_admin(user) and can_view_content(user, community)


def is_document_manager(user, document) -> bool:
    """The owner (while they can still read the community) or a document moderator."""
    if not can_view_content(user, document.community):
        return False
    return _is_owner(user, document) or is_document_moderator(user, document.community)


def _has_clean_reference(document) -> bool:
    version = document.current_version
    return version is not None and version.scan_status == DocumentVersion.ScanStatus.CLEAN


def _meets_read_role(user, document) -> bool:
    if document.visibility != Document.Visibility.RESTRICTED:
        return True
    return _role_at_least(user, document.community, document.min_role)


# Reading ---------------------------------------------------------------------------


def can_view_document(user, document) -> bool:
    if not _is_active_user(user) or not can_view_content(user, document.community):
        return False
    if is_document_manager(user, document):
        return True
    return (
        document.status == Document.Status.ACTIVE
        and _has_clean_reference(document)
        and _meets_read_role(user, document)
    )


def can_view_version(user, version) -> bool:
    """Older versions are listed to readers once clean; managers see every version."""
    document = version.document
    if not can_view_document(user, document):
        return False
    return version.is_clean or is_document_manager(user, document)


def can_download(user, document, version=None) -> bool:
    """Download (or preview) ``version`` (the reference version by default)."""
    version = version or document.current_version
    if version is None or version.document_id != document.pk or not version.is_clean:
        return False
    if not can_view_document(user, document):
        return False
    if is_document_manager(user, document):
        return True
    return _role_at_least(user, document.community, document.download_min_role)


# Writing ---------------------------------------------------------------------------


def can_upload(user, community) -> bool:
    if not _is_active_user(user) or not _is_live(community):
        return False
    membership = membership_of(user, community)
    if membership is None:
        return False
    if role_at_least(membership.role, CommunityRole.CONTRIBUTOR):
        return True
    return community.allow_member_uploads


def can_edit_document(user, document) -> bool:
    """Edit metadata and add versions: the owner (who may still upload there) or a moderator,
    in an active community, on a document that is not archived."""
    if document.status == Document.Status.ARCHIVED or not _is_live(document.community):
        return False
    if not can_view_document(user, document):
        return False
    if is_document_moderator(user, document.community):
        return True
    return _is_owner(user, document) and can_upload(user, document.community)


def can_set_reference(user, version) -> bool:
    return version.is_clean and can_edit_document(user, version.document)


def can_archive_document(user, document) -> bool:
    """Archive (and restore): the owner or a moderator, whatever the community status
    (moderation works on read-only communities, as for posts)."""
    if not can_view_document(user, document):
        return False
    return _is_owner(user, document) or is_document_moderator(user, document.community)


def can_link_document(user, document, post) -> bool:
    """Link ``document`` to ``post`` of the same community (the caller checks it may see the
    post)."""
    return post.community_id == document.community_id and can_edit_document(user, document)


def can_bookmark_document(user, document) -> bool:
    return document.status == Document.Status.ACTIVE and can_view_document(user, document)


def can_report_document(user, document) -> bool:
    return (
        document.status == Document.Status.ACTIVE
        and not _is_owner(user, document)
        and can_view_document(user, document)
    )
