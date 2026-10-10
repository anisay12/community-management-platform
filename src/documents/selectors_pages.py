"""Read queries of the document pages (L5): the community Resources tab, the document page,
the post page and the dashboard blocks. Visibility always comes from
``Document.objects.visible_to`` (and ``Post.objects.visible_to`` for posts)."""

from django.db.models import Exists, OuterRef, Prefetch, Q
from django.db.models.functions import Lower

from communities.models import CommunityMembership
from posts.models import Post
from taxonomy.models import Tag

from .models import Document, DocumentLink, DocumentVersion

CLEAN = DocumentVersion.ScanStatus.CLEAN

# Resources tab sort orders (``sort`` query parameter).
SORTS = {
    "recent": ("-updated_at", "-pk"),
    "downloads": ("-download_count", "-updated_at", "-pk"),
    "title": (Lower("title"), "pk"),
}
# Status filter of the managers (``status`` query parameter); "" is the default view.
STATUS_ACTIVE = ""
STATUS_FILTERS = ("", "archived", "expired", "scanning", "all")
LINKABLE_POSTS_LIMIT = 50


def _with_list_relations(queryset):
    return queryset.select_related("current_version", "community").prefetch_related(
        Prefetch("tags", queryset=Tag.objects.order_by("name"))
    )


def community_documents(
    user, community, *, doc_type="", tag="", query="", sort="recent", status=STATUS_ACTIVE
):
    """Documents of ``community`` that ``user`` may open, filtered and sorted.

    The default view lists active documents with a clean reference version. ``status`` (only
    meaningful to managers: readers never see the others) selects ``archived``, ``expired``,
    ``scanning`` (reference version not clean: pending, failed or infected) or ``all``.
    """
    documents = Document.objects.visible_to(user).filter(community=community)
    if status == "all":
        pass
    elif status == "archived":
        documents = documents.filter(status=Document.Status.ARCHIVED)
    elif status == "expired":
        documents = documents.filter(status=Document.Status.EXPIRED)
    elif status == "scanning":
        documents = documents.exclude(current_version__scan_status=CLEAN)
    else:
        documents = documents.filter(
            status=Document.Status.ACTIVE, current_version__scan_status=CLEAN
        )
    if doc_type:
        documents = documents.filter(doc_type=doc_type)
    if tag:
        documents = documents.filter(tags__slug=tag)
    if query:
        documents = documents.filter(title__icontains=query)
    return _with_list_relations(documents.order_by(*SORTS.get(sort, SORTS["recent"])))


def community_document_tags(user, community):
    """Tags used by the documents of ``community`` that ``user`` may open (filter choices)."""
    documents = Document.objects.visible_to(user).filter(community=community).values("pk")
    return Tag.objects.filter(documents__in=documents).distinct().order_by("name")


def owns_documents_in(user, community) -> bool:
    """Whether ``user`` owns at least one document of ``community`` (status filter shown)."""
    return Document.objects.filter(community=community, owner=user).exists()


def document_versions(document):
    """Every version of ``document``, newest first, with its uploader."""
    return list(document.versions.select_related("uploaded_by").order_by("-number"))


def document_links(user, document):
    """Links of ``document`` to posts ``user`` may open, newest first."""
    visible_posts = Post.objects.visible_to(user).values("pk")
    return list(
        DocumentLink.objects.filter(document=document, post__in=visible_posts)
        .select_related("post", "post__community")
        .order_by("-created_at", "-pk")
    )


def linkable_posts(user, document, limit=LINKABLE_POSTS_LIMIT):
    """The most recent published posts of the document's community that ``user`` may open and
    that are not linked to it yet."""
    linked = DocumentLink.objects.filter(document=document, post=OuterRef("pk"))
    return list(
        Post.objects.visible_to(user)
        .filter(community_id=document.community_id, status=Post.Status.PUBLISHED)
        .exclude(Exists(linked))
        .order_by("-published_at", "-pk")[:limit]
    )


def post_documents(user, post):
    """Documents linked to ``post`` that ``user`` may open (one query)."""
    return list(
        Document.objects.visible_to(user)
        .filter(links__post=post)
        .select_related("current_version")
        .order_by(Lower("title"), "pk")
        .distinct()
    )


def recent_member_documents(user, limit=5):
    """The ``limit`` most recently updated readable documents (active, clean reference) of the
    communities ``user`` is a member of."""
    member_of = CommunityMembership.objects.filter(user=user).values("community_id")
    return list(
        Document.objects.visible_to(user)
        .filter(
            community_id__in=member_of,
            status=Document.Status.ACTIVE,
            current_version__scan_status=CLEAN,
        )
        .select_related("community", "current_version")
        .order_by("-updated_at", "-pk")[:limit]
    )


def latest_scan_errors(limit=5):
    """The ``limit`` most recent versions whose scan failed or found a virus (metadata only),
    each with ``document`` and ``document.community`` loaded."""
    return list(
        DocumentVersion.objects.filter(
            Q(scan_status=DocumentVersion.ScanStatus.ERROR)
            | Q(scan_status=DocumentVersion.ScanStatus.INFECTED)
        )
        .select_related("document", "document__community", "document__current_version")
        .order_by("-created_at", "-pk")[:limit]
    )
