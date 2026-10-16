"""Default permission of every API view: an active account with its second factor verified."""

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.policies import requires_mfa


class IsActiveVerifiedUser(BasePermission):
    """Authenticated (hence ``status == active``) and, for privileged roles, MFA verified.

    DRF's session authentication already treats an inactive account (pending, suspended,
    deactivated) as anonymous, so such a session answers 401. This class adds what
    ``accounts.middleware.MFARequiredMiddleware`` enforces for pages, answering JSON
    instead of redirecting.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not getattr(user, "is_authenticated", False):
            raise NotAuthenticated()
        if requires_mfa(user) and not user.is_verified():
            raise PermissionDenied(
                detail=_("Verify your second factor in the application first."),
                code="mfa_required",
            )
        return True
