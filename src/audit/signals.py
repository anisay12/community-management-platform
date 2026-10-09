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


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="audit_user_roles_changed")
def user_roles_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Audit role changes made through ``user.groups`` or ``group.user_set``."""
    if action == "pre_clear":
        # The related set is gone by post_clear: remember it.
        if reverse:
            instance._audit_cleared = [(u, [instance.name]) for u in instance.user_set.all()]
        else:
            instance._audit_cleared = [
                (instance, list(instance.groups.values_list("name", flat=True)))
            ]
        return
    key = ACTIONS.get(action)
    if key is None:
        return
    if action == "post_clear":
        for user, names in instance.__dict__.pop("_audit_cleared", []):
            _record(user, key, names)
    elif reverse:  # instance is a Group, pk_set holds user ids
        for user in User.objects.filter(pk__in=pk_set):
            _record(user, key, [instance.name])
    else:  # instance is a User, pk_set holds group ids
        _record(instance, key, Group.objects.filter(pk__in=pk_set).values_list("name", flat=True))
