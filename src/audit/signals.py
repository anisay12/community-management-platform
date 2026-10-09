from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from core.context import get_request_context

from .services import record

User = get_user_model()
ACTIONS = {"post_add": "added", "post_remove": "removed", "post_clear": "removed"}


def _record(user, key, names):
    if names:
        record(
            actor=get_request_context().user,
            action="user.roles_changed",
            target=user,
            changes={key: sorted(names)},
        )


def _snapshot_removal(instance, reverse, pk_set):
    """Record who actually holds the requested links: Django's pk_set is only the request."""
    through = User.groups.through
    if reverse:  # instance is a Group, pk_set holds user ids
        held = through.objects.filter(group_id=instance.pk, user_id__in=pk_set)
        users = {u.pk: u for u in User.objects.filter(pk__in=held.values("user_id"))}
        return [(users[uid], [instance.name]) for uid in sorted(users)]
    held_ids = through.objects.filter(user_id=instance.pk, group_id__in=pk_set).values("group_id")
    names = list(Group.objects.filter(pk__in=held_ids).values_list("name", flat=True))
    return [(instance, names)]


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="audit_user_roles_changed")
def user_roles_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Audit role changes made through ``user.groups`` or ``group.user_set``."""
    if action == "pre_clear":
        # The related set is gone by post_clear: remember it.
        if reverse:
            instance._audit_pending = [(u, [instance.name]) for u in instance.user_set.all()]
        else:
            instance._audit_pending = [
                (instance, list(instance.groups.values_list("name", flat=True)))
            ]
        return
    if action == "pre_remove":
        instance._audit_pending = _snapshot_removal(instance, reverse, pk_set or ())
        return
    key = ACTIONS.get(action)
    if key is None:
        return
    if action in ("post_clear", "post_remove"):
        for user, names in instance.__dict__.pop("_audit_pending", []):
            _record(user, key, names)
    elif reverse:  # instance is a Group, pk_set holds user ids
        for user in User.objects.filter(pk__in=pk_set):
            _record(user, key, [instance.name])
    else:  # instance is a User, pk_set holds group ids
        _record(instance, key, Group.objects.filter(pk__in=pk_set).values_list("name", flat=True))
