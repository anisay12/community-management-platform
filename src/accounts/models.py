import uuid
import zoneinfo

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email=self.normalize_email(email).lower())

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("The email address is required.")
        extra.setdefault("status", User.Status.PENDING)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra):
        extra.setdefault("status", User.Status.ACTIVE)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("activated_at", timezone.now())
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        DEACTIVATED = "deactivated", _("Deactivated")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(_("email address"), max_length=254)
    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name = models.CharField(_("last name"), max_length=150, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    is_staff = models.BooleanField(_("staff status"), default=False)
    is_active = models.GeneratedField(
        verbose_name=_("active"),
        expression=models.Case(
            models.When(status="active", then=models.Value(True)),
            default=models.Value(False),
        ),
        output_field=models.BooleanField(),
        db_persist=True,
    )
    last_seen_at = models.DateTimeField(_("last seen at"), null=True, blank=True)
    activated_at = models.DateTimeField(_("activated at"), null=True, blank=True)
    deactivated_at = models.DateTimeField(_("deactivated at"), null=True, blank=True)
    anonymized_at = models.DateTimeField(_("anonymized at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.__class__.objects.normalize_email(self.email).lower()
        adding = self._state.adding
        super().save(*args, **kwargs)
        if not adding:
            # Django 5.2 refreshes generated fields after INSERT only. Dropping the
            # stale value makes it deferred, so it is lazily reloaded from the database
            # on next access (no extra query when it is never read).
            self.__dict__.pop("is_active", None)

    def get_full_name(self):
        if self.anonymized_at:
            return gettext("Former employee")
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name


def validate_timezone(value):
    if value not in zoneinfo.available_timezones():
        raise ValidationError(_("Unknown time zone."), code="invalid_timezone")


class UserProfile(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", _("Only me")
        COMMUNITIES = "communities", _("Members of my communities")
        COMPANY = "company", _("Everyone in the company")

    user = models.OneToOneField(
        User, verbose_name=_("user"), on_delete=models.CASCADE, related_name="profile"
    )
    job_title = models.CharField(_("job title"), max_length=150, blank=True)
    bio = models.TextField(_("bio"), blank=True, validators=[MaxLengthValidator(2000)])
    # Upload UI is deferred to L5 (antivirus scan and private file serving).
    avatar = models.ImageField(_("avatar"), upload_to="avatars/", blank=True, null=True)
    timezone = models.CharField(
        _("time zone"), max_length=64, default="Europe/Paris", validators=[validate_timezone]
    )
    language = models.CharField(
        _("language"),
        max_length=8,
        blank=True,
        choices=[("", _("Automatic")), ("en", "English"), ("fr", "Français")],
    )
    interests = models.ManyToManyField(
        "taxonomy.Tag", verbose_name=_("interests"), blank=True, related_name="profiles"
    )
    profile_visibility = models.CharField(
        _("profile visibility"),
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.COMPANY,
    )
    is_discoverable = models.BooleanField(_("discoverable"), default=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("user profile")
        verbose_name_plural = _("user profiles")

    def __str__(self):
        return str(self.user)


class ExternalIdentity(models.Model):
    user = models.ForeignKey(
        User,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="external_identities",
    )
    provider = models.CharField(_("provider"), max_length=50)
    subject = models.CharField(_("subject"), max_length=255)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    last_login_at = models.DateTimeField(_("last login at"), null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "subject"],
                name="accounts_externalidentity_provider_subject_unique",
            ),
            # One identity per provider per account: a second subject never links.
            models.UniqueConstraint(
                fields=["user", "provider"],
                name="accounts_externalidentity_user_provider_unique",
            ),
        ]
        verbose_name = _("external identity")
        verbose_name_plural = _("external identities")

    def __str__(self):
        return f"{self.provider}:{self.subject}"

    def save(self, *args, **kwargs):
        if self.pk:
            stored = type(self).objects.filter(pk=self.pk).values("provider", "subject").first()
            if stored and (stored["provider"], stored["subject"]) != (self.provider, self.subject):
                raise ValueError("ExternalIdentity provider and subject are immutable.")
        super().save(*args, **kwargs)


class UserSession(models.Model):
    """A session opened by a user, so that all of them can be ended at once."""

    user = models.ForeignKey(
        User,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="tracked_sessions",
    )
    session_key = models.CharField(_("session key"), max_length=40, unique=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("user session")
        verbose_name_plural = _("user sessions")

    def __str__(self):
        return str(self.user)


class DataExport(models.Model):
    """A copy of a user's personal data, built in the background and kept for a few days."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Being prepared")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        User, verbose_name=_("user"), on_delete=models.CASCADE, related_name="data_exports"
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.PENDING
    )
    file = models.FileField(_("file"), upload_to="exports/", blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("data export")
        verbose_name_plural = _("data exports")

    def __str__(self):
        return f"{self.user} ({self.status})"

    def is_downloadable(self) -> bool:
        return (
            self.status == self.Status.READY
            and bool(self.file)
            and self.expires_at is not None
            and self.expires_at > timezone.now()
        )
