from django.contrib import admin

from .models import Document, DocumentVersion


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    can_delete = False
    fields = ("version_label", "original_filename", "size", "scan_status", "is_reference")
    readonly_fields = fields


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "doc_type", "status", "owner_display", "updated_at")
    list_filter = ("doc_type", "status", "visibility")
    search_fields = ("title", "owner_display")
    raw_id_fields = ("community", "owner", "current_version", "archived_by")
    readonly_fields = ("public_id", "download_count")
    inlines = [DocumentVersionInline]


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document", "version_label", "scan_status", "size", "created_at")
    list_filter = ("scan_status",)
    raw_id_fields = ("document", "uploaded_by")
    readonly_fields = ("public_id", "storage_key", "sha256", "size", "mime_type")
