import pytest
from django.apps import apps
from django.utils.functional import Promise

APP_LABELS = {"accounts", "organizations", "taxonomy"}
# Inherited from Django base classes, already translated by Django itself.
DJANGO_INHERITED = {"password", "last_login", "is_superuser", "groups", "user_permissions"}


def _declared_fields():
    for model in apps.get_models():
        if model._meta.app_label not in APP_LABELS:
            continue
        for field in model._meta.get_fields():
            if not getattr(field, "concrete", False) and not field.many_to_many:
                continue
            if field.auto_created:
                continue
            if field.name in DJANGO_INHERITED:
                continue
            yield pytest.param(field, id=f"{model._meta.label}.{field.name}")


@pytest.mark.parametrize("field", list(_declared_fields()))
def test_field_has_lazy_translated_verbose_name(field):
    assert isinstance(field._verbose_name, Promise), (
        f"{field.model._meta.label}.{field.name} needs a gettext_lazy verbose_name"
    )
