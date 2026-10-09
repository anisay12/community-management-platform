from django.contrib import admin

from .models import Employment, OrganizationUnit


@admin.register(OrganizationUnit)
class OrganizationUnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "parent")
    search_fields = ("code", "name")


@admin.register(Employment)
class EmploymentAdmin(admin.ModelAdmin):
    list_display = ("user", "unit", "manager")
    search_fields = ("user__email", "user__last_name")
    raw_id_fields = ("user", "manager")
