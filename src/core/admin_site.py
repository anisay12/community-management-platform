from functools import wraps

from django.contrib.admin import AdminSite
from django.http import Http404


class SecureAdminSite(AdminSite):
    """Admin site reserved to active staff with a verified second factor.

    Everyone else gets a 404, so the admin path is not disclosed: there is no admin
    login page, users sign in through the application and its MFA flow.
    """

    def has_permission(self, request) -> bool:
        user = request.user
        is_verified = getattr(user, "is_verified", None)
        return bool(user.is_active and user.is_staff and callable(is_verified) and is_verified())

    def admin_view(self, view, cacheable=False):
        protected = super().admin_view(view, cacheable)

        @wraps(view)
        def inner(request, *args, **kwargs):
            if not self.has_permission(request):
                raise Http404
            return protected(request, *args, **kwargs)

        return inner

    def login(self, request, extra_context=None):
        raise Http404
