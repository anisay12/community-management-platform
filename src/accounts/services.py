import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import import_module

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from audit.services import record
from core.context import suppress_m2m_audit
from core.errors import DomainError
from organizations.models import Employment, OrganizationUnit

from .models import User, UserProfile
from .policies import hierarchy_allows
from .roles import Role
from .tasks import send_email
from .tokens import activation_token_generator


def site_url(path: str) -> str:
    """Absolute URL for ``path`` on the configured public site (never the Host header)."""
    return settings.SITE_URL.rstrip("/") + path


def user_language(user, default: str | None = None) -> str:
    try:
        language = user.profile.language
    except UserProfile.DoesNotExist:
        language = ""
    return language or default or settings.LANGUAGE_CODE


def enqueue_email(*, user, subject_template: str, body_template: str, context: dict, language):
    """Send the email through Celery once the current transaction commits."""
    kwargs = {
        "to": user.email,
        "subject_template": subject_template,
        "body_template": body_template,
        "context": {"first_name": user.first_name, **context},
        "language": language,
    }
    transaction.on_commit(lambda: send_email.delay(**kwargs))


def send_activation_email(user, *, request=None) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = activation_token_generator.make_token(user)
    url = site_url(reverse("accounts:activate", args=[uid, token]))
    enqueue_email(
        user=user,
        subject_template="emails/activation_subject.txt",
        body_template="emails/activation_body.txt",
        context={
            "activation_url": url,
            "expiry_hours": settings.ACCOUNT_ACTIVATION_TIMEOUT // 3600,
        },
        language=user_language(user),
    )


def end_all_sessions(user) -> int:
    """Delete every session opened by ``user``; return how many were ended."""
    store = import_module(settings.SESSION_ENGINE).SessionStore
    tracked = list(user.tracked_sessions.all())
    for row in tracked:
        store(session_key=row.session_key).delete()
    user.tracked_sessions.filter(pk__in=[row.pk for row in tracked]).delete()
    return len(tracked)


# Account administration -----------------------------------------------------------

ROLE_CODES = frozenset(role.value for role in Role)
SUPERUSER_ONLY_ROLES = frozenset({Role.TECHNICAL_ADMIN.value})


def _role_codes(user) -> list[str]:
    return sorted(user.groups.filter(name__in=ROLE_CODES).values_list("name", flat=True))


def _check_roles(*, actor, before: Iterable[str], after: Iterable[str]) -> list[str]:
    after = sorted(set(after))
    unknown = [code for code in after if code not in ROLE_CODES]
    if unknown:
        raise DomainError("unknown_role", _("Unknown role: %s.") % ", ".join(unknown))
    changed = set(before) ^ set(after)
    if changed & SUPERUSER_ONLY_ROLES and not getattr(actor, "is_superuser", False):
        raise DomainError(
            "forbidden_role",
            _("Only a superuser can grant or revoke the technical administrator role."),
        )
    return after


def _ensure_can_administer(*, actor, user) -> None:
    """Only a superuser may change the status or roles of a protected account."""
    if not hierarchy_allows(actor, user):
        raise DomainError(
            "forbidden_target",
            _(
                "Only a superuser can change the status or the roles of a superuser, "
                "a staff member or a technical administrator."
            ),
        )


EMAIL_MAX_LENGTH = 254
NAME_MAX_LENGTH = 150
MANAGER_PROBLEMS = frozenset({"manager_self", "manager_requires_unit", "unknown_manager"})


