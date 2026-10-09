"""Write services on posts (Task 2): creation, editing, review, pinning, hiding, archiving,
accepted answers, sharing and tags.

Every service is atomic, keyword-only with the actor first, checks its policy itself and
raises ``core.errors.DomainError`` (``forbidden``, ``read_only``, ``edit_conflict``, ...).
"""

from django.db import transaction


@transaction.atomic
def create_post(*, actor, community, kind, title, body, tags=(), publish=True):
    raise NotImplementedError


@transaction.atomic
def update_post(*, actor, post, title, body, tags, version):
    raise NotImplementedError


@transaction.atomic
def publish_draft(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def delete_draft(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def approve_review(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def reject_review(*, actor, post, note):
    raise NotImplementedError


@transaction.atomic
def pin_post(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def unpin_post(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def hide_post(*, actor, post, reason):
    raise NotImplementedError


@transaction.atomic
def unhide_post(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def archive_post(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def accept_answer(*, actor, post, comment):
    raise NotImplementedError


@transaction.atomic
def clear_accepted_answer(*, actor, post):
    raise NotImplementedError


@transaction.atomic
def share_post(*, actor, post, target_community, comment=""):
    raise NotImplementedError


@transaction.atomic
def set_tags(*, actor, post, names):
    raise NotImplementedError
