import json
from functools import wraps
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .forms import AuditFilterForm
from .policies import can_view_audit_log
from .selectors import action_choices, events_visible_to, filter_events

PAGE_SIZE = 50


def audit_required(view):
    """Read-only (GET/HEAD) page answering 404 to anyone outside the audit log scope."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_view_audit_log(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return never_cache(require_safe(wrapped))


@audit_required
def event_list(request):
    scope = events_visible_to(request.user)
    form = AuditFilterForm(request.GET, actions=action_choices(scope))
    events = scope
    querystring = ""
    if form.is_valid():
        filters = {key: value for key, value in form.cleaned_data.items() if value}
        events = filter_events(scope, **filters)
        querystring = urlencode({key: str(value) for key, value in filters.items()})
    page_obj = Paginator(events, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "audit/event_list.html",
        {"page_obj": page_obj, "form": form, "querystring": querystring},
    )


@audit_required
def event_detail(request, pk):
    event = get_object_or_404(events_visible_to(request.user), pk=pk)
    changes = json.dumps(event.changes, indent=2, ensure_ascii=False, sort_keys=True)
    return render(request, "audit/event_detail.html", {"event": event, "changes": changes})
