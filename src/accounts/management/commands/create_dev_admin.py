import getpass
import os

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.functions import Lower

from accounts.models import User
from accounts.roles import Role
from audit.services import record

PASSWORD_ENV_VAR = "DEV_ADMIN_PASSWORD"  # noqa: S105  # variable name, not a secret


class Command(BaseCommand):
    help = (
        "Development helper: create an active superuser with the functional_admin role. "
        "Refuses to run when DEBUG is false. The password is prompted, or read from "
        f"${PASSWORD_ENV_VAR} with --password-from-env."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--password-from-env",
            action="store_true",
            help=f"Read the password from the {PASSWORD_ENV_VAR} environment variable.",
        )

    def handle(self, *args, email, password_from_env=False, **options):
        if not settings.DEBUG:
            raise CommandError("create_dev_admin only runs when DEBUG is true (development).")
        email = User.objects.normalize_email(email.strip()).lower()
        if User.objects.annotate(email_lower=Lower("email")).filter(email_lower=email).exists():
            raise CommandError(f"An account with the email {email} already exists.")
        password = self._password(password_from_env)
        try:
            validate_password(password)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        with transaction.atomic():
            user = User.objects.create_superuser(
                email, password, first_name="Dev", last_name="Admin"
            )
            user.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
            record(
                actor=None,
                action="user.dev_admin_created",
                target=user,
                changes={"roles": [Role.FUNCTIONAL_ADMIN.value]},
            )
        self.stdout.write(self.style.SUCCESS(f"Created the development administrator {email}."))
        self.stdout.write(
            "Next step: sign in at /accounts/login/. The first login asks you to enrol a "
            "two-factor (TOTP) authenticator app before anything else is available."
        )

    def _password(self, from_env: bool) -> str:
        if from_env:
            password = os.environ.get(PASSWORD_ENV_VAR, "")
            if not password:
                raise CommandError(f"{PASSWORD_ENV_VAR} is not set.")
            return password
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("The passwords do not match.")
        return password
