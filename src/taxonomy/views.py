"""Tag administration (functional administrators; 404 for anyone else): list with usage
counts and search, and merging two tags after a confirmation page."""

from functools import wraps

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.errors import DomainError
from posts.policies import can_merge_tags

from . import services
from .forms import TagMergeForm
from .models import Tag

PAGE_SIZE = 20


def tag_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_merge_tags(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return never_cache(wrapped)


def _tag_list_response(request, merge_form, status=200):
    query = request.GET.get("q", "").strip()
    tags = Tag.objects.annotate(
        post_count=Count("posts", distinct=True),
        community_count=Count("communities", distinct=True),
    ).order_by("name", "pk")
    if query:
        tags = tags.filter(
            Q(name__icontains=query) | Q(key__contains=services.normalize_tag_key(query))
        )
    context = {
        "page_obj": Paginator(tags, PAGE_SIZE).get_page(request.GET.get("page")),
        "query": query,
        "merge_form": merge_form,
    }
    return render(request, "manage/tag_list.html", context, status=status)


@tag_admin_required
def tag_list(request):
    return _tag_list_response(request, TagMergeForm())


@tag_admin_required
@require_POST
def tag_merge(request):
    """Without ``confirm``: the confirmation page (nothing changes). With it: the merge."""
    form = TagMergeForm(request.POST)
    if not form.is_valid():
        return _tag_list_response(request, form, status=400)
    source, target = form.cleaned_data["source"], form.cleaned_data["target"]
    if not form.cleaned_data["confirm"]:
        context = {
            "source": source,
            "target": target,
            "post_count": source.posts.count(),
            "community_count": source.communities.count(),
        }
        return render(request, "manage/tag_merge_confirm.html", context)
    try:
        services.merge_tags(actor=request.user, source=source, target=target)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request,
            _("“%(source)s” has been merged into “%(target)s”.")
            % {"source": source.name, "target": target.name},
        )
    return redirect("manage:tag_list")
