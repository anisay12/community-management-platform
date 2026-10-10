"""Decisions of the moderation queue (Task 7), built on the report services of Task 3.

The queue shows the open reports grouped by target, so one decision closes every open
report of that target. A reported post or comment can be hidden; a reported document (L5) is
never hidden but can be archived (``resolve_archive``).
"""

from django.db import transaction
from django.utils import timezone

from audit.services import record
from documents import policies as document_policies
from documents.models import Document
from notifications.services import notify

from . import policies
from .models import Comment
from .selectors_moderation import open_reports, report_target
from .services_interactions import _forbidden, _invalid_state, dismiss_report, resolve_report

RESOLVE_AND_HIDE = "resolve_hide"
RESOLVE_AND_ARCHIVE = "resolve_archive"
RESOLVE = "resolve"
DISMISS = "dismiss"
DECISIONS = (RESOLVE_AND_HIDE, RESOLVE_AND_ARCHIVE, RESOLVE, DISMISS)


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
    if report.document_id:
        return _decide_document_reports(actor=actor, report=report, decision=decision, note=note)
    if decision == RESOLVE_AND_ARCHIVE:
        raise _invalid_state()  # posts are archived from their own page
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
    return _close_all(actor=actor, report=report, target=target, decision=decision, note=note)


def _close_all(*, actor, report, target, decision, note) -> int:
    """Resolve (or dismiss) ``report`` then every other open report of ``target``."""
    service = dismiss_report if decision == DISMISS else resolve_report
    # The first call locks the target: the other open reports are read after it.
    service(actor=actor, report=report, note=note)
    others = list(open_reports(target))
    for other in others:
        service(actor=actor, report=other, note=note)
    return 1 + len(others)


def _decide_document_reports(*, actor, report, decision, note) -> int:
    """``resolve`` / ``dismiss`` close every open report of the document; ``resolve_archive``
    also archives it (``documents.policies.can_archive_document``). ``resolve_hide`` does not
    apply to documents (``invalid_state``)."""
    if decision == RESOLVE_AND_HIDE:
        raise _invalid_state()
    document = Document.objects.select_for_update().get(pk=report.document_id)
    closed = _close_all(actor=actor, report=report, target=document, decision=decision, note=note)
    if decision == RESOLVE_AND_ARCHIVE:
        document.refresh_from_db()
        if not document_policies.can_archive_document(actor, document):
            raise _forbidden()
        _archive_document(actor=actor, document=document)
    return closed


def _archive_document(*, actor, document) -> None:
    """Archive ``document`` (audited ``document.archive``); an archived document stays as it
    is. The caller holds the row lock and has checked ``can_archive_document``.

    TODO(L5 integration): replace with ``documents.services.archive_document``.
    """
    if document.status == Document.Status.ARCHIVED:
        return
    before = document.status
    document.status = Document.Status.ARCHIVED
    document.archived_at = timezone.now()
    document.archived_by = actor
    document.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])
    record(
        actor=actor,
        action="document.archive",
        target=document,
        changes={"status": {"before": before, "after": document.status}},
        community=document.community,
    )


def _open_count(target) -> int:
    return open_reports(target).count()
