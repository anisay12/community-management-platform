import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import translation

from core.views_errors import server_error

pytestmark = pytest.mark.django_db


def test_404_english_uses_app_layout(client):
    response = client.get("/does-not-exist/")
    assert response.status_code == 404
    assert b"<main" in response.content
    assert b'<html lang="en"' in response.content
    assert b"Page not found" in response.content


def test_404_french(client):
    response = client.get("/does-not-exist/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.status_code == 404
    assert b'<html lang="fr"' in response.content
    assert b"<main" in response.content
    assert b"Page introuvable" in response.content


@pytest.mark.urls("core.tests.urls_errors")
def test_403_status_and_layout(client):
    response = client.get("/forbidden/")
    assert response.status_code == 403
    assert b"<main" in response.content
    assert b"Access denied" in response.content


@pytest.mark.urls("core.tests.urls_errors")
def test_403_french(client):
    response = client.get("/forbidden/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.status_code == 403
    assert "Accès refusé".encode() in response.content


@pytest.mark.urls("core.tests.urls_errors")
def test_429_status_and_layout(client):
    response = client.get("/too-many/")
    assert response.status_code == 429
    assert b"<main" in response.content
    assert b"Too many requests" in response.content


def test_500_is_static_without_db_access(django_assert_num_queries):
    request = RequestFactory().get("/boom")
    request.request_id = "req-12345678"
    with django_assert_num_queries(0):
        response = server_error(request)
    assert response.status_code == 500
    assert b"req-12345678" in response.content
    assert b"<html" in response.content


def test_500_without_request_id():
    response = server_error(RequestFactory().get("/boom"))
    assert response.status_code == 500
    assert b"Reference" not in response.content


def test_500_escapes_request_id():
    request = RequestFactory().get("/boom")
    request.request_id = "<script>"
    response = server_error(request)
    assert b"<script>" not in response.content


def test_500_escapes_request_id_markup():
    request = RequestFactory().get("/boom")
    request.request_id = "<script>"
    response = server_error(request)
    assert b"&lt;script&gt;" in response.content


def test_500_french():
    request = RequestFactory().get("/boom")
    request.request_id = "req-12345678"
    with translation.override("fr"):
        response = server_error(request)
    assert b'<html lang="fr"' in response.content
    assert b"Erreur serveur" in response.content
    assert "Référence".encode() in response.content


@pytest.mark.urls("core.tests.urls_errors")
def test_429_french(client):
    response = client.get("/too-many/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.status_code == 429
    assert b'<html lang="fr"' in response.content
    assert "Trop de requêtes".encode() in response.content


def test_home_greets_signed_in_user(client):
    user = get_user_model().objects.create_user(
        "alice@example.com",
        password="pw-123456-x",
        first_name="Alice",
        last_name="Doe",
        status="active",
    )
    client.force_login(user)
    response = client.get("/")
    assert b"Hello, alice@example.com." in response.content
