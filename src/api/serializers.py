"""Serializers shared by every app's API."""

from rest_framework import serializers

from accounts.policies import requires_mfa
from accounts.roles import user_roles
from accounts.templatetags.accounts_tags import avatar_url


class UserSummarySerializer(serializers.Serializer):
    """How another user appears in the API: the name and photo every employee sees in the UI.

    Bio and interests are not part of it (they follow ``profile_visibility``). Lists must
    ``select_related("profile")``.
    """

    public_id = serializers.UUIDField(read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    def get_display_name(self, user) -> str:
        return user.get_full_name() or user.email

    def get_avatar_url(self, user) -> str:
        return avatar_url(user)


class MeSerializer(UserSummarySerializer):
    email = serializers.EmailField(read_only=True)
    roles = serializers.SerializerMethodField()
    requires_mfa = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    timezone = serializers.SerializerMethodField()

    def get_roles(self, user) -> list[str]:
        return sorted(user_roles(user))

    def get_requires_mfa(self, user) -> bool:
        return requires_mfa(user)

    def get_language(self, user) -> str:
        profile = getattr(user, "profile", None)
        return profile.language if profile is not None else ""

    def get_timezone(self, user) -> str:
        profile = getattr(user, "profile", None)
        return profile.timezone if profile is not None else ""
