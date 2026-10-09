"""Read side of communities: catalogue, community page, members tab, administrator access.

A community the viewer may not see answers 404; a visible one whose content or action is
refused answers 403 (see ``policies``).
"""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_safe

from core.errors import DomainError

from . import policies, tabs
from .forms import SORT_ORDERING, AdminAccessForm, CatalogueFilterForm
from .models import CommunityMembership
from .roles import CommunityRole
from .selectors import visible_communities

CATALOGUE_PAGE_SIZE = 24
MEMBERS_PAGE_SIZE = 50
# Bootstrap colour family of ``components/community_card.html`` per access mode.
CARD_ACCESS_STYLE = {"open": "open", "request": "restricted", "invite": "private"}


def _filter_catalogue(user, communities, data):
    if data.get("q"):
        query = SearchQuery(data["q"], search_type="websearch", config="simple")
        communities = communities.filter(Q(search_vector=query) | Q(name__icontains=data["q"]))
    if data.get("category"):
        communities = communities.filter(category=data["category"])
    if data.get("access_mode"):
        communities = communities.filter(access_mode=data["access_mode"])
    if data.get("mine"):
        member_of = CommunityMembership.objects.filter(user=user).values("community_id")
        communities = communities.filter(pk__in=member_of)
    ordering = SORT_ORDERING.get(data.get("sort") or "activity")
    return communities.order_by(*ordering) if ordering else communities.order_by(Lower("name"))


@login_required
@require_safe
def catalogue(request):
    form = CatalogueFilterForm(request.GET)
    data = form.cleaned_data if form.is_valid() else {}
    communities = _filter_catalogue(request.user, visible_communities(request.user), data)
    page_obj = Paginator(communities, CATALOGUE_PAGE_SIZE).get_page(request.GET.get("page"))
    for community in page_obj:
        community.card_access_style = CARD_ACCESS_STYLE[community.access_mode]
    querystring = urlencode(
        {
            key: getattr(value, "slug", value)
            for key, value in data.items()
            if value and key in form.fields
        }
    )
    return render(
        request,
        "communities/catalogue.html",
        {"form": form, "page_obj": page_obj, "querystring": querystring},
    )


def _community_page(request, slug, tab):
    """The visible community and the shared header context (404 when invisible)."""
    leads = CommunityMembership.objects.filter(role=CommunityRole.OWNER).select_related("user")
    community = get_object_or_404(
        visible_communities(request.user).prefetch_related(
            Prefetch("memberships", queryset=leads, to_attr="leads")
        ),
        slug=slug,
    )
    user = request.user
    content_visible = policies.can_view_content(user, community)
    context = {
        "community": community,
        "membership": policies.membership_of(user, community),
        "content_visible": content_visible,
        "show_admin_access": policies.is_functional_admin(user) and not content_visible,
        "tabs": tabs.tabs_for(user, community, tab),
    }
    return community, context


@login_required
@require_safe
def detail(request, slug):
    _, context = _community_page(request, slug, "about")
    return render(request, "communities/detail.html", context)


@login_required
@require_safe
def members(request, slug):
    community, context = _community_page(request, slug, "members")
    if not context["content_visible"]:
        raise PermissionDenied
    memberships = community.memberships.select_related("user").order_by("joined_at", "pk")
    context["page_obj"] = Paginator(memberships, MEMBERS_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    return render(request, "communities/members.html", context)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def admin_access(request, slug):
    """ "Access as administrator": a functional admin states a reason to read the content."""
    community, context = _community_page(request, slug, "about")
    if not policies.is_functional_admin(request.user):
        raise PermissionDenied
    form = AdminAccessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # Task 2's service; imported here only, as the read side does not depend on services.
        from . import services

        try:
            services.grant_admin_access(
                actor=request.user, community=community, reason=form.cleaned_data["reason"]
            )
        except DomainError as error:
            form.add_error(None, error.message)
        else:
            return redirect("communities:detail", slug=community.slug)
    context["form"] = form
    return render(request, "communities/admin_access.html", context)
