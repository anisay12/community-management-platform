"""Write services on interactions (Task 3): comments, reactions, bookmarks and collections,
content reports.

Every service is atomic, keyword-only with the actor first, checks its policy itself and
raises ``core.errors.DomainError`` (``forbidden``, ``own_content``, ``already_reported``, ...).
"""

from django.db import transaction


@transaction.atomic
def add_comment(*, actor, post, body, parent=None):
    raise NotImplementedError


@transaction.atomic
def update_comment(*, actor, comment, body):
    raise NotImplementedError


@transaction.atomic
def hide_comment(*, actor, comment, reason):
    raise NotImplementedError


@transaction.atomic
def unhide_comment(*, actor, comment):
    raise NotImplementedError


@transaction.atomic
def toggle_reaction(*, actor, target, kind) -> bool:
    raise NotImplementedError


@transaction.atomic
def toggle_bookmark(*, actor, post, collection=None) -> bool:
    raise NotImplementedError


@transaction.atomic
def create_collection(*, actor, name):
    raise NotImplementedError


@transaction.atomic
def rename_collection(*, actor, collection, name):
    raise NotImplementedError


@transaction.atomic
def delete_collection(*, actor, collection):
    raise NotImplementedError


@transaction.atomic
def move_bookmark(*, actor, bookmark, collection):
    raise NotImplementedError


@transaction.atomic
def report(*, actor, target, reason, details=""):
    raise NotImplementedError


@transaction.atomic
def resolve_report(*, actor, report, note="", hide=False):
    raise NotImplementedError


@transaction.atomic
def dismiss_report(*, actor, report, note=""):
    raise NotImplementedError
