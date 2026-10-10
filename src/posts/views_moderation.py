"""Moderation queue of a community: open reports grouped by target, and posts awaiting review.

Reserved to moderators+ and functional administrators who can read the content, whatever the
community status. A viewer who may not see the community content answers 404 on a private
community and 403 where the community is known to them (open community, functional
administrator without a grant, member below moderator).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from communities.models import Community
from communities.policies import is_functional_admin
from communities.views import community_page
from core.errors import DomainError

from . import policies, selectors_moderation
from .forms_moderation import ReportDecisionForm
from .models import Comment, ContentReport, Post
from .services_moderation import RESOLVE_AND_HIDE, decide_reports, report_target
from .views_errors import domain_error_response

QUEUE_PAGE_SIZE = 20
REPORTS, REVIEW = "reports", "review"
TAB_LABELS = {REPORTS: gettext_lazy("Reports"), REVIEW: gettext_lazy("Awaiting review")}


def queue_url(community, tab=REPORTS) -> str:
    url = reverse("posts:moderation_queue", args=[community.slug])
    return url if tab == REPORTS else f"{url}?tab={tab}"


@login_required
@require_safe
@never_cache
def moderation_queue(request, slug):
    community, context = community_page(request, slug, "moderation")
    user = request.user
    if not policies.can_moderate(user, community):
        private = community.access_mode != Community.AccessMode.OPEN
        if not context["content_visible"] and private and not is_functional_admin(user):
            raise Http404
        raise PermissionDenied
    tab = REVIEW if request.GET.get("tab") == REVIEW else REPORTS
    reported_count, pending_count = selectors_moderation.queue_counts(community)
    if tab == REPORTS:
        page_obj = Paginator(
            selectors_moderation.report_groups(community), QUEUE_PAGE_SIZE
        ).get_page(request.GET.get("page"))
        page_obj.object_list = selectors_moderation.load_report_groups(
            community, page_obj.object_list
        )
    else:
        page_obj = Paginator(
            selectors_moderation.pending_posts(community), QUEUE_PAGE_SIZE
        ).get_page(request.GET.get("page"))
    counts = {REPORTS: reported_count, REVIEW: pending_count}
    context.update(
        {
            "tab": tab,
            "queue_tabs": [
                (TAB_LABELS[key], queue_url(community, key), key == tab, counts[key])
                for key in (REPORTS, REVIEW)
            ],
            "page_obj": page_obj,
            "reported_count": reported_count,
            "pending_count": pending_count,
            "can_review": policies.can_review(user, community),
        }
    )
    return render(request, "posts/moderation_queue.html", context)


@login_required
@require_POST
def report_decide(request, public_id):
    report = get_object_or_404(
        ContentReport.objects.select_related(
            "community", "post__community", "comment__post__community"
        ),
        public_id=public_id,
    )
    target = report_target(report)
    post = target.post if isinstance(target, Comment) else target
    if not policies.can_view_post(request.user, post):
        raise Http404
    if not policies.can_moderate(request.user, report.community):
        raise PermissionDenied
    form = ReportDecisionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    redirect_to = queue_url(report.community)
    decision = form.cleaned_data["decision"]
    try:
        decide_reports(
            actor=request.user, report=report, decision=decision, note=form.cleaned_data["note"]
        )
    except DomainError as error:
        if error.code == "reason_required":
            return _report_decision_page(request, report, form.cleaned_data["note"], error)
        return domain_error_response(request, error, redirect_to=redirect_to)
    if decision == RESOLVE_AND_HIDE:
        messages.success(request, _("The content has been hidden and the reports resolved."))
    elif decision == "resolve":
        messages.success(request, _("The reports have been resolved."))
    else:
        messages.success(request, _("The reports have been dismissed."))
    return redirect(redirect_to)


def _report_decision_page(request, report, note, error):
    """The decision form of ``report``'s target alone, with ``error`` and the typed ``note``."""
    target = report_target(report)
    rows = [
        {
            "post_id": target.pk if isinstance(target, Post) else None,
            "comment_id": target.pk if isinstance(target, Comment) else None,
            "is_open": True,
            "report_count": 0,
            "latest": None,
        }
    ]
    group = selectors_moderation.load_report_groups(report.community, rows)[0]
    context = {
        "community": report.community,
        "group": group,
        "note": note,
        "error": error.message,
        "back_url": queue_url(report.community),
    }
    return render(request, "posts/report_decision.html", context)
