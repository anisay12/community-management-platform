from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("category", "recipient", "created_at", "read_at")
    list_filter = ("category",)
    raw_id_fields = ("recipient", "actor", "community")
