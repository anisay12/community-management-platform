from django.contrib import admin

from .models import (
    AdminAccessGrant,
    Community,
    CommunityCategory,
    CommunityCreationRequest,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)


@admin.register(CommunityCategory)
class CommunityCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "icon", "order", "is_active")
    search_fields = ("name", "slug")


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "access_mode", "status", "member_count")
    list_filter = ("access_mode", "status", "category")
    search_fields = ("name", "slug")
    raw_id_fields = ("created_by",)
    filter_horizontal = ("tags",)


@admin.register(CommunityMembership)
class CommunityMembershipAdmin(admin.ModelAdmin):
    list_display = ("community", "user", "role", "joined_at")
    list_filter = ("role",)
    raw_id_fields = ("community", "user")


@admin.register(MembershipRequest)
class MembershipRequestAdmin(admin.ModelAdmin):
    list_display = ("community", "user", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("community", "user", "decided_by")


@admin.register(CommunityInvitation)
class CommunityInvitationAdmin(admin.ModelAdmin):
    list_display = ("community", "invited_user", "role", "status", "expires_at")
    list_filter = ("status",)
    raw_id_fields = ("community", "invited_user", "invited_by")


@admin.register(CommunityCreationRequest)
class CommunityCreationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "requester", "category", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("requester", "decided_by", "community")


@admin.register(AdminAccessGrant)
class AdminAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("community", "user", "created_at", "expires_at")
    raw_id_fields = ("community", "user")

    def has_change_permission(self, request, obj=None):
        return False