def validate_new_user_fields(
    *,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    unit=None,
    manager_email: str | None = None,
    email_taken: bool | None = None,
    manager_known: bool | None = None,
) -> list[tuple[str, str]]:
    """Return the problems with a new account's fields as ``(code, message)`` pairs.

    Shared by the creation form, ``create_user`` and the CSV import so that the rules
    and their messages live in one place. ``None`` names are not checked; ``unit`` is
    only tested for presence. ``email_taken`` and ``manager_known`` are looked up in the
    database unless the caller already knows the answer.
    """
    problems: list[tuple[str, str]] = []
    email = (email or "").strip().lower()
    try:
        if len(email) > EMAIL_MAX_LENGTH:
            raise ValidationError("too long")
        validate_email(email)
    except ValidationError:
        problems.append(("invalid_email", _("Invalid email address: “%s”.") % email))
    else:
        if email_taken is None:
            email_taken = User.objects.filter(email__iexact=email).exists()
        if email_taken:
            problems.append(("email_taken", _("An account already exists for %s.") % email))
    for prefix, value, required in (
        ("first_name", first_name, _("The first name is required.")),
        ("last_name", last_name, _("The last name is required.")),
    ):
        if value is None:
            continue
        if not value.strip():
            problems.append((f"{prefix}_required", required))
        elif len(value.strip()) > NAME_MAX_LENGTH:
            problems.append((f"{prefix}_too_long", _("Names are limited to 150 characters.")))
    manager_email = (manager_email or "").strip().lower()
    if manager_email:
        if manager_email == email:
            problems.append(("manager_self", _("A user cannot be their own manager.")))
        else:
            if not unit:
                problems.append(
                    ("manager_requires_unit", _("A manager can only be set with a unit."))
                )
            if manager_known is None:
                manager_known = User.objects.filter(email__iexact=manager_email).exists()
            if not manager_known:
                problems.append(("unknown_manager", _("Unknown manager: %s.") % manager_email))
    return problems


def _replace_role_groups(user, codes: list[str]) -> None:
    with suppress_m2m_audit():
        user.groups.remove(*user.groups.filter(name__in=ROLE_CODES))
        user.groups.add(*Group.objects.filter(name__in=codes))


@transaction.atomic
def create_user(
    *,
    actor,
    email: str,
    first_name: str,
    last_name: str,
    unit=None,
    manager=None,
    roles: Iterable[str] = (Role.EMPLOYEE.value,),
    send_activation: bool = True,
    audit_action: str = "user.created",
) -> User:
    """Create a pending account (no usable password) and invite its owner by email."""
    email = User.objects.normalize_email(email.strip()).lower()
    codes = _check_roles(actor=actor, before=(), after=roles)
    problems = validate_new_user_fields(
        email=email,
        first_name=first_name,
        last_name=last_name,
        unit=unit,
        manager_email=manager.email if manager is not None else None,
        manager_known=True,
    )
    if problems:
        raise DomainError(*problems[0])
    user = User.objects.create_user(
        email, first_name=first_name.strip(), last_name=last_name.strip()
    )
    if unit is not None:
        Employment.objects.create(user=user, unit=unit, manager=manager)
    _replace_role_groups(user, codes)
    record(
        actor=actor,
        action=audit_action,
        target=user,
        changes={
            "status": [None, user.status],
            "roles": codes,
            "unit": unit.code if unit is not None else None,
        },
    )
    if send_activation:
        send_activation_email(user)
    return user


def _change_status(*, actor, user, allowed_from, new_status: str, action: str, blocks_access):
    if blocks_access and actor is not None and actor.pk == user.pk:
        raise DomainError("self_action", _("You cannot do this to your own account."))
    locked = User.objects.select_for_update().get(pk=user.pk)
    _ensure_can_administer(actor=actor, user=locked)
    old_status = locked.status
    if old_status not in allowed_from:
        raise DomainError(
            "invalid_status",
            _("This action is not possible for an account with status “%(status)s”.")
            % {"status": locked.get_status_display()},
        )
    if new_status != old_status:
        locked.status = new_status
        if new_status == User.Status.DEACTIVATED:
            locked.deactivated_at = timezone.now()
        locked.save(update_fields=["status", "deactivated_at", "updated_at"])
    if blocks_access:
        end_all_sessions(locked)
    record(actor=actor, action=action, target=locked, changes={"status": [old_status, new_status]})
    user.status = locked.status
    user.deactivated_at = locked.deactivated_at
    user.__dict__.pop("is_active", None)
    return locked


