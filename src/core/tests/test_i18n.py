from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_languages_configured():
    assert settings.LANGUAGE_CODE == "en"
    assert [code for code, _ in settings.LANGUAGES] == ["en", "fr"]


def test_home_defaults_to_english(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b'<html lang="en"' in response.content


def test_home_follows_accept_language(client):
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="fr-FR,fr;q=0.9")
    assert b'<html lang="fr"' in response.content
    assert "Communautés".encode() in response.content


def test_set_language_switches_to_french(client):
    response = client.post(reverse("set_language"), {"language": "fr", "next": "/"})
    assert response.status_code == 302
    assert b'<html lang="fr"' in client.get("/").content


def test_french_catalogue_is_complete():
    po = Path(settings.LOCALE_PATHS[0]) / "fr" / "LC_MESSAGES" / "django.po"
    text = po.read_text(encoding="utf-8")
    blocks = text.split("\n\n")[1:]
    assert all("#, fuzzy" not in block for block in blocks)
    untranslated = [
        b for b in blocks if b.rstrip().endswith('msgstr ""') and "msgid_plural" not in b
    ]
    assert untranslated == []
