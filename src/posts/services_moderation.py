"""Decisions of the moderation queue (Task 7), built on the report services of Task 3.

The queue shows the open reports grouped by target, so one decision closes every open
report of that target.
"""

from django.db import transaction

from notifications.services import notify

from . import policies
from .models import Comment
from .selectors_moderation import open_reports, report_target
from .services_interactions import dismiss_report, resolve_report

RESOLVE_AND_HIDE = "resolve_hide"
RESOLVE = "resolve"
DISMISS = "dismiss"
DECISIONS = (RESOLVE_AND_HIDE, RESOLVE, DISMISS)


def _can_view(user, target) -> bool:
    if isinstance(target, Comment):
        return policies.can_view_comment(user, target)
    return policies.can_view_post(user, target)


@transaction.atomic
def decide_reports(*, actor, report, decision, note="") -> int:
    """Apply ``decision`` to ``report`` and to every other open report of its target; return
    the number of reports closed.

    ``resolve_hide`` hides the target with ``note`` as the reason (or confirms an automatic
    hiding) and tells its author (``system``); ``resolve`` and ``dismiss`` close the reports,
    and an automatically hidden target comes back once none remains open. The report
    services check the actor's rights and audit each decision.
    """
    if decision not in DECISIONS:
        raise ValueError(f"Unknown report decision: {decision!r}")
    # Lock the target first (as the report services do) and read its state under the lock:
    # a hiding decided meanwhile by another moderator is neither repeated nor notified twice.
    target = report_target(report)
    target = type(target).objects.select_for_update().get(pk=target.pk)
    was_hidden_by_moderator = (
        target.status == target.Status.HIDDEN and target.hidden_by_id is not None
    )
    if decision == RESOLVE_AND_HIDE:
        before = _open_count(target)
        resolve_report(actor=actor, report=report, note=note, hide=True)
        target.refresh_from_db()
        if (
            not was_hidden_by_moderator
            and target.author is not None
            and _can_view(target.author, target)
        ):
            notify(
                "system", [target.author], actor=actor, target=target, community=report.community
            )
        return before
    service = resolve_report if decision == RESOLVE else dismiss_report
    # The first call locks the target: the other open reports are read after it.
    service(actor=actor, report=report, note=note)
    others = list(open_reports(target))
    for other in others:
        service(actor=actor, report=other, note=note)
    return 1 + len(others)


def _open_count(target) -> int:
    return open_reports(target).count()
