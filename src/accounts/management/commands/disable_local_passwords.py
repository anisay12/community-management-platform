from django.conf import settings
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX, make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.backends import is_break_glass
from accounts.models import User
from audit.services import record


class Command(BaseCommand):
    help = (
        "Set an unusable password on every account except the break-glass account. "
        "Only allowed when AUTH_MODE is sso_only. Changing the password signs the "
        "affected users out of their current sessions (Django's session hash is derived "
        "from the password); they sign in again with single sign-on. Password reset is "
        "unavailable in sso_only: the break-glass password is reset by a technical "
        "administrator (manage.py changepassword)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Only count the accounts concerned."
        )

    def handle(self, *args, dry_run=False, **options):
        if settings.AUTH_MODE != "sso_only":
            raise CommandError(
                f"AUTH_MODE is {settings.AUTH_MODE}: local passwords may only be disabled "
                "in sso_only mode."
            )
        with transaction.atomic():
            users = [
                user
                for user in User.objects.select_for_update()
                .exclude(password__startswith=UNUSABLE_PASSWORD_PREFIX)
                .only("pk", "email", "password")
                if not is_break_glass(user)
            ]
            break_glass_email = settings.BREAK_GLASS_EMAIL.strip()
            break_glass_kept = bool(break_glass_email) and (
                User.objects.filter(email__iexact=break_glass_email).exists()
            )
            if dry_run:
                self.stdout.write(
                    f"Dry run: {len(users)} account(s) would have their local password "
                    "disabled and their current sessions signed out."
                )
                return
            for user in users:
                user.password = make_password(None)
            User.objects.bulk_update(users, ["password"], batch_size=500)
            record(
                actor=None,
                action="auth.local_passwords_disabled",
                target=("accounts.user", "*"),
                changes={"count": len(users), "break_glass_kept": break_glass_kept},
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Disabled local passwords for {len(users)} account(s); "
                "their current sessions are signed out."
            )
        )
        if not break_glass_kept:
            self.stdout.write(self.style.WARNING("No break-glass account is configured."))
