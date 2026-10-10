import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts import avatars, services
from accounts.models import User, UserProfile
from accounts.privacy import anonymize_user
from audit.models import AuditEvent
from config.celery import app as celery_app
from core.errors import DomainError
from documents import scanner
from documents.scanner import ScannerUnavailable, ScanResult

pytestmark = pytest.mark.django_db

Scan = UserProfile.AvatarScan


def image_bytes(fmt="PNG", size=(64, 48), exif=None, mode="RGB"):
    buffer = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    Image.new(mode, size, "red").save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def upload(content=None, name="me.png", content_type="image/png"):
    return SimpleUploadedFile(name, content if content is not None else image_bytes(), content_type)


@pytest.fixture
def clean_scan(monkeypatch):
    calls = []

    def fake(stream):
        calls.append(stream.read())
        return ScanResult(clean=True)

    monkeypatch.setattr(scanner, "scan_stream", fake)
    return calls


@pytest.fixture
def owner(make_user):
    return make_user("bob@example.com", first_name="Bob", last_name="Ray")


@pytest.fixture
def eager_retries(monkeypatch):
    """Let Celery's eager ``apply`` loop run the retries, as a worker would."""
    monkeypatch.setitem(celery_app.conf, "CELERY_TASK_EAGER_PROPAGATES", False)


@pytest.fixture
def saved_keys(monkeypatch):
    """Record the quarantine keys written by ``upload_avatar``."""
    from documents import storage

    keys = []
    original = storage.save_to_quarantine

    def save(upload):
        keys.append(original(upload))
        return keys[-1]

    monkeypatch.setattr(storage, "save_to_quarantine", save)
    return keys


def do_upload(user, django_capture_on_commit_callbacks, file=None):
    with django_capture_on_commit_callbacks(execute=True):
        services.upload_avatar(actor=user, upload=file or upload())
    return UserProfile.objects.get(user=user)


# Upload and scan ------------------------------------------------------------------


def test_clean_upload_becomes_reencoded_avatar(
    owner, clean_scan, saved_keys, django_capture_on_commit_callbacks
):
    profile = do_upload(owner, django_capture_on_commit_callbacks)
    assert profile.avatar_scan_status == Scan.NONE
    assert profile.avatar_pending_key == ""
    name = profile.avatar.name
    assert name.startswith("avatars/") and name.endswith(".webp")
    assert "bob" not in name.lower()
    with default_storage.open(name, "rb") as f:
        image = Image.open(f)
        assert image.format == "WEBP"
        assert image.size == (64, 48)
    assert len(clean_scan) == 1
    assert not default_storage.exists(saved_keys[0])
    assert AuditEvent.objects.filter(action="profile.avatar_update").count() == 1


def test_pending_keeps_previous_avatar(owner, monkeypatch):
    monkeypatch.setattr("accounts.services.delay_on_commit", lambda *a, **k: None)
    owner.profile.avatar.name = avatars.save_clean(image_bytes("WEBP"))
    owner.profile.save()
    services.upload_avatar(actor=owner, upload=upload())
    profile = UserProfile.objects.get(user=owner)
    assert profile.avatar_scan_status == Scan.PENDING
    assert profile.avatar_pending_key.startswith("quarantine/")
    assert profile.avatar.name.startswith("avatars/")


def test_large_image_is_downscaled_and_exif_stripped(
    owner, clean_scan, django_capture_on_commit_callbacks
):
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x0132] = "2026:01:01 00:00:00"
    content = image_bytes("JPEG", size=(2000, 1000), exif=exif.tobytes())
    assert b"SpyCam" in content
    profile = do_upload(
        owner, django_capture_on_commit_callbacks, upload(content, "p.jpg", "image/jpeg")
    )
    with default_storage.open(profile.avatar.name, "rb") as f:
        data = f.read()
    image = Image.open(io.BytesIO(data))
    assert max(image.size) == avatars.AVATAR_MAX_SIDE
    assert not image.getexif()
    assert b"SpyCam" not in data


