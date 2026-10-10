from django.contrib import admin

from .models import Comment, ContentReport, Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "kind", "status", "author_display", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("title", "author_display")
    raw_id_fields = (
        "community",
        "author",
        "pinned_by",
        "hidden_by",
        "accepted_answer",
        "shared_from",
    )
    readonly_fields = ("public_id", "body_html")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "post", "author_display", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("post", "author", "parent", "hidden_by")
    readonly_fields = ("public_id", "body_html")


@admin.register(ContentReport)
class ContentReportAdmin(admin.ModelAdmin):
    list_display = ("public_id", "community", "reason", "status", "created_at")
    list_filter = ("status", "reason")
    raw_id_fields = ("reporter", "post", "comment", "community", "handled_by")
    readonly_fields = ("public_id",)
