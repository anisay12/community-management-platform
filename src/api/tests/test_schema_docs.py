import re

import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/api/schema/", "/api/docs/"])
def test_anonymous_refused(api_client, path):
    assert api_client.get(path).status_code == 401


def test_schema_for_employees(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get("/api/schema/")
    assert response.status_code == 200
    assert b"/api/v1/me/" in response.content


def test_docs_have_no_inline_script_nor_cdn(client, active_user):
    client.force_login(active_user)
    html = client.get("/api/docs/").content.decode()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
    assert "cdn." not in html and "unpkg" not in html


def test_docs_script_is_guarded_and_served_as_javascript(api_client, active_user):
    assert api_client.get("/api/docs/?script=").status_code == 401
    api_client.force_login(active_user)
    response = api_client.get("/api/docs/?script=")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
