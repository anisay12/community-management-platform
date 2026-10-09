import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .roles import CommunityRole

INVITATION_TTL = timedelta(days=14)
ADMIN_ACCESS_TTL = timedelta(hours=1)
REASON_MIN_LENGTH = 10


def _invitation_expiry():
    return timezone.now() + INVITATION_TTL


def _admin_access_expiry():
    return timezone.now() + ADMIN_ACCESS_TTL


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), default=timezone.now)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class CommunityCategory(TimestampedModel):
    name = models.CharField(_("name"), max_length=100, unique=True)
    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    description = models.TextField(_("description"), blank=True)
    icon = models.CharField(
        _("icon"), max_length=64, default="people", help_text=_("Bootstrap Icons name.")
    )
    order = models.IntegerField(_("order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("community category")
        verbose_name_plural = _("community categories")

    def __str__(self) -> str:
        return self.name


class CommunityQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Communities whose metadata ``user`` may see (see ``policies.can_view_metadata``)."""
        from .policies import is_functional_admin

        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if is_functional_admin(user):
            return self.all()
        Status, AccessMode = Community.Status, Community.AccessMode
        public = models.Q(
            access_mode__in=[AccessMode.OPEN, AccessMode.REQUEST],
            status__in=[Status.ACTIVE, Status.SUSPENDED],
        )
        invite = models.Q(
            access_mode=AccessMode.INVITE,
            status__in=[Status.ACTIVE, Status.SUSPENDED],
            listed=True,
        )
        member_ids = CommunityMembership.objects.filter(user=user).values("community_id")
        # Members see their communities whatever the access mode or status.
        return self.filter(public | invite | models.Q(pk__in=member_ids))


class Community(TimestampedModel):
    class AccessMode(models.TextChoices):
        OPEN = "open", _("Open")
        REQUEST = "request", _("On request")
        INVITE = "invite", _("Invitation only")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        ARCHIVED = "archived", _("Archived")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=140, unique=True)
    category = models.ForeignKey(
        CommunityCategory,
        on_delete=models.PROTECT,
        related_name="communities",
        verbose_name=_("category"),
    )
    tagline = models.CharField(_("tagline"), max_length=160)
    description = models.TextField(_("description"), blank=True)
    rules = models.TextField(_("rules"), blank=True)
    objectives = models.TextField(_("objectives"), blank=True)
    cover_image = models.FileField(_("cover image"), upload_to="communities/covers/", blank=True)
    access_mode = models.CharField(
        _("access mode"), max_length=16, choices=AccessMode.choices, default=AccessMode.OPEN
    )
    listed = models.BooleanField(_("listed in the catalogue"), default=False)
    allow_member_uploads = models.BooleanField(_("members may upload files"), default=False)
    require_post_review = models.BooleanField(_("posts require review"), default=False)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("created by"),
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag", blank=True, related_name="communities", verbose_name=_("tags")
    )
    search_vector = SearchVectorField(_("search vector"), null=True, editable=False)
    member_count = models.PositiveIntegerField(_("member count"), default=0)
    last_activity_at = models.DateTimeField(_("last activity"), default=timezone.now)

    objects = CommunityQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                condition=~models.Q(status="archived"),
                name="community_name_unique_active",
            ),
        ]
        indexes = [
            GinIndex(fields=["search_vector"], name="community_search_gin"),
            models.Index(fields=["category"], name="community_category_idx"),
            models.Index(fields=["access_mode"], name="community_access_mode_idx"),
        ]
        verbose_name = _("community")
        verbose_name_plural = _("communities")

    def __str__(self) -> str:
        return self.name

    @property
    def is_read_only(self) -> bool:
        return self.status != self.Status.ACTIVE


class CommunityMembership(models.Model):
    Role = CommunityRole

    class NotificationLevel(models.TextChoices):
        ALL = "all", _("All activity")
        HIGHLIGHTS = "highlights", _("Highlights")
        NONE = "none", _("None")

    community = models.ForeignKey(
        Community, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("community")
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_memberships",
        verbose_name=_("user"),
    )
    role = models.CharField(
        _("role"), max_length=16, choices=CommunityRole.choices, default=CommunityRole.MEMBER
    )
    joined_at = models.DateTimeField(_("joined at"), default=timezone.now)
    notification_level = models.CharField(
        _("notification level"),
        max_length=16,
        choices=NotificationLevel.choices,
        default=NotificationLevel.HIGHLIGHTS,
    )
    created_at = models.DateTimeField(_("created at"), default=timezone.now)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["community", "user"], name="one_membership_per_user"),
        ]
        indexes = [
            models.Index(fields=["user", "community"], name="membership_user_community_idx"),
            models.Index(fields=["community", "role"], name="membership_community_role_idx"),
        ]
        verbose_name = _("community membership")
        verbose_name_plural = _("community memberships")

    def __str__(self) -> str:
        return f"{self.user_id}@{self.community_id} ({self.role})"


class MembershipRequest(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="membership_requests",
        verbose_name=_("community"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_membership_requests",
        verbose_name=_("user"),
    )
    message = models.TextField(_("message"), max_length=500, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    decision_note = models.TextField(_("decision note"), blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["community", "user"],
                condition=models.Q(status="pending"),
                name="one_pending_request",
            ),
        ]
        verbose_name = _("membership request")
        verbose_name_plural = _("membership requests")

    def __str__(self) -> str:
        return f"{self.user_id}->{self.community_id} ({self.status})"


class CommunityInvitation(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")
        REVOKED = "revoked", _("Revoked")
        EXPIRED = "expired", _("Expired")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    community = models.ForeignKey(
        Community, on_delete=models.CASCADE, related_name="invitations", verbose_name=_("community")
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_invitations",
        verbose_name=_("invited user"),
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("invited by"),
    )
    role = models.CharField(
        _("role"), max_length=16, choices=CommunityRole.choices, default=CommunityRole.MEMBER
    )
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    expires_at = models.DateTimeField(_("expires at"), default=_invitation_expiry)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["community", "invited_user"],
                condition=models.Q(status="pending"),
                name="one_pending_invitation",
            ),
            models.CheckConstraint(
                condition=~models.Q(role="owner"), name="invitation_role_below_owner"
            ),
        ]
        verbose_name = _("community invitation")
        verbose_name_plural = _("community invitations")

    def __str__(self) -> str:
        return f"{self.invited_user_id}->{self.community_id} ({self.status})"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()


class CommunityCreationRequest(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_creation_requests",
        verbose_name=_("requester"),
    )
    name = models.CharField(_("name"), max_length=120)
    category = models.ForeignKey(
        CommunityCategory, on_delete=models.PROTECT, related_name="+", verbose_name=_("category")
    )
    justification = models.TextField(_("justification"))
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    decision_note = models.TextField(_("decision note"), blank=True)
    community = models.ForeignKey(
        Community,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("community"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("community creation request")
        verbose_name_plural = _("community creation requests")

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class AdminAccessGrantQuerySet(models.QuerySet):
    def valid(self):
        return self.filter(expires_at__gt=timezone.now())


class AdminAccessGrant(models.Model):
    """Time-limited "access as administrator" to a community's content (audited)."""

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="admin_access_grants",
        verbose_name=_("community"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_admin_grants",
        verbose_name=_("user"),
    )
    reason = models.TextField(_("reason"), validators=[MinLengthValidator(REASON_MIN_LENGTH)])
    created_at = models.DateTimeField(_("created at"), default=timezone.now)
    expires_at = models.DateTimeField(_("expires at"), default=_admin_access_expiry)

    objects = AdminAccessGrantQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["community", "user", "expires_at"], name="admin_grant_lookup_idx")
        ]
        verbose_name = _("administrator access grant")
        verbose_name_plural = _("administrator access grants")

    def __str__(self) -> str:
        return f"{self.user_id}@{self.community_id} until {self.expires_at:%Y-%m-%d %H:%M}"