@transaction.atomic
def suspend_user(*, actor, user) -> None:
    """Block an active account and end all its sessions."""
    _change_status(
        actor=actor,
        user=user,
        allowed_from={User.Status.ACTIVE},
        new_status=User.Status.SUSPENDED,
        action="user.suspended",
        blocks_access=True,
    )


@transaction.atomic
def reactivate_user(*, actor, user) -> None:
    """Give a suspended account its access back."""
    _change_status(
        actor=actor,
        user=user,
        allowed_from={User.Status.SUSPENDED},
        new_status=User.Status.ACTIVE,
        action="user.reactivated",
        blocks_access=False,
    )


@transaction.atomic
def deactivate_user(*, actor, user) -> None:
    """Close an account for good (anonymization follows later) and end all its sessions."""
    _change_status(
        actor=actor,
        user=user,
        allowed_from={User.Status.PENDING, User.Status.ACTIVE, User.Status.SUSPENDED},
        new_status=User.Status.DEACTIVATED,
        action="user.deactivated",
        blocks_access=True,
    )


@transaction.atomic
def resend_activation(*, actor, user) -> None:
    """Send a new activation link to a pending account."""
    locked = _change_status(
        actor=actor,
        user=user,
        allowed_from={User.Status.PENDING},
        new_status=User.Status.PENDING,
        action="user.activation_resent",
        blocks_access=False,
    )
    send_activation_email(locked)


@transaction.atomic
def set_roles(*, actor, user, roles: Iterable[str]) -> None:
    """Replace the role groups of ``user`` (other groups are kept) and audit it once."""
    # Serialise concurrent role edits of the same user.
    locked = User.objects.select_for_update().get(pk=user.pk)
    _ensure_can_administer(actor=actor, user=locked)
    before = _role_codes(user)
    after = _check_roles(actor=actor, before=before, after=roles)
    if before == after:
        return
    _replace_role_groups(user, after)
    record(
        actor=actor,
        action="user.roles_changed",
        target=user,
        changes={"before": before, "after": after},
    )


# CSV import -------------------------------------------------------------------------

IMPORT_HEADER = ["email", "first_name", "last_name", "unit_code", "manager_email"]
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ImportLineError:
    line: int
    message: str


@dataclass
class ImportResult:
    created: int = 0
    errors: list[ImportLineError] = field(default_factory=list)


@dataclass
class _ImportRow:
    line: int
    email: str
    first_name: str
    last_name: str
    unit_code: str
    manager_email: str


def _read_rows(file) -> tuple[list[_ImportRow], list[ImportLineError]]:
    """Decode and split the CSV; any error here stops the import before validation."""
    data = file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        return [], [ImportLineError(0, _("The file is larger than 2 MB."))]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [ImportLineError(0, _("The file must be encoded in UTF-8."))]
    if "\x00" in text:
        return [], [ImportLineError(0, _("The file is not a valid CSV file."))]
    lines = text.splitlines()
    header_line = lines[0] if lines else ""
    delimiter = next(
        (d for d in (",", ";") if [c.strip() for c in header_line.split(d)] == IMPORT_HEADER),
        None,
    )
    if delimiter is None:
        return [], [
            ImportLineError(1, _("The first line must be exactly: %s") % ",".join(IMPORT_HEADER))
        ]
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    next(reader)
    rows: list[_ImportRow] = []
    errors: list[ImportLineError] = []
    try:
        for values in reader:
            if not any(value.strip() for value in values):
                continue
            if len(rows) + len(errors) >= MAX_IMPORT_ROWS:
                return [], [
                    ImportLineError(
                        0, _("The file has more than %(max)d rows.") % {"max": MAX_IMPORT_ROWS}
                    )
                ]
            if len(values) != len(IMPORT_HEADER):
                errors.append(
                    ImportLineError(
                        reader.line_num,
                        _("Expected %(count)d columns.") % {"count": len(IMPORT_HEADER)},
                    )
                )
                continue
            email, first_name, last_name, unit_code, manager_email = (v.strip() for v in values)
            rows.append(
                _ImportRow(
                    reader.line_num,
                    email.lower(),
                    first_name,
                    last_name,
                    unit_code,
                    manager_email.lower(),
                )
            )
    except csv.Error:
        return [], [ImportLineError(reader.line_num, _("The file is not a valid CSV file."))]
    if not rows and not errors:
        errors.append(ImportLineError(0, _("The file contains no users.")))
    return rows, errors


