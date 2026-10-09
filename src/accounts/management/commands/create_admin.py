import getpass
import os

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.functions import Lower

from accounts.models import User
from accounts.roles import Role
from audit.services import record
from core.context import suppress_m2m_audit

NAME_MAX_LENGTH = 150


class Command(BaseCommand):
    help = (
        "Bootstrap the first administrator of an environment (staging or production): "
        "an active superuser with the technical_admin role, recorded in the audit log. "
        "Refuses when an active superuser already exists, unless --force-additional. "
        "The password is prompted, or read from the environment variable named by "
        "--password-from-env."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="Platform")
        parser.add_argument("--last-name", default="Administrator")
        parser.add_argument(
            "--password-from-env",
            metavar="VAR",
            help="Read the password from this environment variable instead of prompting.",
        )
        parser.add_argument(
            "--force-additional",
            action="store_true",
            help="Create the account even though an active superuser already exists.",
        )

    def handle(self, *args, **options):
        email = User.objects.normalize_email(options["email"].strip()).lower()
        first_name = options["first_name"].strip()
        last_name = options["last_name"].strip()
        if not first_name or not last_name:
            raise CommandError("The first and last names must not be empty.")
        if len(first_name) > NAME_MAX_LENGTH or len(last_name) > NAME_MAX_LENGTH:
            raise CommandError(f"Names are limited to {NAME_MAX_LENGTH} characters.")
        force_additional = options["force_additional"]
        self._check_preconditions(email, force_additional)
        password = self._password(options["password_from_env"])
        try:
            validate_password(password, User(email=email, first_name=first_name))
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        with transaction.atomic():
            # Re-checked under the transaction: two concurrent bootstraps cannot both pass
            # the email check (unique constraint) and the second sees the first superuser.
            self._check_preconditions(email, force_additional)
            user = User.objects.create_superuser(
                email, password, first_name=first_name, last_name=last_name
            )
            with suppress_m2m_audit():
                user.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
            record(
                actor=None,
                action="user.admin_bootstrapped",
                target=user,
                changes={
                    "status": [None, user.status],
                    "roles": [Role.TECHNICAL_ADMIN.value],
                    "is_superuser": True,
                    "force_additional": force_additional,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Created the administrator {email}."))
        self.stdout.write(
            "MFA enrollment is required at first login: signing in at /accounts/login/ "
            "asks for a two-factor (TOTP) authenticator app before anything else is "
            "available."
        )

    @staticmethod
    def _check_preconditions(email: str, force_additional: bool) -> None:
        if User.objects.annotate(email_lower=Lower("email")).filter(email_lower=email).exists():
            raise CommandError(f"An account with the email {email} already exists.")
        active_superusers = User.objects.filter(is_superuser=True, status=User.Status.ACTIVE)
        if not force_additional and active_superusers.exists():
            raise CommandError(
                "An active superuser already exists. Use --force-additional to create "
                "another one (the creation is audited)."
            )

    @staticmethod
    def _password(env_var: str | None) -> str:
        if env_var:
            password = os.environ.get(env_var, "")
            if not password:
                raise CommandError(f"The environment variable {env_var} is not set or empty.")
            return password
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("The passwords do not match.")
        return password