def test_infected_upload_is_deleted_and_audited(
    owner, monkeypatch, saved_keys, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(
        scanner, "scan_stream", lambda s: ScanResult(clean=False, signature="Eicar")
    )
    profile = do_upload(owner, django_capture_on_commit_callbacks)
    assert profile.avatar_scan_status == Scan.INFECTED
    assert profile.avatar_pending_key == ""
    assert not profile.avatar
    assert not default_storage.exists(saved_keys[0])
    event = AuditEvent.objects.get(action="profile.avatar_infected")
    assert event.actor is None


def test_scanner_unavailable_retries_then_error(
    owner, monkeypatch, eager_retries, saved_keys, django_capture_on_commit_callbacks
):
    calls = []

    def down(stream):
        calls.append(1)
        raise ScannerUnavailable("down")

    monkeypatch.setattr(scanner, "scan_stream", down)
    profile = do_upload(owner, django_capture_on_commit_callbacks)
    assert len(calls) == 1 + 5
    assert profile.avatar_scan_status == Scan.ERROR
    assert not profile.avatar
    assert not default_storage.exists(saved_keys[0])


def test_scan_task_ignores_replaced_upload(owner, clean_scan, monkeypatch):
    from accounts.tasks import scan_avatar

    monkeypatch.setattr("accounts.services.delay_on_commit", lambda *a, **k: None)
    services.upload_avatar(actor=owner, upload=upload())
    scan_avatar.delay(owner.profile.pk, "quarantine/other")
    profile = UserProfile.objects.get(user=owner)
    assert profile.avatar_scan_status == Scan.PENDING
    assert clean_scan == []


# Refusals -------------------------------------------------------------------------


def test_too_large(owner, settings):
    settings.AVATAR_MAX_UPLOAD_BYTES = 100
    with pytest.raises(DomainError) as exc:
        services.upload_avatar(actor=owner, upload=upload())
    assert exc.value.code == "file_too_large"


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "x.svg"),
        (b"just some text pretending to be an image\n" * 10, "x.png"),
        (None, "x.gif"),
    ],
)
def test_wrong_type(owner, content, name):
    if content is None:
        content = image_bytes("GIF")
    with pytest.raises(DomainError) as exc:
        services.upload_avatar(actor=owner, upload=upload(content, name))
    assert exc.value.code == "invalid_type"
    assert UserProfile.objects.get(user=owner).avatar_scan_status == Scan.NONE


def test_truncated_image_refused(owner):
    with pytest.raises(DomainError) as exc:
        services.upload_avatar(actor=owner, upload=upload(image_bytes()[:20]))
    assert exc.value.code == "invalid_image"


def test_decompression_bomb_refused(owner, settings, saved_keys):
    settings.AVATAR_MAX_PIXELS = 1000
    with pytest.raises(DomainError) as exc:
        services.upload_avatar(actor=owner, upload=upload(image_bytes(size=(100, 100))))
    assert exc.value.code == "image_too_large"
    assert saved_keys == []


def test_reencode_refuses_bomb_header():
    # A PNG header announcing 50 000 x 50 000 pixels (checked before any decoding).
    import zlib

    content = bytearray(image_bytes(size=(1, 1)))
    content[16:24] = (50000).to_bytes(4, "big") * 2
    content[29:33] = zlib.crc32(bytes(content[12:29])).to_bytes(4, "big")
    with pytest.raises(DomainError) as exc:
        avatars.reencode(io.BytesIO(bytes(content)))
    assert exc.value.code == "image_too_large"


# Replace and remove ---------------------------------------------------------------


def test_replace_deletes_previous_object(owner, clean_scan, django_capture_on_commit_callbacks):
    first = do_upload(owner, django_capture_on_commit_callbacks).avatar.name
    second = do_upload(owner, django_capture_on_commit_callbacks).avatar.name
    assert first != second
    assert not default_storage.exists(first)
    assert default_storage.exists(second)


def test_new_upload_discards_pending_one(owner, monkeypatch, django_capture_on_commit_callbacks):
    monkeypatch.setattr("accounts.services.delay_on_commit", lambda *a, **k: None)
    services.upload_avatar(actor=owner, upload=upload())
    first = UserProfile.objects.get(user=owner).avatar_pending_key
    with django_capture_on_commit_callbacks(execute=True):
        services.upload_avatar(actor=owner, upload=upload())
    assert not default_storage.exists(first)


def test_remove(owner, clean_scan, django_capture_on_commit_callbacks):
    name = do_upload(owner, django_capture_on_commit_callbacks).avatar.name
    with django_capture_on_commit_callbacks(execute=True):
        services.remove_avatar(actor=owner)
    profile = UserProfile.objects.get(user=owner)
    assert not profile.avatar
    assert not default_storage.exists(name)
    assert AuditEvent.objects.filter(action="profile.avatar_remove").count() == 1
    services.remove_avatar(actor=owner)  # nothing left: no second event
    assert AuditEvent.objects.filter(action="profile.avatar_remove").count() == 1


