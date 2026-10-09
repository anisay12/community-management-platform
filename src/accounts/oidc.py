"""OpenID Connect sign-in (Microsoft Entra ID) with immutable identity linking.

An account is bound to one identity-provider subject. The first SSO sign-in links an
existing account by email only when that account has no identity for the provider yet;
from then on only that subject reaches it, whatever email the provider later sends.
"""

import structlog
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError, transaction
from django.shortcuts import resolve_url
from django.utils import timezone
from django.utils.translation import gettext as _
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

from audit.services import record

from .models import ExternalIdentity, User

logger = structlog.get_logger(__name__)

NAME_MAX_LENGTH = 150


def claim_subject(claims: dict) -> str:
    """The provider's stable identifier: Entra's object ID when present, else ``sub``."""
    return str(claims.get("oid") or claims.get("sub") or "").strip()


def claim_email(claims: dict) -> str:
    return User.objects.normalize_email(str(claims.get("email") or "").strip()).lower()


class TalanOIDCBackend(OIDCAuthenticationBackend):
    """Resolves the provider's claims to an active account, never by email alone twice."""

    @property
    def provider(self) -> str:
        return settings.OIDC_PROVIDER_NAME

    def verify_claims(self, claims):
        if claims.get("email_verified") is False:
            return False
        return bool(claim_subject(claims) and claim_email(claims))

    def filter_users_by_claims(self, claims):
        linked = User.objects.filter(
            external_identities__provider=self.provider,
            external_identities__subject=claim_subject(claims),
        )
        if linked.exists():
            return linked
        # First sign-in: link by email, but never an account already bound to a subject.
        return User.objects.filter(email=claim_email(claims)).exclude(
            external_identities__provider=self.provider
        )

    def create_user(self, claims):
        """Create a pending account bound to the subject; an administrator activates it."""
        user = User.objects.create_user(
            claim_email(claims),
            first_name=str(claims.get("given_name") or "")[:NAME_MAX_LENGTH],
            last_name=str(claims.get("family_name") or "")[:NAME_MAX_LENGTH],
            status=User.Status.PENDING,
        )
        ExternalIdentity.objects.create(
            user=user, provider=self.provider, subject=claim_subject(claims)
        )
        record(
            actor=None,
            action="user.created_from_sso",
            target=user,
            changes={"provider": self.provider},
        )
        logger.info("sso_user_created", user=str(user.public_id), provider=self.provider)
        return user

    def get_or_create_user(self, access_token, id_token, payload):
        self._check_audience(payload)
        claims = self._merge_claims(self.get_userinfo(access_token, id_token, payload), payload)
        if not self.verify_claims(claims):
            raise SuspiciousOperation("Claims verification failed")
        try:
            with transaction.atomic():
                return self._sign_in(claims)
        except IntegrityError:
            # A concurrent first sign-in linked or created the same identity.
            logger.warning("sso_identity_race", provider=self.provider)
            return None

    def _check_audience(self, payload: dict) -> None:
        audience = payload.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if self.OIDC_RP_CLIENT_ID not in audiences:
            raise SuspiciousOperation("ID token audience does not match this client")

    @staticmethod
    def _merge_claims(userinfo: dict, payload: dict) -> dict:
        """Userinfo claims, with the subject identifiers taken from the signed ID token."""
        claims = dict(userinfo)
        for key in ("sub", "oid"):
            if payload.get(key):
                if claims.get(key) not in (None, payload[key]):
                    raise SuspiciousOperation(f"Userinfo {key} does not match the ID token")
                claims[key] = payload[key]
        return claims

    def _sign_in(self, claims: dict):
        subject = claim_subject(claims)
        users = list(self.filter_users_by_claims(claims).select_for_update(of=("self",)))
        if len(users) > 1:
            raise SuspiciousOperation("Multiple users returned")
        if not users:
            if User.objects.filter(email=claim_email(claims)).exists():
                # The email belongs to an account bound to another subject.
                logger.warning("sso_identity_conflict", provider=self.provider)
                return None
            self.create_user(claims)
            return None
        user = users[0]
        if user.status != User.Status.ACTIVE:
            logger.info("sso_login_refused", user=str(user.public_id), status=user.status)
            return None
        identity = ExternalIdentity.objects.filter(user=user, provider=self.provider).first()
        if identity is None:
            identity = ExternalIdentity.objects.create(
                user=user, provider=self.provider, subject=subject
            )
            record(
                actor=user,
                action="auth.sso_linked",
                target=user,
                changes={"provider": self.provider},
            )
        elif identity.subject != subject:
            logger.warning("sso_identity_conflict", provider=self.provider)
            return None
        identity.last_login_at = timezone.now()
        identity.save(update_fields=["last_login_at"])
        record(
            actor=user, action="auth.sso_login", target=user, changes={"provider": self.provider}
        )
        return user


class TalanOIDCCallbackView(OIDCAuthenticationCallbackView):
    """Sends a failed sign-in back to the login page with a neutral explanation."""

    @property
    def failure_url(self):
        return resolve_url(settings.LOGIN_URL)

    def login_failure(self):
        messages.error(
            self.request,
            _(
                "Single sign-on did not complete. If your account is new or inactive, "
                "contact an administrator."
            ),
        )
        return super().login_failure()
