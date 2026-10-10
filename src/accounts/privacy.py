"""Personal data (GDPR): export of a user's data and anonymization of closed accounts.

Each lot owning personal data registers an exporter (a named section of the export) and
an anonymizer (run when an account is anonymized), typically from its AppConfig.ready().
"""

import json
from collections.abc import Callable

from axes.models import AccessAttempt, AccessFailureLog, AccessLog
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_otp import devices_for_user

from audit.models import AuditEvent
from audit.services import record
from core.errors import DomainError
from organizations.models import Employment

from .models import DataExport, ExternalIdentity, User, UserProfile
from .policies import hierarchy_allows
from .roles import user_roles

Exporter = Callable[[User], dict | list]
Anonymizer = Callable[[User], None]

_exporters: dict[str, Exporter] = {}
_anonymizers: list[Anonymizer] = []


def register_exporter(name: str, fn: Exporter) -> None:
    """Add (or replace) the export section ``name``, built by ``fn(user)``."""
    _exporters[name] = fn


def register_anonymizer(fn: Anonymizer) -> None:
    """Run ``fn(user)`` inside the transaction that anonymizes ``user``."""
    if fn not in _anonymizers:
        _anonymizers.append(fn)


# Export ---------------------------------------------------------------------------


def export_account(user: User) -> dict:
    """The account, profile, employment, roles and sign-in providers of ``user``."""
    profile = UserProfile.objects.get(user=user)
    employment = Employment.objects.select_related("unit", "manager").filter(user=user).first()
    return {
        "public_id": user.public_id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "status": user.status,
        "created_at": user.created_at,
        "activated_at": user.activated_at,
        "last_seen_at": user.last_seen_at,
        "deactivated_at": user.deactivated_at,
        "profile": {
            "job_title": profile.job_title,
            "bio": profile.bio,
            "has_avatar": bool(profile.avatar),
            "timezone": profile.timezone,
            "language": profile.language,
            "profile_visibility": profile.profile_visibility,
            "is_discoverable": profile.is_discoverable,
            "interests": list(profile.interests.values_list("name", flat=True)),
        },
        "employment": (
            {
                "unit": employment.unit.code,
                "manager_email": employment.manager.email if employment.manager else None,
            }
            if employment
            else None
        ),
        "roles": sorted(user_roles(user)),
        # Subjects are identifiers of the identity provider, not shown to the user.
        "external_identities": [
            {"provider": provider, "created_at": created, "last_login_at": last_login}
            for provider, created, last_login in ExternalIdentity.objects.filter(
                user=user
            ).values_list("provider", "created_at", "last_login_at")
        ],
    }


def export_audit(user: User) -> list:
    """The actions ``user`` performed, without their targets' identifiers."""
    return [
        {"action": action, "target_type": target_type, "created_at": created_at}
        for action, target_type, created_at in AuditEvent.objects.filter(actor=user)
        .order_by("created_at")
        .values_list("action", "target_type", "created_at")
    ]


register_exporter("account", export_account)
register_exporter("audit", export_audit)


def build_export(user: User) -> dict:
    return {
        "generated_at": timezone.now(),
        "sections": {name: fn(user) for name, fn in _exporters.items()},
    }


def render_export(user: User) -> bytes:
    data = build_export(user)
    return json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2).encode("utf-8")


# Anonymization --------------------------------------------------------------------


def delete_export_files(exports) -> None:
    """Delete the stored files of ``exports`` once the current transaction commits."""
    names = [export.file.name for export in exports if export.file]
    if names:
        storage = DataExport._meta.get_field("file").storage

        def _delete():
            for name in names:
                storage.delete(name)

        transaction.on_commit(_delete)


def _clear_profile(user: User) -> None:
    profile, _created = UserProfile.objects.get_or_create(user=user)
    if profile.avatar:
        avatar_name, storage = profile.avatar.name, profile.avatar.storage
        transaction.on_commit(lambda: storage.delete(avatar_name))
    if profile.avatar_pending_key:  # an upload still in quarantine (its scan task stops)
        pending_key = profile.avatar_pending_key
        transaction.on_commit(lambda: default_storage.delete(pending_key))
    defaults = {
        field: UserProfile._meta.get_field(field).get_default()
        for field in (
            "job_title",
            "bio",
            "avatar",
            "avatar_pending_key",
            "avatar_scan_status",
            "timezone",
            "language",
            "profile_visibility",
            "is_discoverable",
        )
    }
    for field, value in defaults.items():
        setattr(profile, field, value)
    profile.save()
    profile.interests.clear()


ANONYMIZE_STATUS_MESSAGE = gettext_lazy("Only a deactivated account can be anonymized.")


def can_be_anonymized(user: User) -> bool:
    """Only a deactivated account that is not yet anonymized can be anonymized."""
    return user.status == User.Status.DEACTIVATED and user.anonymized_at is None


@transaction.atomic
def anonymize_user(*, actor, user: User) -> None:
    """Irreversibly erase the personal data of a deactivated account (idempotent).

    The row is kept, so audit events keep pointing at it; it is shown as "Former
    employee". ``actor`` is None when the retention task does it.
    """
    # Imported here: services imports the Celery tasks, which import this module.
    from .services import end_all_sessions

    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.anonymized_at is not None:
        return
    if actor is not None and not hierarchy_allows(actor, locked):
        raise DomainError(
            "forbidden_target",
            _(
                "Only a superuser can anonymize a superuser, a staff member or a "
                "technical administrator."
            ),
        )
    if not can_be_anonymized(locked):
        raise DomainError("invalid_status", str(ANONYMIZE_STATUS_MESSAGE))

    _clear_profile(locked)
    Employment.objects.filter(user=locked).delete()
    ExternalIdentity.objects.filter(user=locked).delete()
    end_all_sessions(locked)
    # Locked before their file names are read: a build that is saving its file holds
    # the row, and once it commits the file name it stored is read here and deleted.
    exports = list(DataExport.objects.select_for_update().filter(user=locked))
    delete_export_files(exports)
    DataExport.objects.filter(pk__in=[export.pk for export in exports]).delete()
    for device in devices_for_user(locked, confirmed=None):
        device.delete()
    # Sign-in records of django-axes are keyed by the (lowercased) email address.
    for model in (AccessAttempt, AccessFailureLog, AccessLog):
        model.objects.filter(username__iexact=locked.email).delete()

    locked.first_name = ""
    locked.last_name = ""
    locked.email = f"anonymized-{locked.public_id}@invalid.invalid"
    locked.set_unusable_password()
    locked.anonymized_at = timezone.now()
    locked.save(
        update_fields=[
            "first_name",
            "last_name",
            "email",
            "password",
            "anonymized_at",
            "updated_at",
        ]
    )
    for anonymizer in _anonymizers:
        anonymizer(locked)
    # No personal data in the event: the target is the account's public identifier.
    record(actor=actor, action="user.anonymized", target=locked, changes={})

    for field in ("first_name", "last_name", "email", "password", "anonymized_at"):
        setattr(user, field, getattr(locked, field))
