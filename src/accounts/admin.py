from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .admin_forms import UserAddForm
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Django admin for accounts.

    Status and privilege flags are read-only here: status changes go through the audited
    ``/manage/`` pages (which also end sessions), superusers through ``create_admin``.
    Group edits stay possible; the m2m signal audits them.
    """

    list_display = ("email", "first_name", "last_name", "status", "is_staff")
    list_filter = ("status", "is_staff", "groups")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    readonly_fields = (
        "public_id",
        "status",
        "activated_at",
        "deactivated_at",
        "anonymized_at",
        "is_staff",
        "is_superuser",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("public_id", "email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (_("Status"), {"fields": ("status", "activated_at", "deactivated_at", "anonymized_at")}),
        (
            _("Permissions"),
            {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_seen_at", "created_at", "updated_at")}),
    )
    add_form = UserAddForm
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name")}),
    )
    filter_horizontal = ("groups", "user_permissions")

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        created = form.create(actor=request.user)
        # The admin goes on with ``obj`` (related objects, change message, redirect).
        for model_field in User._meta.concrete_fields:
            setattr(obj, model_field.attname, getattr(created, model_field.attname))
        obj._state.adding = False
        obj._state.db = created._state.db