def _validate_rows(rows: list[_ImportRow]) -> tuple[list[ImportLineError], dict, dict]:
    file_lines: dict[str, int] = {}
    for row in rows:
        file_lines.setdefault(row.email, row.line)
    emails = set(file_lines)
    managers = {row.manager_email for row in rows if row.manager_email}
    taken = set(
        User.objects.annotate(email_lower=Lower("email"))
        .filter(email_lower__in=emails | managers)
        .values_list("email_lower", flat=True)
    )
    existing_managers = {
        user.email: user for user in User.objects.filter(email__in=managers & taken)
    }
    units = {
        unit.code: unit
        for unit in OrganizationUnit.objects.filter(code__in={r.unit_code for r in rows})
    }
    errors = []
    for row in rows:
        problem = _row_problem(row, file_lines, taken, units, existing_managers)
        if problem:
            errors.append(ImportLineError(row.line, problem))
    return errors, units, existing_managers


def _row_problem(row, file_lines, taken, units, existing_managers) -> str | None:
    problems = validate_new_user_fields(
        email=row.email,
        first_name=row.first_name,
        last_name=row.last_name,
        unit=row.unit_code,
        manager_email=row.manager_email,
        email_taken=row.email in taken,
        manager_known=row.manager_email in file_lines or row.manager_email in existing_managers,
    )
    if problems and problems[0][0] == "invalid_email":
        return problems[0][1]
    if file_lines[row.email] != row.line:
        return _("%(email)s is already listed on line %(line)d.") % {
            "email": row.email,
            "line": file_lines[row.email],
        }
    ordered = [message for code, message in problems if code not in MANAGER_PROBLEMS]
    if row.unit_code and row.unit_code not in units:
        ordered.append(_("Unknown unit code: “%s”.") % row.unit_code)
    ordered += [message for code, message in problems if code in MANAGER_PROBLEMS]
    return ordered[0] if ordered else None


@transaction.atomic
def import_users_csv(*, actor, file) -> ImportResult:
    """Create the users listed in a CSV file: all of them, or none if any line is wrong."""
    rows, errors = _read_rows(file)
    if errors:
        return ImportResult(errors=errors)
    errors, units, managers = _validate_rows(rows)
    if errors:
        return ImportResult(errors=errors)
    created = {}
    row = None
    try:
        # A savepoint: if a row fails now (e.g. its email was taken concurrently since
        # the validation pass), every account, audit event and email of this import is
        # rolled back.
        with transaction.atomic():
            for row in rows:
                created[row.email] = create_user(
                    actor=actor,
                    email=row.email,
                    first_name=row.first_name,
                    last_name=row.last_name,
                    unit=units.get(row.unit_code),
                    audit_action="user.imported",
                )
            # Managers are resolved once everyone exists, so they may appear anywhere.
            for row in rows:
                if row.manager_email:
                    manager = created.get(row.manager_email) or managers[row.manager_email]
                    Employment.objects.filter(user=created[row.email]).update(manager=manager)
            record(
                actor=actor,
                action="users.csv_import",
                target=("accounts.user", "csv_import"),
                changes={"created": len(created)},
            )
    except DomainError as error:
        return ImportResult(errors=[ImportLineError(row.line if row else 0, error.message)])
    return ImportResult(created=len(created))
