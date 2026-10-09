from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class ActivationTokenGenerator(PasswordResetTokenGenerator):
    """Single-use account activation token, valid ``ACCOUNT_ACTIVATION_TIMEOUT`` seconds.

    The hash covers the status and the password hash, so the token stops working as soon
    as the account is activated (or its status changes for any other reason).
    """

    key_salt = "accounts.tokens.ActivationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.status}{user.password}{user.email}{timestamp}"

    def check_token(self, user, token):
        # Same checks as Django 5.2's PasswordResetTokenGenerator.check_token, with the
        # activation timeout instead of PASSWORD_RESET_TIMEOUT.
        if not (user and token):
            return False
        if user.status != user.Status.PENDING:
            return False
        try:
            ts_b36, _ = token.split("-")
        except ValueError:
            return False
        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= settings.ACCOUNT_ACTIVATION_TIMEOUT


activation_token_generator = ActivationTokenGenerator()


class PasswordResetTokenGeneratorWithStatus(PasswordResetTokenGenerator):
    """Password reset token that also stops working when the account status changes.

    A link issued before a suspension (or deactivation) must not survive it.
    """

    key_salt = "accounts.tokens.PasswordResetTokenGeneratorWithStatus"

    def _make_hash_value(self, user, timestamp):
        return f"{super()._make_hash_value(user, timestamp)}{user.status}"


password_reset_token_generator = PasswordResetTokenGeneratorWithStatus()