# Views ----------------------------------------------------------------------------


def test_upload_and_remove_views(client, owner, clean_scan, django_capture_on_commit_callbacks):
    client.force_login(owner)
    assert client.get(reverse("accounts:avatar_upload")).status_code == 405
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("accounts:avatar_upload"), {"photo": upload()})
    assert response.status_code == 302
    profile = UserProfile.objects.get(user=owner)
    assert profile.avatar
    page = client.get(reverse("accounts:profile_edit")).content.decode()
    assert reverse("accounts:avatar", args=[owner.public_id]) in page
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("accounts:avatar_remove"))
    assert not UserProfile.objects.get(user=owner).avatar


def test_upload_view_shows_refusal(client, owner):
    client.force_login(owner)
    response = client.post(
        reverse("accounts:avatar_upload"), {"photo": upload(b"text " * 50)}, follow=True
    )
    assert "Only PNG, JPEG and WebP" in response.content.decode()


def test_upload_requires_csrf(owner):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    response = client.post(reverse("accounts:avatar_upload"), {"photo": upload()})
    assert response.status_code == 403


@pytest.fixture
def with_avatar(owner):
    owner.profile.avatar.name = avatars.save_clean(image_bytes("WEBP"))
    owner.profile.save()
    return owner


def avatar_url(user):
    return reverse("accounts:avatar", args=[user.public_id])


def test_serve_anonymous_redirects_to_login(client, with_avatar):
    response = client.get(avatar_url(with_avatar))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


def test_serve_owner_headers(client, with_avatar):
    client.force_login(with_avatar)
    response = client.get(avatar_url(with_avatar))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert response["Content-Disposition"] == "inline"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, max-age=300"
    assert response["Content-Security-Policy"].startswith("sandbox")
    assert response["X-Accel-Redirect"] == "/_protected/" + with_avatar.profile.avatar.name
    assert response.content == b""


def test_serve_dev_streaming(client, with_avatar, settings):
    settings.DEBUG = True
    settings.DOCUMENT_DEV_STREAMING = True
    client.force_login(with_avatar)
    response = client.get(avatar_url(with_avatar))
    assert b"".join(response.streaming_content)[:4] == b"RIFF"
    assert "X-Accel-Redirect" not in response


def test_serve_other_viewer_and_hidden_owner(client, with_avatar, make_user):
    viewer = make_user("carol@example.com")
    client.force_login(viewer)
    assert client.get(avatar_url(with_avatar)).status_code == 200
    with_avatar.status = User.Status.SUSPENDED
    with_avatar.save()
    assert client.get(avatar_url(with_avatar)).status_code == 404


def test_serve_without_avatar_404(client, owner):
    client.force_login(owner)
    assert client.get(avatar_url(owner)).status_code == 404


def test_profile_page_shows_photo_decoratively(client, with_avatar):
    client.force_login(with_avatar)
    html = client.get(
        reverse("accounts:profile_detail", args=[with_avatar.public_id])
    ).content.decode()
    assert f'src="{avatar_url(with_avatar)}?v=' in html
    assert 'alt=""' in html


# GDPR -----------------------------------------------------------------------------


def test_anonymization_deletes_avatar_and_pending_upload(
    owner, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr("accounts.services.delay_on_commit", lambda *a, **k: None)
    owner.profile.avatar.name = avatars.save_clean(image_bytes("WEBP"))
    owner.profile.save()
    services.upload_avatar(actor=owner, upload=upload())
    profile = UserProfile.objects.get(user=owner)
    current, pending = profile.avatar.name, profile.avatar_pending_key
    owner.status = User.Status.DEACTIVATED
    owner.save()
    with django_capture_on_commit_callbacks(execute=True):
        anonymize_user(actor=None, user=owner)
    profile = UserProfile.objects.get(user=owner)
    assert not profile.avatar and profile.avatar_pending_key == ""
    assert profile.avatar_scan_status == Scan.NONE
    assert not default_storage.exists(current)
    assert not default_storage.exists(pending)


def test_export_has_avatar(owner):
    from accounts.privacy import export_account

    owner.profile.avatar.save("x.webp", ContentFile(b"x"))
    assert export_account(owner)["profile"]["has_avatar"] is True
