from django.apps import apps
from django.contrib import admin

from .models import AuditEvent


class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target_type", "target_id", "actor", "request_id")
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "request_id", "actor__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# django.contrib.admin is installed by a later task; register once it is.
if apps.is_installed("django.contrib.admin"):
    admin.site.register(AuditEvent, AuditEventAdmin)
