from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "status", "is_staff")
    list_filter = ("status", "is_staff", "groups")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    readonly_fields = ("public_id", "last_seen_at", "created_at", "updated_at")
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
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name")}),
    )
    filter_horizontal = ("groups", "user_permissions")
