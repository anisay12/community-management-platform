from django.conf import settings

from core.tasks import ping


def test_ping_task_runs():
    assert ping.delay().get(timeout=5) == "pong"


def test_celery_reliability_settings():
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 240
    assert settings.CELERY_TASK_TIME_LIMIT == 300
