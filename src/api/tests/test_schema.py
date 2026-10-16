import yaml
from django.core.management import call_command


def test_schema_has_no_warnings_and_describes_cookie_session_auth(tmp_path, settings):
    out = tmp_path / "schema.yml"

    call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(out))

    schema = yaml.safe_load(out.read_text())
    scheme = schema["components"]["securitySchemes"]["sessionAuth"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "cookie"
    assert scheme["name"] == settings.SESSION_COOKIE_NAME
